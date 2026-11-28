// Dedicated v0.4 transport. The ordinary guide keeps its v0.3 client and policy.
import { InvalidResponse, parseErrorBody, wire } from '../api/types';
import type { VoiceMessage } from './controller';
import {
  VINCENT_CONTRACT, VINCENT_ARTWORK, parseVincentConversation, parseVincentHistory,
  parseVincentResult, validateVincentMessage, type VincentConversation, type VincentHistory,
} from './vincentContract';
export { VINCENT_CONTRACT, VINCENT_ARTWORK } from './vincentContract';
export type { VincentCitation, VincentTurn, VincentConversation, VincentHistory } from './vincentContract';

export const VINCENT_WAIT_MS = 70_000;
const HISTORY_TIMEOUT_MS = 10_000;
const POLL_MS = 1_000;
export class VincentError extends Error {
  constructor(message: string, readonly code: string | null, readonly retryable: boolean, readonly status = 0) {
    super(message);
  }
}
interface Options { signal?: AbortSignal; onHistory?: (history: VincentHistory) => void; deadline?: number }
const cancelled = () => new DOMException('The page was closed.', 'AbortError');
const timeout = () => new VincentError('Vincent took too long to answer. Check the conversation before trying again.', 'request_timeout', true);

function validate<T>(parse: () => T): T {
  try { return parse(); } catch (error) {
    if (!(error instanceof InvalidResponse)) throw error;
    throw new VincentError('The server returned an invalid Vincent conversation. Please reload to check it.', 'invalid_response', false);
  }
}

/** Stop waiting immediately on navigation, including while sleeping between polls. */
function withSignal<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(cancelled());
  return new Promise((resolve, reject) => {
    const abort = () => reject(cancelled());
    signal.addEventListener('abort', abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function call(fetcher: typeof fetch, method: 'GET' | 'POST', path: string, body: unknown, timeoutMs: number, signal?: AbortSignal): Promise<unknown> {
  if (signal?.aborted) throw cancelled();
  const abort = new AbortController();
  const cancel = () => abort.abort();
  signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, timeoutMs);
  try {
    const response = await fetcher(path, {
      method, credentials: 'same-origin', redirect: 'error', signal: abort.signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
    const json: unknown = await response.json();
    if (signal?.aborted) throw cancelled();
    if (!response.ok) {
      const error = validate(() => parseErrorBody(wire.obj(json, 'error response').error));
      throw new VincentError(error.message, error.code, error.retryable, response.status);
    }
    return json;
  } catch (error) {
    if (signal?.aborted) throw cancelled();
    if (error instanceof VincentError) throw error;
    if (error instanceof SyntaxError) throw new VincentError('The server returned unreadable data.', 'invalid_response', false);
    throw abort.signal.aborted ? timeout() : new VincentError('The connection dropped.', null, true);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

export function createVincentClient(fetcher: typeof fetch = (...args) => fetch(...args), sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))) {
  // Share the initial create across StrictMode effects. Aborting one subscriber
  // does not cancel the cookie-setting request needed by the other subscriber.
  let opening: Promise<VincentConversation> | null = null;
  const attempted = new Set<string>();
  const history = async (id: string, options: Options = {}, timeoutMs = HISTORY_TIMEOUT_MS): Promise<VincentHistory> => {
    const raw = await call(fetcher, 'GET', `/api/conversations/${id}/messages`, undefined, timeoutMs, options.signal);
    return validate(() => parseVincentHistory(raw, id));
  };
  const remaining = (deadline: number) => {
    const ms = deadline - Date.now();
    if (ms <= 0) throw timeout();
    return ms;
  };
  const pause = (deadline: number, signal?: AbortSignal) => withSignal(sleep(Math.min(POLL_MS, remaining(deadline))), signal);

  return {
    async open(options: Options = {}): Promise<VincentConversation> {
      if (options.signal?.aborted) throw cancelled();
      if (!opening) {
        opening = call(fetcher, 'POST', '/api/conversations',
          { contract_version: VINCENT_CONTRACT, artwork_id: VINCENT_ARTWORK, conversation_mode: 'vincent' }, HISTORY_TIMEOUT_MS)
          .then((raw) => validate(() => parseVincentConversation(wire.obj(raw, 'create response').conversation)))
          .finally(() => { opening = null; });
      }
      return withSignal(opening, options.signal);
    },
    history,
    /** Refresh recovery is read-only: a saved turn is shown without automatic speech. */
    async recover(id: string, options: Options = {}): Promise<VincentHistory> {
      const deadline = options.deadline ?? Date.now() + VINCENT_WAIT_MS;
      while (true) {
        const current = await history(id, options, Math.min(HISTORY_TIMEOUT_MS, remaining(deadline)));
        options.onHistory?.(current);
        if (current.pending_request?.state !== 'processing') return current;
        await pause(deadline, options.signal);
      }
    },
    async submit(id: string, message: VoiceMessage, options: Options = {}) {
      const body = {
        contract_version: VINCENT_CONTRACT, client_message_id: message.clientMessageId, question: message.question,
        language_override: message.languageOverride, expected_revision: message.expectedRevision,
      };
      const key = `${id}/${message.clientMessageId}`;
      let reconcile = attempted.has(key);
      attempted.add(key);
      if (attempted.size > 64) attempted.delete(attempted.values().next().value!);
      const deadline = options.deadline ?? Date.now() + VINCENT_WAIT_MS;
      while (true) {
        if (options.signal?.aborted) throw cancelled();
        if (reconcile) {
          // Keep reading while pending (or while GET is unavailable). Only a
          // confirmed absent/retryable failed request allows an identical POST.
          let current: VincentHistory;
          try { current = await history(id, options, Math.min(HISTORY_TIMEOUT_MS, remaining(deadline))); }
          catch (error) {
            if (!(error instanceof VincentError) || !error.retryable) throw error;
            await pause(deadline, options.signal);
            continue;
          }
          options.onHistory?.(current);
          const turn = current.turns.find((t) => t.client_message_id === message.clientMessageId);
          if (turn) {
            validate(() => validateVincentMessage(turn, message));
            return { conversation: current.conversation, turn };
          }
          const pending = current.pending_request;
          if (pending && pending.client_message_id === message.clientMessageId) {
            if (pending.question !== message.question || pending.language_override !== message.languageOverride || pending.expected_revision !== message.expectedRevision) {
              throw new VincentError('This request does not match the saved question.', 'invalid_response', false);
            }
            if (pending.state === 'processing') { await pause(deadline, options.signal); continue; }
            if (pending.last_error && !pending.last_error.retryable) {
              throw new VincentError(pending.last_error.message, pending.last_error.code, false);
            }
          }
          if (current.conversation.revision !== message.expectedRevision || (pending && pending.client_message_id !== message.clientMessageId && pending.state === 'processing')) {
            throw new VincentError('The conversation has changed. Review your question before sending it again.', 'conversation_state_conflict', false);
          }
        }
        try {
          const raw = await call(fetcher, 'POST', `/api/conversations/${id}/messages`, body, remaining(deadline), options.signal);
          return validate(() => parseVincentResult(raw, id, message));
        } catch (error) {
          if (!(error instanceof VincentError) || !error.retryable || ![null, 'request_timeout', 'request_in_progress', 'agent_unavailable'].includes(error.code)) throw error;
          reconcile = true;
        }
      }
    },
  };
}
