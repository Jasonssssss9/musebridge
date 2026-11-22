"""Veto an explicitly requested spatial detail missing from the selected statements."""
import re


# These recognize unambiguous spatial expressions in an English source, not
# arbitrary occurrences of "left" (departed), "right" (correct) or "top" (best).
_SIDE = r"(?:on|to|at|in|near) (?:the |its |her |his |their |our )?"
_LEVEL = r"(?:on|at|in|near) (?:the |its )?"
_REGION = r"(?:side|edge|corner|half|portion|part|section|area|margin|quadrant)"
_FORMS = {
    "left": rf"\b(?:{_SIDE}left(?:[- ]hand)?|left[- ]hand|leftmost)(?: {_REGION})?\b|\bleft (?:of|{_REGION})\b",
    "right": rf"\b(?:{_SIDE}right(?:[- ]hand)?|right[- ]hand|rightmost)(?: {_REGION})?\b|\bright (?:of|{_REGION})\b",
    "upper": rf"\b(?:{_LEVEL}top|uppermost|upper[- ]{_REGION}|top[- ]{_REGION})\b",
    "lower": rf"\b(?:{_LEVEL}bottom|lowermost|lower[- ]{_REGION}|bottom[- ]{_REGION})\b",
    "above": r"\b(?:above|higher than|directly over)\b",
    "below": r"\b(?:below|beneath|underneath|lower than|directly under)\b",
    "foreground": r"\b(?:foreground|(?:at|in) the front of the (?:scene|picture|composition|image))\b",
    "background": r"\b(?:background|(?:at|in) the back of the (?:scene|picture|composition|image))\b",
}
_PATTERNS = {key: re.compile(form, re.IGNORECASE) for key, form in _FORMS.items()}
# Focus on location requests and source-verification questions. A work-specific
# "what color is the shape on the left?" is asking for a color, not a placement.
_LOCATION = re.compile(r"^(?:is|are|was|were|where|which (?:side|corner|part|half|region))\b|"
                       r"^(?:does|do|did|can)\b.*?\b(?:mention|locate|place|position|put|show|depict|"
                       r"state|say|specify|indicate|describe|confirm|verify|appear|sit|stand|lie|hang)\b")
# A qualifier immediately before a new predicate is often part of the subject
# ("does the museum say the figure on the left is reading?"). Only recognize
# a terminal location phrase. Loose/unrecognized English is left to assessment.
_REFERENCE = r"(?:the |a |an |its |her |his |their |this |that )?[\w’-]+"
_REGION_TAIL = re.compile(rf"(?:(?: of| in| within) {_REFERENCE}(?: (?:frame|scene|picture|surface|area))?)?$")
_OBJECT_TAIL = re.compile(rf" {_REFERENCE}$")


def missing_spatial_detail(intent, selected_english):
    """Return True only to withhold a stronger claim. Presence never proves it."""
    question = " ".join(intent.lower().strip().rstrip("?.!").split())
    if not _LOCATION.search(question):
        return False
    requested = set()
    for axis, pattern in _PATTERNS.items():
        for match in pattern.finditer(question):
            tail = question[match.end():]
            if axis in {"above", "below"} or match.group().endswith(" of"):
                terminal = _OBJECT_TAIL.fullmatch(tail)
            else:
                terminal = _REGION_TAIL.fullmatch(tail)
            if terminal:
                requested.add(axis)
    if not requested:
        return False
    # An explicit choice between locations can be answered with either one.
    # Skip that construction unless its full set of options was identified.
    if " or " in question:
        return False
    sources = " ".join(selected_english)
    return any(not _PATTERNS[axis].search(sources) for axis in requested)
