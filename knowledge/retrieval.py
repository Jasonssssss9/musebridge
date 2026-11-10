"""Deterministic keyword retrieval for the local museum dataset."""

import re
import unicodedata

from .dataset import Dataset
from .errors import RetrievalError


_TOPIC_ALIASES = {
    "material": (
        "material", "materials", "medium", "made of", "oil", "canvas", "painting support",
        "材料", "材质", "媒介", "油画", "画布", "底材",
    ),
    "brushwork": (
        "brushwork", "brushstroke", "brushstrokes", "brush stroke", "brush strokes",
        "brush-stroke", "brush-strokes", "brush mark", "brush marks", "paint mark", "paint marks",
        "笔触", "画痕", "笔痕",
    ),
    "energy": ("nervous", "energy", "紧张"),
    "color": ("color", "colors", "colour", "colours", "颜色", "色彩"),
    "perspective": ("perspective", "receding lines", "透视"),
    "background": ("background", "wallpaper", "bedspread", "背景", "墙纸", "床罩"),
}

_TOPIC_PATTERNS = {
    topic: re.compile(
        "|".join(
            rf"\b{re.escape(alias)}\b" if alias.isascii() else re.escape(alias)
            for alias in aliases
        )
    )
    for topic, aliases in _TOPIC_ALIASES.items()
}


