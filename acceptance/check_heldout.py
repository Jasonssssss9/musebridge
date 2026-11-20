#!/usr/bin/env python3
"""Structural checks and freeze manifest for the visitor held-out suite.

Usage (from the repository root):
    python3 acceptance/check_heldout.py              # check the draft
    python3 acceptance/check_heldout.py --freeze     # write MANIFEST.json after review
    python3 acceptance/check_heldout.py --verify     # confirm files still match MANIFEST.json

These checks prove counts, labels and source IDs are well formed. They do not
prove that the wording is natural, that the expected outcomes are correct, or
anything about model quality. Those need the human reviews in README.md.
"""
import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SUITE = HERE / "visitor-heldout" / "v0.1"
FILES = ("single-turn.json", "multi-turn.json")
SEED = ROOT / "knowledge" / "data" / "aic-seed-v0.1.json"

LANGS = ["en", "es", "zh-CN", "ko"]
PREFS = ["auto"] + LANGS
STATUSES = {"answered", "partially_answered", "clarification_needed", "insufficient", "out_of_scope", "conversational"}
CITED = {"answered", "partially_answered"}
ROUTES = {"direct_response", "retrieve", "answer_from_context", "clarify", "out_of_scope"}
KINDS = {"conversation", "general_art_knowledge", "catalog_fact", "artwork_fact", "outside_scope"}
RETRIEVAL = {"none", "required", "either"}
PENDING = {"none", "local", "whole", "kept", "cleared", "local_or_whole", "local_or_whole_or_none"}
CATEGORY_COUNTS = {"direct_conversation": 12, "supported_fact": 16, "mixed": 12, "unsupported_fact": 8,
                   "ambiguity": 6, "scope": 6}
FAMILIES = [f"V{n:02d}" for n in range(1, 13)]
REQUIRED_COVERAGE = {
    "fifth/sixth boundary", "thanks during pending", "abandoned pending", "resolved pending", "source reuse",
    "source gap", "artwork isolation", "catalog", "one-off translation", "persistent switch", "correction",
    "expression preference", "exact quote unavailable", "local clarification", "request overflow",
    "pointing without vision", "unsupported comparison", "refresh while processing", "stale revision",
    "reset not available", "creative story deferred", "turn-only instruction", "neutral acknowledgement",
}

errors = []


def err(where, msg):
    errors.append(f"{where}: {msg}")


def load_sources():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    owner = {c["chunk_id"]: c["artwork_id"] for c in seed["chunks"]}
    for a in seed["artworks"]:
        for field in ("title", "artist", "date_display"):
            owner[f"{a['artwork_id']}-catalog-{field}"] = a["artwork_id"]
    return owner


def check_expect(where, e, artwork, owner, status_keys=("status", "status_any")):
    statuses = [e["status"]] if "status" in e else e.get("status_any") or []
    if not statuses or any(s not in STATUSES for s in statuses):
        err(where, f"bad status {statuses}")
    c = e.get("citations", {})
    ids = set(c.get("required", [])) | set(c.get("allowed", [])) | {i for g in c.get("any_of", []) for i in g}
    for i in ids:
        if i not in owner:
            err(where, f"unknown source id {i}")
        elif owner[i] != artwork:
            err(where, f"source {i} belongs to another artwork")
    needs_source = bool(c.get("required") or c.get("any_of"))
    if needs_source and not set(statuses) <= CITED:
        err(where, "required sources but a status that must have no citations")
    if set(statuses) == {"answered"} or set(statuses) == {"partially_answered"}:
        if not needs_source:
            err(where, "a cited status with no required sources")
    if not ids and set(statuses) & CITED:
        err(where, "a cited status with no permitted sources")
    if e.get("retrieval") not in RETRIEVAL:
        err(where, f"bad retrieval {e.get('retrieval')}")
    if e.get("pending_after") not in PENDING:
        err(where, f"bad pending_after {e.get('pending_after')}")
    for key in ("answer_language",):
        if key in e and e[key] not in LANGS:
            err(where, f"bad {key} {e[key]}")
    if "preference_after" in e and e["preference_after"] not in PREFS:
        err(where, f"bad preference_after {e['preference_after']}")
    if "auto_language_after" in e and e["auto_language_after"] not in LANGS:
        err(where, f"bad auto_language_after {e['auto_language_after']}")


def check_single(data, owner, texts):
    sc = data["scenarios"]
    cats = collections.Counter(s["category"] for s in sc)
    if dict(cats) != CATEGORY_COUNTS:
        err("single-turn", f"category counts {dict(cats)} != {CATEGORY_COUNTS}")
    ids = [s["id"] for s in sc]
    if len(set(ids)) != len(ids):
        err("single-turn", "duplicate scenario ids")
    risk = [s["id"] for s in sc if s["risk_repeat"]]
    if len(risk) < 12:
        err("single-turn", f"{len(risk)} risk-repeat scenarios; need at least 12")
    arts = collections.Counter(s["artwork_id"] for s in sc)
    if len(arts) != 2 or min(arts.values()) < 20:
        err("single-turn", f"artwork balance {dict(arts)}")
    for s in sc:
        w = s["id"]
        e = s["expected"]
        if not s.get("intent"):
            err(w, "missing intent")
        if any(r not in ROUTES for r in e["routes"]) or not e["routes"]:
            err(w, f"bad routes {e['routes']}")
        if any(k not in KINDS for k in e["request_kinds"]):
            err(w, f"bad kinds {e['request_kinds']}")
        check_expect(w, e, s["artwork_id"], owner)
        if [v["language"] for v in s["variants"]] != LANGS:
            err(w, "variants must be en, es, zh-CN, ko in that order")
        for v in s["variants"]:
            vw = f"{w}/{v['language']}"
            if not v["text"].strip():
                err(vw, "empty text")
            texts[v["text"]].append(vw)
            if v["language_override"] not in [None] + PREFS:
                err(vw, f"bad override {v['language_override']}")
            check_expect(vw, {**v["expect"], "retrieval": "none", "pending_after": "none", "status": "conversational"},
                         s["artwork_id"], owner)
        if s["category"] in ("unsupported_fact", "scope") and not e["must_not"]:
            err(w, "gap and scope cases need must_not labels")
    return len(sc), sum(len(s["variants"]) for s in sc), risk


