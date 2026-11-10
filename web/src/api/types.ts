// Wire types for contract revision local-demo-v0.1 (docs/api-contract.md).
// The validators check every required field and enum, and ignore unknown ones.

export type AnswerLanguage = 'en' | 'zh-CN';
export type AnswerStatus = 'answered' | 'insufficient' | 'unsupported_question';

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

export interface Conversation {
  conversation_id: string;
  artwork_id: string;
  dataset_version: string;
  created_at: string;
  expires_at: string;
}

export interface Evidence {
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

export interface Answer {
  status: AnswerStatus;
  mode: 'fixture';
  text: string;
  citations: Evidence[];
}

export interface Turn {
  turn_id: string;
  sequence: number;
  client_message_id: string;
  question: string;
  answer_language: AnswerLanguage;
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
  answer_language: AnswerLanguage;
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
  if (typeof v !== 'string' || v === '') throw new InvalidResponse(`${k} is not a nonempty string`);
  return v;
}
function oneOf<T extends string>(o: Obj, k: string, allowed: readonly T[]): T {
  const v = str(o, k);
  if (!(allowed as readonly string[]).includes(v)) throw new InvalidResponse(`${k} has unsupported value`);
  return v as T;
}
function arr(o: Obj, k: string): unknown[] {
  const v = o[k];
  if (!Array.isArray(v)) throw new InvalidResponse(`${k} is not an array`);
  return v;
}

const LANGS = ['en', 'zh-CN'] as const;

export function parseArtwork(v: unknown): Artwork {
  const o = obj(v, 'artwork');
  const id = o.museum_record_id;
  if (typeof id !== 'number' || !Number.isInteger(id) || id <= 0) throw new InvalidResponse('museum_record_id');
  const img = o.image_url;
  if (img !== null && (typeof img !== 'string' || img === '')) throw new InvalidResponse('image_url');
  return {
    artwork_id: str(o, 'artwork_id'),
    slug: str(o, 'slug'),
    title: str(o, 'title'),
    artist: str(o, 'artist'),
    date_display: str(o, 'date_display'),
    museum: str(o, 'museum'),
    museum_record_id: id,
    accession_number: str(o, 'accession_number'),
    source_url: str(o, 'source_url'),
    image_url: img,
    dataset_version: str(o, 'dataset_version'),
  };
}

export function parseConversation(v: unknown): Conversation {
  const o = obj(v, 'conversation');
  return {
    conversation_id: str(o, 'conversation_id').toLowerCase(),
    artwork_id: str(o, 'artwork_id'),
    dataset_version: str(o, 'dataset_version'),
    created_at: str(o, 'created_at'),
    expires_at: str(o, 'expires_at'),
  };
}

function parseEvidence(v: unknown): Evidence {
  const o = obj(v, 'citation');
  return {
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

export function parseTurn(v: unknown): Turn {
  const o = obj(v, 'turn');
  const a = obj(o.answer, 'answer');
  const seq = o.sequence;
  if (typeof seq !== 'number' || !Number.isInteger(seq) || seq < 1) throw new InvalidResponse('sequence');
  // An unknown status or mode must never be shown as a successful answer.
  const status = oneOf(a, 'status', ['answered', 'insufficient', 'unsupported_question'] as const);
  const mode = oneOf(a, 'mode', ['fixture'] as const);
  const citations = arr(a, 'citations').map(parseEvidence);
  if (status === 'answered' && citations.length === 0) throw new InvalidResponse('answered turn without citations');
  if (status !== 'answered' && citations.length > 0) throw new InvalidResponse('fallback turn with citations');
  return {
    turn_id: str(o, 'turn_id').toLowerCase(),
    sequence: seq,
    client_message_id: str(o, 'client_message_id').toLowerCase(),
    question: str(o, 'question'),
    answer_language: oneOf(o, 'answer_language', LANGS),
    dataset_version: str(o, 'dataset_version'),
    created_at: str(o, 'created_at'),
    answer: { status, mode, text: str(a, 'text'), citations },
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
  return {
    client_message_id: str(o, 'client_message_id').toLowerCase(),
    question: str(o, 'question'),
    answer_language: oneOf(o, 'answer_language', LANGS),
    state,
    last_error: o.last_error === null || o.last_error === undefined ? null : parseErrorBody(o.last_error),
  };
}

export function parseHistory(v: unknown): History {
  const o = obj(v, 'history');
  const turns = arr(o, 'turns').map(parseTurn).sort((a, b) => a.sequence - b.sequence);
  return { conversation: parseConversation(o.conversation), turns, pending_request: parsePending(o.pending_request) };
}
