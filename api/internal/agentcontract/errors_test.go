package agentcontract

import "testing"

func TestMapRunFailureUsesSharedPolicyWithoutHTTP200Misclassification(t *testing.T) {
	for code, policy := range errorPolicies {
		t.Run(code, func(t *testing.T) {
			upstream := AgentError{Code: code, Message: "PRIVATE diagnostic", Retryable: policy.Retryable, Phase: "validation"}
			failure, err := MapRunFailure(upstream)
			if code == "agent_run_in_progress" {
				if err == nil || failure != nil {
					t.Fatal("an in-progress error cannot describe a terminal failed run")
				}
				return
			}
			if err != nil || failure == nil || failure.Status != policy.PublicStatus ||
				failure.Error.Code != policy.Code || failure.Error.Message != messages[policy.Code] ||
				failure.Error.Retryable != policy.Retryable || failure.Reconcile {
				t.Fatalf("retained failure did not follow shared policy: %+v, %v", failure, err)
			}
			upstream.Retryable = !upstream.Retryable
			if result, err := MapRunFailure(upstream); err == nil || result != nil {
				t.Fatal("inconsistent retryability was accepted")
			}
		})
	}
}

func TestMapRunFailureRejectsMalformedErrors(t *testing.T) {
	for _, upstream := range []AgentError{
		{},
		{Code: "unknown", Message: "PRIVATE", Phase: "retrieval"},
		{Code: "retrieval_unavailable", Message: "PRIVATE", Retryable: true, Phase: "unknown"},
		{Code: "retrieval_unavailable", Message: " ", Retryable: true, Phase: "retrieval"},
	} {
		if result, err := MapRunFailure(upstream); err == nil || result != nil {
			t.Fatal("malformed retained failure was accepted")
		}
	}
}

func TestSafeAgentFailureUsesCanonicalMessages(t *testing.T) {
	for _, policy := range errorPolicies {
		reconcile := policy.Code == "agent_unavailable" && policy.Retryable
		result, err := SafeAgentFailure(Failure{Status: policy.PublicStatus, Reconcile: reconcile,
			Error: PublicError{Code: policy.Code, Retryable: policy.Retryable, Message: "PRIVATE injected diagnostic"}})
		if err != nil || result.Error.Message != messages[policy.Code] || result.Reconcile != reconcile {
			t.Fatal("public Agent failure was not canonicalized")
		}
	}
}

func TestSafeAgentFailureRejectsImpossibleTuples(t *testing.T) {
	for _, input := range []Failure{
		{Status: 200, Error: PublicError{Code: "agent_unavailable", Retryable: true}},
		{Status: 503, Error: PublicError{Code: "unknown", Retryable: true}},
		{Status: 502, Error: PublicError{Code: "retrieval_unavailable", Retryable: true}},
		{Status: 503, Reconcile: true, Error: PublicError{Code: "agent_unavailable", Retryable: false}},
	} {
		if result, err := SafeAgentFailure(input); err == nil || result != nil {
			t.Fatal("impossible Agent failure tuple was accepted")
		}
	}
}