# Each alternative describes cues that must occur in the same clause. Ambiguous
# words such as "surface", "uneasy" and "behind" are not standalone aliases.
_CLAUSE_SEPARATOR = re.compile(r"[,.;:!?，。；：！？、]+")
# Keep ordinary commas with their question. A subject before a comma can carry
# a topic word while the unsupported detail is requested after the comma.
_QUESTION_SEPARATOR = re.compile(
    r"[.!?;。！？；]+"
    r"|(?:[,，]\s*)?\b(?:and|but|also)\s+(?=(?:what|which|how|why|who|when|where"
    r"|did|do|does|is|are|was|were|can|could|would|has|have|will)\b)"
    r"|[,，]\s*(?=另外|此外|还想问|也想问|再问|还有|又(?:是)?(?:怎么|怎样|如何|为什么|用|有))",
    re.IGNORECASE,
)
_COMMA_SEPARATOR = re.compile(r"[,，]\s*")
_QUESTION_CUE = re.compile(
    r"^\s*(?:what|which|how|why|who|when|where|did|do|does|is|are|was|were|can|could|would|has|have|will)\b"
    r"|什么|多少|怎么|怎样|如何|哪[个种类件些]|是否|是不是|有没有|[吗呢]\s*$",
    re.IGNORECASE,
)
_EN_SUPPORT = r"(?:paper|canvas|cloth|fabric|linen|wood(?:en panel)?|woven (?:fabric|surface))"
_ZH_SUPPORT = r"(?:纸(?:张|面)?|布(?:面|料)?|织物|亚麻布|木板)"
_SUPPORT_CHOICE = re.compile(
    rf"\bpainted (?:on|onto) (?:an? |the )?{_EN_SUPPORT}(?:,\s*| )"
    rf"(?:or|rather than) (?:on |onto )?(?:an? |the )?{_EN_SUPPORT}\b"
    rf"|(?:画|绘)在{_ZH_SUPPORT}上(?:的)?\s*[,，]?\s*(?:还是|或是?)"
    rf"(?:(?:画|绘)在)?{_ZH_SUPPORT}上"
)
_CONTEXTUAL_TOPIC_CUES = {
    "material": (
        (r"\b(?:what|which)\b", r"\bpainted (?:on|onto)(?=\s*(?:$|(?:and|or)\b))"),
        (
            r"\b(?:what|which) (?:(?:kind|type) of )?(?:surface|support)\b",
            r"\b(?:paint(?:ed|ing)?|picture|artwork)\b",
        ),
        (r"\b(?:paint and (?:the )?(?:surface|support)|(?:surface|support) and (?:the )?paint)\b",),
        (r"(?:画|绘)在.{0,16}(?:什么|哪[种类样]).{0,12}上",),
    ),
    "brushwork": (
        (r"\b(?:painter|artist)['’]s marks?\b",),
        (
            r"\b(?:are|do|does|describe|characteri[sz]e) (?:the |these |those )?"
            r"(?:marks?|strokes?) (?:from|left by|made (?:with|by)) (?:the |a |her |his )?brush\b",
            r"\b(?:look|appearance|like|smooth|continuous|thick|broken|rough|soft|feathery|delicate)\b",
        ),
    ),
    "energy": (
        (
            r"\b(?:room|scene|painting|picture|image|composition) "
            r"(?:(?:can|could|may|might) )?(?:feels?|seems?|looks?) "
            r"(?:(?:so|very|rather|somewhat|slightly|a little|a bit) )?(?:unsettling|uneasy)\b",
        ),
        (r"(?:画面|房间|场景|构图).{0,12}(?:显得|看起来|让人|令人|给人).{0,6}不安",),
    ),
    "background": (
        (
            r"\bbehind (?:her|(?:the )?(?:figure|woman|sitter))\b",
            r"\b(?:floral|flowers?|flower[- ]like|patterns?|shapes?|looks? like|suggest(?:s|ed)?)\b",
        ),
        (r"身后", r"花形|花纹|花卉|花朵|图案|看起来像|看着像|像什么"),
        (
            r"\b(?:what|which)\b",
            r"\b(?:room|scene|picture|painting|image|background)\b",
            r"\b(?:flower(?:ed|[- ]like)?|floral) (?:patterns?|motifs?|shapes?|designs?)\b",
            r"\b(?:looks? (?:as if|like)|suggests?|seems?|resembles?|appears?)\b",
        ),
        (
            r"(?:什么|哪些|哪种|哪件)(?:物件|物品|东西)",
            r"房间|画面|背景|身后",
            r"花卉图案|花形|花纹|花卉纹样",
            r"看起来|看着|像|似乎|仿佛",
        ),
    ),
}
# These request details fall outside the released excerpts. The keys identify
# which candidate topics are affected, so other questions can still contribute.
_UNSUPPORTED_DETAIL_CUES = {
    ("material",): (
        (r"\b(?:what|which) (?:(?:kind|type|sort) of )?oil\b",),
        (r"\boil\b", r"\b(?:binder|mixed with|used to mix|diluted with)\b"),
        (r"(?:什么|哪[种类样])油(?!画)",),
        (
            r"\b(?:canvas|painting support)\b",
            r"\b(?:cut down|trim(?:med|ming)?|cropped|restretched|re[- ]?lined|replaced)\b",
        ),
        (r"画布|底材", r"裁(?:小|剪|过|掉|边)|重新绷|换过"),
        (
            r"^\s*(?:how (?:wide|tall|large)|(?:what|which) (?:is|are|was|were) (?:the )?"
            r"(?:(?:actual|real|exact|physical) )?(?:measurements?|dimensions?|width|height|size))\b"
            r"(?![,\s]+(?:(?:and|or) )?(?:the )?(?:medium|materials?|painting support)\b)",
        ),
        (r"(?:画布|底材).{0,14}(?:多宽|多高|多大|(?:尺寸|宽度|高度)(?:是)?多少)",),
    ),
    ("brushwork",): (
        (
            r"\b(?:brush (?:size|width)|(?:size|width) of (?:the |her |his |their )?brush(?:es)?"
            r"|(?:small|large|big|wide|narrow|thick|thin) brush(?:es)?)\b(?![ -](?:marks?|strokes?|work)\b)",
        ),
        (r"(?:小|大|宽|窄|粗|细)画笔|画笔.{0,6}(?:大小|尺寸|宽度)",),
    ),
    ("energy", "brushwork", "color", "perspective", "background"): (
        (
            r"\b(?:artist|painter|van gogh|morisot|he|she)\b.{0,24}"
            r"\b(?:said|say|wrote|write|stated|state|intend(?:ed)?|want(?:ed)?|aim(?:ed)?)\b",
        ),
        (r"梵高|莫里索|画家|艺术家|他|她", r"说过|写过|想让|有意|故意|刻意|意图"),
    ),
    ("perspective",): (
        (r"\b(?:actual|real|exact|physical) (?:measurements?|dimensions?|width|height)\b",),
        (r"(?:实际|真实|实测|精确)(?:尺寸|宽度|高度|测量)",),
    ),
    ("background",): (
        (r"\b(?:copy|copied|model(?:ed|led)?|inspir(?:e|ed)|bas(?:e|ed))\b", r"\b(?:real|actual)\b"),
        (r"照着|照搬|仿照|临摹|取自", r"真实|实际|现实|实景"),
        (r"\b(?:who|which (?:person|patron))\b.{0,16}\bcommission(?:ed)?\b",),
        (r"谁(?:委托|订制|定制)",),
        (
            r"\b(?:paint|layers?)\b",
            r"\b(?:dry|dried|drying)\b",
            r"\b(?:before|after|first|then|wait(?:ed)?)\b",
        ),
        (r"颜料|涂层|上色", r"干了|干透|晾干|未干|干燥", r"才|之后|以后|再|之前|等"),
    ),
}

# The short title "The Bedroom" needs a title form, a catalog ID, or an
# attribution cue; an ordinary lowercase reference to a room is ambiguous.
_ARTWORK_REFERENCES = {
    "aic-28560": re.compile(
        r"(?i:\baic-28560\b|[\"“]the bedroom[\"”]|\bvan gogh\b.{0,60}\bthe bedroom\b)"
        r"|\b(?:[Tt]he Bedroom|THE BEDROOM)\b|《\s*卧室\s*》|[\"“]卧室[\"”]"
    ),
    "aic-11723": re.compile(r"\baic-11723\b|\bwoman at her toilette\b|梳妆的女[人子]", re.IGNORECASE),
}
_CURRENT_WORK_REFERENCE = re.compile(
    r"\b(?:this|current|selected) (?:artwork|painting|picture|work)\b|\bhere\b"
    r"|这[幅张件](?:画|作品)|本(?:画|作品)|眼前这[幅张件]",
    re.IGNORECASE,
)
_DEPENDENT_WORK_REFERENCE = re.compile(
    r"\b(?:it|its|that (?:artwork|painting|picture|work))\b|它|那[幅张件](?:画|作品)",
    re.IGNORECASE,
)


