package retrieval

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

const seedPath = "../../../knowledge/data/aic-seed-v0.1.json"

func testDataset(t *testing.T) *catalog.Dataset {
	t.Helper()
	dataset, err := catalog.Load(seedPath, "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	return dataset
}

func testClient(t *testing.T, baseURL string) *Client {
	t.Helper()
	client, err := New(baseURL, testDataset(t))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.CloseIdleConnections)
	return client
}

func sampleRequest() Request {
	return Request{ArtworkID: "aic-28560", Query: "What material is this artwork made of?", RequestID: "req-test-001"}
}

func evidenceObject(t *testing.T, id string) map[string]any {
	t.Helper()
	evidence, exists := testDataset(t).EvidenceByChunkID(id)
	if !exists {
		t.Fatal("unknown test evidence")
	}
	encoded, _ := json.Marshal(evidence)
	var object map[string]any
	if err := json.Unmarshal(encoded, &object); err != nil {
		t.Fatal(err)
	}
	return object
}

func successObject(t *testing.T) map[string]any {
	t.Helper()
	return map[string]any{
		"dataset_version": "aic-seed-v0.1",
		"evidence":        []any{evidenceObject(t, "aic-28560-medium")},
	}
}

func respond(w http.ResponseWriter, r *http.Request, status int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func assertFailure(t *testing.T, result Result, err error, code string, status int, retryable bool) {
	t.Helper()
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != code || failure.HTTPStatus != status || failure.Retryable != retryable {
		t.Fatalf("unexpected classification: %#v, %v", failure, err)
	}
	if result.DatasetVersion != "" || len(result.Evidence) != 0 {
		t.Fatalf("failure leaked a partial result: %+v", result)
	}
}

func TestRequestAndValidatedEvidence(t *testing.T) {
	body := successObject(t)
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "POST" || r.URL.Path != "/retrieve" || r.URL.RawQuery != "" ||
			r.Header.Get("Content-Type") != "application/json" || r.Header.Get("X-Request-ID") != "req-test-001" ||
			r.ContentLength <= 0 || len(r.TransferEncoding) != 0 || r.Header.Get("Cookie") != "" || r.Header.Get("Origin") != "" {
			t.Error("B constructed a request outside C's private transport profile")
		}
		encoded, err := io.ReadAll(r.Body)
		if err != nil || int64(len(encoded)) != r.ContentLength {
			t.Error("request length did not match the actual body")
		}
		var payload map[string]any
		if err := json.Unmarshal(encoded, &payload); err != nil || len(payload) != 4 ||
			payload["artwork_id"] != "aic-28560" || payload["query"] != "What material is this artwork made of?" ||
			payload["top_k"] != float64(2) || payload["dataset_version"] != "aic-seed-v0.1" {
			t.Error("wrong retrieval payload")
		}
		respond(w, r, 200, body)
	}))
	defer server.Close()
	client := testClient(t, server.URL)
	if client.timeout != 3*time.Second {
		t.Fatal("production retrieval deadline is not three seconds")
	}
	input := sampleRequest()
	input.Query = "  " + input.Query + "\n"
	result, err := client.Retrieve(context.Background(), input)
	if err != nil || result.DatasetVersion != "aic-seed-v0.1" || len(result.Evidence) != 1 || calls.Load() != 1 {
		t.Fatalf("valid retrieval failed: %+v %v", result, err)
	}
	trusted, _ := client.dataset.EvidenceByChunkID("aic-28560-medium")
	if result.Evidence[0] != trusted {
		t.Fatal("returned evidence did not retain its complete provenance")
	}
	result.Evidence[0].Excerpt = "changed caller response"
	again, err := client.Retrieve(context.Background(), input)
	if err != nil || again.Evidence[0] != trusted {
		t.Fatal("caller mutation affected a later retrieval")
	}
}

func TestEmptyEvidenceIsSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		respond(w, r, 200, map[string]any{"dataset_version": "aic-seed-v0.1", "evidence": []any{}})
	}))
	defer server.Close()
	result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
	if err != nil || result.Evidence == nil || len(result.Evidence) != 0 || result.DatasetVersion != "aic-seed-v0.1" {
		t.Fatalf("empty evidence was confused with a service failure: %+v %v", result, err)
	}
}

