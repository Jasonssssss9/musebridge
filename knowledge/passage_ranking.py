"""Rank topic tags and passage text within the selected artwork and source type."""

from collections import Counter
from math import log
import re
import unicodedata

from .retrieval import DENSE_MIN_COSINE, fuse_rankings

_STOP = set("""a about according an and are art artist artwork as at be by can could describe
    describes description did do does explain for from give how i in into is it its me museum
    of on or painting please record say says show tell that the their there these this to
    us was were what which who why with work would you getty musebridge guide note notes""".split())
_MEDIUM = re.compile(r"\b(medium|material[s]?|made (?:of|from)|painted on|painted with|support)\b")
_DIMENSIONS = re.compile(r"\b(dimensions?|size|measurements?|height|width|how (?:big|large|wide|tall))\b")
_OVERVIEW = re.compile(r"\b(overview|introduction|summary|introduce|summari[sz]e|tell me about)\b"
                       r"|^describe (?:this |the )?(?:work|painting|piece)\b")
_GUIDE = re.compile(
    r"\b(?:guide (?:notes?|suggestions?|tips?|advice|recommendations?|prompts?)"
    r"|(?:viewing|looking) (?:tips?|advice|guidance|prompts?|suggestions?|activities)"
    r"|(?:guided|close)[ -]looking (?:activity|exercise)|(?:viewing|viewer) guidance)\b"
    r"|\b(?:recommend|suggest)(?:ed)? (?:looking|focusing|starting|paying attention)\b"
    r"|\b(?:where|how|what) to (?:look|start|begin|notice|focus)\b"
    r"|\b(?:should|can|could|would|might) (?:i|we|you|a visitor|the visitor|visitors|someone) "
    r"(?:start|begin|look|notice|focus|pay attention)\b"
    r"|\b(?:starting point|first (?:place|detail)) (?:for looking|to look|to notice)\b"
)
_OFFICIAL = re.compile(r"\b(getty|museum|curat(?:or|orial)|catalog(?:ue)?)\b")
_VENUE = re.compile(r"\bat (?:the )?(?:j\.? paul )?getty(?: (?:museum|center))?\b(?!['’])"
                    r"|\b(?:this|that|a) getty (?:painting|artwork|work)\b")
_TEAM = re.compile(r"\bmusebridge\b.{0,32}\b(?:suggest\w*|recommend\w*|advice|guide|note|tip)\b"
                   r"|\b(?:guide|notes?|suggestions?|advice|recommendations?) (?:from|by) musebridge\b")


def _source_kind(intent):
    """A venue identifies the work. An explicitly requested institution identifies the speaker."""
    if _OFFICIAL.search(_VENUE.sub(" ", intent)):
        return "museum_record"
    return "guide_note" if _GUIDE.search(intent) or _TEAM.search(intent) else "museum_record"


def _plain(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", _plain(text))
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w
            for w in words if w not in _STOP}


def _explicit_work(query, work):
    name = _plain(work["title"])
    return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", query))


def rank(dataset, artwork_id, query, vector_index):
    text = _plain(query)
    active = dataset.artworks[artwork_id]
    intent = text.replace(_plain(active["title"]), " ").replace(_plain(active["artist"]), " ")
    other_work = any(_explicit_work(intent, work) for key, work in dataset.artworks.items()
                     if key != artwork_id and work["title"] != active["title"])
    # A comparison is outside a single-artwork search.
    if other_work:
        return [], [], []
    source_kind = _source_kind(intent)
    chunks = [chunk for chunk in dataset.chunks if chunk["artwork_id"] == artwork_id
              and dataset.sources[chunk["source_id"]]["source_kind"] == source_kind]
    query_words = _tokens(intent)
    if re.search(r"\b(where|location|place)\b", intent):
        query_words.add("setting")
    if _MEDIUM.search(intent):
        query_words |= {"medium", "material"}
    if _DIMENSIONS.search(intent):
        query_words.add("dimension")
    if _OVERVIEW.search(intent):
        query_words.add("overview")
    frequencies = Counter()
    indexed = []
    for chunk in chunks:
        tags = _tokens(" ".join(chunk["topics"]).replace("_", " ") + " " + chunk["source_locator"])
        if chunk["source_locator"].lower().startswith("object description"):
            tags.add("overview")
        words = _tokens(chunk["excerpt"])
        frequencies.update(tags | words)
        indexed.append((chunk, tags, words))
    scored = []
    for chunk, tags, words in indexed:
        matched = query_words & (tags | words)
        weight = sum((3 if term in tags else 1) * (1 + log((1 + len(chunks)) / (1 + frequencies[term])))
                     for term in matched)
        if weight or source_kind == "guide_note":
            scored.append((-weight, chunk["chunk_id"]))
    lexical = [key for _, key in sorted(scored)]
    if not vector_index or not chunks:
        return lexical, lexical, []
    allowed = {chunk["chunk_id"] for chunk in chunks}
    hits = [(key, score) for key, score in vector_index.rank(artwork_id, query) if key in allowed]
    dense = [key for key, score in hits if score >= DENSE_MIN_COSINE]
    return fuse_rankings(lexical, dense), lexical, hits
