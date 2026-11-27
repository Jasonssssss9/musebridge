"""Trusted role binding for the existing bounded museum conversation graph."""
from dataclasses import replace
from types import MappingProxyType

from agent.contracts.codec import v3_validator
from agent.runtime.errors import WorkflowError
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2


def vincent_manifest(gallery):
    """Resolve only the pinned Irises gallery and artist-reference evidence."""
    from knowledge.vincent import VINCENT_SCOPE, load_vincent_corpus

    corpus = load_vincent_corpus(VINCENT_SCOPE)
    artwork_id = VINCENT_SCOPE["artwork_id"]
    if (gallery.dataset_version != VINCENT_SCOPE["dataset_version"]
            or corpus.release_hashes["gallery"]["sha256"] != gallery.sha256):
        raise ValueError("Vincent requires the pinned Getty gallery release.")
    records = {}
    sources = {key: source for key, source in gallery.sources.items()
               if source["artwork_id"] == artwork_id}
    for chunk_id, evidence in corpus.evidence.items():
        record = MappingProxyType(dict(evidence))
        records[chunk_id] = record
        sources.setdefault(record["source_id"], record)
    return replace(gallery, artworks=MappingProxyType({artwork_id: gallery.artworks[artwork_id]}),
                   records=MappingProxyType(records), sources=MappingProxyType(sources),
                   chunk_ids_by_artwork=MappingProxyType({artwork_id: frozenset(records)}))


def vincent_retrieval(manifest, *, base_url, client=None):
    """The URL binds the server-selected scope. Model-authored fields cannot select it."""
    return RetrievalTool(manifest, base_url=base_url, client=client, mode="hybrid", vincent=True)


class MuseumQAWorkflowV3(MuseumQAWorkflowV2):
    """Each instance has exactly one role, tool and canonical manifest."""

    def __init__(self, *, conversation_mode, **dependencies):
        super().__init__(**dependencies)
        self.conversation_mode = conversation_mode
        self.validator = v3_validator()

    def _validate_input(self, run, request_id):
        scope = run.get("scope", {})
        if scope.get("conversation_mode") != self.conversation_mode:
            raise WorkflowError("agent_scope_mismatch", "The selected conversation role does not match the run.",
                                phase="context")
        if self.conversation_mode == "vincent":
            from knowledge.vincent import VINCENT_SCOPE

            if scope != VINCENT_SCOPE or run.get("language_override") not in (None, "en"):
                raise WorkflowError("agent_scope_mismatch", "The requested Vincent scope or language is not enabled.",
                                    phase="context")
        return super()._validate_input(run, request_id)


def create_vincent_workflow(*, model, profile, prompt_sizer, manifest, retrieval):
    if retrieval.retrieval_strategy_version != "openai-hybrid-v1":
        raise ValueError("Vincent conversations require the hybrid evidence tool.")
    return MuseumQAWorkflowV3(conversation_mode="vincent", model=model, profile=profile,
                              prompt_sizer=prompt_sizer, manifest=manifest, retrieval=retrieval)
