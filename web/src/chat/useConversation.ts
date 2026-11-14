import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError, BROWSER_WAIT_MS, HISTORY_TIMEOUT_MS, TransportError, getHistory, openConversation, postMessage, type Submission,
} from '../api/client';
import type { Conversation, History, LanguagePreference, PendingRequest, Turn } from '../api/types';
import { uuidv4 } from '../api/uuid';
import { clearDraft, lastConversationId, loadDraft, rememberConversation, saveDraft, stageDraft } from './drafts';

export interface PendingView extends Submission {
  /** sending: POST in flight. checking: outcome unknown, reading history. stalled: the wait budget ran out with no result. */
  phase: 'sending' | 'checking' | 'stalled' | 'failed';
  error: { code: string; retryable: boolean; requestId: string | null } | null;
}

export interface ConversationView {
  status: 'starting' | 'ready' | 'unavailable';
  /** The server's latest view of this conversation (revision and language settings under the agent contract). */
  conversation: Conversation | null;
  turns: Turn[];
  pending: PendingView | null;
  sessionEnded: boolean;
  /** The conversation moved on elsewhere (another tab) before this question was accepted. */
  conversationChanged: boolean;
  /** Text to put back into the input box (set after a lost session or an edit). */
  restoredText: string | null;
  consumeRestoredText: () => void;
  canSend: boolean;
  /** `languageOverride`: the visitor's new language selection, or null when they did not change it. */
  send: (question: string, languageOverride: LanguagePreference | null) => void;
  retry: () => void;
  checkAgain: () => void;
  edit: () => void;
  reopen: () => void;
}

const POLL_MS = 1000;
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const payload = (p: Submission | PendingRequest): Submission => ({
  client_message_id: p.client_message_id, question: p.question,
  language_override: p.language_override, expected_revision: p.expected_revision,
});

/** Trim once and drop control characters the API rejects (contract 1.1). */
export function cleanQuestion(q: string): string {
  return q.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '').trim();
}

/**
 * Owns one artwork's conversation: create or restore it, load history, submit,
 * and recover. The page signal cancels history reads when the visitor leaves.
 * Accepted question submissions can still finish on the server.
 */
