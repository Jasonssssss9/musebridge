"""Trial scheduling and evidence checks for the frozen visitor suite."""
from collections import Counter
from functools import lru_cache
import json

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.followup_plan import expand_plan
from agent.tools.evidence import EvidenceManifest
from agent.testing.v2 import simulate_commit


GROUPS = ("single", "risk", "scripts")


def schedule(directory, group="all"):
    """Each single variant/repetition and each script gets a fresh visitor."""
    single = json.loads((directory / "single-turn.json").read_text(encoding="utf-8"))
    multi = json.loads((directory / "multi-turn.json").read_text(encoding="utf-8"))
    yield from schedule_documents(single, multi, group)


def schedule_documents(single, multi, group="all"):
    for current in GROUPS:
        if group not in ("all", current):
            continue
        if current == "scripts":
            for script in multi["scripts"]:
                yield current, script["id"], script["type"], script["steps"]
            continue
        for scenario in single["scenarios"]:
            if current == "risk" and not scenario["risk_repeat"]:
                continue
            for variant in scenario["variants"]:
                step = {**variant, "kind": "message", "step": 1, "artwork_id": scenario["artwork_id"],
                        "expect": {**scenario["expected"], **variant["expect"]}, "intent": scenario["intent"]}
                for repeat in ((1,) if current == "single" else (2, 3)):
                    key = f"{scenario['id']}/{variant['language']}/{repeat}"
                    yield current, key, scenario["category"], [step]


def check_independent_coverage(single, multi):
    """Enforce the design's sample sizes from the signed files before execution."""
    languages = {"en", "es", "zh-CN", "ko"}
    categories = {"direct_conversation": 12, "supported_fact": 16, "mixed": 12, "unsupported_fact": 8,
                  "ambiguity": 6, "scope": 6}
    scenarios = single["scenarios"]
    if (Counter(case["category"] for case in scenarios) != categories
            or len({case["id"] for case in scenarios}) != 60
            or sum(case["risk_repeat"] is True for case in scenarios) < 12):
        raise ValueError("The signed single-turn suite does not meet the coverage requirements.")
    for case in scenarios:
        variants = [variant["language"] for variant in case["variants"]]
        if len(variants) != 4 or set(variants) != languages:
            raise ValueError("Each independent single-turn scenario needs all four language variants.")
    scripts = multi["scripts"]
    mono = Counter(script["primary_language"] for script in scripts if script["type"] == "monolingual")
    if (len(scripts) < 16 or len({script["id"] for script in scripts}) != len(scripts)
            or any(mono[code] < 2 for code in languages) or sum(s["type"] == "switching" for s in scripts) < 8):
        raise ValueError("The signed scripts do not meet the language or switching coverage requirements.")
    for script in scripts:
        steps = script["steps"]
        if (sum(s["kind"] == "message" for s in steps) < 8
                or len({s["step"] for s in steps}) != len(steps)):
            raise ValueError("Each script needs unique steps and at least eight visitor messages.")


def development_schedule():
    """Exercise the runner using the two public M1 material checks and a refresh."""
    for artwork in ("aic-28560", "aic-11723"):
        question = {"kind": "message", "step": 1, "artwork_id": artwork, "language": "en",
                    "language_override": None, "text": "What material is this painting made of?",
                    "expect": {"status": "answered", "routes": ["retrieve"], "request_kinds": ["artwork_fact"],
                               "retrieval": "required", "pending_after": "none", "answer_language": "en",
                               "preference_after": "auto", "auto_language_after": "en",
                               "citations": {"required": [artwork + "-medium"], "any_of": [], "allowed": []}}}
        refresh = {"kind": "browser", "step": 2, "artwork_id": artwork,
                   "action": "Refresh the page.", "expect": "Restore the same turn and conversation."}
        yield "development", artwork, "known_material", [question, refresh]


