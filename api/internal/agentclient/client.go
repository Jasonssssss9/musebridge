// Package agentclient implements the private Agent run transport without
// conversation persistence, model access, polling, or automatic POST retries.
package agentclient

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
	"unicode"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

// MaxCallDuration is a defensive transport ceiling. Callers must pass the
// original Go attempt context so admission, reconciliation and commit share
// the existing 60-second budget rather than starting another budget here.
const MaxCallDuration = 60 * time.Second

// Client can be reused concurrently. Authentication is private configuration;
// artwork scope, snapshots and the Agent deadline come from the original run.
type Client struct {
	endpoint  string
	token     string
	http      *http.Client
	validator *ac.Validator
	timeout   time.Duration
}

// New requires an HTTP(S) origin and a visible ASCII service token of at least
// 43 characters, matching the FastAPI factory's 32-random-byte encoding rule.
// Length cannot establish entropy. Constructor errors never echo configuration.
func New(baseURL, serviceToken string) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") ||
		u.Hostname() == "" || u.User != nil || u.Opaque != "" ||
		(u.Path != "" && u.Path != "/") || u.RawPath != "" || u.RawQuery != "" || u.ForceQuery ||
		strings.Contains(baseURL, "#") || strings.ContainsFunc(baseURL, unicode.IsSpace) ||
		strings.HasSuffix(u.Host, ":") {
		return nil, fmt.Errorf("Agent base URL must be an HTTP(S) origin without credentials, query or fragment")
	}
	if port := u.Port(); port != "" {
		number, err := strconv.Atoi(port)
		if err != nil || number < 1 || number > 65535 {
			return nil, fmt.Errorf("Agent base URL has an invalid port")
		}
	}
	if strings.Contains(u.Hostname(), ":") && net.ParseIP(u.Hostname()) == nil {
		return nil, fmt.Errorf("Agent base URL has an invalid IPv6 host")
	}
	if len(serviceToken) < 43 || !visibleASCII(serviceToken, 0) {
		return nil, fmt.Errorf("Agent service token must encode at least 32 random bytes as visible ASCII")
	}
	validator, err := ac.DefaultValidator()
	if err != nil {
		return nil, fmt.Errorf("Agent contract initialization failed")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableCompression = true
	return &Client{endpoint: strings.TrimSuffix(baseURL, "/") + "/internal/v2/agent/runs",
		token: serviceToken, validator: validator, timeout: MaxCallDuration,
		http: &http.Client{Transport: transport,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

func (c *Client) CloseIdleConnections() { c.http.CloseIdleConnections() }

// Formatting a client must not print its Bearer token.
func (c *Client) String() string   { return "agentclient.Client" }
func (c *Client) GoString() string { return c.String() }

// Run performs one POST and returns only a validated completed snapshot.
// It does not reject an elapsed execution.deadline_at locally: the Agent must
// check retained-run replay before new-run admission deadlines. The caller's
// original Go attempt deadline still bounds the entire transport and validation.
func (c *Client) Run(ctx context.Context, input ac.RunRequest, requestID string) (ac.RunSnapshot, error) {
	return c.call(ctx, input, requestID, http.MethodPost)
}

// Get queries the original run without starting work or polling. Processing
// returns a snapshot with no result. Completed results are bound to the frozen
// original request. A retained failed run returns a safe *Error with RunState
// "failed" and no snapshot. HTTP/query failures leave RunState empty.
// An elapsed Agent admission deadline does not prevent retained-run lookup;
// callers must retain the original Go attempt context and remaining budget.
func (c *Client) Get(ctx context.Context, input ac.RunRequest, requestID string) (ac.RunSnapshot, error) {
	return c.call(ctx, input, requestID, http.MethodGet)
}

func (c *Client) call(ctx context.Context, input ac.RunRequest, requestID, method string) (ac.RunSnapshot, error) {
	if ctx == nil || !visibleASCII(requestID, 128) {
		return ac.RunSnapshot{}, invalidResponse()
	}
	attempt, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	failed := func(err error) (ac.RunSnapshot, error) {
		if expired := contextFailure(ctx, attempt); expired != nil {
			return ac.RunSnapshot{}, expired
		}
		return ac.RunSnapshot{}, err
	}
	if err := contextFailure(ctx, attempt); err != nil {
		return ac.RunSnapshot{}, err
	}
	body, err := c.validator.Encode("RunRequest", input)
	if err != nil {
		return failed(invalidResponse())
	}
	// Decode the encoded copy before I/O so later caller edits cannot change the
	// request against which this response is checked.
	var run ac.RunRequest
	if c.validator.Decode("RunRequest", body, &run) != nil || c.validator.CheckRunRequest(run) != nil {
		return failed(invalidResponse())
	}
	if err := contextFailure(ctx, attempt); err != nil {
		return ac.RunSnapshot{}, err
	}
	endpoint := c.endpoint
	var content io.Reader
	if method == http.MethodPost {
		content = bytes.NewReader(body)
	} else {
		// The shared UUID schema has already validated this path component.
		endpoint += "/" + string(run.RunID)
	}
	request, err := http.NewRequestWithContext(attempt, method, endpoint, content)
	if err != nil {
		return failed(invalidResponse())
	}
	request.Header.Set("Authorization", "Bearer "+c.token)
	request.Header.Set("X-Request-ID", requestID)
	if method == http.MethodPost {
		request.Header.Set("Content-Type", "application/json")
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Accept-Encoding", "identity")
	response, err := c.http.Do(request)
	if err != nil {
		return failed(unavailable(true, nil))
	}
	defer response.Body.Close()
	if err := contextFailure(ctx, attempt); err != nil {
		return ac.RunSnapshot{}, err
	}
	if !validHeaders(response, requestID) {
		return failed(invalidResponse())
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, ac.PrivateBodyLimit+1))
	if err != nil {
		// A broken response leaves the run outcome unknown. The caller can query
		// the original run within its budget, without a new model execution.
		return failed(unavailable(true, nil))
	}
	if err := contextFailure(ctx, attempt); err != nil {
		return ac.RunSnapshot{}, err
	}
	if len(data) > ac.PrivateBodyLimit || (response.ContentLength >= 0 && response.ContentLength != int64(len(data))) {
		return failed(invalidResponse())
	}
	if response.StatusCode != http.StatusOK {
		return failed(c.decodeFailure(response.StatusCode, data, run.RunID, requestID))
	}
	var snapshot ac.RunSnapshot
	if c.validator.Decode("RunSnapshot", data, &snapshot) != nil || snapshot.RunID != run.RunID {
		return failed(invalidResponse())
	}
	switch snapshot.State {
	case "completed":
		if snapshot.Result == nil || snapshot.Error != nil || c.validator.CheckRunResult(run, *snapshot.Result) != nil {
			return failed(invalidResponse())
		}
	case "processing":
		if method != http.MethodGet || snapshot.Result != nil || snapshot.Error != nil {
			return failed(invalidResponse())
		}
	case "failed":
		if method != http.MethodGet || snapshot.Result != nil || snapshot.Error == nil {
			return failed(invalidResponse())
		}
		failure, err := ac.MapRunFailure(*snapshot.Error)
		if err != nil {
			return failed(invalidResponse())
		}
		return failed(&Error{Failure: *failure, RunState: "failed"})
	default:
		return failed(invalidResponse())
	}
	if err := contextFailure(ctx, attempt); err != nil {
		return ac.RunSnapshot{}, err
	}
	return snapshot, nil
}

func (c *Client) decodeFailure(status int, data []byte, runID ac.UUID, requestID string) error {
	var envelope ac.ErrorEnvelope
	if c.validator.Decode("ErrorEnvelope", data, &envelope) != nil || envelope.RequestID != requestID ||
		(envelope.RunID != nil && *envelope.RunID != runID) {
		return invalidResponse()
	}
	return &Error{Failure: *ac.MapAgentFailure(status, envelope.Error)}
}

func visibleASCII(value string, maximum int) bool {
	if len(value) == 0 || (maximum > 0 && len(value) > maximum) {
		return false
	}
	for i := range value {
		if value[i] < 33 || value[i] > 126 {
			return false
		}
	}
	return true
}

func validHeaders(response *http.Response, requestID string) bool {
	header := response.Header
	if len(header.Values("Content-Type")) != 1 || len(header.Values("X-Request-ID")) != 1 ||
		header.Get("X-Request-ID") != requestID || len(header.Values("Cache-Control")) != 1 ||
		!strings.EqualFold(strings.TrimSpace(header.Get("Cache-Control")), "no-store") ||
		response.ContentLength > ac.PrivateBodyLimit || response.Uncompressed {
		return false
	}
	mediaType, parameters, err := mime.ParseMediaType(header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" || len(parameters) > 1 {
		return false
	}
	for key, value := range parameters {
		if key != "charset" || !strings.EqualFold(value, "utf-8") {
			return false
		}
	}
	encoding := header.Values("Content-Encoding")
	return len(encoding) == 0 || (len(encoding) == 1 && strings.EqualFold(strings.TrimSpace(encoding[0]), "identity"))
}
