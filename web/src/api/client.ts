import { mockFetch } from './mock';
import {
  AGENT_CONTRACT, type Artwork, type Conversation, type History, type LanguagePreference, type Turn,
  InvalidResponse, parseArtwork, parseConversation, parseErrorBody, parseHistory, parseTurn,
} from './types';

export const IS_MOCK = import.meta.env.VITE_API_MODE === 'mock';

/**
 * Which public contract this build speaks. The mock always speaks the agent
 * contract. A live build speaks the fixture contract unless it is built with
 * PUBLIC_CONTRACT=agent, so the current Go API keeps working until its
 * agent-demo-v0.3 endpoints exist.
 */
export const CONTRACT: 'fixture' | 'agent' = IS_MOCK || import.meta.env.VITE_PUBLIC_CONTRACT === 'agent' ? 'agent' : 'fixture';

/**
 * How long the browser actively waits for one question, including recovery
 * polling. Fixture: 12 s (api-contract 7.2). Agent: 70 s (agent-api-contract 7.4).
 */
export const BROWSER_WAIT_MS = CONTRACT === 'agent' ? 70_000 : 12_000;
/** One history read. Agent contract 7.4 caps it at 10 s. */
export const HISTORY_TIMEOUT_MS = CONTRACT === 'agent' ? 10_000 : 12_000;

/** A structured non-2xx reply from the API. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public retryable: boolean,
    public requestId: string | null,
    message: string,
  ) {
    super(message);
  }
}

/** No usable reply: the request timed out, the network failed, or the body broke the contract. */
export class TransportError extends Error {
  constructor(public kind: 'timeout' | 'network' | 'invalid_response', message: string) {
    super(message);
  }
}

interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function request(method: 'GET' | 'POST', path: string, body?: unknown, options: RequestOptions = {}): Promise<unknown> {
  const ctrl = new AbortController();
  const cancel = () => ctrl.abort();
  const { signal, timeoutMs = HISTORY_TIMEOUT_MS } = options;
  if (signal?.aborted) cancel();
  else signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, timeoutMs);
  let res: Response;
  let json: unknown;
  try {
    const init: RequestInit = { method, signal: ctrl.signal, credentials: 'same-origin' };
    if (body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    res = await (IS_MOCK ? mockFetch(path, init) : fetch(path, init));
    const text = await res.text();
    try {
      json = JSON.parse(text);
    } catch {
      // e.g. the dev proxy answering in plain text while the Go API is down
      throw new TransportError(res.ok ? 'invalid_response' : 'network', `non-JSON reply (HTTP ${res.status})`);
    }
  } catch (e) {
    if (e instanceof TransportError) throw e;
    throw new TransportError(ctrl.signal.aborted ? 'timeout' : 'network', String(e));
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
  if (!res.ok) {
    try {
      const o = json as { request_id?: unknown; error?: unknown };
      const err = parseErrorBody(o.error);
      throw new ApiError(res.status, err.code, err.retryable, typeof o.request_id === 'string' ? o.request_id : null, err.message);
    } catch (e) {
      if (e instanceof ApiError) throw e;
      throw new TransportError('invalid_response', `malformed error envelope (HTTP ${res.status})`);
    }
  }
  return json;
}

function parsed<T>(fn: () => T): T {
  try {
    return fn();
  } catch (e) {
    if (e instanceof InvalidResponse) throw new TransportError('invalid_response', e.message);
    throw e;
  }
}

export async function getArtwork(slug: string): Promise<Artwork> {
  const j = (await request('GET', `/api/artworks/${encodeURIComponent(slug)}`)) as { artwork?: unknown };
  return parsed(() => parseArtwork(j.artwork));
}

// Serialize creation across artworks so the next request sees the visitor cookie.
const creating = new Map<string, Promise<Conversation>>();
let creationTail = Promise.resolve();

export function openConversation(artworkId: string): Promise<Conversation> {
  const existing = creating.get(artworkId);
  if (existing) return existing;
  const p = creationTail.then(async () => {
    const body = CONTRACT === 'agent' ? { contract_version: AGENT_CONTRACT, artwork_id: artworkId } : { artwork_id: artworkId };
    const j = (await request('POST', '/api/conversations', body)) as { conversation?: unknown };
    const c = parsed(() => parseConversation(j.conversation));
    if (c.artwork_id !== artworkId) throw new TransportError('invalid_response', 'conversation is bound to another artwork');
    checkContract(c);
    return c;
  }).finally(() => creating.delete(artworkId));
  creating.set(artworkId, p);
  creationTail = p.then(() => undefined, () => undefined);
  return p;
}

/** A fixture conversation must never be read as an agent one, or the reverse (contract 2.1). */
function checkContract(c: Conversation): void {
  if ((c.agent !== null) !== (CONTRACT === 'agent')) throw new TransportError('invalid_response', 'conversation uses another contract');
}

export async function getHistory(conversationId: string, options?: RequestOptions): Promise<History> {
  const j = await request('GET', `/api/conversations/${conversationId}/messages`, undefined, options);
  const h = parsed(() => parseHistory(j));
  if (h.conversation.conversation_id !== conversationId) throw new TransportError('invalid_response', 'history is for another conversation');
  checkContract(h.conversation);
  return h;
}

/**
 * One logical question. A retry must resend exactly these values, because the
 * server treats (question, language_override, expected_revision) as the payload
 * identity for a client_message_id (agent contract 2.3).
 */
export interface Submission {
  client_message_id: string;
  question: string;
  /**
   * Agent: null when the visitor has not changed the language selector, otherwise their selection.
   * Fixture: always the selected answer language (en or zh-CN).
   */
  language_override: LanguagePreference | null;
  /** Agent: the conversation revision the visitor was looking at. Fixture: unused, 0. */
  expected_revision: number;
}

export interface SubmissionResult {
  turn: Turn;
  /** Agent: the server's current conversation projection. Fixture: null. */
  conversation: Conversation | null;
}

export async function postMessage(conversationId: string, s: Submission): Promise<SubmissionResult> {
  const body = CONTRACT === 'agent'
    ? {
      contract_version: AGENT_CONTRACT,
      client_message_id: s.client_message_id,
      question: s.question,
      language_override: s.language_override,
      expected_revision: s.expected_revision,
    }
    : { client_message_id: s.client_message_id, question: s.question, answer_language: s.language_override ?? 'en' };
  const j = (await request('POST', `/api/conversations/${conversationId}/messages`, body, { timeoutMs: BROWSER_WAIT_MS })) as {
    turn?: unknown; conversation?: unknown;
  };
  const turn = parsed(() => parseTurn(j.turn));
  if (turn.client_message_id !== s.client_message_id) throw new TransportError('invalid_response', 'turn is for another message');
  if ((turn.answer.mode === 'llm') !== (CONTRACT === 'agent')) throw new TransportError('invalid_response', 'turn uses another contract');
  if (CONTRACT !== 'agent') return { turn, conversation: null };
  const conversation = parsed(() => parseConversation(j.conversation));
  if (conversation.conversation_id !== conversationId) throw new TransportError('invalid_response', 'reply is for another conversation');
  checkContract(conversation);
  return { turn, conversation };
}
