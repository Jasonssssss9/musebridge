"""Separate the role of a visitor sentence from the English query it may require."""
from agent.runtime.validation import ContractViolation


def passage_items(definitions, count):
    query = definitions["SearchQuery"]["properties"]["query"]
    excerpt = definitions["InterpretedRequest"]["properties"]["original_fragment"]

    def role(name, **properties):
        fields = {"role": {"type": "string", "const": name}, **properties}
        return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}

    clause = {"type": "object", "properties": {"original_excerpt": excerpt, "english_search_question": query},
              "required": ["original_excerpt", "english_search_question"], "additionalProperties": False}
    return {"type": "array", "minItems": count or 2, "maxItems": count or 4, "items": {"oneOf": [
        role("context_for_the_request"),
        role("one_evidence_request", english_search_question=query),
        role("two_independent_evidence_requests", clauses={
            "type": "array", "minItems": 2, "maxItems": 2, "items": clause}),
    ]}}


def bind_passages(decision, count):
    """The index of each reading binds it to an actual current-message sentence."""
    readings = decision["passages"]
    if len(readings) != count or not 2 <= count <= 4:
        raise ContractViolation("Each current visitor sentence requires one ordered reading.")
    facts = []
    for number, reading in enumerate(readings, 1):
        if reading["role"] == "context_for_the_request":
            continue
        if reading["role"] == "one_evidence_request":
            facts.append({"source": {"sentence": number}, "english_search_question": reading["english_search_question"]})
        else:
            for clause in reading["clauses"]:
                facts.append({"source": {"sentence": number, "excerpt": clause["original_excerpt"]},
                              "english_search_question": clause["english_search_question"]})
    if not 1 <= len(facts) <= 2:
        raise ContractViolation("A factual plan must contain one or two independently requested checks.")
    return {**{key: value for key, value in decision.items() if key != "passages"}, "facts": facts}
