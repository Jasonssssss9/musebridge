"""Generate an Ollama grammar envelope; the original schema stays authoritative."""
from copy import deepcopy


def grammar_schema(schema):
    """Remove unsupported assertion keywords before generation, never repair data.

    Ollama's converter treats pattern-only allOf string branches as objects.
    String patterns, formats and conditional assertions therefore run solely in
    the mandatory original-schema validator after strict JSON parsing. Keeping
    a broader grammar does not permit any invalid result to cross the provider.
    """
    def node(value):
        if type(value) is not dict:
            return deepcopy(value)
        # Some v2 objects wrap their entire base type in allOf, alongside a
        # semantic assertion. Dropping that base would allow arbitrary JSON.
        if set(value) == {"allOf"}:
            for clause in value["allOf"]:
                if set(clause) == {"$ref"}:
                    return {"$ref": clause["$ref"]}
        result = {}
        for key, item in value.items():
            if key in {"allOf", "if", "then", "else", "pattern", "format"}:
                continue
            if key in {"properties", "$defs"}:
                result[key] = {name: node(child) for name, child in item.items()}
            elif key in {"oneOf", "anyOf", "prefixItems"}:
                result[key] = [node(child) for child in item]
            elif key in {"items", "additionalProperties"}:
                result[key] = node(item)
            else:
                result[key] = deepcopy(item)
        return result

    projected = node(schema)
    defs = projected.get("$defs", {})
    if "DirectResponsePlan" in defs and "InterpretedRequest" in defs:
        # The canonical direct-route contract excludes a clear artwork_fact.
        # Encode the same restriction in concrete branches for the decoder.
        request = defs["InterpretedRequest"]
        ordinary = deepcopy(request)
        ordinary["properties"]["kind"]["enum"].remove("artwork_fact")
        unresolved = deepcopy(request)
        unresolved["properties"]["kind"] = {"const": "artwork_fact"}
        unresolved["properties"]["resolution"] = {"const": "needs_clarification"}
        defs["DirectResponsePlan"]["properties"]["requests"]["items"] = {"oneOf": [ordinary, unresolved]}
    for name in ("ClarificationPlan", "ClarificationDecision", "ScopePlan"):
        if name in defs and "PendingUpdate" in defs:
            memory = deepcopy(defs["MemoryUpdate"])
            variants = defs["PendingUpdate"]["oneOf"]
            if name == "ScopePlan":
                allowed = [v for v in variants if v["properties"]["action"]["const"] in {"keep", "clear"}]
                memory["properties"]["pending_update"] = {"oneOf": allowed}
            else:
                pending = deepcopy(next(v for v in variants if v["properties"]["action"]["const"] == "set"))
                pending["properties"]["request_id"] = {"type": "null"}
                memory["properties"]["pending_update"] = pending
            defs[name]["properties"]["memory_update"] = memory
    return projected
