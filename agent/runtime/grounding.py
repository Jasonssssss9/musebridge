"""Render local evidence gaps and build a source-linked factual handoff."""
from agent.runtime.errors import WorkflowError
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


def grounded_memory(run, plan, results, proposal):
    """Record factual decisions once; their exact wording lives in the cited sources."""
    requests = plan.get("requests", [])
    if (not results or plan["language_decision"]["translation"] is not None
            or not all(r["kind"] in FACT_KINDS and r["resolution"] == "clear" for r in requests)):
        return proposal

    state = run["conversation_state"]
    turn = state["revision"] + 1
    # The visitor's own English words best preserve the predicate. For other
    # languages the frozen plan already supplies a standalone English intent.
    field = "original_fragment" if plan["language_decision"]["detected_language"] == "en" else "intent"
    topics = [request[field] for request in requests]
    if len(requests) == 1 and field == "original_fragment":
        # The literal fragment may be just the quotation the visitor challenged.
        # Preserve the whole question so the quotation is never saved as its premise.
        topics = [run["question"]]
    lines = []
    # MemoryUpdate has a 1,000-character limit. Keep every support decision
    # even when one question (or its list of citations) consumes the whole budget.
    line_limit = (1000 - len(results) + 1) // len(results)
    for index, (part, topic) in enumerate(zip(results, topics), 1):
        label = "Open here" if part["support"] == "insufficient" else "Answered"
        source = ", ".join(part["citation_ids"])
        citation = f" [source: {source}]" if source else ""
        prefix = f"Turn {turn}, visitor question {index} ({label}): "
        line = prefix + topic + citation
        if len(line) > line_limit:
            line = prefix + "see the visitor's question in this turn" + citation
            if len(line) > line_limit:
                line = prefix + "see this turn's question and citations"
        lines.append(line)
    summary = "\n".join(lines)
    # Keep previous goals and preferences as their own complete note if they
    # fit. The recent-turn window still holds the original conversations.
    prior = state["continuity_summary"]
    if prior and len(prior) + 1 + len(summary) <= 1000:
        summary = prior + "\n" + summary
    topic = "Visitor asked: " + " / ".join(topics)
    return {**proposal, "active_topic": topic if len(topic) <= 300 else run["context"]["artwork_title"][:300],
            "continuity_summary": summary}
