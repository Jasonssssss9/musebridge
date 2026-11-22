"""Bind model-selected catalog fields to the admitted artwork snapshot."""

import re

from agent.prompts.v2.readings import catalog_statement
from agent.runtime.validation import ContractViolation

_QUESTIONS = {
    "title": "What title does the catalog list for {title}?",
    "artist": "Who is listed as the artist of {title}?",
    "date_display": "What date does the catalog list for {title}?",
}


def catalog_field_for_query(query, title):
    """Recognize a complete English field lookup scoped to this exact work."""
    subject = re.escape(title)
    forms = {
        "artist": rf"(?:who (?:painted|created|made) {subject}|who is (?:the |listed )?artist of {subject})",
        "title": rf"what (?:is the title of {subject}|title does the (?:museum|catalog) list for {subject})",
        "date_display": rf"(?:when was {subject} (?:painted|created|made)|what date does the catalog list for {subject})",
    }
    return next((field for field, expression in forms.items()
                 if re.fullmatch(expression + r"[?.]?", query, re.IGNORECASE)), None)


def catalog_plan(fields, question, language_decision, payload):
    """Project one catalog result per requested field from the admitted artwork snapshot."""
    if language_decision["translation"] is not None:
        raise ContractViolation("A catalog lookup cannot replace a requested translation.")
    if (question != payload["original_question"] or not fields or
            any(field not in _QUESTIONS for field in fields) or len(set(fields)) != len(fields)):
        raise ContractViolation("Select distinct released catalog fields for the current question.")

    artwork_title = payload["artwork_context"]["artwork_title"]
    requests, results = [], []
    for index, field in enumerate(fields, 1):
        matches = [fact for fact in payload["catalog_facts"] if fact["field"] == field]
        if len(matches) != 1:
            raise ContractViolation("The requested catalog field is unavailable or ambiguous.")
        source = catalog_statement(matches[0], language_decision["answer_language"])
        request_id = f"r{index}"
        # Coordinated fields can share one literal phrase, e.g. “its title and artist”.
        requests.append({"id": request_id, "original_fragment": question, "kind": "catalog_fact",
                         "intent": _QUESTIONS[field].format(title=artwork_title), "resolution": "clear"})
        results.append({"request_id": request_id, "support": "supported", "text": source["wording"],
                        "citation_ids": [source["citation_id"]]})

    state = payload["conversation_context"]
    return {
        "action": "direct_response", "language_decision": language_decision,
        "requests": requests, "results": results,
        # grounded_memory appends each cited English source statement after answer validation.
        "memory_update": {"active_topic": state["active_topic"], "continuity_summary": state["continuity_summary"],
                          "pending_update": {"action": "keep"}},
    }
