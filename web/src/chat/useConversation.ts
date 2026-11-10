import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError, TransportError, getHistory, openConversation, postMessage, type Submission,
} from '../api/client';
import type { AnswerLanguage, Conversation, History, Turn } from '../api/types';
import { uuidv4 } from '../api/uuid';
import { clearDraft, loadDraft, saveDraft } from './drafts';

export interface PendingView extends Submission {
  /** sending: POST in flight. checking: outcome unknown, reading history. stalled: polled 12 s with no result. */
  phase: 'sending' | 'checking' | 'stalled' | 'failed';
  error: { code: string; retryable: boolean; requestId: string | null } | null;
}

export interface ConversationView {
  status: 'starting' | 'ready' | 'unavailable';
  turns: Turn[];
  pending: PendingView | null;
  sessionEnded: boolean;
  /** Text to put back into the input box (set after a lost session or an edit). */
  restoredText: string | null;
  consumeRestoredText: () => void;
  canSend: boolean;
  send: (question: string, lang: AnswerLanguage) => void;
  retry: () => void;
  checkAgain: () => void;
  edit: () => void;
  reopen: () => void;
}

const POLL_MS = 1000;
const POLL_LIMIT_MS = 12_000;
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Trim once and drop control characters the API rejects (contract 1.1). */
export function cleanQuestion(q: string): string {
  return q.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '').trim();
}

/**
 * Owns one artwork's conversation: create or restore it, load history, submit,
 * and recover. `token` marks the mounted artwork page; once the visitor leaves,
 * every late reply for that page is dropped instead of rendered (contract 3.2).
 */
