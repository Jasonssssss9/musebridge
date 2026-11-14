"""Run fixed v2 conversation scenarios with a scripted model and real retrieval.

This is a local diagnostic, not a public API or a real-model chat application.
"""
import argparse
import asyncio
import json
import threading

from knowledge.http_service import create_server

from agent.testing.v2 import (
    MOCK_MODE, FixtureProfile, ScriptedModel, TestBudget, fixture_prompt_sizer,
    language_decision, make_run, medium_answer, medium_plan, memory_update, simulate_commit,
)
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2

SCENARIOS = ("medium", "dialogue", "catalog", "partial", "reuse", "refine", "translation")

def dialogue_plan(run):
    return {
        "action": "direct_response", "language_decision": language_decision(),
        "requests": [{"id": "r1", "original_fragment": run["question"],
                      "intent": "Respond to the visitor's appreciation.",
                      "kind": "conversation", "resolution": "clear"}],
        "results": [{"request_id": "r1", "support": "not_applicable",
                     "text": "What about it catches your attention?", "citation_ids": []}],
        "memory_update": memory_update(topic="Visitor appreciation",
                                        summary="The visitor expressed appreciation for the work."),
    }


async def run_demo(base_url, artwork_id, scenario):
    manifest = EvidenceManifest.load()
    tool = RetrievalTool(manifest, base_url=base_url)
    turns = []

    async def execute(run, responses):
        model = ScriptedModel(*responses)
        workflow = MuseumQAWorkflowV2(
            model=model, retrieval=tool, manifest=manifest, profile=FixtureProfile(),
            prompt_sizer=fixture_prompt_sizer,
        )
        outcome = await workflow.run(run, request_id="c-v2-demo", budget=TestBudget())
        turns.append({"visitor_message": run["question"], **outcome.result_fields(),
                      "workflow": outcome.execution_fields(), "diagnostics": outcome.diagnostics})
        return outcome

    run = make_run(artwork_id=artwork_id, manifest=manifest)
    plan, answer = medium_plan(run), medium_answer(run)
    if scenario == "dialogue":
        run["question"] = "This artwork is beautiful."
        responses = [dialogue_plan(run)]
    elif scenario == "catalog":
        run["question"] = "Who made this artwork, and when?"
        plan = dialogue_plan(run)
        creator, date = run["context"]["catalog_facts"][1:]
        plan["requests"][0].update(kind="catalog_fact", intent="Give the catalog creator and displayed date.")
        plan["results"][0].update(support="supported", text=f"The catalog lists {creator['value']}, {date['value']}.",
                                  citation_ids=[creator["fact_id"], date["fact_id"]])
        plan["memory_update"] = memory_update(topic="Catalog creator and date",
            summary="The visitor asked about the catalog creator and displayed date.")
        responses = [plan]
    elif scenario == "partial":
        run["question"] = "What is it made of, and what is that small object?"
        plan = medium_plan(run)
        plan["requests"][0]["original_fragment"] = "What is it made of"
        plan["requests"].append({"id": "r2", "original_fragment": "what is that small object",
            "intent": "Identify the small object the visitor means.", "kind": "artwork_fact",
            "resolution": "needs_clarification"})
        answer["results"].append({"request_id": "r2", "support": "clarification_needed",
                                  "text": "Which small object do you mean?", "citation_ids": []})
        answer["memory_update"]["pending_update"] = {"action": "set", "source": "current_message", "request_id": "r2"}
        responses = [plan, answer]
    elif scenario == "refine":
        run["question"] = "How does the museum describe the brushwork?"
        plan = medium_plan(run)
        title = run["context"]["artwork_title"]
        plan["requests"][0]["intent"] = f"Describe the brushwork of {title}."
        plan["queries"][0]["query"] = f"What does the museum say about the facture of {title}?"
        refine = {"action": "refine", "reason_code": "no_candidates", "request_ids": ["r1"],
                  "queries": [{"id": "q2", "request_ids": ["r1"],
                               "query": f"How does the museum describe the brushwork of {title}?"}]}
        chunk = dict(manifest.records[artwork_id + "-description-01"])
        answer["results"][0].update(text="The museum's description says: " + chunk["excerpt"],
                                    citation_ids=[chunk["chunk_id"]])
        answer["memory_update"] = memory_update(topic="The artwork's brushwork",
            summary="The visitor asked how the museum describes the brushwork.")
        responses = [plan, refine, answer]
    else:
        responses = [plan, answer]
    outcome = await execute(run, responses)
    state, history_turn = simulate_commit(run, outcome.result_fields())
    if scenario in {"reuse", "translation"}:
        question = ("Explain that material more simply." if scenario == "reuse"
                    else "Translate your previous answer into Korean for my friend.")
        run = make_run(artwork_id=artwork_id, question=question,
                       history=[history_turn], conversation_state=state, manifest=manifest)
        plan = medium_plan(run)
        plan["action"] = "answer_from_context"
        del plan["queries"]
        plan["requests"][0]["intent"] = "Restate the previous supported material explanation."
        answer = medium_answer(run, text="It is oil paint on canvas.")
        if scenario == "translation":
            plan["language_decision"] = {"detected_language": "und", "explicit_instruction": None,
                "answer_language": "ko", "translation": {"target_language": "ko",
                "original_fragment": question, "source": {"kind": "history_answer", "sequence": 1}}}
            plan["requests"][0]["intent"] = "Translate the previous supported material explanation into Korean."
            answer["results"][0]["text"] = "미술관은 캔버스에 유채라고 설명합니다."
        outcome = await execute(run, [plan, answer])
        state, _ = simulate_commit(run, outcome.result_fields())
    return {
        "test_mode": MOCK_MODE, "scenario": scenario,
        "notice": "Model decisions and token counts are scripted. State commits are simulated in memory. No real LLM or application backend is used.",
        "turns": turns, "simulated_next_state": state,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artwork-id", choices=["aic-28560", "aic-11723"], default="aic-28560")
    parser.add_argument("--scenario", choices=SCENARIOS, default="medium")
    args = parser.parse_args()
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        result = asyncio.run(run_demo(f"http://127.0.0.1:{server.server_address[1]}", args.artwork_id, args.scenario))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
