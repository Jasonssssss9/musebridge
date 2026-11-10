// Package retrieval implements B's bounded, validating HTTP client for C.
package retrieval

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
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
	"unicode/utf8"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/jsonwire"
)

const (
	RequestTimeout   = 3 * time.Second
	MaxResponseBytes = 64 << 10
	TopK             = 2
)

// Request contains only the scope and prepared query that B is allowed to send.
// Version and result limit come from the pinned dataset and local contract.
type Request struct {
	ArtworkID string
	Query     string
	RequestID string
}

// Result contains validated evidence in C's rank order. Empty evidence is a
// successful retrieval result, not an answer status or a dependency failure.
type Result struct {
	DatasetVersion string             `json:"dataset_version"`
	Evidence       []catalog.Evidence `json:"evidence"`
}

// Client is reusable across concurrent calls. Its dataset is immutable.
type Client struct {
	endpoint string
	dataset  *catalog.Dataset
	http     *http.Client
	timeout  time.Duration
}

func New(baseURL string, dataset *catalog.Dataset) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") ||
		u.Hostname() == "" || u.User != nil || u.Opaque != "" || u.Path != "" ||
		u.RawQuery != "" || u.ForceQuery || strings.Contains(baseURL, "#") ||
		strings.ContainsFunc(baseURL, unicode.IsSpace) || strings.HasSuffix(u.Host, ":") {
		return nil, fmt.Errorf("retrieval base URL must be an HTTP(S) origin without credentials, path, query or fragment")
	}
	if port := u.Port(); port != "" {
		number, err := strconv.Atoi(port)
		if err != nil || number < 1 || number > 65535 {
			return nil, fmt.Errorf("retrieval base URL has an invalid port")
		}
	}
	if strings.Contains(u.Hostname(), ":") && net.ParseIP(u.Hostname()) == nil {
		return nil, fmt.Errorf("retrieval base URL has an invalid IPv6 host")
	}
	if dataset == nil {
		return nil, fmt.Errorf("retrieval requires a loaded dataset")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// The private retrieval address is reached directly, including in Compose.
	transport.Proxy = nil
	return &Client{
		endpoint: baseURL + "/retrieve", dataset: dataset, timeout: RequestTimeout,
		http: &http.Client{
			Transport:     transport,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func (c *Client) CloseIdleConnections() { c.http.CloseIdleConnections() }

// Retrieve performs one HTTP attempt, validates all evidence, and returns no
// partial result on failure. Parent cancellation/deadline errors are preserved
// for the question orchestrator to classify; C's own deadline is retrieval_timeout.
func (c *Client) Retrieve(ctx context.Context, input Request) (Result, error) {
	if ctx == nil || !validRequestID(input.RequestID) || !validQuery(input.Query) {
		return Result{}, invalidRequest()
	}
	if _, exists := c.dataset.ArtworkBySlug(input.ArtworkID); !exists {
		return Result{}, &Error{Code: "artwork_not_found", HTTPStatus: 404, Message: "The requested artwork is not in the catalog."}
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	body, err := json.Marshal(struct {
		ArtworkID      string `json:"artwork_id"`
		Query          string `json:"query"`
		TopK           int    `json:"top_k"`
		DatasetVersion string `json:"dataset_version"`
	}{input.ArtworkID, strings.TrimSpace(input.Query), TopK, c.dataset.Version()})
	if err != nil || len(body) > 8192 {
		return Result{}, invalidRequest()
	}
	attempt, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(attempt, http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return Result{}, invalidRequest()
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-Request-ID", input.RequestID)
	response, err := c.http.Do(request)
	if err != nil {
		return Result{}, classifyIOError(ctx, attempt, err)
	}
	defer response.Body.Close()
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if attempt.Err() != nil {
		return Result{}, timedOut(attempt.Err())
	}
	if !allowedStatus(response.StatusCode) || !jsonContentType(response.Header) ||
		len(response.Header.Values("X-Request-ID")) != 1 || response.Header.Get("X-Request-ID") != input.RequestID ||
		response.ContentLength > MaxResponseBytes {
		return Result{}, invalidResponse(nil)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, MaxResponseBytes+1))
	if err != nil {
		if ctx.Err() != nil || attempt.Err() != nil {
			return Result{}, classifyIOError(ctx, attempt, err)
		}
		if errors.Is(err, io.ErrUnexpectedEOF) {
			return Result{}, invalidResponse(err)
		}
		return Result{}, classifyIOError(ctx, attempt, err)
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if attempt.Err() != nil {
		return Result{}, timedOut(attempt.Err())
	}
	if len(data) > MaxResponseBytes {
		return Result{}, invalidResponse(nil)
	}
	if err := jsonwire.Validate(data); err != nil {
		return Result{}, invalidResponse(err)
	}
	if response.StatusCode != http.StatusOK {
		return Result{}, decodeFailure(response.StatusCode, input.RequestID, data)
	}
	result, err := c.decodeResult(data, input.ArtworkID)
	if err != nil {
		return Result{}, invalidResponse(err)
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if attempt.Err() != nil {
		return Result{}, timedOut(attempt.Err())
	}
	return result, nil
}

func (c *Client) decodeResult(data []byte, artworkID string) (Result, error) {
	var envelope struct {
		DatasetVersion string            `json:"dataset_version"`
		Evidence       []json.RawMessage `json:"evidence"`
	}
	if err := jsonwire.DecodeFields(data, "dataset_version evidence", &envelope); err != nil {
		return Result{}, err
	}
	if envelope.DatasetVersion != c.dataset.Version() || len(envelope.Evidence) > TopK {
		return Result{}, fmt.Errorf("unexpected dataset version or result limit")
	}
	result := Result{DatasetVersion: envelope.DatasetVersion, Evidence: make([]catalog.Evidence, 0, len(envelope.Evidence))}
	seen := make(map[string]bool)
	for _, raw := range envelope.Evidence {
		var evidence catalog.Evidence
		if err := jsonwire.DecodeFields(raw, "chunk_id artwork_id source_id source_title source_url source_language source_locator excerpt license license_url attribution normalization", &evidence); err != nil {
			return Result{}, err
		}
		trusted, exists := c.dataset.EvidenceByChunkID(evidence.ChunkID)
		if !exists || seen[evidence.ChunkID] || evidence.ArtworkID != artworkID || evidence != trusted {
			return Result{}, fmt.Errorf("evidence does not match the pinned artwork and seed")
		}
		seen[evidence.ChunkID] = true
		result.Evidence = append(result.Evidence, evidence)
	}
	return result, nil
}

func decodeFailure(status int, requestID string, data []byte) error {
	var envelope struct {
		RequestID string          `json:"request_id"`
		Error     json.RawMessage `json:"error"`
	}
	if err := jsonwire.DecodeFields(data, "request_id error", &envelope); err != nil {
		return invalidResponse(err)
	}
	var failure struct {
		Code      string `json:"code"`
		Message   string `json:"message"`
		Retryable bool   `json:"retryable"`
	}
	if err := jsonwire.DecodeFields(envelope.Error, "code message retryable", &failure); err != nil {
		return invalidResponse(err)
	}
	expected := map[int]string{
		400: "invalid_request", 404: "artwork_not_found", 409: "dataset_version_mismatch",
		500: "internal_error", 503: "retrieval_unavailable",
	}
	if envelope.RequestID != requestID || failure.Code != expected[status] || strings.TrimSpace(failure.Message) == "" ||
		failure.Retryable != (status == 500 || status == 503) {
		return invalidResponse(nil)
	}
	if status == 500 || status == 503 {
		return unavailable(nil)
	}
	return invalidResponse(nil)
}

func allowedStatus(status int) bool {
	return status == 200 || status == 400 || status == 404 || status == 409 || status == 500 || status == 503
}

func jsonContentType(header http.Header) bool {
	if len(header.Values("Content-Type")) != 1 {
		return false
	}
	mediaType, parameters, err := mime.ParseMediaType(header.Get("Content-Type"))
	return err == nil && mediaType == "application/json" &&
		(parameters["charset"] == "" || strings.EqualFold(parameters["charset"], "utf-8"))
}

func validRequestID(value string) bool {
	if len(value) == 0 || len(value) > 128 || strings.TrimSpace(value) == "" {
		return false
	}
	for _, char := range value {
		if char < 32 || char > 126 {
			return false
		}
	}
	return true
}

func validQuery(value string) bool {
	if !utf8.ValidString(value) || strings.ContainsFunc(value, func(r rune) bool {
		return unicode.IsControl(r) && r != '\t' && r != '\n' && r != '\r'
	}) {
		return false
	}
	length := utf8.RuneCountInString(strings.TrimSpace(value))
	return length >= 1 && length <= 1000
}

func classifyIOError(parent, attempt context.Context, err error) error {
	if parent.Err() != nil {
		return parent.Err()
	}
	var networkError net.Error
	if errors.Is(attempt.Err(), context.DeadlineExceeded) ||
		(errors.As(err, &networkError) && networkError.Timeout()) {
		return timedOut(err)
	}
	return unavailable(err)
}
