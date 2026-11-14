// Wire types for the two public contracts the browser can speak:
//   fixture: local-demo-v0.1  (docs/api-contract.md)        mode "fixture", three statuses
//   agent:   agent-demo-v0.3  (docs/agent-api-contract.md)  mode "llm", six statuses
// The validators check every required field and enum, and ignore unknown ones.

export const AGENT_CONTRACT = 'agent-demo-v0.3';

export type Language = 'en' | 'es' | 'zh-CN' | 'ko';
export type LanguagePreference = 'auto' | Language;
/** Kept for the fixture contract, which only knows these two. */
export type AnswerLanguage = Language;

export const LANGUAGES: readonly Language[] = ['en', 'es', 'zh-CN', 'ko'];
export const PREFERENCES: readonly LanguagePreference[] = ['auto', ...LANGUAGES];

export type FixtureStatus = 'answered' | 'insufficient' | 'unsupported_question';
export type AgentStatus =
  | 'answered' | 'partially_answered' | 'clarification_needed' | 'insufficient' | 'out_of_scope' | 'conversational';
export type AnswerStatus = FixtureStatus | AgentStatus;

export interface Artwork {
  artwork_id: string;
  slug: string;
  title: string;
  artist: string;
  date_display: string;
  museum: string;
  museum_record_id: number;
  accession_number: string;
  source_url: string;
  image_url: string | null;
  dataset_version: string;
}

/** Agent-contract metadata on a conversation (contract 2.2). Null for a fixture conversation. */
export interface ConversationMeta {
  contract_version: typeof AGENT_CONTRACT;
  revision: number;
  language_preference: LanguagePreference;
  auto_language: Language;
  last_answer_language: Language | null;
}

export interface Conversation {
  conversation_id: string;
  artwork_id: string;
  dataset_version: string;
  created_at: string;
  expires_at: string;
  agent: ConversationMeta | null;
}

/** A museum passage returned by retrieval. */
export interface Evidence {
  kind: 'evidence';
  chunk_id: string;
  artwork_id: string;
  source_id: string;
  source_title: string;
  source_url: string;
  source_language: string;
  source_locator: string;
  excerpt: string;
  license: string;
  license_url: string;
  attribution: string;
  normalization: string;
}

/** A released catalog value (title, artist or date). It is metadata, not a museum quotation. */
export interface CatalogFact {
  kind: 'catalog';
  fact_id: string;
  artwork_id: string;
  dataset_version: string;
  field: 'title' | 'artist' | 'date_display';
  value: string;
  source_id: string;
  source_title: string;
  source_url: string;
  source_locator: string;
  attribution: string;
  normalization: string;
}

export type Citation = Evidence | CatalogFact;
export const citationKey = (c: Citation): string => (c.kind === 'catalog' ? `catalog:${c.fact_id}` : `evidence:${c.chunk_id}`);

export interface Answer {
  status: AnswerStatus;
  mode: 'fixture' | 'llm';
  text: string;
  citations: Citation[];
}

export interface Turn {
  turn_id: string;
  sequence: number;
  client_message_id: string;
  question: string;
  /** The language the answer was actually written in. */
  answer_language: Language;
  /** Agent contract: what the visitor selected for this message. Null when nothing was selected. */
  language_override: LanguagePreference | null;
  dataset_version: string;
  created_at: string;
  answer: Answer;
}

export interface ErrorBody {
  code: string;
  message: string;
  retryable: boolean;
}

export interface PendingRequest {
  client_message_id: string;
  question: string;
  language_override: LanguagePreference | null;
  expected_revision: number;
  state: 'processing' | 'failed';
  last_error: ErrorBody | null;
}

export interface History {
  conversation: Conversation;
  turns: Turn[];
  pending_request: PendingRequest | null;
}

export class InvalidResponse extends Error {}

type Obj = Record<string, unknown>;

