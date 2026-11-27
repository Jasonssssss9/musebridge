"""Generate an Ollama grammar envelope; the original schema stays authoritative."""
from copy import deepcopy


def _vincent_plan_branches(defs):
    """Encode the common one-request relationships using grammar-supported branches.

    Longer plans retain the published mixed-request shapes and cross-field checks.
    The strict original schema and workflow validators still run after decoding.
    """
    defs["RequestID"] = {"type": "string", "enum": [f"r{i}" for i in range(1, 5)]}
    defs["QueryID"] = {"type": "string", "enum": [f"q{i}" for i in range(1, 5)]}

    def single(array, item):
        array.update(items=item, minItems=1, maxItems=1)

    def single_request(plan, kinds):
        item = deepcopy(defs["InterpretedRequest"])
        item["properties"].update(id={"const": "r1"}, kind={"type": "string", "enum": kinds},
                                  resolution={"const": "clear"})
        single(plan["properties"]["requests"], item)

    for name in ("RetrievePlan", "ContextAnswerPlan"):
        if name not in defs:
            continue
        multiple = defs[name]
        one = deepcopy(multiple)
        single_request(one, ["artwork_fact"])
        if name == "RetrievePlan":
            query = deepcopy(defs["SearchQuery"])
            single(query["properties"]["request_ids"], {"const": "r1"})
            one["properties"]["queries"]["items"] = query
        multiple["properties"]["requests"]["minItems"] = 2
        defs[name] = {"oneOf": [one, multiple]}

    multiple = defs["DirectResponsePlan"]
    simple = []
    for kinds, supports in ((["conversation", "general_art_knowledge"], {"not_applicable"}),
                            (["catalog_fact"], {"supported", "insufficient"})):
        one = deepcopy(multiple)
        single_request(one, kinds)
        result_shapes = []
        for variant in defs["RequestResult"]["oneOf"]:
            props = variant["properties"]
            allowed = [s for s in props["support"]["enum"] if s in supports]
            if allowed:
                item = deepcopy(variant)
                item["properties"].update(request_id={"const": "r1"}, support={"type": "string", "enum": allowed})
                result_shapes.append(item)
        single(one["properties"]["results"], {"oneOf": result_shapes})
        memory = deepcopy(defs["MemoryUpdate"])
        pending = defs["PendingUpdate"]["oneOf"]
        memory["properties"]["pending_update"] = {"oneOf": [
            p for p in pending if p["properties"]["action"].get("const") in ("keep", "clear")]}
        one["properties"]["memory_update"] = memory
        simple.append(one)
    multiple["properties"]["requests"]["minItems"] = 2
    multiple["properties"]["results"]["minItems"] = 2
    defs["DirectResponsePlan"] = {"oneOf": [*simple, multiple]}


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
    if schema.get("x-vincent-planning"):
        _vincent_plan_branches(defs)
        projected.pop("x-vincent-planning", None)
    return projected