func TestEvidenceAndEnvelopeViolations(t *testing.T) {
	cases := map[string]func(map[string]any){
		"version":          func(body map[string]any) { body["dataset_version"] = "other" },
		"missing version":  func(body map[string]any) { delete(body, "dataset_version") },
		"null evidence":    func(body map[string]any) { body["evidence"] = nil },
		"wrong array type": func(body map[string]any) { body["evidence"] = "not an array" },
		"null record":      func(body map[string]any) { body["evidence"] = []any{nil} },
		"too many hits": func(body map[string]any) {
			hit := body["evidence"].([]any)[0]
			body["evidence"] = []any{hit, hit, hit}
		},
		"duplicate hits": func(body map[string]any) {
			hit := body["evidence"].([]any)[0]
			body["evidence"] = []any{hit, hit}
		},
	}
	for _, field := range []string{"chunk_id", "artwork_id", "source_id", "source_title", "source_url", "source_language", "source_locator", "excerpt", "license", "license_url", "attribution", "normalization"} {
		cases["changed "+field] = func(body map[string]any) { body["evidence"].([]any)[0].(map[string]any)[field] = "changed" }
		cases["missing "+field] = func(body map[string]any) { delete(body["evidence"].([]any)[0].(map[string]any), field) }
		cases["null "+field] = func(body map[string]any) { body["evidence"].([]any)[0].(map[string]any)[field] = nil }
		cases["wrong type "+field] = func(body map[string]any) { body["evidence"].([]any)[0].(map[string]any)[field] = 7 }
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			body := successObject(t)
			mutate(body)
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				respond(w, r, 200, body)
			}))
			defer server.Close()
			result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
			assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
			if calls.Load() != 1 {
				t.Fatal("an invalid response triggered an automatic retry")
			}
		})
	}
}

func TestWholeResultRejectedForForeignEvidence(t *testing.T) {
	body := successObject(t)
	body["evidence"] = append(body["evidence"].([]any), evidenceObject(t, "aic-11723-medium"))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { respond(w, r, 200, body) }))
	defer server.Close()
	result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
	assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
}

func TestAdditiveMetadataCannotShadowRequiredFields(t *testing.T) {
	body := successObject(t)
	body["debug"] = map[string]any{"ranker": "keyword"}
	body["DATASET_VERSION"] = "ignored uppercase metadata"
	hit := body["evidence"].([]any)[0].(map[string]any)
	hit["score"] = 1
	hit["EXCERPT"] = "ignored uppercase metadata"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { respond(w, r, 200, body) }))
	defer server.Close()
	result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
	if err != nil || len(result.Evidence) != 1 || result.Evidence[0].Excerpt != "Oil on canvas" {
		t.Fatalf("additive metadata altered the required evidence fields: %+v %v", result, err)
	}
}

func TestProtocolViolations(t *testing.T) {
	valid, _ := json.Marshal(successObject(t))
	cases := map[string]func(http.ResponseWriter, *http.Request){
		"malformed JSON": func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, `{`) },
		"trailing JSON":  func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(append(valid, []byte(` {}`)...)) },
		"duplicate field": func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.WriteString(w, strings.Replace(string(valid), `"dataset_version":`, `"dataset_version":"other","dataset_version":`, 1))
		},
		"bad Unicode": func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.WriteString(w, strings.Replace(string(valid), "Oil on canvas", `\ud800`, 1))
		},
		"invalid UTF-8": func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte{'"', 0xff, '"'}) },
		"HTML": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "text/html")
			_, _ = io.WriteString(w, "<html>not JSON</html>")
		},
		"wrong charset": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json; charset=iso-8859-1")
			_, _ = w.Write(valid)
		},
		"duplicate content type": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Add("Content-Type", "text/html")
			_, _ = w.Write(valid)
		},
		"missing request ID": func(w http.ResponseWriter, r *http.Request) { w.Header().Del("X-Request-ID"); _, _ = w.Write(valid) },
		"wrong request ID": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("X-Request-ID", "another-request")
			_, _ = w.Write(valid)
		},
		"duplicate request ID": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Add("X-Request-ID", "another-request")
			_, _ = w.Write(valid)
		},
		"declared oversized": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Length", "65537")
			w.WriteHeader(200)
		},
		"streamed oversized": func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			w.(http.Flusher).Flush()
			_, _ = io.WriteString(w, strings.Repeat(" ", MaxResponseBytes+1))
		},
		"truncated body": func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Length", "100")
			_, _ = io.WriteString(w, `{}`)
		},
		"unlisted status": func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(418) },
	}
	for name, handle := range cases {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
				handle(w, r)
			}))
			defer server.Close()
			result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
			assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
		})
	}
}

