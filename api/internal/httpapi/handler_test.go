package httpapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

func testDataset(t *testing.T) *catalog.Dataset {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	return dataset
}

func call(handler http.Handler, method, target string) *httptest.ResponseRecorder {
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(method, target, nil)
	request.Header.Set("X-Request-ID", "untrusted-client-value")
	handler.ServeHTTP(recorder, request)
	return recorder
}

func TestArtworkResponses(t *testing.T) {
	handler := New(testDataset(t), nil)
	for slug, title := range map[string]string{"aic-28560": "The Bedroom", "aic-11723": "Woman at Her Toilette"} {
		recorder := call(handler, http.MethodGet, "/api/artworks/"+slug)
		if recorder.Code != http.StatusOK {
			t.Fatalf("lookup failed: %d %s", recorder.Code, recorder.Body.String())
		}
		var response struct {
			Artwork catalog.Artwork `json:"artwork"`
		}
		if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
			t.Fatal(err)
		}
		if response.Artwork.ArtworkID != slug || response.Artwork.Title != title ||
			response.Artwork.DatasetVersion != "aic-seed-v0.1" || !strings.Contains(recorder.Body.String(), `"image_url":null`) {
			t.Fatalf("incorrect artwork response: %s", recorder.Body.String())
		}
		if recorder.Header().Get("Cache-Control") != "no-store" ||
			recorder.Header().Get("Content-Type") != "application/json; charset=utf-8" ||
			recorder.Header().Get("Content-Length") != strconv.Itoa(recorder.Body.Len()) {
			t.Fatal("response headers violate the JSON contract")
		}
		if id := recorder.Header().Get("X-Request-ID"); id == "" || id == "untrusted-client-value" {
			t.Fatal("B did not generate its own request ID")
		}
	}
}

func TestErrorsAndMethods(t *testing.T) {
	handler := New(testDataset(t), nil)
	cases := []struct {
		method, target, code string
		status               int
	}{
		{"GET", "/api/artworks/missing", "artwork_not_found", 404},
		{"GET", "/missing", "route_not_found", 404},
		{"GET", "/api/artworks/", "route_not_found", 404},
		{"GET", "/api/artworks/aic-28560/extra", "route_not_found", 404},
		{"GET", "/api/artworks/aic-28560?language=en", "invalid_request", 400},
		{"GET", "/api/artworks/aic-28560?", "invalid_request", 400},
		{"GET", "/healthz?debug=true", "invalid_request", 400},
		{"POST", "/api/artworks/aic-28560", "method_not_allowed", 405},
		{"OPTIONS", "/readyz", "method_not_allowed", 405},
		{"POST", "/api/conversations", "route_not_found", 404},
	}
	for _, tc := range cases {
		t.Run(tc.method+" "+tc.target, func(t *testing.T) {
			recorder := call(handler, tc.method, tc.target)
			var response struct {
				RequestID string   `json:"request_id"`
				Error     apiError `json:"error"`
			}
			if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
				t.Fatal(err)
			}
			if recorder.Code != tc.status || response.Error.Code != tc.code || response.Error.Retryable ||
				response.RequestID != recorder.Header().Get("X-Request-ID") {
				t.Fatalf("incorrect error response: %d %s", recorder.Code, recorder.Body.String())
			}
			if tc.status == 405 && recorder.Header().Get("Allow") != "GET" {
				t.Fatal("method error omitted Allow")
			}
		})
	}
	head := call(handler, http.MethodHead, "/healthz")
	if head.Code != 405 || head.Body.Len() != 0 || head.Header().Get("Content-Length") == "" {
		t.Fatal("HEAD must retain applicable headers and omit the response body")
	}
}

func TestLocalReadinessDoesNotDependOnRetrieval(t *testing.T) {
	for _, loaded := range []bool{true, false} {
		var dataset *catalog.Dataset
		if loaded {
			dataset = testDataset(t)
		}
		handler := New(dataset, nil)
		live := call(handler, http.MethodGet, "/healthz")
		if live.Code != 200 {
			t.Fatal("liveness depends on a loaded dataset")
		}
		ready := call(handler, http.MethodGet, "/readyz")
		if loaded && (ready.Code != 200 || !strings.Contains(ready.Body.String(), `"dataset_version":"aic-seed-v0.1"`)) {
			t.Fatal("loaded service was not ready without C")
		}
		if !loaded {
			if !strings.Contains(live.Body.String(), `"dataset_version":null`) || ready.Code != 503 ||
				!strings.Contains(ready.Body.String(), `"code":"service_not_ready"`) ||
				call(handler, "GET", "/api/artworks/aic-28560").Code != 503 {
				t.Fatal("uninitialized local state was exposed as ready")
			}
		}
	}
}

func TestLogsOmitUntrustedInput(t *testing.T) {
	var logs bytes.Buffer
	handler := New(testDataset(t), slog.New(slog.NewJSONHandler(&logs, nil)))
	request := httptest.NewRequest("GET", "/api/artworks/aic-28560?secret-question=private-text", nil)
	request.Header.Set("Cookie", "musebridge_visitor=private-cookie")
	request.Header.Set("X-Request-ID", "private-client-id")
	handler.ServeHTTP(httptest.NewRecorder(), request)
	if strings.Contains(logs.String(), "private-") || strings.Contains(logs.String(), "secret-question") {
		t.Fatalf("request data leaked into diagnostics: %s", logs.String())
	}
	if !strings.Contains(logs.String(), `"request_id":`) || !strings.Contains(logs.String(), `"code":"invalid_request"`) {
		t.Fatal("safe request diagnostics are missing")
	}
}

func TestConcurrentArtworkIsolation(t *testing.T) {
	handler := New(testDataset(t), nil)
	var wait sync.WaitGroup
	var lock sync.Mutex
	seen := make(map[string]bool)
	for i := range 40 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			slug := "aic-28560"
			if i%2 != 0 {
				slug = "aic-11723"
			}
			recorder := call(handler, "GET", "/api/artworks/"+slug)
			if recorder.Code != 200 || !strings.Contains(recorder.Body.String(), `"artwork_id":"`+slug+`"`) {
				t.Error("concurrent request received the wrong artwork")
			}
			lock.Lock()
			defer lock.Unlock()
			id := recorder.Header().Get("X-Request-ID")
			if seen[id] || id == "" {
				t.Error("concurrent requests shared a request ID")
			}
			seen[id] = true
		}()
	}
	wait.Wait()
}