def _compile_cues(rules):
    return tuple(tuple(re.compile(cue) for cue in rule) for rule in rules)


_CONTEXTUAL_TOPIC_PATTERNS = {topic: _compile_cues(rules) for topic, rules in _CONTEXTUAL_TOPIC_CUES.items()}
_UNSUPPORTED_DETAIL_PATTERNS = {topics: _compile_cues(rules) for topics, rules in _UNSUPPORTED_DETAIL_CUES.items()}


def _matches_cues(text, alternatives):
    return any(all(cue.search(text) for cue in rule) for rule in alternatives)


def _topics_in_question(normalized_query: str) -> set[str]:
    topics = {
        topic for topic, pattern in _TOPIC_PATTERNS.items() if pattern.search(normalized_query)
    }
    clauses = _CLAUSE_SEPARATOR.split(normalized_query)
    for topic, alternatives in _CONTEXTUAL_TOPIC_PATTERNS.items():
        if topic in topics:
            continue
        if any(_matches_cues(clause, alternatives) for clause in clauses):
            topics.add(topic)
    if _SUPPORT_CHOICE.search(normalized_query):
        topics.add("material")
    return topics


def _question_parts(query: str):
    for part in _QUESTION_SEPARATOR.split(query):
        if not part.strip():
            continue
        clauses = _COMMA_SEPARATOR.split(part)
        # Split a comma only when each side reads as a separate request.
        if len(clauses) > 1 and all(_QUESTION_CUE.search(clause) for clause in clauses):
            yield from clauses
        else:
            yield part


def _query_topics(query: str, artwork_id: str) -> set[str]:
    topics = set()
    foreign_subject = False
    for question in _question_parts(query):
        mentioned = {key for key, pattern in _ARTWORK_REFERENCES.items() if pattern.search(question)}
        current_reference = _CURRENT_WORK_REFERENCE.search(question)
        if mentioned or current_reference:
            foreign_subject = bool(mentioned) and artwork_id not in mentioned and not current_reference
        elif not _DEPENDENT_WORK_REFERENCE.search(question):
            foreign_subject = False
        if foreign_subject:
            continue

        normalized = question.lower()
        question_topics = _topics_in_question(normalized)
        for affected, alternatives in _UNSUPPORTED_DETAIL_PATTERNS.items():
            if question_topics.intersection(affected) and _matches_cues(normalized, alternatives):
                question_topics.difference_update(affected)
        topics.update(question_topics)
    return topics


def retrieve(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
) -> dict:
    """Return fresh evidence records for a query scoped to one artwork."""
    if not isinstance(artwork_id, str) or not artwork_id.strip():
        raise RetrievalError("invalid_request", "artwork_id must be a nonempty string.")
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise RetrievalError("invalid_request", "dataset_version must be a nonempty string.")
    if not isinstance(query, str):
        raise RetrievalError("invalid_request", "query must be a string.")
    if any(
        unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\n\r"
        for char in query
    ):
        raise RetrievalError("invalid_request", "query contains invalid characters.")

    query = query.strip()
    if not 1 <= len(query) <= 1000:
        raise RetrievalError("invalid_request", "query must contain 1 to 1000 Unicode code points.")
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise RetrievalError("invalid_request", "top_k must be an integer from 1 to 5.")
    if dataset_version != dataset.dataset_version:
        raise RetrievalError("dataset_version_mismatch", "The requested dataset version is not loaded.")
    if artwork_id not in dataset.artworks:
        raise RetrievalError("artwork_not_found", "The requested artwork is not in the dataset.")

    normalized_query = " ".join(unicodedata.normalize("NFKC", query).split())
    topics = _query_topics(normalized_query, artwork_id)
    ranked_chunks = []
    for chunk in dataset.chunks:
        if chunk["artwork_id"] != artwork_id:
            continue
        score = len(topics.intersection(chunk["topics"]))
        if score:
            ranked_chunks.append((score, chunk))
    ranked_chunks.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))

    evidence = []
    for _, chunk in ranked_chunks[:top_k]:
        source = dataset.sources[chunk["source_id"]]
        evidence.append(
            {
                "chunk_id": chunk["chunk_id"],
                "artwork_id": chunk["artwork_id"],
                "source_id": chunk["source_id"],
                "source_title": source["source_title"],
                "source_url": source["source_url"],
                "source_language": source["source_language"],
                "source_locator": chunk["source_locator"],
                "excerpt": chunk["excerpt"],
                "license": chunk["license"],
                "license_url": chunk["license_url"],
                "attribution": chunk["attribution"],
                "normalization": chunk["normalization"],
            }
        )
    return {"dataset_version": dataset.dataset_version, "evidence": evidence}
