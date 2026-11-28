"""Stable references to literal source text, shared by Vincent planning and answers."""
import re


def literal_spans(text, prefix):
    """Number exact substrings without rewriting the author's words."""
    pieces = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text.strip()):
        clauses = re.split(r"(?<=[,:])\s+|\s+(?=—\s)", sentence) if len(sentence) > 220 else [sentence]
        pieces.extend(clause for clause in clauses if clause)
    return {f"{prefix}{n}": phrase for n, phrase in enumerate(pieces, 1)}