function obj(v: unknown, what: string): Obj {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new InvalidResponse(`${what} is not an object`);
  return v as Obj;
}
function str(o: Obj, k: string): string {
  const v = o[k];
  if (typeof v !== 'string' || v.trim() === '') throw new InvalidResponse(`${k} is not a nonempty string`);
  return v;
}
function oneOf<T extends string>(o: Obj, k: string, allowed: readonly T[]): T {
  const v = str(o, k);
  if (!(allowed as readonly string[]).includes(v)) throw new InvalidResponse(`${k} has unsupported value`);
  return v as T;
}
function nullableOneOf<T extends string>(o: Obj, k: string, allowed: readonly T[]): T | null {
  if (o[k] === null) return null;
  return oneOf(o, k, allowed);
}
function int(o: Obj, k: string, min: number): number {
  const v = o[k];
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > 2147483647) throw new InvalidResponse(`${k} is not a valid integer`);
  return v;
}
function arr(o: Obj, k: string): unknown[] {
  const v = o[k];
  if (!Array.isArray(v)) throw new InvalidResponse(`${k} is not an array`);
  return v;
}

export function parseArtwork(v: unknown): Artwork {
  const o = obj(v, 'artwork');
  const img = o.image_url;
  if (img !== null && (typeof img !== 'string' || img === '')) throw new InvalidResponse('image_url');
  return {
    artwork_id: str(o, 'artwork_id'),
    slug: str(o, 'slug'),
    title: str(o, 'title'),
    artist: str(o, 'artist'),
    date_display: str(o, 'date_display'),
    museum: str(o, 'museum'),
    museum_record_id: int(o, 'museum_record_id', 1),
    accession_number: str(o, 'accession_number'),
    source_url: str(o, 'source_url'),
    image_url: img,
    dataset_version: str(o, 'dataset_version'),
  };
}

export function parseConversation(v: unknown): Conversation {
  const o = obj(v, 'conversation');
  let agent: ConversationMeta | null = null;
  if (o.contract_version !== undefined) {
    // An unknown contract version must not be read as if it were this one.
    oneOf(o, 'contract_version', [AGENT_CONTRACT] as const);
    agent = {
      contract_version: AGENT_CONTRACT,
      revision: int(o, 'revision', 0),
      language_preference: oneOf(o, 'language_preference', PREFERENCES),
      auto_language: oneOf(o, 'auto_language', LANGUAGES),
      last_answer_language: nullableOneOf(o, 'last_answer_language', LANGUAGES),
    };
    if (agent.revision > 0 && agent.last_answer_language === null) throw new InvalidResponse('last_answer_language missing after a turn');
  }
  return {
    conversation_id: str(o, 'conversation_id').toLowerCase(),
    artwork_id: str(o, 'artwork_id'),
    dataset_version: str(o, 'dataset_version'),
    created_at: str(o, 'created_at'),
    expires_at: str(o, 'expires_at'),
    agent,
  };
}

function parseCitation(v: unknown): Citation {
  const o = obj(v, 'citation');
  if (o.kind !== undefined) {
    // "kind" marks a catalog fact; a retrieval passage never carries it (contract 5.5).
    oneOf(o, 'kind', ['catalog'] as const);
    return {
      kind: 'catalog',
      fact_id: str(o, 'fact_id'),
      artwork_id: str(o, 'artwork_id'),
      dataset_version: str(o, 'dataset_version'),
      field: oneOf(o, 'field', ['title', 'artist', 'date_display'] as const),
      value: str(o, 'value'),
      source_id: str(o, 'source_id'),
      source_title: str(o, 'source_title'),
      source_url: str(o, 'source_url'),
      source_locator: str(o, 'source_locator'),
      attribution: str(o, 'attribution'),
      normalization: str(o, 'normalization'),
    };
  }
  return {
    kind: 'evidence',
    chunk_id: str(o, 'chunk_id'),
    artwork_id: str(o, 'artwork_id'),
    source_id: str(o, 'source_id'),
    source_title: str(o, 'source_title'),
    source_url: str(o, 'source_url'),
    source_language: str(o, 'source_language'),
    source_locator: str(o, 'source_locator'),
    excerpt: str(o, 'excerpt'),
    license: str(o, 'license'),
    license_url: str(o, 'license_url'),
    attribution: str(o, 'attribution'),
    normalization: str(o, 'normalization'),
  };
}

const FIXTURE_STATUSES = ['answered', 'insufficient', 'unsupported_question'] as const;
const AGENT_STATUSES = ['answered', 'partially_answered', 'clarification_needed', 'insufficient', 'out_of_scope', 'conversational'] as const;
/** Statuses that must come with at least one source; every other status must come with none. */
const CITED: readonly AnswerStatus[] = ['answered', 'partially_answered'];

