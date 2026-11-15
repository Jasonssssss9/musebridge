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

    return node(schema)