def public_checks(expected, before, message, response):
    conversation, turn = response["conversation"], response["turn"]
    answer = turn["answer"]
    citations = {c.get("chunk_id", c.get("fact_id")) for c in answer["citations"]}
    gold = expected["citations"]
    required, alternatives = set(gold["required"]), [set(g) for g in gold["any_of"]]
    permitted = required.union(gold["allowed"], *alternatives)
    return {
        "scope_and_revision": (conversation["conversation_id"] == before["conversation_id"]
            and conversation["artwork_id"] == before["artwork_id"]
            and conversation["dataset_version"] == turn["dataset_version"] == before["dataset_version"]
            and conversation["revision"] == turn["sequence"] == before["revision"] + 1
            and turn["client_message_id"] == message["client_message_id"]
            and turn["question"] == message["question"]),
        "status": answer["status"] in expected.get("status_any", [expected.get("status")]),
        "citation_ids": (required <= citations <= permitted and all(citations & g for g in alternatives)),
        "answer_language_field": turn["answer_language"] == expected["answer_language"],
        "saved_preference": conversation["language_preference"] == expected["preference_after"],
        "auto_language": conversation["auto_language"] == expected["auto_language_after"],
    }


def pending_check(label, previous, current):
    if label == "kept":
        return previous is not None and current == previous
    if label == "cleared":
        return previous is not None and current is None
    actual = "none" if current is None else "whole" if current["original_fragment"] is None else "local"
    return actual in label.split("_or_")


@lru_cache(maxsize=1)
def _evidence_manifest():
    return EvidenceManifest.load()


def trace_verdict(expected, events, response):
    """Compare the actual trace to its public commit before inspecting private state."""
    counts = Counter(e["event"] for e in events)
    counts = dict(counts)
    public = response.get("turn")
    grading = {"counts": counts, "route": None, "latency_class": None, "checks": {}, "evidence": {},
               "failure_codes": [e.get("code") for e in events if e["event"] in ("model_failed", "workflow_failed")]}
    if public is None:
        return grading
    by_name = {e["event"]: e for e in events}
    result = by_name.get("private_workflow_result", {}).get("result")
    run = by_name.get("private_run_input", {}).get("request")
    plan = next((e["decision"] for e in events if e["event"] == "private_model_decision"
                 and e["operation"] == "plan"), None)
    if not (result and run and plan):
        return grading
    checks = grading["checks"]
    checks["trace_binding"] = (run["question"] == public["question"]
        and run["client_message_id"] == public["client_message_id"]
        and run["conversation_state"]["conversation_id"] == response["conversation"]["conversation_id"]
        and run["scope"]["artwork_id"] == response["conversation"]["artwork_id"]
        and result["conversation_update"]["base_revision"] + 1 == public["sequence"]
        and all(result["answer"][k] == public["answer"][k] for k in ("status", "text", "citations")))
    if not checks["trace_binding"]:
        return grading
    if "visitor_task" in plan:
        plan = expand_plan(plan, build_plan_input(run, _evidence_manifest()))
    route = plan["action"]
    execution = result["execution"]
    grading["route"] = route
    grading["latency_class"] = ("refined" if execution["refinement_rounds"] else
                                "retrieval_or_reuse" if route in ("retrieve", "answer_from_context") else "direct")
    checks["observed_model_calls"] = (counts.get("model_http_attempt", 0) == counts.get("model_validated", 0)
        == execution["model_calls"] and counts.get("model_failed", 0) == 0)
    checks["observed_retrieval_calls"] = counts.get("retrieval_attempt", 0) == execution["retrieval_calls"]
    if "routes" in expected:
        checks["route"] = route in expected["routes"]
    if "request_kinds" in expected:
        # The two terminal Plan branches carry no interpreted request list.
        checks["request_kinds"] = ("n/a" if route in ("clarify", "out_of_scope") else
            set(expected["request_kinds"]) == {r["kind"] for r in result["requests"]})
    rule, lookups = expected["retrieval"], execution["retrieval_calls"]
    checks["retrieval_policy"] = rule == "either" or (lookups > 0 if rule == "required" else lookups == 0)
    proposed, _ = simulate_commit(run, result)
    checks["pending_label"] = pending_check(expected["pending_after"], run["conversation_state"]["pending_clarification"],
                                            proposed["pending_clarification"])
    grading["evidence"] = {"plan": plan, "result": result, "previous_state": run["conversation_state"],
                           "projected_state_after_public_commit": proposed}
    return grading