export function useConversation(artworkId: string): ConversationView {
  const [status, setStatus] = useState<ConversationView['status']>('starting');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingView | null>(null);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [restoredText, setRestoredText] = useState<string | null>(null);

  const token = useRef({ cancelled: false });
  const conv = useRef<Conversation | null>(null);
  const busy = useRef(false); // a POST or a reconcile loop is running

  const failed = (s: Submission, code: string, retryable: boolean, requestId: string | null = null): PendingView => ({
    ...s, phase: 'failed', error: { code, retryable, requestId },
  });

  /** Apply a history snapshot for submission `s`. Returns true when the outcome is settled. */
  const settleFromHistory = useCallback((h: History, s: Submission): boolean => {
    const id = h.conversation.conversation_id;
    setTurns(h.turns);
    if (h.turns.some((t) => t.client_message_id === s.client_message_id)) {
      clearDraft(id);
      setPending(null);
      return true;
    }
    const p = h.pending_request;
    if (p && p.state === 'processing') return false; // ours or another key: keep waiting
    if (p && p.client_message_id === s.client_message_id) {
      setPending(failed(s, p.last_error?.code ?? 'internal_error', p.last_error?.retryable ?? true));
      return true;
    }
    // No trace of this key on the server: it never arrived. The same UUID and payload are safe to
    // resend (if another request has since replaced it, the retry is rejected and history is reloaded).
    setPending(failed(s, 'unconfirmed', true));
    return true;
  }, []);

  const open = useCallback(async (my: { cancelled: boolean }, lostQuestion: string | null) => {
    setStatus('starting');
    try {
      const c = await openConversation(artworkId);
      const h = await getHistory(c.conversation_id);
      if (my.cancelled) return;
      conv.current = c;
      setTurns(h.turns);

      // Reconcile the saved draft with the server's view (contract 3.2).
      let draft = loadDraft(c.conversation_id);
      if (draft && h.turns.some((t) => t.client_message_id === draft!.client_message_id)) {
        clearDraft(c.conversation_id);
        draft = null;
      }
      const p = h.pending_request;
      if (p) {
        const s: Submission = { client_message_id: p.client_message_id, question: p.question, answer_language: p.answer_language };
        saveDraft(c.conversation_id, s); // the server's pending request outranks an older draft
        if (p.state === 'processing') {
          setPending({ ...s, phase: 'checking', error: null });
          void reconcile(my, s);
        } else {
          setPending(failed(s, p.last_error?.code ?? 'internal_error', p.last_error?.retryable ?? true));
        }
      } else if (draft) {
        setPending(failed(draft, 'unconfirmed', true));
      } else {
        setPending(null);
      }
      if (lostQuestion !== null) setRestoredText(lostQuestion);
      setStatus('ready');
    } catch {
      if (my.cancelled) return;
      conv.current = null;
      setStatus('unavailable');
    }
  }, [artworkId]);

  /** The session is gone (expired, or the API restarted): explain, start a new one, keep the text unsent. */
  const sessionLost = useCallback((my: { cancelled: boolean }, question: string) => {
    if (conv.current) clearDraft(conv.current.conversation_id);
    conv.current = null;
    setPending(null);
    setTurns([]);
    setSessionEnded(true);
    void open(my, question);
  }, [open]);

  /** Outcome unknown: read history, polling once a second for at most 12 s while this page is active. */
  const reconcile = useCallback(async (my: { cancelled: boolean }, s: Submission) => {
    const c = conv.current;
    if (!c) return;
    busy.current = true;
    try {
      const started = Date.now();
      for (;;) {
        let h: History;
        try {
          h = await getHistory(c.conversation_id);
        } catch (e) {
          if (my.cancelled) return;
          if (e instanceof ApiError && e.code === 'conversation_not_found') return sessionLost(my, s.question);
          setPending(failed(s, 'unconfirmed', true));
          return;
        }
        if (my.cancelled) return;
        if (settleFromHistory(h, s)) return;
        if (Date.now() - started >= POLL_LIMIT_MS) {
          setPending({ ...s, phase: 'stalled', error: null });
          return;
        }
        await wait(POLL_MS);
        if (my.cancelled) return;
      }
    } finally {
      busy.current = false;
    }
  }, [sessionLost, settleFromHistory]);

  const submit = useCallback(async (s: Submission) => {
    const my = token.current;
    const c = conv.current;
    if (!c || busy.current) return;
    busy.current = true;
    saveDraft(c.conversation_id, s);
    setSessionEnded(false);
    setPending({ ...s, phase: 'sending', error: null });
    let next: 'done' | 'reconcile' = 'done';
    try {
      const turn = await postMessage(c.conversation_id, s);
      clearDraft(c.conversation_id); // confirmed, even if the visitor has moved on
      if (my.cancelled) return;
      setTurns((ts) => (ts.some((t) => t.turn_id === turn.turn_id) ? ts : [...ts, turn].sort((a, b) => a.sequence - b.sequence)));
      setPending(null);
    } catch (e) {
      if (my.cancelled) return;
      if (e instanceof ApiError) {
        switch (e.code) {
          case 'request_in_progress':
          case 'conversation_busy':
            next = 'reconcile';
            break;
          case 'conversation_not_found':
            busy.current = false;
            return sessionLost(my, s.question);
          case 'idempotency_conflict':
          case 'request_superseded':
            // This key can never run again. Show what the server has and hand the text back.
            clearDraft(c.conversation_id);
            setPending(null);
            setRestoredText(s.question);
            busy.current = false;
            return void open(my, null);
          default:
            setPending(failed(s, e.code, e.retryable, e.requestId));
        }
      } else if (e instanceof TransportError && e.kind === 'invalid_response') {
        setPending(failed(s, 'bad_response', false));
      } else {
        next = 'reconcile'; // timeout or dropped connection: the server may still have committed it
      }
    } finally {
      busy.current = false;
    }
    if (next === 'reconcile') {
      setPending({ ...s, phase: 'checking', error: null });
      void reconcile(my, s);
    }
  }, [open, reconcile, sessionLost]);

  useEffect(() => {
    const my = { cancelled: false };
    token.current = my;
    conv.current = null;
    busy.current = false;
    setTurns([]);
    setPending(null);
    setSessionEnded(false);
    setRestoredText(null);
    void open(my, null);
    return () => { my.cancelled = true; };
  }, [open]);

  const canSend = status === 'ready' && (pending === null || pending.phase === 'failed');

  return {
    status, turns, pending, sessionEnded, restoredText,
    consumeRestoredText: useCallback(() => setRestoredText(null), []),
    canSend,
    send: (question, lang) => {
      const q = cleanQuestion(question);
      const n = [...q].length;
      if (!canSend || n < 1 || n > 500) return;
      // A new question (or an edited failed one) always gets a new UUID.
      void submit({ client_message_id: uuidv4(), question: q, answer_language: lang });
    },
    retry: () => {
      // Same UUID, same question, same language as first submitted.
      if (pending && pending.phase === 'failed') {
        void submit({ client_message_id: pending.client_message_id, question: pending.question, answer_language: pending.answer_language });
      }
    },
    checkAgain: () => {
      if (pending && pending.phase === 'stalled') {
        const s = { client_message_id: pending.client_message_id, question: pending.question, answer_language: pending.answer_language };
        setPending({ ...s, phase: 'checking', error: null });
        void reconcile(token.current, s);
      }
    },
    edit: () => {
      if (pending && pending.phase === 'failed') setRestoredText(pending.question);
    },
    reopen: () => void open(token.current, null),
  };
}
