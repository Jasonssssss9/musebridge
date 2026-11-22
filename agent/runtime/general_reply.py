"""A single general art definition comes from the model, outside the artwork's evidence."""
import re
from copy import deepcopy

from agent.runtime.memory_notes import append_notes
from agent.runtime.validation import ContractViolation


# A compact definition must be anchored in a vocabulary request. Other
# forms of general discussion are still available through the ordinary Plan.
_DEFINITION_CUE = re.compile(
    r"\b(?:define|definition|meaning|terminology)\b|\b(?:does|would|can)\b.{0,100}\bmean\b"
    r"|\bwhat (?:exactly )?is (?:an? |meant by )|\b(?:qué (?:es|significa)|significado|definición|definir)\b"
    r"|什么意思|什么是|是什么意[思义]|含义|定义|指什么|是什么意思"
    r"|무슨 뜻|무슨 의미|어떤 의미|뜻(?:인가|이|은|을)|정의(?:가|는|를)|용어",
    re.IGNORECASE,
)


def offers_definition(question):
    return _DEFINITION_CUE.search(question) is not None


def general_schema(definitions):
    text = definitions["InterpretedRequest"]["properties"]["intent"]
    return {"type": "object", "properties": {
        "visitor_task": {"type": "string", "const": "define_general_art_term"},
        "language_decision": {"$ref": "#/$defs/LanguageDecision"},
        "original_term": {**text, "maxLength": 120, "description":
            "Copy the actual art term from the current visitor message, in the original language. "
            "A reference to an earlier speaker, word or answer is not a term."},
        "english_concept": {**text, "maxLength": 120, "description": "The English equivalent of original_term."},
        "definition": {**text, "maxLength": 600, "description":
            "A standalone definition in the reply language. Explain the general term without referring to this work."}},
        "required": ["visitor_task", "language_decision", "original_term", "english_concept", "definition"],
        "additionalProperties": False}


def general_plan(data, payload):
    language = data["language_decision"]
    if language["translation"] is not None:
        raise ContractViolation("A definition cannot replace a translation.")
    question = payload["original_question"]
    term = data["original_term"]
    if not term.strip() or term != term.strip() or term not in question:
        raise ContractViolation("The defined term must be quoted exactly from the current visitor message.")
    # An English term already supplies its own label; another language needs
    # the model's English equivalent for the standalone interpreted request.
    concept = term if language["detected_language"] == "en" else data["english_concept"].strip()
    definition = data["definition"].strip()
    # The general branch should stand on its own when shown beside a different
    # work. Reject explicit references to the selected title or artist. A title
    # that happens to be the requested term can still be used as an ordinary noun.
    for name in payload["artwork_context"].values():
        if name.casefold() != concept.casefold() and re.search(
            r"(?<!\w)" + re.escape(name) + r"(?!\w)", definition, re.IGNORECASE
        ):
            raise ContractViolation("A general definition must stand independently of the selected artwork.")
    state = payload["conversation_context"]
    note = "Visitor quotation: “" + " ".join(question.splitlines()) + "”"
    return {
        "action": "direct_response", "language_decision": deepcopy(language),
        "requests": [{"id": "r1", "original_fragment": question,
                      "intent": f"What does the term {concept} mean in art?",
                      "kind": "general_art_knowledge", "resolution": "clear"}],
        "results": [{"request_id": "r1", "support": "not_applicable", "text": definition, "citation_ids": []}],
        "memory_update": {"active_topic": note if len(note) <= 300 else "The visitor's general art question",
                          "continuity_summary": append_notes(state["continuity_summary"], [note]),
                          "pending_update": {"action": "keep"}},
    }
