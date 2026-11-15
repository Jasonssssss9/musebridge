"""Synthetic service inputs and controllable executors; never production wiring."""
import asyncio
from copy import deepcopy

from agent.runtime.context_v2 import validate_run_context
from agent.runtime.runs import RunRegistry
from agent.testing.v2 import example, make_run
from agent.tools.evidence import EvidenceManifest


def service_run():
    return make_run(question="Who painted this? When was it made?")


def service_result(run):
    result = example("catalog_only_run_result")
    result["scope"] = deepcopy(run["scope"])
    result["catalog_snapshot"] = deepcopy(run["context"]["catalog_facts"])
    result["conversation_update"]["base_revision"] = run["conversation_state"]["revision"]
    return result


class ControlledExecutor:
    def __init__(self, *, blocked=False, error=None, mutate=None, ignore_cancel=False):
        self.started, self.release, self.cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        if not blocked:
            self.release.set()
        self.error, self.mutate, self.ignore_cancel = error, mutate, ignore_cancel
        self.calls = []

    async def __call__(self, run, *, request_id, budget):
        self.calls.append((deepcopy(run), request_id, budget))
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.ignore_cancel:
                raise
            await self.release.wait()
        if self.error:
            raise self.error
        result = service_result(run)
        if self.mutate:
            self.mutate(result)
        return result


def controlled_registry(executor=None, **options):
    manifest = EvidenceManifest.load()
    return RunRegistry(executor or ControlledExecutor(),
                       validate_input=lambda run: validate_run_context(run, manifest),
                       ready=options.pop("ready", lambda: True), **options)
