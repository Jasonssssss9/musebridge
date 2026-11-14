"""V2 model decisions, source binding and terminal response validation.

These checks enforce deterministic relationships, not semantic truth. Go owns
admission and persistence; canonical source loading belongs to the context/tool
layer. No function here calls a model or writes conversation state.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import (
    ContractValidator, ContractViolation, model_decision, normalized_query,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/agent-v2.schema.json"
LANGUAGES = frozenset(("en", "es", "zh-CN", "ko"))
FACT_KINDS = frozenset(("catalog_fact", "artwork_fact"))
_INVALID = (ContractViolation, KeyError, TypeError, IndexError)


class ContractValidatorV2(ContractValidator):
    """Consume the shared v2 schema without duplicating its wire types."""

    def __init__(self, path: Path = SCHEMA_PATH):
        super().__init__(path)


@lru_cache(maxsize=1)
def _shared_validator() -> ContractValidatorV2:
    return ContractValidatorV2()


def _invalid(phase: str, message: str) -> WorkflowError:
    return WorkflowError("generation_invalid_response", message, phase=phase)


def _literal(fragment: str, question: str) -> None:
    if not fragment.strip() or fragment not in question:
        raise ContractViolation("A source fragment is not in the original message.")


def _language(run: dict, decision: dict, validator: ContractValidator) -> dict:
    validator.validate("LanguageDecision", decision)
    state = run["conversation_state"]
    candidate = run["language_override"]
    if candidate is None:
        candidate = state["language_preference"]
    validator.validate("LanguagePreference", candidate)
    validator.validate("Language", state["auto_language"])
    instruction = decision["explicit_instruction"]
    translation = decision["translation"]
    effective = candidate
    if instruction is not None:
        _literal(instruction["original_fragment"], run["question"])
        effective = instruction["preference"]
    detected = decision["detected_language"]
    automatic = detected if detected in LANGUAGES else state["auto_language"]
    ordinary = automatic if effective == "auto" else effective
    answer_language = ordinary
    if translation is not None:
        _literal(translation["original_fragment"], run["question"])
        answer_language = translation["target_language"]
        if (instruction is not None and instruction["purpose"] == "turn_response"
                and ordinary != answer_language):
            raise ContractViolation("Conflicting temporary language targets.")
    if decision["answer_language"] != answer_language:
        raise ContractViolation("Response language does not follow the priority rules.")
    saved = (instruction["preference"] if instruction is not None
             and instruction["purpose"] == "conversation_setting" else candidate)
    return {"answer_language": answer_language, "next_language_preference": saved,
            "next_auto_language": automatic}


def resolve_language(run: dict, language_decision: dict) -> dict:
    """Validate model inference fields and derive deterministic language effects."""
    try:
        return _language(run, language_decision, _shared_validator())
    except _INVALID as exc:
        raise _invalid("planning", "The model language decision is invalid.") from exc


def _translation(run: dict, decision: dict, recent_turns: list[dict],
                 validator: ContractValidator) -> dict | None:
    validator.validate("LanguageDecision", decision)
    task = decision["translation"]
    if task is None:
        return None
    _literal(task["original_fragment"], run["question"])
    source = task["source"]
    if source is None:
        return None
    if source["kind"] == "current_quote":
        text = source["excerpt"]
        _literal(text, run["question"])
    else:
        matches = [turn for turn in recent_turns if turn["sequence"] == source["sequence"]]
        if len(matches) != 1:
            raise ContractViolation("The selected exact source is not in the fitted history.")
        original = matches[0]["answer_text"]
        text = original if source["kind"] == "history_answer" else source["excerpt"]
        if not text.strip() or text not in original:
            raise ContractViolation("The selected source is not an exact historical excerpt.")
    resolved = {"target_language": task["target_language"],
                "source": deepcopy(source), "text": text}
    validator.validate("ResolvedTranslation", resolved)
    return resolved


def resolve_translation(run: dict, language_decision: dict, *,
                        recent_turns: list[dict] | None = None) -> dict | None:
    """Bind an exact source from the actual fitted planning input, never a summary."""
    try:
        turns = run["context"]["recent_turns"] if recent_turns is None else recent_turns
        return _translation(run, language_decision, turns, _shared_validator())
    except _INVALID as exc:
        raise _invalid("planning", "The model translation source is invalid.") from exc


def _requests(run: dict, requests: list[dict], validator: ContractValidator) -> None:
    if not 1 <= len(requests) <= 4:
        raise ContractViolation("Invalid request count.")
    for index, request in enumerate(requests, 1):
        validator.validate("InterpretedRequest", request)
        if request["id"] != f"r{index}":
            raise ContractViolation("Request IDs must follow the original order.")
        _literal(request["original_fragment"], run["question"])
    if sum(item["resolution"] == "needs_clarification" for item in requests) > 1:
        raise ContractViolation("Only one local clarification can be retained.")


def _query_batch(queries: list[dict], eligible: set[str], executed: list[dict],
                 validator: ContractValidator) -> None:
    if not 1 <= len(queries) <= 2 or len(executed) + len(queries) > 4:
        raise ContractViolation("Query count exceeds the bounded workflow.")
    seen_text = {normalized_query(item["query"]) for item in executed}
    seen_ids = {item["id"] for item in executed}
    if len(seen_text) != len(executed) or len(seen_ids) != len(executed):
        raise ContractViolation("Executed query history is inconsistent.")
    if [item["id"] for item in executed] != [f"q{i}" for i in range(1, len(executed) + 1)]:
        raise ContractViolation("Executed query IDs are not contiguous.")
    linked = set()
    for index, query in enumerate(queries, len(executed) + 1):
        validator.validate("SearchQuery", query)
        if query["id"] != f"q{index}" or query["query"] != query["query"].strip():
            raise ContractViolation("Invalid query ID or surrounding whitespace.")
        ids = set(query["request_ids"])
        normalized = normalized_query(query["query"])
        if not ids <= eligible or normalized in seen_text:
            raise ContractViolation("Query links or normalized query uniqueness are invalid.")
        linked.update(ids)
        seen_text.add(normalized)
    if not eligible or linked != eligible:
        raise ContractViolation("Queries must cover exactly the selected clear artwork requests.")


def _terminal(plan: dict, decision: dict) -> dict:
    return plan if plan["action"] in ("direct_response", "clarify", "out_of_scope") else decision


def _derive_pending(state: dict, question: str, requests: list[dict], terminal: dict,
                    sequence: int, validator: ContractValidator) -> dict | None:
    memory = terminal["memory_update"]
    validator.validate("MemoryUpdate", memory)
    update = memory["pending_update"]
    prior = state["pending_clarification"]
    if prior is not None:
        validator.validate("PendingClarification", prior)
        if prior["created_sequence"] > state["revision"]:
            raise ContractViolation("Pending sequence is in the future.")
        if prior["original_fragment"] is not None:
            _literal(prior["original_fragment"], prior["original_question"])
    whole = terminal["action"] == "clarify"
    results = terminal.get("results", [])
    clarifying = [part for part in results if part["support"] == "clarification_needed"]
    if whole:
        if update["action"] != "set" or update["request_id"] is not None:
            raise ContractViolation("A whole-message clarification must set an unlinked pending item.")
    elif clarifying:
        if len(clarifying) != 1 or update["action"] != "set":
            raise ContractViolation("Local clarification must set its unique pending item.")
        if update["request_id"] != clarifying[0]["request_id"]:
            raise ContractViolation("Pending update is bound to the wrong result.")
    elif update["action"] == "set":
        raise ContractViolation("A pending question requires an actual clarification.")
    if update["action"] == "keep":
        return deepcopy(prior)
    if update["action"] == "clear":
        return None
    if type(sequence) is not int or sequence != state["revision"] + 1:
        raise ContractViolation("Pending sequence must be the next committed sequence.")
    previous = update["source"] == "pending_clarification"
    if previous and prior is None:
        raise ContractViolation("The selected earlier pending question does not exist.")
    original = prior["original_question"] if previous else question
    fragment = prior["original_fragment"] if previous else None
    intent = prior["unresolved_intent"] if previous else None
    if whole:
        text = terminal["text"]
    else:
        request_id = update["request_id"]
        matches = [request for request in requests if request["id"] == request_id]
        if len(matches) != 1 or matches[0]["resolution"] != "needs_clarification":
            raise ContractViolation("Pending question is not bound to an unresolved request.")
        request = matches[0]
        _literal(request["original_fragment"], question)
        if not previous:
            fragment = request["original_fragment"]
        intent = request["intent"]
        text = clarifying[0]["text"]
    result = {"original_question": original, "original_fragment": fragment,
              "unresolved_intent": intent, "question": text,
              "created_sequence": sequence}
    validator.validate("PendingClarification", result)
    return result


def derive_pending(state: dict, question: str, plan: dict, decision: dict,
                   sequence: int) -> dict | None:
    """Pure pending-field derivation for validation and the application handoff.

    This returns a value, not a committed state; Go must still apply its own
    active-attempt/revision/deadline transaction guards.
    """
    try:
        return _derive_pending(state, question, plan.get("requests", []),
                               _terminal(plan, decision), sequence, _shared_validator())
    except _INVALID as exc:
        raise _invalid("validation", "The pending clarification proposal is invalid.") from exc


def validate_plan(run: dict, plan: dict, validator: ContractValidator, *,
                  plan_input: dict | None = None, complete_scope: bool = False) -> None:
    """Validate structure, language, source binding, routes and query relationships."""
    try:
        validator.validate("Plan", plan)
        _language(run, plan["language_decision"], validator)
        turns = run["context"]["recent_turns"] if plan_input is None else plan_input["recent_turns"]
        _translation(run, plan["language_decision"], turns, validator)
        translation = plan["language_decision"]["translation"]
        action = plan["action"]
        if translation is not None and translation["source"] is None and action != "clarify":
            raise ContractViolation("A missing translation source requires whole-message clarification.")
        if action in ("clarify", "out_of_scope"):
            _derive_pending(run["conversation_state"], run["question"], [], plan,
                            run["conversation_state"]["revision"] + 1, validator)
            return
        requests = plan["requests"]
        _requests(run, requests, validator)
        factual = {item["id"] for item in requests
                   if item["kind"] == "artwork_fact" and item["resolution"] == "clear"}
        cache = (run["conversation_state"]["verified_evidence"] if plan_input is None
                 else plan_input["cached_evidence"])
        complete = complete_scope or (plan_input is not None and plan_input["complete_scope"])
        if action == "retrieve":
            if complete:
                raise ContractViolation("A complete retrieval scope cannot be searched again.")
            _query_batch(plan["queries"], factual, [], validator)
        elif action == "answer_from_context":
            if not factual or not cache:
                raise ContractViolation("Evidence reuse requires a clear artwork request and validated cache.")
        elif action == "direct_response":
            if factual:
                raise ContractViolation("A direct response cannot answer a clear noncatalog artwork fact.")
            _validate_parts(requests, plan["results"], validator)
            _derive_pending(run["conversation_state"], run["question"], requests, plan,
                            run["conversation_state"]["revision"] + 1, validator)
    except _INVALID as exc:
        raise _invalid("planning", "The model plan has invalid language, source or request relationships.") from exc


def validate_refinement(plan: dict, decision: dict, executed_queries: list[dict],
                        validator: ContractValidator, *, complete_scope: bool,
                        allow_refinement: bool) -> None:
    """Validate a permitted single refinement; runtime separately checks its budget."""
    try:
        validator.validate("RefineDecision", decision)
        if not allow_refinement or complete_scope:
            raise ContractViolation("Refinement is not permitted at this stage.")
        if plan["action"] not in ("retrieve", "answer_from_context"):
            raise ContractViolation("This route cannot refine.")
        eligible = {item["id"] for item in plan["requests"]
                    if item["kind"] == "artwork_fact" and item["resolution"] == "clear"}
        selected = set(decision["request_ids"])
        if not selected <= eligible:
            raise ContractViolation("Refinement changed the selected request scope.")
        _query_batch(decision["queries"], selected, executed_queries, validator)
    except _INVALID as exc:
        raise _invalid("assessment", "The model refinement decision is invalid.") from exc


def _validate_parts(requests: list[dict], results: list[dict],
                    validator: ContractValidator) -> None:
    if not 1 <= len(requests) <= 4 or len({request["id"] for request in requests}) != len(requests):
        raise ContractViolation("Result-bearing requests must be nonempty and uniquely identified.")
    if [part["request_id"] for part in results] != [request["id"] for request in requests]:
        raise ContractViolation("Results must cover every request exactly once in order.")
    if sum(part["support"] == "clarification_needed" for part in results) > 1:
        raise ContractViolation("Only one local clarification is permitted.")
    for request, part in zip(requests, results):
        validator.validate("InterpretedRequest", request)
        validator.validate("RequestResult", part)
        if request["resolution"] == "needs_clarification":
            allowed = {"clarification_needed"}
        elif request["kind"] in FACT_KINDS:
            allowed = {"supported", "insufficient"}
        elif request["kind"] == "outside_scope":
            allowed = {"out_of_scope"}
        else:
            allowed = {"not_applicable"}
        if part["support"] not in allowed:
            raise ContractViolation("Result support conflicts with the frozen request.")


def derive_status(requests: list[dict], results: list[dict]) -> str:
    """Derive public status from validated result kinds, not model labels."""
    try:
        _validate_parts(requests, results, _shared_validator())
        supports = {part["support"] for part in results}
        if "supported" in supports:
            return ("partially_answered" if supports & {
                "insufficient", "out_of_scope", "clarification_needed"} else "answered")
        if "clarification_needed" in supports:
            return "clarification_needed"
        if any(request["kind"] in FACT_KINDS for request in requests):
            return "insufficient"
        return "out_of_scope" if "out_of_scope" in supports else "conversational"
    except _INVALID as exc:
        raise _invalid("validation", "The model result support is invalid.") from exc


def assemble_answer(run: dict, plan: dict, decision: dict, evidence: list[dict],
                    catalog_facts: list[dict], validator: ContractValidator
                    ) -> tuple[dict, list[dict], dict]:
    """Resolve only accepted source IDs and return detached answer/parts/memory."""
    try:
        validator.validate("Plan", plan)
        _language(run, plan["language_decision"], validator)
        requests = plan.get("requests", [])
        if requests:
            _requests(run, requests, validator)
        terminal = _terminal(plan, decision)
        if terminal is decision and plan["action"] not in ("direct_response", "clarify", "out_of_scope"):
            validator.validate("FinalDecision", decision)
        by_id = {}
        retrieval_ids = set()
        catalog_ids = set()
        scope = run["scope"]
        if len(evidence) > 8 or len(catalog_facts) != 3:
            raise ContractViolation("Accepted source counts exceed the contract.")
        if [fact["field"] for fact in catalog_facts] != ["title", "artist", "date_display"]:
            raise ContractViolation("Catalog records are not in canonical order.")
        for record in evidence + catalog_facts:
            validator.validate("Citation", record)
            catalog = record.get("kind") == "catalog"
            identifier = record["fact_id"] if catalog else record["chunk_id"]
            if identifier in by_id or record["artwork_id"] != scope["artwork_id"]:
                raise ContractViolation("Accepted source identity is inconsistent.")
            if catalog and record["dataset_version"] != scope["dataset_version"]:
                raise ContractViolation("Catalog source release is inconsistent.")
            by_id[identifier] = record
            (catalog_ids if catalog else retrieval_ids).add(identifier)
        if terminal["action"] == "clarify" and plan["action"] in ("retrieve", "answer_from_context"):
            clear = [request for request in requests if request["resolution"] == "clear"]
            local = any(request["resolution"] == "needs_clarification" for request in requests)
            independent = any(request["kind"] != "artwork_fact" for request in clear)
            # A declared local ambiguity must not erase independent answers.
            # Exact translation can still require a whole-message source/length
            # clarification; its semantic reason remains a model-quality check.
            if plan["language_decision"]["translation"] is None and clear and (local or independent):
                raise ContractViolation("Whole-message clarification would discard independent answers.")
        if terminal["action"] in ("clarify", "out_of_scope"):
            results = []
            status = "clarification_needed" if terminal["action"] == "clarify" else "out_of_scope"
            answer = {"status": status, "text": terminal["text"], "citations": []}
        else:
            results = terminal["results"]
            _validate_parts(requests, results, validator)
            selected_ids = []
            for request, part in zip(requests, results):
                ids = part["citation_ids"]
                if any(identifier not in by_id for identifier in ids):
                    raise ContractViolation("A citation is not in the accepted source collection.")
                if part["support"] == "supported":
                    if request["kind"] == "catalog_fact" and not set(ids) <= catalog_ids:
                        raise ContractViolation("A catalog fact cannot cite an unrelated excerpt.")
                    if request["kind"] == "artwork_fact" and not set(ids) & retrieval_ids:
                        raise ContractViolation("Catalog metadata cannot prove a noncatalog artwork claim.")
                for identifier in ids:
                    if identifier not in selected_ids:
                        selected_ids.append(identifier)
            answer = {"status": derive_status(requests, results),
                      "text": "\n\n".join(part["text"] for part in results),
                      "citations": [deepcopy(by_id[identifier]) for identifier in selected_ids]}
        _derive_pending(run["conversation_state"], run["question"], requests, terminal,
                        run["conversation_state"]["revision"] + 1, validator)
        validator.validate("AgentAnswer", answer)
        return deepcopy(answer), deepcopy(results), deepcopy(terminal["memory_update"])
    except _INVALID as exc:
        raise _invalid("validation", "The model answer has invalid support, sources or memory.") from exc
