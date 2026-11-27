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
  image_alt?: string;
  image_credit?: string;
  image_license?: string;
  image_source_url?: string;
  suggested_questions?: string[];
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
  source_kind?: 'museum_record' | 'guide_note';
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
function str(o: Obj, k: string, max = Infinity): string {
  const v = o[k];
  if (typeof v !== 'string' || v.trim() === '' || [...v].length > max
    || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F\uD800-\uDFFF]/u.test(v)) {
    throw new InvalidResponse(`${k} is not valid text`);
  }
  return v;
}
function uuid(o: Obj, k: string, canonical = false): string {
  const v = str(o, k);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)
    || (canonical && v !== v.toLowerCase())) throw new InvalidResponse(`${k} is not a UUID`);
  return v.toLowerCase();
}
function timestamp(o: Obj, k: string): string {
  const v = str(o, k);
  const ms = Date.parse(v);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(v)
    || !Number.isFinite(ms) || new Date(ms).toISOString().slice(0, 19) !== v.slice(0, 19)) {
    throw new InvalidResponse(`${k} is not a UTC timestamp`);
  }
  return v;
}
function httpsUrl(o: Obj, k: string): string {
  const v = str(o, k, 2048);
  try {
    const u = new URL(v);
    if (!v.startsWith('https://') || u.protocol !== 'https:' || !u.hostname || u.username || u.password) throw new Error();
  } catch { throw new InvalidResponse(`${k} is not an HTTPS URL`); }
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
  const slug = str(o, 'slug');
  if (!/^[a-z0-9-]+$/.test(slug)) throw new InvalidResponse('invalid artwork slug');
  if (img !== null && img !== `/artworks/${slug}.jpg`) httpsUrl(o, 'image_url');
  if (slug.startsWith('getty-')) {
    const source = new URL(httpsUrl(o, 'image_source_url'));
    const reviewed = (o.image_license === 'CC0-1.0' && source.host === 'media.getty.edu' && /^\/iiif\/image\/.+/.test(source.pathname))
      || (o.image_license === 'Public Domain Mark 1.0' && source.host === 'commons.wikimedia.org' && /^\/wiki\/File:[^/]+$/.test(source.pathname));
    if (!reviewed || source.href.includes('?') || source.hash) throw new InvalidResponse('image source does not match its rights statement');
  }
  return {
    artwork_id: str(o, 'artwork_id', 128),
    slug,
    title: str(o, 'title'),
    artist: str(o, 'artist'),
    date_display: str(o, 'date_display'),
    museum: str(o, 'museum'),
    museum_record_id: int(o, 'museum_record_id', 1),
    accession_number: str(o, 'accession_number'),
    source_url: httpsUrl(o, 'source_url'),
    image_url: img as string | null,
    ...(o.image_alt === undefined ? {} : { image_alt: str(o, 'image_alt') }),
    ...(o.image_credit === undefined ? {} : { image_credit: str(o, 'image_credit') }),
    ...(o.image_license === undefined ? {} : { image_license: str(o, 'image_license') }),
    ...(o.image_source_url === undefined ? {} : { image_source_url: httpsUrl(o, 'image_source_url') }),
    ...(o.suggested_questions === undefined ? {} : {
      suggested_questions: arr(o, 'suggested_questions').map((q) => str({ q }, 'q', 500)),
    }),
    dataset_version: str(o, 'dataset_version', 128),
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
    if (agent.revision === 0 && (agent.last_answer_language !== null || agent.language_preference !== 'auto' || agent.auto_language !== 'en')) {
      throw new InvalidResponse('invalid initial language state');
    }
  }
  return {
    conversation_id: uuid(o, 'conversation_id', agent !== null),
    artwork_id: str(o, 'artwork_id', 128),
    dataset_version: str(o, 'dataset_version', 128),
    created_at: timestamp(o, 'created_at'),
    expires_at: timestamp(o, 'expires_at'),
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
      fact_id: str(o, 'fact_id', 128),
      artwork_id: str(o, 'artwork_id', 128),
      dataset_version: str(o, 'dataset_version', 128),
      field: oneOf(o, 'field', ['title', 'artist', 'date_display'] as const),
      value: str(o, 'value', 500),
      source_id: str(o, 'source_id', 128),
      source_title: str(o, 'source_title', 500),
      source_url: httpsUrl(o, 'source_url'),
      source_locator: str(o, 'source_locator', 500),
      attribution: str(o, 'attribution', 500),
      normalization: str(o, 'normalization', 1000),
    };
  }
  return {
    kind: 'evidence',
    ...(o.source_kind === undefined ? {} : { source_kind: oneOf(o, 'source_kind', ['museum_record', 'guide_note'] as const) }),
    chunk_id: str(o, 'chunk_id', 128),
    artwork_id: str(o, 'artwork_id', 128),
    source_id: str(o, 'source_id', 128),
    source_title: str(o, 'source_title', 500),
    source_url: httpsUrl(o, 'source_url'),
    source_language: oneOf(o, 'source_language', ['en'] as const),
    source_locator: str(o, 'source_locator', 500),
    excerpt: str(o, 'excerpt', 4000),
    license: oneOf(o, 'license', ['CC0-1.0', 'CC-BY-4.0', 'In Copyright'] as const),
    license_url: httpsUrl(o, 'license_url'),
    attribution: str(o, 'attribution', 500),
    normalization: str(o, 'normalization', 1000),
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
  if (mode === 'llm' && citations.length > 11) throw new InvalidResponse('too many citations');
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
    turn_id: uuid(o, 'turn_id', answer.mode === 'llm'),
    sequence: int(o, 'sequence', 1),
    client_message_id: uuid(o, 'client_message_id', answer.mode === 'llm'),
    question: str(o, 'question', 500),
    // Fixture turns only ever use en / zh-CN; agent turns may use all four.
    answer_language: oneOf(o, 'answer_language', answer.mode === 'fixture' ? (['en', 'zh-CN'] as const) : LANGUAGES),
    language_override: answer.mode === 'llm' ? nullableOneOf(o, 'language_override', PREFERENCES) : null,
    dataset_version: str(o, 'dataset_version', 128),
    created_at: timestamp(o, 'created_at'),
    answer,
  };
}

