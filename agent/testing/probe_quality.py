"""Frozen visitor probes and independent checks for private Qwen reports."""
from collections import Counter
from dataclasses import dataclass
from uuid import uuid4

from agent.runtime.followup_plan import expand_plan
from agent.runtime.validation import ContractViolation
from agent.testing.v2 import make_run


@dataclass(frozen=True)
class Case:
    name: str
    suite: str
    run: dict
    status: str
    language: str
    evidence: tuple[str, ...]
    detected_language: str
    instruction: str | None = None


def visitor_cases(manifest):
    questions = {"en": "What material is this painting made of?", "es": "¿De qué material está hecha esta pintura?",
                 "zh-CN": "这幅画是什么材质？", "ko": "이 그림은 어떤 재료로 그렸나요?"}
    greetings = {"en": "Thank you!", "es": "¡Muchas gracias!", "zh-CN": "谢谢你的介绍！", "ko": "설명해 주셔서 감사합니다!"}
    for art in ("aic-28560", "aic-11723"):
        for lang, question in questions.items():
            run = make_run(artwork_id=art, question=question, language_override=lang, manifest=manifest)
            yield Case(f"{art}-{lang}", "development", run, "answered", lang, (art + "-medium",), lang)
    for lang, question in greetings.items():
        run = make_run(question=question, language_override=lang, manifest=manifest)
        yield Case("direct-" + lang, "development", run, "conversational", lang, (), "und")
    history = [{"turn_id": str(uuid4()), "sequence": i, "question": "We discussed the artwork.",
                "answer_language": "en", "answer_status": "conversational", "answer_mode": "llm",
                "answer_text": " a" * 1000} for i in range(1, 6)]
    yield Case("five-pair-sizing", "development", make_run(question=questions["en"], history=history,
               manifest=manifest), "answered", "en", ("aic-28560-medium",), "en")

    # Written before examining any new model output. Keep these independent of prompt examples.
    for name, art, question, override, language, status, evidence, detected, instruction in (
        ("holdout-support", "aic-11723", "How does the label describe the painting surface and medium? Answer in Spanish.",
         "en", "es", "answered", ("aic-11723-medium",), "en", "Answer in Spanish"),
        ("holdout-price", "aic-28560", "¿Cuánto pagó el primer comprador por este cuadro?",
         None, "es", "insufficient", (), "es", None),
        ("holdout-kindness", "aic-11723", "谢谢，刚才的解释帮到我了。", "ko", "ko", "conversational", (), "und", None),
        ("holdout-brushwork", "aic-28560", "붓질을 어떻게 묘사했나요?", None, "ko", "answered",
         ("aic-28560-description-01",), "ko", None),
    ):
        run = make_run(artwork_id=art, question=question, language_override=override, manifest=manifest)
        yield Case(name, "holdout", run, status, language, evidence, detected, instruction)


# Each judgment is made against the actual question, retrieved excerpts, answer and proposed memory.
# The selected answer language alone cannot establish the language of the visitor-facing text.
REVIEW_FIELDS = ("planning_meaning", "query_meaning", "grounding", "answer_language", "memory")


def expected(case):
    return {"route": "direct_response" if case.status == "conversational" else "retrieve",
            "status": case.status, "language": case.language, "detected_language": case.detected_language, "evidence_ids": list(case.evidence),
            "instruction": ({"original_fragment": case.instruction, "purpose": "conversation_setting",
                             "persistence": "conversation", "preference": case.language} if case.instruction else None)}


def _observed_plan(attempts):
    attempt = next((item for item in attempts if item["operation"] == "plan"), {})
    data = attempt.get("completion", {}).get("data")
    if "canonical_plan" in attempt:
        return attempt["canonical_plan"]
    if data and "visitor_task" in data:
        # Archived dialogue probes saved the fitted input before the capability
        # probe began recording the canonical projection alongside raw output.
        try:
            return expand_plan(data, attempt.get("model_input", {}))
        except ContractViolation:
            return None
    return data


