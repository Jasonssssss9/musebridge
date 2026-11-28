"""Serialize the model's source perspective alongside its English search question."""
import re

from agent.runtime.validation import ContractViolation


_QUALIFIERS = {
    "any_source": "",
    "artist_letter": " Consult the artist's letters.",
    "museum_record": " Consult the museum record.",
    "guide_note": " Use the MuseBridge guide notes.",
}

# Remove only our own serialized suffixes before adding the model's selected perspective.
_suffixes = [r"\s+".join(re.escape(word) for word in q.split()) for q in _QUALIFIERS.values() if q]
_SERIALIZED_PERSPECTIVE = re.compile(r"(?:\s+(?:" + "|".join(_suffixes) + r"))+$", re.IGNORECASE)


def query_fields():
    """Choose provenance first, then author the actual question for retrieval."""
    return {
        "perspective": {"type": "string", "enum": list(_QUALIFIERS)},
        "query": {"type": "string", "minLength": 1, "maxLength": 1000 - max(map(len, _QUALIFIERS.values()))},
    }


def render_query(selection):
    question = selection["query"]
    if not question or question != question.strip():
        raise ContractViolation("A search question must have content and no outer whitespace.")
    return _SERIALIZED_PERSPECTIVE.sub("", question) + _QUALIFIERS[selection["perspective"]]