export function parseErrorBody(v: unknown): ErrorBody {
  const o = obj(v, 'error');
  if (typeof o.retryable !== 'boolean') throw new InvalidResponse('retryable');
  return { code: str(o, 'code'), message: str(o, 'message', 500), retryable: o.retryable };
}

export type ConversationScope = Pick<Conversation, 'artwork_id' | 'dataset_version'>;

/** The browser checks scope relationships; B/C independently verify canonical source content. */
export function validateTurnScope(turn: Turn, conversation: ConversationScope): void {
  if (turn.dataset_version !== conversation.dataset_version) throw new InvalidResponse('turn uses another source release');
  for (const citation of turn.answer.citations) {
    if (citation.artwork_id !== conversation.artwork_id
      || (citation.kind === 'catalog' && citation.dataset_version !== conversation.dataset_version)) {
      throw new InvalidResponse('citation is outside the conversation scope');
    }
  }
}

function parsePending(v: unknown, conversation: Conversation): PendingRequest | null {
  if (v === null) return null;
  const o = obj(v, 'pending_request');
  const state = oneOf(o, 'state', ['processing', 'failed'] as const);
  const last_error = o.last_error === null ? null : parseErrorBody(o.last_error);
  if ((state === 'failed') !== (last_error !== null)) throw new InvalidResponse('pending error does not match its state');
  const agent = conversation.agent !== null;
  if (agent) oneOf(o, 'contract_version', [AGENT_CONTRACT] as const);
  else if (o.contract_version !== undefined) throw new InvalidResponse('pending request uses another contract');
  const pending = {
    client_message_id: uuid(o, 'client_message_id', agent),
    question: str(o, 'question', 500),
    // The fixture contract carries a plain answer_language; keep it as the explicit choice.
    language_override: agent ? nullableOneOf(o, 'language_override', PREFERENCES) : oneOf(o, 'answer_language', ['en', 'zh-CN'] as const),
    expected_revision: agent ? int(o, 'expected_revision', 0) : 0,
    state,
    last_error,
  };
  if (conversation.agent && pending.expected_revision !== conversation.agent.revision) throw new InvalidResponse('pending request uses a different revision');
  return pending;
}

export function parseHistory(v: unknown): History {
  const o = obj(v, 'history');
  const conversation = parseConversation(o.conversation);
  const turns = arr(o, 'turns').map(parseTurn);
  const expectedMode = conversation.agent ? 'llm' : 'fixture';
  const turnIds = new Set<string>();
  const messageIds = new Set<string>();
  for (const [index, turn] of turns.entries()) {
    if (turn.answer.mode !== expectedMode) throw new InvalidResponse('turn mode does not match the conversation contract');
    if (turn.sequence !== index + 1) throw new InvalidResponse('history is not complete and ordered');
    if (turnIds.has(turn.turn_id) || messageIds.has(turn.client_message_id)) throw new InvalidResponse('duplicate history identity');
    turnIds.add(turn.turn_id);
    messageIds.add(turn.client_message_id);
    validateTurnScope(turn, conversation);
  }
  if (conversation.agent) {
    if (conversation.agent.revision !== turns.length) throw new InvalidResponse('revision does not match the latest turn');
    const last = turns[turns.length - 1];
    if (last && last.answer_language !== conversation.agent.last_answer_language) throw new InvalidResponse('last answer language does not match history');
  }
  const pending_request = parsePending(o.pending_request, conversation);
  if (pending_request && messageIds.has(pending_request.client_message_id)) throw new InvalidResponse('completed message is still pending');
  return { conversation, turns, pending_request };
}
