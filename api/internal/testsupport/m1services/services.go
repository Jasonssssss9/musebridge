//go:build integration

// Package m1services provides test-only real Python services for tagged Go checks.
package m1services

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
)

type Connection struct {
	Event           string `json:"event"`
	Mode            string `json:"mode"`
	Fault           string `json:"fault"`
	AgentURL        string `json:"agent_url"`
	RetrievalURL    string `json:"retrieval_url"`
	DatasetVersion  string `json:"dataset_version"`
	ContractVersion string `json:"contract_version"`
	ModelName       string `json:"model_name"`
}

type Event struct {
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

type Services struct {
	Connection Connection
	Client     *agentclient.Client
	directory  string
	token      string
	command    *exec.Cmd
	done       chan struct{}
	waitError  error // Read only after done closes.
	cancel     context.CancelFunc
	stopped    bool
}

// Start never skips or substitutes an in-process transport. Missing
// prerequisites, failed readiness and unavailable dependencies fail the check.
func Start(t *testing.T, fault string) *Services {
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
	harness := &Services{directory: filepath.Join(parent, "services"),
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
	t.Cleanup(func() { harness.Close(t) })
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
			if err != nil || json.Unmarshal(data, &harness.Connection) != nil {
				continue
			}
			info := harness.Connection
			if info.Event != "services_ready" || info.Mode != "m1-scripted-model-real-http-retrieval" ||
				info.Fault != fault || info.DatasetVersion != "aic-seed-v0.1" ||
				info.ContractVersion != "agent-run-v2" || info.ModelName != "SYNTHETIC-M1-TEST-ONLY" ||
				!strings.HasPrefix(info.AgentURL, "http://127.0.0.1:") || !strings.HasPrefix(info.RetrievalURL, "http://127.0.0.1:") {
				t.Fatal("Python M1 services advertised an unexpected test configuration")
			}
			harness.Client, err = agentclient.New(info.AgentURL, harness.token)
			if err != nil {
				t.Fatal("cannot configure the existing Go Agent client")
			}
			return harness
		}
	}
}

func (h *Services) Close(t *testing.T) {
	t.Helper()
	if h.stopped {
		return
	}
	h.stopped = true
	defer h.cancel()
	if h.Client != nil {
		h.Client.CloseIdleConnections()
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
	if h.Connection.Event == "services_ready" {
		if _, err := os.Stat(filepath.Join(h.directory, "connection.json")); !os.IsNotExist(err) {
			t.Error("the harness retained a stale readiness file")
		}
		audit := h.Audit(t)
		if len(audit) == 0 || audit[len(audit)-1].Event != "services_stopped" {
			t.Error("the harness did not record completed service cleanup")
		}
	}
}

func (h *Services) Audit(t *testing.T) []Event {
	t.Helper()
	file, err := os.Open(filepath.Join(h.directory, "audit.jsonl"))
	if err != nil {
		t.Fatal("missing real-service audit")
	}
	defer file.Close()
	var events []Event
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var event Event
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
