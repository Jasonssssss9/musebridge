"""Render local evidence gaps and build a source-linked factual handoff."""
from agent.prompts.v2.readings import catalog_statement, source_statements, statement_sentence
from agent.runtime.errors import WorkflowError
from agent.runtime.memory_notes import append_notes
from agent.runtime.validation_v2 import FACT_KINDS


# A failed lookup says only what this turn could verify. Original fragments
# identify the precise gap without asking the model to paraphrase it again.
_GAP = {
    "en": ("“{}” — I can’t verify that from the information I have here.",
           "For part {}, I can’t verify that from the information I have here.",
           "I can’t verify that here."),
    "es": ("Sobre «{}», no puedo confirmarlo con la información que tengo aquí.",
           "Sobre la parte {}, no puedo confirmarla con la información que tengo aquí.",
           "Aquí no puedo confirmarlo."),
    "zh-CN": ("关于你问的“{}”，凭这里的信息，我还无法确认。",
              "关于第 {} 个问题，凭这里的信息，我还无法确认。", "我目前无法确认这一点。"),
    "ko": ("질문하신 “{}”에 대해서는 지금 주어진 정보만으로 확인할 수 없습니다.",
           "{}번째 질문은 지금 주어진 정보만으로 확인할 수 없습니다.", "여기서는 확인할 수 없습니다."),
}


def render_catalog_facts(plan, answer, parts, catalog):
    """Replace supported catalog prose with the exact fields accepted for that result."""
    decision = plan["language_decision"]
    if decision["translation"] is not None:
        return
    accepted = {record["fact_id"]: record for record in catalog}
    rendered = []
    for request, part in zip(plan.get("requests", []), parts):
        if request["kind"] == "catalog_fact" and part["support"] == "supported":
            # assemble_answer has already checked citation membership, kind and scope.
            records = [accepted[key] for key in part["citation_ids"]]
            part["text"] = " ".join(catalog_statement(r, decision["answer_language"])["wording"] for r in records)
            rendered.append((part, records))
    if not rendered:
        return
    text = "\n\n".join(part["text"] for part in parts)
    if len(text) > 2000:
        # Bare catalog values also answer these fields. Preserve other complete
        # parts verbatim without cutting names or silently dropping a question.
        for part, records in rendered:
            part["text"] = ", ".join(record["value"] for record in records)
        text = "\n\n".join(part["text"] for part in parts)
        if len(text) > 2000:
            raise WorkflowError("generation_invalid_response", "The complete sourced answer exceeds its limit.",
                                phase="validation")
    answer["text"] = text


def render_evidence_gaps(plan, answer, parts):
    """Localize verified gap decisions; keep supported prose and translations intact."""
    language = plan["language_decision"]
    if language["translation"] is not None:
        return
    gaps = [(i, request, part) for i, (request, part) in enumerate(zip(plan.get("requests", []), parts), 1)
            if request["kind"] in FACT_KINDS and part["support"] == "insufficient"]
    if not gaps:
        return
    language_code = language["answer_language"]
    quote, position, brief = _GAP[language_code]
    fixed_size = sum(len(p["text"]) for p in parts if p["support"] != "insufficient") + 2 * (len(parts) - 1)
    room = (2000 - fixed_size) // len(gaps)
    if room < len(brief):
        raise WorkflowError("generation_invalid_response", "No room for a scoped evidence gap.",
                            phase="validation")
    for index, request, part in gaps:
        text = position.format(index) if len(parts) > 1 else brief
        if language["detected_language"] == language_code:
            quoted = quote.format(request["original_fragment"])
            if len(quoted) <= room:
                text = quoted
        part["text"] = text if len(text) <= room else brief
    answer["text"] = "\n\n".join(p["text"] for p in parts)


def grounded_memory(run, plan, results, proposal, evidence=()):
    """Record factual decisions once; their exact wording lives in the cited sources."""
    requests = plan.get("requests", [])
    if (not results or plan["language_decision"]["translation"] is not None
            or not any(r["kind"] in FACT_KINDS and r["resolution"] == "clear" for r in requests)):
        return proposal

    state = run["conversation_state"]
    turn = state["revision"] + 1
    question = run["question"]
    language = plan["language_decision"]["answer_language"]
    statements = source_statements(evidence, language, run["context"].get("catalog_facts", []),
                                   include_catalog_only=True)
    header = f"Turn {turn}, visitor message: ({len(question)} characters) “{question}”"
    lines, details, cited = [], [], []
    for index, (request, part) in enumerate(zip(requests, results), 1):
        fragment = request["original_fragment"]
        # Positions refer to the literal message above; they identify what the
        # guide checked without turning a statement or conjecture into a question.
        start = question.find(fragment)
        locator = f" (characters {start + 1}–{start + len(fragment)})" if len(results) > 1 and start >= 0 else ""
        label = {"supported": "Source returned", "insufficient": "Open here"}.get(part["support"], "Visitor context")
        lines.append(f"Guide check {index}{locator}: {label}")
        cited.append(part["citation_ids"])
        selected = [row["english"] for row in statements.values() if row["citation_id"] in part["citation_ids"]
                    and statement_sentence(row["wording"], language) in part.get("text", "")]
        details.append(" | Source wording: " + " / ".join(selected) if selected else "")

    # Reserve room for the literal utterance, every decision and its citations
    # before adding complete English source statements. A short verified list
    # at the end avoids repeating long identifiers across different checks.
    refs = list(dict.fromkeys(key for ids in cited for key in ids))
    citations = [" [source: " + ", ".join(ids) + "]" if ids else "" for ids in cited]
    bare = [line + cite for line, cite in zip(lines, citations)]
    if len("\n".join([header, *bare])) > 1000:
        reference = {key: str(i) for i, key in enumerate(refs, 1)}
        bare = [line + (" [" + ",".join(reference[key] for key in ids) + "]" if ids else "")
                for line, ids in zip(lines, cited)]
        bare.append("Sources: " + ", ".join(f"[{reference[key]}] {key}" for key in refs))
    if len("\n".join([header, *bare])) > 1000:
        # An unusually long future source ID can exceed the semantic summary
        # cap. The validated citations remain in the run result and source cache.
        bare = [line + (" (see this turn’s citations)" if ids else "") for line, ids in zip(lines, cited)]

    remaining = 1000 - len("\n".join([header, *bare]))
    for i, detail in enumerate(details):
        if len(detail) <= remaining:
            bare[i] += detail
            remaining -= len(detail)
    note = "\n".join([header, *bare])
    summary = append_notes(state["continuity_summary"], [note])
    topic = f"Visitor message: “{question}”"
    if len(topic) > 300:
        topic = f"The visitor’s message in turn {turn}"
    return {**proposal, "active_topic": topic, "continuity_summary": summary}
