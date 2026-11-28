import { InvalidResponse, parseErrorBody, wire, type CatalogFact, type Evidence, type ErrorBody } from '../api/types';
import type { VoiceMessage } from './controller';

export const VINCENT_CONTRACT = 'agent-demo-v0.4';
export const VINCENT_ARTWORK = 'getty-103jnh';
export const VINCENT_DATASET = 'getty-gallery-v2';
export const VINCENT_PACK = 'vincent-irises-v1';
export type VincentEvidence = Omit<Evidence, 'source_kind'> & {
  source_kind: 'artist_letter' | 'museum_record' | 'guide_note';
};
export type VincentCitation = CatalogFact | VincentEvidence;
const { obj, str, uuid, timestamp, httpsUrl, oneOf, nullableOneOf, int, arr } = wire;

export function parseVincentConversation(value: unknown, id?: string) {
  const o = obj(value, 'conversation');
  const conversation = {
    contract_version: oneOf(o, 'contract_version', [VINCENT_CONTRACT]),
    conversation_id: uuid(o, 'conversation_id', true),
    artwork_id: oneOf(o, 'artwork_id', [VINCENT_ARTWORK]),
    dataset_version: oneOf(o, 'dataset_version', [VINCENT_DATASET]),
    conversation_mode: oneOf(o, 'conversation_mode', ['vincent'] as const),
    reference_pack_version: oneOf(o, 'reference_pack_version', [VINCENT_PACK]),
    revision: int(o, 'revision', 0),
    language_preference: oneOf(o, 'language_preference', ['en'] as const),
    auto_language: oneOf(o, 'auto_language', ['en'] as const),
    last_answer_language: nullableOneOf(o, 'last_answer_language', ['en'] as const),
    created_at: timestamp(o, 'created_at'), expires_at: timestamp(o, 'expires_at'),
  };
  if ((id !== undefined && id !== conversation.conversation_id) ||
      (conversation.revision === 0) !== (conversation.last_answer_language === null)) {
    throw new InvalidResponse('conversation identity or language state mismatch');
  }
  return conversation;
}
export type VincentConversation = ReturnType<typeof parseVincentConversation>;

function parseCitation(value: unknown): VincentCitation {
  const o = obj(value, 'citation');
  const common = {
    artwork_id: oneOf(o, 'artwork_id', [VINCENT_ARTWORK]), source_id: str(o, 'source_id', 128),
    source_title: str(o, 'source_title', 500), source_url: httpsUrl(o, 'source_url'),
    source_locator: str(o, 'source_locator', 500), attribution: str(o, 'attribution', 500),
    normalization: str(o, 'normalization', 1000),
  };
  if (o.kind !== undefined) return {
    ...common, kind: oneOf(o, 'kind', ['catalog'] as const), fact_id: str(o, 'fact_id', 128),
    dataset_version: oneOf(o, 'dataset_version', [VINCENT_DATASET]),
    field: oneOf(o, 'field', ['title', 'artist', 'date_display'] as const), value: str(o, 'value', 500),
  };
  return {
    ...common, kind: 'evidence', chunk_id: str(o, 'chunk_id', 128),
    source_kind: oneOf(o, 'source_kind', ['artist_letter', 'museum_record', 'guide_note'] as const),
    source_language: oneOf(o, 'source_language', ['en']), excerpt: str(o, 'excerpt', 4000),
    license: oneOf(o, 'license', ['CC0-1.0', 'CC-BY-4.0', 'CC-BY-NC-SA-4.0', 'In Copyright']),
    license_url: httpsUrl(o, 'license_url'),
  };
}

function parseTurn(value: unknown) {
  const o = obj(value, 'turn');
  const a = obj(o.answer, 'answer');
  const answer = {
    mode: oneOf(a, 'mode', ['llm'] as const),
    status: oneOf(a, 'status', ['answered', 'partially_answered', 'clarification_needed', 'insufficient', 'out_of_scope', 'conversational'] as const),
    text: str(a, 'text', 2000), citations: arr(a, 'citations').map(parseCitation),
  };
  const keys = answer.citations.map((c) => c.kind === 'catalog' ? `catalog:${c.fact_id}` : `evidence:${c.chunk_id}`);
  if (keys.length > 11 || new Set(keys).size !== keys.length ||
      ['answered', 'partially_answered'].includes(answer.status) !== (keys.length > 0)) {
    throw new InvalidResponse('invalid citation count or status');
  }
  return {
    turn_id: uuid(o, 'turn_id', true), client_message_id: uuid(o, 'client_message_id', true), sequence: int(o, 'sequence', 1),
    question: str(o, 'question', 500), language_override: nullableOneOf(o, 'language_override', ['en'] as const),
    answer_language: oneOf(o, 'answer_language', ['en'] as const), dataset_version: oneOf(o, 'dataset_version', [VINCENT_DATASET]),
    created_at: timestamp(o, 'created_at'), answer,
  };
}
export type VincentTurn = ReturnType<typeof parseTurn>;
export interface VincentPending {
  client_message_id: string; question: string; language_override: 'en' | null; expected_revision: number;
  state: 'processing' | 'failed'; last_error: ErrorBody | null;
}
export interface VincentHistory {
  conversation: VincentConversation; turns: VincentTurn[]; pending_request: VincentPending | null;
}

export function parseVincentHistory(value: unknown, id: string): VincentHistory {
  const o = obj(value, 'history');
  const conversation = parseVincentConversation(o.conversation, id);
  const turns = arr(o, 'turns').map(parseTurn);
  if (conversation.revision !== turns.length || turns.some((t, index) => t.sequence !== index + 1) ||
      new Set(turns.map((t) => t.turn_id)).size !== turns.length || new Set(turns.map((t) => t.client_message_id)).size !== turns.length) {
    throw new InvalidResponse('history is not complete and ordered');
  }
  let pending_request: VincentPending | null = null;
  if (o.pending_request !== null) {
    const p = obj(o.pending_request, 'pending_request');
    oneOf(p, 'contract_version', [VINCENT_CONTRACT]);
    pending_request = {
      client_message_id: uuid(p, 'client_message_id', true), question: str(p, 'question', 500),
      language_override: nullableOneOf(p, 'language_override', ['en'] as const), expected_revision: int(p, 'expected_revision', 0),
      state: oneOf(p, 'state', ['processing', 'failed'] as const), last_error: p.last_error === null ? null : parseErrorBody(p.last_error),
    };
    if ((pending_request.state === 'failed') !== (pending_request.last_error !== null) ||
        pending_request.expected_revision !== conversation.revision || turns.some((t) => t.client_message_id === pending_request?.client_message_id)) {
      throw new InvalidResponse('pending request does not match history');
    }
  }
  return { conversation, turns, pending_request };
}

export function validateVincentMessage(turn: VincentTurn, message: VoiceMessage): void {
  if (turn.client_message_id !== message.clientMessageId || turn.question !== message.question ||
      turn.language_override !== message.languageOverride || turn.sequence !== message.expectedRevision + 1) {
    throw new InvalidResponse('reply does not match the original question');
  }
}

export function parseVincentResult(value: unknown, id: string, message: VoiceMessage) {
  const o = obj(value, 'result');
  const conversation = parseVincentConversation(o.conversation, id);
  const turn = parseTurn(o.turn);
  validateVincentMessage(turn, message);
  if (turn.sequence > conversation.revision) throw new InvalidResponse('turn is newer than conversation');
  return { conversation, turn };
}