func TestUpstreamErrorMappingAndSafeMessages(t *testing.T) {
	for status, code := range map[int]string{400: "invalid_request", 404: "artwork_not_found", 409: "dataset_version_mismatch", 500: "internal_error", 503: "retrieval_unavailable"} {
		t.Run(code, func(t *testing.T) {
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				respond(w, r, status, map[string]any{"request_id": r.Header.Get("X-Request-ID"), "error": map[string]any{
					"code": code, "message": "private upstream body /secret/path", "retryable": status >= 500,
				}})
			}))
			defer server.Close()
			result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
			if status >= 500 {
				assertFailure(t, result, err, "retrieval_unavailable", 503, true)
			} else {
				assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
			}
			if strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), "/secret") || calls.Load() != 1 {
				t.Fatal("upstream diagnostics leaked or the failed request retried")
			}
		})
	}
}

func TestMalformedErrorEnvelopeIsInvalidResponse(t *testing.T) {
	cases := map[string]func(map[string]any){
		"wrong request ID": func(body map[string]any) { body["request_id"] = "wrong-request" },
		"null error":       func(body map[string]any) { body["error"] = nil },
		"wrong retry type": func(body map[string]any) { body["error"].(map[string]any)["retryable"] = "true" },
		"wrong retry flag": func(body map[string]any) { body["error"].(map[string]any)["retryable"] = false },
		"unknown code":     func(body map[string]any) { body["error"].(map[string]any)["code"] = "unknown" },
		"empty message":    func(body map[string]any) { body["error"].(map[string]any)["message"] = " " },
		"missing code":     func(body map[string]any) { delete(body["error"].(map[string]any), "code") },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			body := map[string]any{"request_id": "req-test-001", "error": map[string]any{
				"code": "retrieval_unavailable", "message": "unavailable", "retryable": true,
			}}
			mutate(body)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { respond(w, r, 503, body) }))
			defer server.Close()
			result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
			assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
		})
	}
}

func TestRedirectIsNotFollowed(t *testing.T) {
	var redirected atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirected.Add(1) }))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 302) }))
	defer server.Close()
	result, err := testClient(t, server.URL).Retrieve(context.Background(), sampleRequest())
	assertFailure(t, result, err, "retrieval_invalid_response", 502, false)
	if redirected.Load() != 0 {
		t.Fatal("client followed an upstream redirect")
	}
}

func TestConnectionFailureIsUnavailable(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	address := server.URL
	server.Close()
	result, err := testClient(t, address).Retrieve(context.Background(), sampleRequest())
	assertFailure(t, result, err, "retrieval_unavailable", 503, true)
}

func TestDeadlineCoversHeadersAndBody(t *testing.T) {
	for _, sendHeaders := range []bool{false, true} {
		t.Run(map[bool]string{false: "waiting for headers", true: "waiting for body"}[sendHeaders], func(t *testing.T) {
			release := make(chan struct{})
			var calls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				_, _ = io.Copy(io.Discard, r.Body)
				if sendHeaders {
					w.Header().Set("Content-Type", "application/json")
					w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
					w.WriteHeader(200)
					w.(http.Flusher).Flush()
				}
				select {
				case <-r.Context().Done():
				case <-release:
				}
			}))
			defer server.Close()
			defer close(release)
			client := testClient(t, server.URL)
			client.timeout = 100 * time.Millisecond
			started := time.Now()
			result, err := client.Retrieve(context.Background(), sampleRequest())
			assertFailure(t, result, err, "retrieval_timeout", 504, true)
			if time.Since(started) > 2*time.Second || calls.Load() != 1 {
				t.Fatal("deadline did not bound a single attempt")
			}
		})
	}
}

