"""Bound the shared conversation summary without truncating individual notes."""
import re


# The length of an attributed message lets us skip literal newlines, even if
# a visitor quotes text that itself looks like a note from an earlier exchange.
_NOTE_START = re.compile(
    r"^(?:Turn [0-9]+, visitor message: \(([0-9]+) characters\) “"
    r"|Turn [0-9]+, visitor question [0-9]+ \((?:Answered|Open here|Visitor context)\): "
    r"|Visitor (?:impression|quotation): )", re.MULTILINE,
)


def _prior_notes(prior):
    start = cursor = 0
    while match := _NOTE_START.search(prior, cursor):
        if match.start() > start:
            yield prior[start:match.start() - 1]
            start = match.start()
        cursor = match.end() + int(match.group(1) or 0)
    if prior:
        yield prior[start:]


def append_notes(prior, lines):
    summary = "\n".join(lines)
    for note in reversed(list(_prior_notes(prior))):
        if len(note) + 1 + len(summary) > 1000:
            break
        summary = note + "\n" + summary
    return summary
