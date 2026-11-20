//go:build integration

package conversation

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
)

type m1Connection struct {
	Event           string `json:"event"`
	Mode            string `json:"mode"`
	Fault           string `json:"fault"`
	AgentURL        string `json:"agent_url"`
	RetrievalURL    string `json:"retrieval_url"`
	DatasetVersion  string `json:"dataset_version"`
	ContractVersion string `json:"contract_version"`
	ModelName       string `json:"model_name"`
}

type m1Event struct {
	Event               string   `json:"event"`
	RequestID           string   `json:"request_id"`
	ArtworkID           string   `json:"artwork_id"`
	DatasetVersion      string   `json:"dataset_version"`
	Status              int      `json:"status"`
	OriginalEvidenceIDs []string `json:"original_evidence_ids"`
	EvidenceIDs         []string `json:"evidence_ids"`
	Fault               string   `json:"fault"`
	InjectionBoundary   *string  `json:"injection_boundary"`
	Boundary            string   `json:"boundary"`
	Code                string   `json:"code"`
	Phase               string   `json:"phase"`
	Operation           string   `json:"operation"`
}

type m1Services struct {
	connection m1Connection
	client     *agentclient.Client
	directory  string
	token      string
	command    *exec.Cmd
	done       chan struct{}
	waitError  error // Read only after done closes.
	cancel     context.CancelFunc
	stopped    bool
}

// startM1Services never skips or substitutes an in-process transport. Missing
// prerequisites, failed readiness and unavailable dependencies fail the check.
func startM1Services(t *testing.T, fault string) *m1Services {
	t.Helper()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal("cannot resolve the repository root")
	}
	python := os.Getenv("M1_PYTHON")
	if python == "" {
		python = filepath.Join(root, ".venv", "bin", "python")
	} else if strings.ContainsRune(python, filepath.Separator) && !filepath.IsAbs(python) {
		python = filepath.Join(root, python)
	}
	parent := t.TempDir()
	openLog := func(name string) *os.File {
		file, err := os.OpenFile(filepath.Join(parent, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err != nil {
			t.Fatal("cannot create a private launcher log")
		}
		t.Cleanup(func() { _ = file.Close() })
		return file
	}
	stdout, stderr := openLog("launcher.stdout"), openLog("launcher.stderr")
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		t.Fatal("cannot create a service token")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	harness := &m1Services{directory: filepath.Join(parent, "services"),
		token: base64.RawURLEncoding.EncodeToString(random[:]), done: make(chan struct{}), cancel: cancel}
	harness.command = exec.CommandContext(ctx, python, "-B", "-m", "agent.testing.m1_services",
		"--state-dir", harness.directory, "--fault", fault)
	harness.command.Dir = root
	for _, value := range os.Environ() {
		if !strings.HasPrefix(value, "AGENT_SERVICE_TOKEN=") {
			harness.command.Env = append(harness.command.Env, value)
		}
	}
	harness.command.Env = append(harness.command.Env, "AGENT_SERVICE_TOKEN="+harness.token)
	harness.command.Stdout, harness.command.Stderr = stdout, stderr
	harness.command.WaitDelay = 2 * time.Second
	if err := harness.command.Start(); err != nil {
		cancel()
		t.Fatal("cannot start the Python M1 harness; install the documented prerequisites or set M1_PYTHON")
	}
	go func() {
		harness.waitError = harness.command.Wait()
		close(harness.done)
	}()
	t.Cleanup(func() { harness.stop(t) })
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(15 * time.Second)
	defer timeout.Stop()
	for {
		select {
		case <-harness.done:
			t.Fatal("Python M1 services exited before readiness; no fixture fallback is allowed")
		case <-timeout.C:
			t.Fatal("Python M1 services did not become ready within 15 seconds")
		case <-ticker.C:
			data, err := os.ReadFile(filepath.Join(harness.directory, "connection.json"))
			if err != nil || json.Unmarshal(data, &harness.connection) != nil {
				continue
			}
			info := harness.connection
			if info.Event != "services_ready" || info.Mode != "m1-scripted-model-real-http-retrieval" ||
				info.Fault != fault || info.DatasetVersion != "aic-seed-v0.1" ||
				info.ContractVersion != "agent-run-v2" || info.ModelName != "SYNTHETIC-M1-TEST-ONLY" ||
				!strings.HasPrefix(info.AgentURL, "http://127.0.0.1:") || !strings.HasPrefix(info.RetrievalURL, "http://127.0.0.1:") {
				t.Fatal("Python M1 services advertised an unexpected test configuration")
			}
			harness.client, err = agentclient.New(info.AgentURL, harness.token)
			if err != nil {
				t.Fatal("cannot configure the existing Go Agent client")
			}
			return harness
		}
	}
}

func (h *m1Services) stop(t *testing.T) {
	t.Helper()
	if h.stopped {
		return
	}
	h.stopped = true
	defer h.cancel()
	if h.client != nil {
		h.client.CloseIdleConnections()
	}
	select {
	case <-h.done:
	default:
		_ = h.command.Process.Signal(syscall.SIGTERM)
	}
	select {
	case <-h.done:
		if h.waitError != nil {
			t.Error("the Python M1 harness did not exit cleanly")
		}
	case <-time.After(10 * time.Second):
		_ = h.command.Process.Kill()
		<-h.done
		t.Error("the Python M1 harness required forced shutdown")
	}
	for _, path := range []string{filepath.Join(h.directory, "audit.jsonl"),
		filepath.Join(filepath.Dir(h.directory), "launcher.stdout"), filepath.Join(filepath.Dir(h.directory), "launcher.stderr")} {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Error("cannot inspect a private M1 log")
			continue
		}
		if bytes.Contains(data, []byte(h.token)) {
			t.Error("a service token appeared in a launcher/audit log")
		}
	}
	if h.connection.Event == "services_ready" {
		if _, err := os.Stat(filepath.Join(h.directory, "connection.json")); !os.IsNotExist(err) {
			t.Error("the harness retained a stale readiness file")
		}
		audit := h.audit(t)
		if len(audit) == 0 || audit[len(audit)-1].Event != "services_stopped" {
			t.Error("the harness did not record completed service cleanup")
		}
	}
}