export function parseAnswer(v: unknown): Answer {
  const a = obj(v, 'answer');
  // An unknown mode or status must never be shown as a successful answer.
  const mode = oneOf(a, 'mode', ['fixture', 'llm'] as const);
  const status: AnswerStatus = mode === 'fixture' ? oneOf(a, 'status', FIXTURE_STATUSES) : oneOf(a, 'status', AGENT_STATUSES);
  const citations = arr(a, 'citations').map(parseCitation);
  const text = str(a, 'text');
  if ([...text].length > 2000) throw new InvalidResponse('answer text is too long');
  if (CITED.includes(status) && citations.length === 0) throw new InvalidResponse(`${status} turn without citations`);
  if (!CITED.includes(status) && citations.length > 0) throw new InvalidResponse(`${status} turn with citations`);
  if (mode === 'fixture' && citations.some((c) => c.kind === 'catalog')) throw new InvalidResponse('fixture turn with a catalog citation');
  if (new Set(citations.map(citationKey)).size !== citations.length) throw new InvalidResponse('duplicate citation');
  return { status, mode, text, citations };
}

export function parseTurn(v: unknown): Turn {
  const o = obj(v, 'turn');
  const answer = parseAnswer(o.answer);
  return {
    turn_id: str(o, 'turn_id').toLowerCase(),
    sequence: int(o, 'sequence', 1),
    client_message_id: str(o, 'client_message_id').toLowerCase(),
    question: str(o, 'question'),
    // Fixture turns only ever use en / zh-CN; agent turns may use all four.
    answer_language: oneOf(o, 'answer_language', answer.mode === 'fixture' ? (['en', 'zh-CN'] as const) : LANGUAGES),
    language_override: answer.mode === 'llm' ? nullableOneOf(o, 'language_override', PREFERENCES) : null,
    dataset_version: str(o, 'dataset_version'),
    created_at: str(o, 'created_at'),
    answer,
  };
}

export function parseErrorBody(v: unknown): ErrorBody {
  const o = obj(v, 'error');
  if (typeof o.retryable !== 'boolean') throw new InvalidResponse('retryable');
  return { code: str(o, 'code'), message: typeof o.message === 'string' ? o.message : '', retryable: o.retryable };
}

function parsePending(v: unknown): PendingRequest | null {
  if (v === null) return null;
  const o = obj(v, 'pending_request');
  const state = oneOf(o, 'state', ['processing', 'failed'] as const);
  const last_error = o.last_error === null || o.last_error === undefined ? null : parseErrorBody(o.last_error);
  if (state === 'failed' && last_error === null) throw new InvalidResponse('failed pending request without an error');
  const agent = o.contract_version !== undefined;
  if (agent) oneOf(o, 'contract_version', [AGENT_CONTRACT] as const);
  return {
    client_message_id: str(o, 'client_message_id').toLowerCase(),
    question: str(o, 'question'),
    // The fixture contract carries a plain answer_language; keep it as the explicit choice.
    language_override: agent ? nullableOneOf(o, 'language_override', PREFERENCES) : oneOf(o, 'answer_language', ['en', 'zh-CN'] as const),
    expected_revision: agent ? int(o, 'expected_revision', 0) : 0,
    state,
    last_error,
  };
}

export function parseHistory(v: unknown): History {
  const o = obj(v, 'history');
  const conversation = parseConversation(o.conversation);
  const turns = arr(o, 'turns').map(parseTurn).sort((a, b) => a.sequence - b.sequence);
  const expectedMode = conversation.agent ? 'llm' : 'fixture';
  if (turns.some((t) => t.answer.mode !== expectedMode)) throw new InvalidResponse('turn mode does not match the conversation contract');
  // The revision is the latest committed turn sequence (contract 2.2).
  const lastSequence = turns.length ? turns[turns.length - 1].sequence : 0;
  if (conversation.agent && conversation.agent.revision !== lastSequence) throw new InvalidResponse('revision does not match the latest turn');
  return { conversation, turns, pending_request: parsePending(o.pending_request) };
}
