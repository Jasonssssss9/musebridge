"""Run a clearly labeled scripted-model workflow against real retrieval HTTP."""
import argparse
import asyncio
import json
import threading

from knowledge.http_service import create_server

from agent.testing.fakes import (
    MOCK_MODE, FixtureProfile, ScriptedModel, TestBudget, fixture_prompt_sizer,
    medium_answer, medium_plan, sample_run,
)
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa import MuseumQAWorkflow


async def run_demo(base_url, artwork_id, scenario):
    run = sample_run(artwork_id=artwork_id)
    if scenario == "clarify":
        run["question"] = "Why is that?"
        responses = [{"action": "clarify", "text": "Which detail in this artwork do you mean?"}]
    else:
        plan, decision = medium_plan(run), medium_answer(run)
        if scenario == "partial":
            run["question"] = "What is it made of, and why did the artist choose that material?"
            plan["requests"][0]["original_fragment"] = "What is it made of"
            plan["requests"].append({
                "id": "r2", "original_fragment": "why did the artist choose that material",
                "question": f"Why did the artist choose the material used for {run['context']['artwork_title']}?",
                "scope": "current_artwork",
            })
            plan["queries"].append({
                "id": "q2", "request_ids": ["r2"],
                "query": f"Why did the artist choose the material used for {run['context']['artwork_title']}?",
            })
            decision["results"].append({
                "request_id": "r2", "support": "insufficient",
                "text": "The available evidence does not explain the artist's choice of material.",
                "citation_chunk_ids": [],
            })
        responses = [plan, decision]
    model = ScriptedModel(*responses)
    manifest = EvidenceManifest.load()
    workflow = MuseumQAWorkflow(
        model=model, retrieval=RetrievalTool(manifest, base_url=base_url),
        manifest=manifest, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
    )
    outcome = await workflow.run(run, request_id="c-m1-demo", budget=TestBudget())
    return {
        "test_mode": MOCK_MODE,
        "notice": "Model decisions and token counts are scripted. This is not a real LLM answer.",
        **outcome.result_fields(),
        "workflow": outcome.execution_fields(),
        "diagnostics": outcome.diagnostics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artwork-id", choices=["aic-28560", "aic-11723"], default="aic-28560")
    parser.add_argument("--scenario", choices=["medium", "partial", "clarify"], default="medium")
    args = parser.parse_args()
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        result = asyncio.run(run_demo(
            f"http://127.0.0.1:{server.server_address[1]}", args.artwork_id, args.scenario,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