export function useConversation(artworkId: string): ConversationView {
  const [status, setStatus] = useState<ConversationView['status']>('starting');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingView | null>(null);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [conversationChanged, setConversationChanged] = useState(false);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [restoredText, setRestoredText] = useState<string | null>(null);

  const page = useRef<AbortSignal | null>(null);
  const conv = useRef<Conversation | null>(null);
  const busy = useRef<symbol | null>(null); // identifies the operation that may update this page
  const owns = useCallback((signal: AbortSignal, operation: symbol) =>
    page.current === signal && !signal.aborted && busy.current === operation, []);

  /** Take the server's conversation view, but never roll back to an older revision (agent contract 2.4). */
  const adopt = useCallback((c: Conversation | null) => {
    if (!c) return true;
    const held = conv.current;
    if (held && held.conversation_id === c.conversation_id && held.agent && c.agent && c.agent.revision < held.agent.revision) return false;
    conv.current = c;
    setConversation(c);
    return true;
  }, []);

  const failed = (s: Submission, code: string, retryable: boolean, requestId: string | null = null): PendingView => ({
    ...s, phase: 'failed', error: { code, retryable, requestId },
  });

  /** Apply a history snapshot for submission `s`. Returns true when the outcome is settled. */
  const settleFromHistory = useCallback((h: History, s: Submission): boolean => {
    const id = h.conversation.conversation_id;
    if (!adopt(h.conversation)) return false;
    setTurns(h.turns);
    if (h.turns.some((t) => t.client_message_id === s.client_message_id)) {
      clearDraft(id, s.client_message_id);
      setPending(null);
      return true;
    }
    const p = h.pending_request;
    if (p && p.state === 'processing') return false; // ours or another key: keep waiting
    if (p) {
      const accepted = payload(p);
      saveDraft(id, accepted);
      if (p.client_message_id !== s.client_message_id) setRestoredText(s.question);
      setPending(failed(accepted, p.last_error?.code ?? 'internal_error', p.last_error?.retryable ?? true));
      return true;
    }
    if (h.conversation.agent && h.conversation.agent.revision > s.expected_revision) {
      clearDraft(id, s.client_message_id);
      setConversationChanged(true);
      setRestoredText(s.question);
      setPending(null);
      return true;
    }
    if (loadDraft(id)?.client_message_id === s.client_message_id) saveDraft(id, s);
    // No trace of this key on the server: it never arrived. The same UUID and payload are safe to
    // resend (if another request has since replaced it, the retry is rejected and history is reloaded).
    setPending(failed(s, 'unconfirmed', true));
    return true;
  }, [adopt]);

  const open = useCallback(async (signal: AbortSignal) => {
    if (signal.aborted) return;
    const operation = Symbol('open');
    busy.current = operation;
    setStatus('starting');
    try {
      const c = await openConversation(artworkId);
      if (!owns(signal, operation)) return;
      const h = await getHistory(c.conversation_id, { signal, expectedScope: c });
      if (!owns(signal, operation)) return;
      if (!adopt(h.conversation)) {
        setStatus('ready');
        return;
      }
      setTurns(h.turns);

      const previousId = lastConversationId(artworkId);
      if (previousId && previousId !== c.conversation_id) {
        setSessionEnded(true);
        const oldDraft = loadDraft(previousId);
        if (oldDraft) {
          setRestoredText(oldDraft.question);
          clearDraft(previousId, oldDraft.client_message_id);
        }
      }
      rememberConversation(artworkId, c.conversation_id);

      // Reconcile the saved draft with the server's view (contract 3.2).
      let draft = loadDraft(c.conversation_id);
      if (draft && h.turns.some((t) => t.client_message_id === draft!.client_message_id)) {
        clearDraft(c.conversation_id, draft.client_message_id);
        draft = null;
      }
      const p = h.pending_request;
      if (p) {
        const s = payload(p);
        if (draft && draft.client_message_id !== s.client_message_id) {
          setRestoredText(draft.question);
        }
        saveDraft(c.conversation_id, s); // the server's pending request outranks an older draft
        if (p.state === 'processing') {
          setPending({ ...s, phase: 'checking', error: null });
          void reconcile(signal, s);
        } else {
          setPending(failed(s, p.last_error?.code ?? 'internal_error', p.last_error?.retryable ?? true));
        }
      } else if (draft) {
        if (h.conversation.agent && draft.expected_revision < h.conversation.agent.revision) {
          clearDraft(c.conversation_id, draft.client_message_id);
          setRestoredText(draft.question);
          setConversationChanged(true);
          setPending(null);
        } else {
          saveDraft(c.conversation_id, draft);
          setPending(failed(draft, 'unconfirmed', true));
        }
      } else {
        setPending(null);
      }
      setStatus('ready');
    } catch {
      if (!owns(signal, operation)) return;
      conv.current = null;
      setConversation(null);
      setStatus('unavailable');
    } finally {
      if (busy.current === operation) busy.current = null;
    }
  }, [artworkId, adopt, owns]);

  /** The session is gone (expired, or the API restarted): explain, start a new one, keep the text unsent. */
  const sessionLost = useCallback((signal: AbortSignal, submission: Submission) => {
    conv.current = null;
    setConversation(null);
    setPending(null);
    setTurns([]);
    setSessionEnded(true);
    setRestoredText(submission.question);
    void open(signal);
  }, [open]);

  /**
   * Outcome unknown: read history, then poll once a second until `deadline` while this page is
   * active. Once that budget is spent, only a deliberate Check again starts a new read.
   */
  const reconcile = useCallback(async (signal: AbortSignal, s: Submission, deadline = performance.now() + BROWSER_WAIT_MS) => {
    const c = conv.current;
    if (!c || signal.aborted) return;
    const operation = Symbol('reconcile');
    busy.current = operation;
    try {
      while (owns(signal, operation)) {
        const remaining = deadline - performance.now();
        if (remaining <= 0) break;
        let h: History;
        try {
          h = await getHistory(c.conversation_id, { signal, expectedScope: c, timeoutMs: Math.min(HISTORY_TIMEOUT_MS, remaining) });
        } catch (e) {
          if (!owns(signal, operation)) return;
          if (e instanceof ApiError && e.code === 'conversation_not_found') return sessionLost(signal, s);
          if (e instanceof TransportError && e.kind === 'timeout') break;
          const malformed = e instanceof TransportError && e.kind === 'invalid_response';
          setPending(failed(s, malformed ? 'bad_response' : 'unconfirmed', !malformed));
          return;
        }
        if (!owns(signal, operation)) return;
        if (settleFromHistory(h, s)) return;
        const waitMs = deadline - performance.now();
        if (waitMs <= 0) break;
        await wait(Math.min(POLL_MS, waitMs));
      }
      if (owns(signal, operation)) setPending({ ...s, phase: 'stalled', error: null });
    } finally {
      if (busy.current === operation) busy.current = null;
    }
  }, [sessionLost, settleFromHistory, owns]);

  const submit = useCallback(async (s: Submission) => {
    const signal = page.current;
    const c = conv.current;
    if (!c || !signal || signal.aborted || busy.current) return;
    const operation = Symbol('submit');
    busy.current = operation;
    stageDraft(c.conversation_id, s);
    setSessionEnded(false);
    setConversationChanged(false);
    // One wait budget covers the POST and any recovery polling after it (agent contract 7.4).
    const deadline = performance.now() + BROWSER_WAIT_MS;
    setPending({ ...s, phase: 'sending', error: null });
    let next: 'done' | 'reconcile' = 'done';
    try {
      const { turn, conversation: latest } = await postMessage(c.conversation_id, s, c);
      clearDraft(c.conversation_id, s.client_message_id); // confirmed, even after navigation
      if (!owns(signal, operation)) return;
      adopt(latest);
      setTurns((ts) => (ts.some((t) => t.turn_id === turn.turn_id) ? ts : [...ts, turn].sort((a, b) => a.sequence - b.sequence)));
      // A completed replay may carry a newer conversation than its saved turn.
      // Load the intervening turns before accepting another context-dependent question.
      if (latest?.agent && latest.agent.revision > turn.sequence) next = 'reconcile';
      else setPending(null);
    } catch (e) {
      if (!owns(signal, operation)) return;
      if (e instanceof ApiError) {
        switch (e.code) {
          case 'request_in_progress':
          case 'conversation_busy':
            next = 'reconcile';
            break;
          case 'conversation_not_found':
            busy.current = null;
            return sessionLost(signal, s);
          case 'conversation_state_conflict':
          case 'idempotency_conflict':
          case 'request_superseded':
            // This key can never run again. Show what the server has and hand the text back.
            // After a state conflict the conversation gained a turn elsewhere: the visitor reviews
            // the text and sends it as a new question; it is never resent automatically (agent contract 2.3).
            if (e.code === 'conversation_state_conflict') setConversationChanged(true);
            clearDraft(c.conversation_id, s.client_message_id);
            setPending(null);
            setRestoredText(s.question);
            busy.current = null;
            return void open(signal);
          default:
            setPending(failed(s, e.code, e.retryable, e.requestId));
        }
      } else {
        // Even a malformed success body may follow a committed turn. Recover its
        // canonical history before allowing a new submission; never repeat the POST.
        next = 'reconcile';
      }
    } finally {
      if (busy.current === operation) busy.current = null;
    }
    if (next === 'reconcile' && page.current === signal && !signal.aborted) {
      setPending({ ...s, phase: 'checking', error: null });
      void reconcile(signal, s, deadline);
    }
  }, [adopt, open, reconcile, sessionLost, owns]);

  useEffect(() => {
    const controller = new AbortController();
    page.current = controller.signal;
    conv.current = null;
    busy.current = null;
    setConversation(null);
    setTurns([]);
    setPending(null);
    setSessionEnded(false);
    setConversationChanged(false);
    setRestoredText(null);
    void open(controller.signal);
    return () => controller.abort();
  }, [open]);

  const canSend = status === 'ready' && (pending === null || pending.phase === 'failed');

  return {
    status, conversation, turns, pending, sessionEnded, conversationChanged, restoredText,
    consumeRestoredText: useCallback(() => setRestoredText(null), []),
    canSend,
    send: (question, languageOverride) => {
      const q = cleanQuestion(question);
      const n = [...q].length;
      if (!canSend || !conv.current || n < 1 || n > 500) return;
      // A new question (or an edited failed one) always gets a new UUID, and is tied to the
      // conversation revision the visitor is looking at now.
      void submit({
        client_message_id: uuidv4(), question: q, language_override: languageOverride,
        expected_revision: conv.current.agent?.revision ?? 0,
      });
    },
    retry: () => {
      // Same UUID, question, language choice and revision as first submitted.
      if (pending?.phase === 'failed' && pending.error?.retryable) void submit(payload(pending));
    },
    checkAgain: () => {
      const signal = page.current;
      if (pending?.phase === 'stalled' && signal && !busy.current) {
        const s = payload(pending);
        setPending({ ...s, phase: 'checking', error: null });
        void reconcile(signal, s);
      }
    },
    edit: () => {
      if (pending && pending.phase === 'failed') setRestoredText(pending.question);
    },
    reopen: () => { if (page.current) void open(page.current); },
  };
}
