"""Generated from docs/contracts/agent-v2.schema.json; do not edit.

TypedDicts provide static wire types. Use ContractCodec for runtime validation.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias, TypedDict, Union

SCHEMA_SHA256 = "05ff57e1097bc8178c82b8b799176f576a30490b540be6df31774de2cba3c5b0"

UUID: TypeAlias = str


RequestID: TypeAlias = str


QueryID: TypeAlias = str


class Scope(TypedDict):
    artwork_id: str
    dataset_version: str


class Evidence(TypedDict):
    chunk_id: str
    artwork_id: str
    source_id: str
    source_title: str
    source_url: str
    source_language: Literal['en']
    source_locator: str
    excerpt: str
    license: Literal['CC0-1.0', 'CC-BY-4.0']
    license_url: Literal['https://creativecommons.org/publicdomain/zero/1.0/', 'https://creativecommons.org/licenses/by/4.0/']
    attribution: str
    normalization: str


class SearchQuery(TypedDict):
    id: QueryID
    request_ids: list[RequestID]
    query: str


class ArtworkContext(TypedDict):
    artwork_title: str
    artist: str


class QueryPolicy(TypedDict):
    search_language: Literal['en']
    max_requests: Literal[4]
    max_queries: Literal[2]
    scope_policy: Literal['current_artwork_only']


class ToolInput(TypedDict):
    tool_version: Literal['evidence-tool-v1']
    query_id: QueryID
    request_ids: list[RequestID]
    query: str
    artwork_id: str
    dataset_version: str
    top_k: Literal[2]


class ToolResult(TypedDict):
    tool_version: Literal['evidence-tool-v1']
    query_id: QueryID
    artwork_id: str
    dataset_version: str
    retrieval_strategy_version: str
    candidates: list[Evidence]


class LegacyRetrieveRequest(TypedDict):
    artwork_id: str
    query: str
    dataset_version: str
    top_k: Literal[2]


class LegacyRetrieveResponse(TypedDict):
    dataset_version: str
    evidence: list[Evidence]


class AgentError(TypedDict):
    code: Literal['invalid_request', 'payload_too_large', 'unsupported_media_type', 'agent_unauthorized', 'route_not_found', 'method_not_allowed', 'agent_contract_mismatch', 'agent_scope_mismatch', 'agent_run_conflict', 'agent_run_in_progress', 'agent_run_not_found', 'agent_busy', 'service_not_ready', 'agent_context_limit', 'retrieval_invalid_response', 'retrieval_unavailable', 'retrieval_timeout', 'generation_invalid_response', 'generation_unavailable', 'generation_timeout', 'request_timeout', 'agent_internal_error']
    message: str
    retryable: bool
    phase: Literal['admission', 'context', 'planning', 'retrieval', 'assessment', 'final_generation', 'validation', 'capacity']


class ErrorEnvelope(TypedDict):
    request_id: str
    run_id: Union['UUID', None]
    error: AgentError


Language: TypeAlias = Literal['en', 'es', 'zh-CN', 'ko']


LanguagePreference: TypeAlias = Literal['auto', 'en', 'es', 'zh-CN', 'ko']


class LanguageInstruction(TypedDict):
    preference: LanguagePreference
    original_fragment: str
    persistence: Literal['turn', 'conversation']
    purpose: Literal['conversation_setting', 'turn_response']


class LanguageDecision(TypedDict):
    detected_language: Literal['en', 'es', 'zh-CN', 'ko', 'und', 'other']
    explicit_instruction: Union[None, 'LanguageInstruction']
    answer_language: Language
    translation: Union[None, 'TranslationTask']


AgentStatus: TypeAlias = Literal['answered', 'partially_answered', 'insufficient', 'conversational', 'clarification_needed', 'out_of_scope']


class PendingClarification(TypedDict):
    original_question: str
    question: str
    created_sequence: int
    original_fragment: Union[None, 'str']
    unresolved_intent: Union[None, 'str']


class PendingUpdateVariant1(TypedDict):
    action: Literal['keep']


class PendingUpdateVariant2(TypedDict):
    action: Literal['clear']


class PendingUpdateVariant3(TypedDict):
    action: Literal['set']
    source: Literal['current_message', 'pending_clarification']
    request_id: Union[None, 'RequestID']


PendingUpdate: TypeAlias = Union['PendingUpdateVariant1', 'PendingUpdateVariant2', 'PendingUpdateVariant3']


class MemoryUpdate(TypedDict):
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_update: PendingUpdate


class ConversationContext(TypedDict):
    language_preference: LanguagePreference
    auto_language: Language
    last_answer_language: Union[None, 'Language']
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_clarification: Union[None, 'PendingClarification']


class ConversationState(TypedDict):
    state_version: Literal['conversation-state-v1']
    conversation_id: UUID
    scope: Scope
    revision: int
    language_preference: LanguagePreference
    auto_language: Language
    last_answer_language: Union[None, 'Language']
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_clarification: Union[None, 'PendingClarification']
    verified_evidence: list[Evidence]


class HistoryTurn(TypedDict):
    turn_id: UUID
    sequence: int
    question: str
    answer_language: Language
    answer_status: AgentStatus
    answer_mode: Literal['llm']
    answer_text: str


class Context(TypedDict):
    artwork_title: str
    artist: str
    recent_turns: list[HistoryTurn]
    catalog_facts: list[CatalogFact]


class RunExecution(TypedDict):
    workflow_id: Literal['museum_qa_v2']
    profile_id: Literal['local-ollama-v2']
    deadline_at: str


class RunRequest(TypedDict):
    contract_version: Literal['agent-run-v2']
    run_id: UUID
    conversation_id: UUID
    client_message_id: UUID
    question: str
    language_override: Union[None, 'LanguagePreference']
    scope: Scope
    context: Context
    conversation_state: ConversationState
    execution: RunExecution


class InterpretedRequest(TypedDict):
    id: RequestID
    original_fragment: str
    intent: str
    kind: Literal['conversation', 'general_art_knowledge', 'artwork_fact', 'outside_scope', 'catalog_fact']
    resolution: Literal['clear', 'needs_clarification']


class RequestResultVariant1(TypedDict):
    request_id: RequestID
    support: Literal['supported']
    text: str
    citation_ids: list[str]


class RequestResultVariant2(TypedDict):
    request_id: RequestID
    support: Literal['insufficient', 'not_applicable', 'out_of_scope', 'clarification_needed']
    text: str
    citation_ids: list[str]


RequestResult: TypeAlias = Union['RequestResultVariant1', 'RequestResultVariant2']


class RetrievePlan(TypedDict):
    action: Literal['retrieve']
    language_decision: LanguageDecision
    requests: list[InterpretedRequest]
    queries: list[SearchQuery]


class ContextAnswerPlan(TypedDict):
    action: Literal['answer_from_context']
    language_decision: LanguageDecision
    requests: list[InterpretedRequest]


class DirectResponsePlan(TypedDict):
    action: Literal['direct_response']
    language_decision: LanguageDecision
    requests: list[DirectResponsePlanRequestsItem]
    results: list[RequestResult]
    memory_update: MemoryUpdate


class DirectResponsePlanRequestsItem(TypedDict):
    id: RequestID
    original_fragment: str
    intent: str
    kind: Literal['conversation', 'general_art_knowledge', 'artwork_fact', 'outside_scope', 'catalog_fact']
    resolution: Literal['clear', 'needs_clarification']


class ClarificationPlan(TypedDict):
    action: Literal['clarify']
    language_decision: LanguageDecision
    text: str
    memory_update: ClarificationPlanMemoryUpdate


class ClarificationPlanMemoryUpdate(TypedDict):
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_update: PendingUpdate


class ScopePlan(TypedDict):
    action: Literal['out_of_scope']
    language_decision: LanguageDecision
    text: str
    memory_update: ScopePlanMemoryUpdate


class ScopePlanMemoryUpdate(TypedDict):
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_update: PendingUpdate


Plan: TypeAlias = Union['RetrievePlan', 'ContextAnswerPlan', 'DirectResponsePlan', 'ClarificationPlan', 'ScopePlan']


class FinishDecision(TypedDict):
    action: Literal['finish']
    results: list[RequestResult]
    memory_update: MemoryUpdate


class ClarificationDecision(TypedDict):
    action: Literal['clarify']
    text: str
    memory_update: ClarificationDecisionMemoryUpdate


class ClarificationDecisionMemoryUpdate(TypedDict):
    active_topic: Union[None, 'str']
    continuity_summary: str
    pending_update: PendingUpdate


class RefineDecision(TypedDict):
    action: Literal['refine']
    reason_code: Literal['no_candidates', 'poor_candidate_relevance', 'uncovered_request']
    request_ids: list[RequestID]
    queries: list[SearchQuery]


AssessmentDecision: TypeAlias = Union['FinishDecision', 'ClarificationDecision', 'RefineDecision']


FinalDecision: TypeAlias = Union['FinishDecision', 'ClarificationDecision']


class PlanInput(TypedDict):
    original_question: str
    language_override: Union[None, 'LanguagePreference']
    artwork_context: ArtworkContext
    recent_turns: list[HistoryTurn]
    conversation_context: ConversationContext
    cached_evidence: list[Evidence]
    complete_scope: bool
    query_policy: QueryPolicy
    catalog_facts: list[CatalogFact]


class AnswerInput(TypedDict):
    original_question: str
    answer_language: Language
    artwork_context: ArtworkContext
    recent_turns: list[HistoryTurn]
    conversation_context: ConversationContext
    requests: list[InterpretedRequest]
    executed_queries: list[SearchQuery]
    evidence: list[Evidence]
    complete_scope: bool
    allow_refinement: bool
    catalog_facts: list[CatalogFact]
    translation: Union[None, 'ResolvedTranslation']


class FinalInput(TypedDict):
    original_question: str
    answer_language: Language
    artwork_context: ArtworkContext
    recent_turns: list[HistoryTurn]
    conversation_context: ConversationContext
    requests: list[InterpretedRequest]
    executed_queries: list[SearchQuery]
    evidence: list[Evidence]
    complete_scope: bool
    allow_refinement: Literal[False]
    catalog_facts: list[CatalogFact]
    translation: Union[None, 'ResolvedTranslation']


class AgentAnswerVariant1(TypedDict):
    status: Literal['answered', 'partially_answered']
    text: str
    citations: list[Citation]


class AgentAnswerVariant2(TypedDict):
    status: Literal['insufficient', 'conversational', 'clarification_needed', 'out_of_scope']
    text: str
    citations: list[Citation]


AgentAnswer: TypeAlias = Union['AgentAnswerVariant1', 'AgentAnswerVariant2']


class PublicAnswerVariant1(TypedDict):
    mode: Literal['llm']
    status: Literal['answered', 'partially_answered']
    text: str
    citations: list[Citation]


class PublicAnswerVariant2(TypedDict):
    mode: Literal['llm']
    status: Literal['insufficient', 'conversational', 'clarification_needed', 'out_of_scope']
    text: str
    citations: list[Citation]


PublicAnswer: TypeAlias = Union['PublicAnswerVariant1', 'PublicAnswerVariant2']


class ExecutionRecord(TypedDict):
    workflow_id: Literal['museum_qa_v2']
    profile_id: Literal['local-ollama-v2']
    prompt_version: Literal['museum-prompts-v2']
    schema_version: Literal['agent-schema-v2']
    retrieval_strategy_version: str
    model_provider: Literal['ollama']
    model_name: str
    model_digest: str
    model_calls: int
    retrieval_calls: int
    refinement_rounds: int
    duration_ms: int


class ConversationUpdate(TypedDict):
    base_revision: int
    memory: MemoryUpdate


class RunResult(TypedDict):
    scope: Scope
    answer_language: Language
    language_decision: LanguageDecision
    answer: AgentAnswer
    requests: list[InterpretedRequest]
    request_results: list[RequestResult]
    evidence_snapshot: list[Evidence]
    reused_chunk_ids: list[str]
    executed_queries: list[SearchQuery]
    conversation_update: ConversationUpdate
    execution: ExecutionRecord
    catalog_snapshot: list[CatalogFact]


class RunSnapshotVariant1(TypedDict):
    contract_version: Literal['agent-run-v2']
    run_id: UUID
    state: Literal['processing']
    result: None
    error: None


class RunSnapshotVariant2(TypedDict):
    contract_version: Literal['agent-run-v2']
    run_id: UUID
    state: Literal['completed']
    result: RunResult
    error: None


class RunSnapshotVariant3(TypedDict):
    contract_version: Literal['agent-run-v2']
    run_id: UUID
    state: Literal['failed']
    result: None
    error: AgentError


RunSnapshot: TypeAlias = Union['RunSnapshotVariant1', 'RunSnapshotVariant2', 'RunSnapshotVariant3']


class PublicCreateConversationRequest(TypedDict):
    contract_version: Literal['agent-demo-v0.3']
    artwork_id: str


class PublicMessageRequest(TypedDict):
    contract_version: Literal['agent-demo-v0.3']
    client_message_id: UUID
    question: str
    language_override: Union[None, 'LanguagePreference']
    expected_revision: int


class PublicConversation(TypedDict):
    contract_version: Literal['agent-demo-v0.3']
    conversation_id: UUID
    artwork_id: str
    dataset_version: str
    created_at: str
    expires_at: str
    revision: int
    language_preference: LanguagePreference
    auto_language: Language
    last_answer_language: Union[None, 'Language']


class PublicTurn(TypedDict):
    turn_id: UUID
    sequence: int
    client_message_id: UUID
    question: str
    answer_language: Language
    dataset_version: str
    created_at: str
    answer: PublicAnswer
    language_override: Union[None, 'LanguagePreference']


class PublicError(TypedDict):
    code: Literal['invalid_request', 'payload_too_large', 'unsupported_media_type', 'origin_not_allowed', 'route_not_found', 'method_not_allowed', 'artwork_not_found', 'conversation_not_found', 'idempotency_conflict', 'request_in_progress', 'conversation_busy', 'request_superseded', 'conversation_state_conflict', 'agent_contract_mismatch', 'agent_unavailable', 'agent_invalid_response', 'service_not_ready', 'agent_context_limit', 'retrieval_invalid_response', 'retrieval_unavailable', 'retrieval_timeout', 'generation_invalid_response', 'generation_unavailable', 'generation_timeout', 'request_timeout', 'internal_error']
    message: str
    retryable: bool


class PendingRequestVariant1(TypedDict):
    contract_version: Literal['agent-demo-v0.3']
    client_message_id: UUID
    question: str
    language_override: Union[None, 'LanguagePreference']
    expected_revision: int
    state: Literal['processing']
    last_error: None


class PendingRequestVariant2(TypedDict):
    contract_version: Literal['agent-demo-v0.3']
    client_message_id: UUID
    question: str
    language_override: Union[None, 'LanguagePreference']
    expected_revision: int
    state: Literal['failed']
    last_error: PublicError


PendingRequest: TypeAlias = Union['PendingRequestVariant1', 'PendingRequestVariant2']


class PublicConversationResponse(TypedDict):
    conversation: PublicConversation
    created: bool


class PublicHistoryResponse(TypedDict):
    conversation: PublicConversation
    turns: list[PublicTurn]
    pending_request: Union[None, 'PendingRequest']


class PublicSubmissionResponse(TypedDict):
    conversation: PublicConversation
    turn: PublicTurn


class PublicErrorEnvelope(TypedDict):
    request_id: str
    error: PublicError


class HealthResponse(TypedDict):
    status: Literal['ok']


class ReadinessResponse(TypedDict):
    status: Literal['ok']
    contract_version: Literal['agent-run-v2']
    workflow_id: Literal['museum_qa_v2']
    profile_id: Literal['local-ollama-v2']
    dataset_version: str
    state_version: Literal['conversation-state-v1']
    supported_languages: Literal[['en', 'es', 'zh-CN', 'ko']]
    history_turn_limit: Literal[5]


class CatalogFact(TypedDict):
    kind: Literal['catalog']
    fact_id: str
    artwork_id: str
    dataset_version: str
    field: Literal['title', 'artist', 'date_display']
    value: str
    source_id: str
    source_title: str
    source_url: str
    source_locator: str
    attribution: str
    normalization: Literal['Released catalog metadata; not a verbatim museum excerpt.']


class CitationVariant1(TypedDict):
    chunk_id: str
    artwork_id: str
    source_id: str
    source_title: str
    source_url: str
    source_language: Literal['en']
    source_locator: str
    excerpt: str
    license: Literal['CC0-1.0', 'CC-BY-4.0']
    license_url: Literal['https://creativecommons.org/publicdomain/zero/1.0/', 'https://creativecommons.org/licenses/by/4.0/']
    attribution: str
    normalization: str


Citation: TypeAlias = Union['CitationVariant1', 'CatalogFact']


class TranslationSourceVariant1(TypedDict):
    kind: Literal['history_answer']
    sequence: int


class TranslationSourceVariant2(TypedDict):
    kind: Literal['history_excerpt']
    sequence: int
    excerpt: str


class TranslationSourceVariant3(TypedDict):
    kind: Literal['current_quote']
    excerpt: str


TranslationSource: TypeAlias = Union['TranslationSourceVariant1', 'TranslationSourceVariant2', 'TranslationSourceVariant3']


class TranslationTask(TypedDict):
    target_language: Language
    original_fragment: str
    source: Union[None, 'TranslationSource']


class ResolvedTranslation(TypedDict):
    target_language: Language
    source: TranslationSource
    text: str
