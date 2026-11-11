import { mockFetch } from './mock';
import {
  type AnswerLanguage, type Artwork, type Conversation, type History, type Turn,
  InvalidResponse, parseArtwork, parseConversation, parseErrorBody, parseHistory, parseTurn,
} from './types';

export const IS_MOCK = import.meta.env.VITE_API_MODE === 'mock';
/** Contract 7.2: after 12 seconds the outcome is unknown and is reconciled with history. */
export const BROWSER_TIMEOUT_MS = 12_000;

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
  const { signal, timeoutMs = BROWSER_TIMEOUT_MS } = options;
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
    const j = (await request('POST', '/api/conversations', { artwork_id: artworkId })) as { conversation?: unknown };
    const c = parsed(() => parseConversation(j.conversation));
    if (c.artwork_id !== artworkId) throw new TransportError('invalid_response', 'conversation is bound to another artwork');
    return c;
  }).finally(() => creating.delete(artworkId));
  creating.set(artworkId, p);
  creationTail = p.then(() => undefined, () => undefined);
  return p;
}

export async function getHistory(conversationId: string, options?: RequestOptions): Promise<History> {
  const j = await request('GET', `/api/conversations/${conversationId}/messages`, undefined, options);
  const h = parsed(() => parseHistory(j));
  if (h.conversation.conversation_id !== conversationId) throw new TransportError('invalid_response', 'history is for another conversation');
  return h;
}

export interface Submission {
  client_message_id: string;
  question: string;
  answer_language: AnswerLanguage;
}

export async function postMessage(conversationId: string, s: Submission): Promise<Turn> {
  const j = (await request('POST', `/api/conversations/${conversationId}/messages`, {
    client_message_id: s.client_message_id,
    question: s.question,
    answer_language: s.answer_language,
  })) as { turn?: unknown };
  const t = parsed(() => parseTurn(j.turn));
  if (t.client_message_id !== s.client_message_id) throw new TransportError('invalid_response', 'turn is for another message');
  return t;
}
