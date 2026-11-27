// Minimal agent-demo-v0.4 client for Talk with Vincent (docs/vincent-contract.md).
// It creates or restores the Vincent conversation, reads history, and submits
// one logical message with lost-response recovery by client_message_id.
// B's page can use it directly or fold it into the main chat client.
import type { VoiceMessage } from './controller';

export const VINCENT_CONTRACT = 'agent-demo-v0.4';
export const VINCENT_ARTWORK = 'getty-103jnh';
const BROWSER_WAIT_MS = 70_000;
const HISTORY_TIMEOUT_MS = 10_000;
const POLL_MS = 1_000;

export interface VincentCitation {
  kind?: 'catalog';
  chunk_id?: string;
  fact_id?: string;
  source_kind?: string;
  source_title: string;
  source_url: string;
  source_locator: string;
  excerpt?: string;
  value?: string;
  attribution: string;
  license?: string;
  license_url?: string;
}

export interface VincentTurn {
  turn_id: string;
  sequence: number;
  client_message_id: string;
  question: string;
  answer: { mode: 'llm'; status: string; text: string; citations: VincentCitation[] };
}

export interface VincentConversation {
  contract_version: typeof VINCENT_CONTRACT;
  conversation_id: string;
  artwork_id: string;
  dataset_version: string;
  conversation_mode: 'guide' | 'vincent';
  reference_pack_version: string | null;
  revision: number;
  language_preference: string;
}

export interface VincentHistory {
  conversation: VincentConversation;
  turns: VincentTurn[];
  pending_request: null | { client_message_id: string; state: 'processing' | 'failed'; last_error?: PublicError };
}

export interface PublicError { code: string; message: string; retryable: boolean }

/** A public API error; code null means no usable reply (network, timeout). */
export class VincentError extends Error {
  constructor(message: string, readonly code: string | null, readonly retryable: boolean, readonly status = 0) {
    super(message);
  }
}

type Fetch = typeof fetch;

async function call<T>(fetcher: Fetch, method: 'GET' | 'POST', path: string, body: unknown, timeoutMs: number): Promise<T> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  let response: Response;
  let json: unknown;
  try {
    response = await fetcher(path, {
      method, credentials: 'same-origin', redirect: 'error', signal: abort.signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    json = await response.json();
  } catch {
    throw new VincentError(abort.signal.aborted ? 'Vincent took too long to answer.' : 'The connection dropped.', null, true);
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    const error = (json as { error?: Partial<PublicError> })?.error;
    throw new VincentError(typeof error?.message === 'string' ? error.message : `HTTP ${response.status}`,
      typeof error?.code === 'string' ? error.code : null, error?.retryable === true, response.status);
  }
  return json as T;
}

function checkScope(conversation: VincentConversation, id?: string): VincentConversation {
  if (conversation?.contract_version !== VINCENT_CONTRACT || conversation.conversation_mode !== 'vincent' ||
      conversation.artwork_id !== VINCENT_ARTWORK || (id !== undefined && conversation.conversation_id !== id)) {
    throw new VincentError('The server returned a different conversation.', null, false);
  }
  return conversation;
}

export function createVincentClient(fetcher: Fetch = (...args) => fetch(...args), sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))) {
  const history = async (id: string): Promise<VincentHistory> => {
    const value = await call<VincentHistory>(fetcher, 'GET', `/api/conversations/${id}/messages`, undefined, HISTORY_TIMEOUT_MS);
    checkScope(value.conversation, id);
    return value;
  };

  return {
    /** Creates or restores this visitor's Vincent conversation for Irises. */
    async open(): Promise<VincentConversation> {
      const value = await call<{ conversation: VincentConversation }>(fetcher, 'POST', '/api/conversations',
        { contract_version: VINCENT_CONTRACT, artwork_id: VINCENT_ARTWORK, conversation_mode: 'vincent' }, HISTORY_TIMEOUT_MS);
      return checkScope(value.conversation);
    },

    history,

    /**
     * Submits one logical message and resolves with its committed turn. After a
     * lost response it reads history first: a committed turn with the same
     * client_message_id is returned, a processing one is awaited, and only then
     * is the identical message sent again. Never creates a second logical turn.
     */
    async submit(id: string, message: VoiceMessage): Promise<{ conversation: VincentConversation; turn: VincentTurn }> {
      const body = {
        contract_version: VINCENT_CONTRACT, client_message_id: message.clientMessageId, question: message.question,
        language_override: message.languageOverride, expected_revision: message.expectedRevision,
      };
      const deadline = Date.now() + BROWSER_WAIT_MS;
      let lastError: VincentError | null = null;
      while (Date.now() < deadline) {
        try {
          const value = await call<{ conversation: VincentConversation; turn: VincentTurn }>(fetcher, 'POST',
            `/api/conversations/${id}/messages`, body, Math.max(1_000, deadline - Date.now()));
          checkScope(value.conversation, id);
          return value;
        } catch (error) {
          const failure = error as VincentError;
          const uncertain = failure.code === null || failure.code === 'request_in_progress' || failure.code === 'agent_unavailable';
          if (!uncertain || !failure.retryable) throw failure;
          lastError = failure;
        }
        // Reconcile by the original logical message before any retry.
        try {
          const current = await history(id);
          const turn = current.turns.find((t) => t.client_message_id === message.clientMessageId);
          if (turn) return { conversation: current.conversation, turn };
          const pending = current.pending_request;
          if (pending?.client_message_id === message.clientMessageId && pending.state === 'failed' && pending.last_error && !pending.last_error.retryable) {
            throw new VincentError(pending.last_error.message, pending.last_error.code, false);
          }
        } catch (error) {
          if (error instanceof VincentError && error.code !== null && !error.retryable) throw error;
        }
        await sleep(POLL_MS);
      }
      throw lastError ?? new VincentError('Vincent took too long to answer.', 'request_timeout', true);
    },
  };
}