func (h *m1Services) audit(t *testing.T) []m1Event {
	t.Helper()
	file, err := os.Open(filepath.Join(h.directory, "audit.jsonl"))
	if err != nil {
		t.Fatal("missing real-service audit")
	}
	defer file.Close()
	var events []m1Event
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var event m1Event
		if json.Unmarshal(scanner.Bytes(), &event) != nil {
			t.Fatal("invalid service audit entry")
		}
		events = append(events, event)
	}
	if scanner.Err() != nil {
		t.Fatal("cannot read the service audit")
	}
	return events
}

// assertM1Trace binds the actual retrieval HTTP response and tool acceptance to
// the same Go trace. Answer prose alone cannot distinguish these two artworks.
func assertM1Trace(t *testing.T, harness *m1Services, trace, artwork string, foreign bool) string {
	t.Helper()
	expected, actual := artwork+"-medium", artwork+"-medium"
	if foreign {
		actual = "aic-11723-medium"
	}
	counts := make(map[string]int)
	for _, event := range harness.audit(t) {
		if event.RequestID != trace {
			continue
		}
		if event.ArtworkID != artwork {
			t.Fatal("a trace crossed the active artwork boundary")
		}
		counts[event.Event]++
		switch event.Event {
		case "retrieval_http_response":
			if event.Status != 200 || event.DatasetVersion != "aic-seed-v0.1" ||
				!reflect.DeepEqual(event.OriginalEvidenceIDs, []string{expected}) ||
				!reflect.DeepEqual(event.EvidenceIDs, []string{actual}) || event.Fault != harness.connection.Fault {
				t.Fatal("the actual HTTP lookup did not return the expected scoped evidence")
			}
			if foreign && (event.InjectionBoundary == nil || *event.InjectionBoundary != "retrieval HTTP response serialization") {
				t.Fatal("the foreign record was not injected at the documented HTTP boundary")
			}
		case "retrieval_accepted":
			if !reflect.DeepEqual(event.EvidenceIDs, []string{expected}) {
				t.Fatal("the tool accepted unexpected evidence")
			}
		case "retrieval_rejected":
			if event.Code != "retrieval_invalid_response" || event.Phase != "retrieval" || event.Boundary != "RetrievalTool.search" {
				t.Fatal("foreign evidence was not rejected by the real retrieval tool")
			}
		case "model_call":
			counts["model_"+event.Operation]++
		default:
			t.Fatalf("unexpected trace event %q", event.Event)
		}
	}
	want := map[string]int{"retrieval_http_response": 1, "retrieval_accepted": 1,
		"model_call": 2, "model_plan": 1, "model_assess": 1}
	if foreign {
		want = map[string]int{"retrieval_http_response": 1, "retrieval_rejected": 1, "model_call": 1, "model_plan": 1}
	}
	if !reflect.DeepEqual(counts, want) {
		t.Fatalf("missing or repeated real-service operations: got %v, expected %v", counts, want)
	}
	return actual
}

func m1Trace(attempt AgentAttempt, name string) string {
	return fmt.Sprintf("m1-go-%s-%s", name, strings.ReplaceAll(string(attempt.Request.RunID), "-", "")[:8])
}