def grade_case(expect, result, attempts, lookups):
    plan = _observed_plan(attempts)
    sources = {c["chunk_id"] for lookup in lookups for c in lookup.get("candidates", [])}
    evidence = set(expect["evidence_ids"])
    requires_lookup = expect["route"] == "retrieve"
    # None = this dimension could not be observed. Never turn a failed call into empty evidence.
    successful_lookups = lookups and all("candidates" in lookup for lookup in lookups)
    instruction_ok = language_ok = None
    if plan:
        decision = plan["language_decision"]
        actual = decision["explicit_instruction"]
        instruction = expect["instruction"]
        instruction_ok = actual is None if instruction is None else bool(actual
            and actual["original_fragment"].strip(" .!?").casefold() == instruction["original_fragment"].casefold()
            and all(actual[k] == instruction[k] for k in ("preference", "purpose", "persistence")))
        language_ok = decision["answer_language"] == expect["language"]
    citations = {c.get("chunk_id", c.get("fact_id")) for c in result["answer"]["citations"]} if result else None
    checks = {"route": plan["action"] == expect["route"] if plan else None,
              "detected_language": plan["language_decision"]["detected_language"] == expect["detected_language"] if plan else None,
              "language_decision": language_ok, "language_instruction": instruction_ok,
              "retrieval_used": bool(lookups) == requires_lookup if plan else None,
              "retrieved_evidence": (sources == evidence if successful_lookups else
                                     "not_applicable" if not requires_lookup and not lookups and plan else None),
              "status": result["answer"]["status"] == expect["status"] if result else None,
              "citations": citations == evidence if result else None,
              "answer_language_field": result["answer_language"] == expect["language"] if result else None}
    required = [v for key,v in checks.items() if key != "retrieved_evidence" or requires_lookup]
    # If only the outcome failed, leave evidence, provider schema and context observations intact.
    return checks, all(check is True for check in required)


def review_slots(attempts, result):
    plan = _observed_plan(attempts)
    any_queries = bool(plan and plan.get("queries")) or any(
        a.get("completion", {}).get("data", {}).get("queries") for a in attempts if a["operation"] != "plan")
    observable = {"planning_meaning": bool(plan), "query_meaning": any_queries,
                  "grounding": bool(result), "answer_language": bool(result), "memory": bool(result)}
    return {key: "pending" if observable[key] else "not_applicable" for key in REVIEW_FIELDS}


def operation_summary(attempts):
    return {name: {**dict(Counter(a["outcome"] for a in group)),
                   "attempted": len(group), "chat_posts": sum(a["chat_attempted"] for a in group),
                   "schema": dict(Counter(a["schema_result"] for a in group))}
            for name in ("plan", "assess", "final") if (group := [a for a in attempts if a["operation"] == name])}


def five_pair_capacity(attempts, history, profile):
    wanted = [t["turn_id"] for t in history]
    plans = [a for a in attempts if a["operation"] == "plan"]
    return len(wanted) == 5 and bool(plans) and all(
        a.get("retained_turn_ids") == wanted and type(a.get("server_prompt_tokens")) is int
        and type(a.get("estimated_prompt_tokens")) is int
        and 0 < a["server_prompt_tokens"] <= a["estimated_prompt_tokens"]
        and a["estimated_prompt_tokens"] + profile.plan_output_tokens + profile.safety_tokens <= profile.context_tokens
        for a in plans)


def aggregate(cases, suite):
    selected = [entry for entry in cases if entry["suite"] == suite]
    metrics = {}
    labels = {True: "pass", False: "fail", None: "unobserved", "not_applicable": "not_applicable"}
    for entry in selected:
        for field, value in entry["machine_checks"].items():
            counts = metrics.setdefault(field, Counter())
            counts[labels[value]] += 1
    semantics = {field: dict(Counter(entry["semantic_review"][field] for entry in selected))
                 for field in REVIEW_FIELDS}
    return {"cases": len(selected), "machine_passed": sum(e["machine_passed"] for e in selected),
            "metrics": metrics, "semantic_review": semantics}