def check_multi(data, owner, texts):
    scripts = data["scripts"]
    if len(scripts) < 16:
        err("multi-turn", f"{len(scripts)} scripts; need at least 16")
    mono = [s for s in scripts if s["type"] == "monolingual"]
    switching = [s for s in scripts if s["type"] == "switching"]
    if len(mono) < 8 or len(switching) < 8:
        err("multi-turn", f"{len(mono)} monolingual / {len(switching)} switching; need 8 / 8")
    per_lang = collections.Counter(s["primary_language"] for s in mono)
    if any(per_lang[l] < 2 for l in LANGS):
        err("multi-turn", f"monolingual per language {dict(per_lang)}; need 2 each")
    seen_families = collections.defaultdict(set)
    coverage, artworks, switch_langs = set(), set(), set()
    for s in scripts:
        w = s["id"]
        msgs = [t for t in s["steps"] if t["kind"] == "message"]
        if len(msgs) < 8:
            err(w, f"{len(msgs)} visitor turns; need at least 8")
        coverage |= set(s["coverage"])
        for t in s["steps"]:
            artworks.add(t["artwork_id"])
        for f in s["families"]:
            seen_families[f].add(s["primary_language"])
        if s["type"] == "monolingual":
            bad = {t["language"] for t in msgs} - {s["primary_language"], "und"}
            if bad:
                err(w, f"monolingual script contains {bad}")
        else:
            switch_langs |= {t["language"] for t in msgs} | {t["expect"]["answer_language"] for t in msgs}
        for t in msgs:
            tw = f"{w}/t{t['visitor_turn']}"
            texts[t["text"]].append(tw)
            if t["language"] not in LANGS + ["und"]:
                err(tw, f"bad detected language {t['language']}")
            check_expect(tw, t["expect"], t["artwork_id"], owner)
    for f in ("V01", "V02", "V03", "V04"):
        missing = set(LANGS) - seen_families[f]
        if missing:
            err("multi-turn", f"{f} not covered in {sorted(missing)}")
    for f in FAMILIES:
        if f not in seen_families:
            err("multi-turn", f"{f} not covered by any script")
    missing = REQUIRED_COVERAGE - coverage
    if missing:
        err("multi-turn", f"coverage missing {sorted(missing)}")
    if artworks != {"aic-28560", "aic-11723"}:
        err("multi-turn", f"artworks {artworks}")
    if set(LANGS) - switch_langs:
        err("multi-turn", f"switching scripts miss {set(LANGS) - switch_langs}")
    return len(scripts), sum(s["visitor_turns"] for s in scripts)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true", help="write MANIFEST.json (run only after the reviews)")
    ap.add_argument("--verify", action="store_true", help="check the files against MANIFEST.json")
    args = ap.parse_args()

    manifest_path = SUITE / "MANIFEST.json"
    if args.verify:
        # Compare hashes before parsing, so any edit (even one that breaks the JSON) is reported plainly.
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad = [f for f in FILES if m["sha256"].get(f) != sha256(SUITE / f)]
        if bad:
            print(f"changed since freeze on {m['frozen_at']}: {', '.join(bad)}")
            return 1
        print(f"matches MANIFEST.json frozen {m['frozen_at']}")

    owner = load_sources()
    single = json.loads((SUITE / FILES[0]).read_text(encoding="utf-8"))
    multi = json.loads((SUITE / FILES[1]).read_text(encoding="utf-8"))
    texts = collections.defaultdict(list)
    n_sc, n_trials, risk = check_single(single, owner, texts)
    n_scripts, n_turns = check_multi(multi, owner, texts)
    dupes = {t: w for t, w in texts.items() if len(w) > 1 and t.strip() not in {"OK.", "Thanks.", "谢谢。"}}
    for t, w in dupes.items():
        err("suite", f"duplicate visitor text {t!r} in {w}")

    print(f"single-turn: {n_sc} scenarios, {n_trials} trials, {len(risk)} risk-repeat ({', '.join(risk)})")
    print(f"multi-turn:  {n_scripts} scripts, {n_turns} visitor turns")
    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print("  -", e)
        return 1
    print("structure: ok")

    if args.freeze:
        m = {
            "suite": "musebridge-visitor-heldout",
            "version": single["version"],
            "frozen_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": {"scenarios": n_sc, "trials": n_trials, "risk_repeat": risk, "scripts": n_scripts,
                       "visitor_turns": n_turns},
            "sha256": {f: sha256(SUITE / f) for f in FILES},
        }
        manifest_path.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {manifest_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