func TestParentCancellationIsPreserved(t *testing.T) {
	release := make(chan struct{})
	entered := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		close(entered)
		select {
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer server.Close()
	defer close(release)
	client := testClient(t, server.URL)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	go func() { _, err := client.Retrieve(ctx, sampleRequest()); finished <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("request never reached the test service")
	}
	cancel()
	select {
	case err := <-finished:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("parent cancellation became a dependency error: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("parent cancellation did not stop the client")
	}
}

func TestInvalidInputNeverContactsC(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer server.Close()
	client := testClient(t, server.URL)
	for _, query := range []string{"", " ", "bad\x00query", "bad\vquery", "bad\xffquery", strings.Repeat("x", 1001)} {
		request := sampleRequest()
		request.Query = query
		result, err := client.Retrieve(context.Background(), request)
		assertFailure(t, result, err, "invalid_request", 400, false)
	}
	for _, id := range []string{"", " ", "bad\r\nheader", strings.Repeat("x", 129), "nonascii-\u00e9"} {
		request := sampleRequest()
		request.RequestID = id
		result, err := client.Retrieve(context.Background(), request)
		assertFailure(t, result, err, "invalid_request", 400, false)
	}
	request := sampleRequest()
	request.ArtworkID = "unknown"
	result, err := client.Retrieve(context.Background(), request)
	assertFailure(t, result, err, "artwork_not_found", 404, false)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.Retrieve(ctx, sampleRequest()); !errors.Is(err, context.Canceled) {
		t.Fatal("already-cancelled parent was ignored")
	}
	if calls.Load() != 0 {
		t.Fatal("invalid input caused a network call")
	}
}

func TestConcurrentRequestsKeepScopeAndTracingSeparate(t *testing.T) {
	dataset := testDataset(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request struct {
			ArtworkID string `json:"artwork_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			return
		}
		evidence, _ := dataset.EvidenceByChunkID(request.ArtworkID + "-medium")
		respond(w, r, 200, Result{DatasetVersion: dataset.Version(), Evidence: []catalog.Evidence{evidence}})
	}))
	defer server.Close()
	client := testClient(t, server.URL)
	var wait sync.WaitGroup
	for index := range 24 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			request := sampleRequest()
			request.RequestID = "req-concurrent-" + strings.Repeat("x", index+1)
			if index%2 != 0 {
				request.ArtworkID = "aic-11723"
			}
			result, err := client.Retrieve(context.Background(), request)
			if err != nil || len(result.Evidence) != 1 || result.Evidence[0].ArtworkID != request.ArtworkID {
				t.Errorf("concurrent retrieval mixed request state: %+v %v", result, err)
			}
		}()
	}
	wait.Wait()
}

func TestClientConfiguration(t *testing.T) {
	dataset := testDataset(t)
	for _, address := range []string{"", "ftp://example.com", "http://user:password@example.com", "http://localhost:8001/", "http://localhost:8001/retrieve", "http://localhost?", "http://localhost#", "http://localhost:99999", "http://[invalid]"} {
		if _, err := New(address, dataset); err == nil {
			t.Fatalf("invalid retrieval origin accepted: %s", address)
		}
	}
	if _, err := New("http://127.0.0.1:8001", nil); err == nil {
		t.Fatal("nil dataset was accepted")
	}
}

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestNetworkErrorsUseTypesRatherThanSensitiveMessages(t *testing.T) {
	for _, timeout := range []bool{false, true} {
		client := testClient(t, "http://127.0.0.1:8001")
		var calls atomic.Int32
		client.http.Transport = transportFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return nil, &net.DNSError{Name: "private-host", Err: "private-diagnostic", IsTimeout: timeout}
		})
		result, err := client.Retrieve(context.Background(), sampleRequest())
		if timeout {
			assertFailure(t, result, err, "retrieval_timeout", 504, true)
		} else {
			assertFailure(t, result, err, "retrieval_unavailable", 503, true)
		}
		if strings.Contains(err.Error(), "private") || calls.Load() != 1 {
			t.Fatal("network error leaked its cause or triggered a retry")
		}
	}
}

func TestShorterParentDeadlineIsPreserved(t *testing.T) {
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		select {
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer server.Close()
	defer close(release)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	result, err := testClient(t, server.URL).Retrieve(ctx, sampleRequest())
	if !errors.Is(err, context.DeadlineExceeded) || result.DatasetVersion != "" {
		t.Fatalf("shorter parent deadline was not preserved: %+v %v", result, err)
	}
	var dependencyFailure *Error
	if errors.As(err, &dependencyFailure) {
		t.Fatal("parent deadline was incorrectly attributed to C")
	}
}
