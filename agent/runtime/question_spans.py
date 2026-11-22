"""Resolve repeated sentence pointers only for visibly coordinated visitor questions."""
import re


# Only additive conjunctions introduce a new question here. An alternative or
# a reported quotation needs the model's ordinary, explicit excerpt binding.
_JOIN = {
    "en": r"(?:\s*,\s*|\s+)and\s+",
    "es": r"(?:\s*,\s*|\s+)y\s+",
    "zh-CN": r"[，,]\s*(?:另外|还有|此外|并且)\s*",
    "ko": r"[,，]\s*(?:그리고|또한|또)\s+",
}
_ALT = re.compile(r'\b(?:or|o|u)\b|还是|或者|아니면|또는|혹은|[“”«»「」"]', re.I)
_KINDS = {
    "en": (("measure", r"how (?:many|much|long|far|old|often)\b"), ("identity", r"(?:what|which)\b"),
           ("who", r"who(?:m|se)?\b"), ("when", r"when\b"), ("where", r"where\b"),
           ("why", r"why\b"), ("how", r"how\b"),
           ("polar", r"(?:do|does|did|is|are|was|were|can|could|would|will|has|have|had)\b")),
    "es": (("measure", r"cuánt[oa]s?\b"), ("identity", r"(?:qué|cuál(?:es)?)\b"),
           ("who", r"quién(?:es)?\b"), ("when", r"cuándo\b"), ("where", r"dónde\b"),
           ("why", r"por qué\b"), ("how", r"cómo\b")),
    "zh-CN": (("measure", r"多少|多久|多长|多远|几"), ("identity", r"什么(?!时候)|哪种|哪些|哪个"),
              ("who", r"谁"), ("when", r"何时|什么时候"), ("where", r"哪里|哪儿"),
              ("why", r"为什么|为何"), ("how", r"怎么|如何")),
    "ko": (("measure", r"얼마|몇"), ("identity", r"무엇|무슨|어느|어떤"),
           ("who", r"누가|누구"), ("when", r"언제"), ("where", r"어디"),
           ("why", r"왜"), ("how", r"어떻게")),
}
_INTERROGATIVE = {lang: re.compile("|".join(f"(?P<{kind}>{pattern})" for kind, pattern in kinds), re.I)
                  for lang, kinds in _KINDS.items()}


def _kind(clause, language, *, allow_subject=False):
    text = clause.strip().lstrip("¿").lstrip()
    if language == "es":
        text = re.sub(r"^(?:de|a|en|con)\s+", "", text, flags=re.I)
    marker = _INTERROGATIVE[language]
    # Chinese and Korean can put the subject before the question word. The
    # right clause must still start with its interrogative for automatic binding.
    hit = marker.search(text) if allow_subject and language in ("zh-CN", "ko") else marker.match(text)
    return hit.lastgroup if hit else None


def coordinated_spans(question, spans, facts, language):
    """Return two ordered literal offsets or defer to strict source validation."""
    if (language not in _JOIN or len(facts) != 2 or len(spans) != 1 or
            any(fact["source"] != {"sentence": 1} for fact in facts)):
        return None
    start, end = spans[0]
    sentence = question[start:end]
    if _ALT.search(sentence) or not sentence.endswith(("?", "？")):
        return None
    boundaries = list(re.finditer(_JOIN[language], sentence, re.I))
    if len(boundaries) != 1:
        return None
    boundary = boundaries[0]
    clauses = (sentence[:boundary.start()], sentence[boundary.end():])
    kinds = (_kind(clauses[0], language, allow_subject=True), _kind(clauses[1], language))
    rewrites = tuple(_kind(fact["english_search_question"], "en") for fact in facts)
    # Matching two different interrogatives in order links a sentence selector
    # to both requests. Identical or reversed questions have no such binding.
    if None in kinds or kinds[0] == kinds[1] or kinds != rewrites:
        return None
    return [(start, start + boundary.start()), (start + boundary.end(), end)]
