package agentclient

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"testing"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

func vincentPayload(t *testing.T, name string) json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/vincent-examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var examples struct {
		Cases []struct {
			Name    string          `json:"name"`
			Payload json.RawMessage `json:"payload"`
		} `json:"schema_cases"`
	}
	if json.Unmarshal(data, &examples) != nil {
		t.Fatal("invalid Vincent examples")
	}
	for _, item := range examples.Cases {
		if item.Name == name {
			return item.Payload
		}
	}
	t.Fatalf("missing %s", name)
	return nil
}

func TestVincentRunsUseTheV3Route(t *testing.T) {
	v3, _ := ac.DefaultValidatorV3()
	var run ac.RunRequest
	var snapshot ac.RunSnapshot
	if err := v3.Decode("RunRequest", vincentPayload(t, "run_initial_vincent"), &run); err != nil {
		t.Fatal(err)
	}
	if err := v3.Decode("RunSnapshot", vincentPayload(t, "vincent_completed_run_snapshot"), &snapshot); err != nil {
		t.Fatal(err)
	}
	var paths []string
	var sent map[string]any
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.Method+" "+r.URL.Path)
		if r.Method == http.MethodPost {
			_ = json.NewDecoder(r.Body).Decode(&sent)
			reply(t, w, r, 200, snapshot)
			return
		}
		reply(t, w, r, 200, snapshot)
	})
	got, err := client.Run(context.Background(), run, "trace-v3")
	if err != nil || got.State != "completed" || got.Result == nil || got.Result.Answer.Text != snapshot.Result.Answer.Text {
		t.Fatalf("v3 run: %+v %v", got, err)
	}
	if _, err := client.Get(context.Background(), run, "trace-v3-get"); err != nil {
		t.Fatal(err)
	}
	if len(paths) != 2 || paths[0] != "POST /internal/v3/agent/runs" || paths[1] != "GET /internal/v3/agent/runs/"+string(run.RunID) {
		t.Fatalf("unexpected v3 routes: %v", paths)
	}
	if sent["contract_version"] != "agent-run-v3" || sent["scope"].(map[string]any)["conversation_mode"] != "vincent" {
		t.Fatalf("the v3 request must keep its version and scope: %v", sent)
	}

	// A v3 result for a different scope is rejected.
	tampered := snapshot
	result := *snapshot.Result
	result.Scope = ac.ScopeFor(ac.PublicVersionV3, ac.ModeGuide, run.Scope.ArtworkID, run.Scope.DatasetVersion)
	tampered.Result = &result
	bad := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, tampered) })
	if _, err := bad.Run(context.Background(), run, "trace-v3-bad"); err == nil {
		t.Fatal("a result for another scope must be rejected")
	}
}
