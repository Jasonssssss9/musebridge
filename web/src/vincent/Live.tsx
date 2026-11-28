import { useEffect, useRef, useState } from 'react';
import { Link } from '../ui/App';
import { createVincentClient, VincentError, VINCENT_WAIT_MS, type VincentHistory, type VincentTurn } from '../voice/vincentClient';
import { validateVincentMessage } from '../voice/vincentContract';
import { useVincentVoice } from '../voice/useVincentVoice';
import type { VoiceMessage } from '../voice/controller';
import { VincentView, type VincentReply } from './View';

// One shared create request survives StrictMode's first cleanup. No session ID
// is copied from the ordinary guide or persisted in browser storage.
const client = createVincentClient();
const explanation = (error: unknown) => error instanceof VincentError ? error.message : 'The conversation could not be loaded. Please try again.';
const asReply = (turn: VincentTurn): VincentReply => ({
  id: turn.turn_id, question: turn.question, text: turn.answer.text, citations: turn.answer.citations,
});

export default function VincentLive() {
  const [initial, setInitial] = useState<VincentHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    void (async () => {
      try {
        const conversation = await client.open({ signal: abort.signal });
        const history = await client.history(conversation.conversation_id, { signal: abort.signal });
        if (!abort.signal.aborted) setInitial(history);
      } catch (failure) {
        if (!abort.signal.aborted) setError(explanation(failure));
      }
    })();
    return () => abort.abort();
  }, [attempt]);
  if (initial) return <ConnectedVincent key={initial.conversation.conversation_id} initial={initial} />;
  return <main className="page vincent-page" lang="en">
    <Link to="/a/getty-103jnh" className="vincent-back">← Back to artwork</Link>
    <h1>Talk with Vincent</h1>
    {error ? <section className="failure" role="alert"><p>{error}</p>
      <button className="btn" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
    </section> : <p role="status">Opening your conversation with Vincent…</p>}
  </main>;
}

function ConnectedVincent({ initial }: { initial: VincentHistory }) {
  const [history, setHistory] = useState(initial);
  const latest = useRef(initial);
  const life = useRef<AbortController | null>(null);
  const [checking, setChecking] = useState(initial.pending_request?.state === 'processing');
  const blocked = useRef(checking);
  const [notice, setNotice] = useState<string | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const recoveringMessage = useRef<string | null>(initial.pending_request?.client_message_id ?? null);
  const id = initial.conversation.conversation_id;
  const accept = (next: VincentHistory) => {
    if (next.conversation.revision < latest.current.conversation.revision) return;
    latest.current = next;
    setHistory(next);
  };
  const markChecking = (value: boolean) => { blocked.current = value; setChecking(value); };

  async function synchronize(signal: AbortSignal, deadline?: number) {
    setSyncBusy(true);
    markChecking(true);
    setNotice('Checking the saved conversation. Your question will not be sent again.');
    try {
      const current = await client.recover(id, { signal, deadline, onHistory: accept });
      if (signal.aborted) return null;
      accept(current);
      markChecking(false);
      setNotice(null);
      return current;
    } catch (error) {
      if (!signal.aborted) setNotice(`${explanation(error)} Use Check again before sending another question.`);
      return null; // Keep input blocked until the saved state is known.
    } finally {
      if (!signal.aborted) setSyncBusy(false);
    }
  }

  async function submit(message: VoiceMessage): Promise<{ turnId: string }> {
    const signal = life.current!.signal;
    const deadline = Date.now() + VINCENT_WAIT_MS;
    recoveringMessage.current = message.clientMessageId;
    setSelected(null);
    try {
      const result = await client.submit(id, message, { signal, deadline, onHistory: accept });
      const saved = latest.current;
      const existing = saved.turns.find((t) => t.client_message_id === message.clientMessageId);
      if (!existing && result.turn.sequence === saved.turns.length + 1 && result.conversation.revision === result.turn.sequence) {
        accept({ conversation: result.conversation, turns: [...saved.turns, result.turn], pending_request: null });
      } else if (!existing || result.conversation.revision > saved.conversation.revision) {
        const current = await synchronize(signal, deadline);
        if (!current?.turns.some((t) => t.turn_id === result.turn.turn_id)) throw new VincentError('Check the saved conversation before continuing.', 'history_unavailable', true);
      }
      recoveringMessage.current = null;
      return { turnId: result.turn.turn_id };
    } catch (error) {
      if (signal.aborted) throw error;
      // Conflict, dropped response, or service failure: update the visible
      // history before permitting a fresh message at the current revision.
      const current = await synchronize(signal, deadline);
      const recovered = current?.turns.find((t) => t.client_message_id === message.clientMessageId);
      if (recovered) {
        validateVincentMessage(recovered, message);
        recoveringMessage.current = null;
        return { turnId: recovered.turn_id };
      }
      throw error;
    }
  }

  const { voice, snapshot } = useVincentVoice(id, { revision: () => latest.current.conversation.revision, submit });
  const currentVoice = useRef(voice);
  currentVoice.current = voice;
  function restorePending(current: VincentHistory) {
    const saved = current.turns.find((t) => t.client_message_id === recoveringMessage.current);
    if (saved) {
      currentVoice.current.restoreTurn(saved.turn_id, saved.question);
      recoveringMessage.current = null;
      return;
    }
    const pending = current.pending_request;
    if (pending?.state !== 'failed' || !pending.last_error) return;
    currentVoice.current.restoreMessage({
      question: pending.question, clientMessageId: pending.client_message_id,
      expectedRevision: pending.expected_revision, languageOverride: pending.language_override,
    }, { stage: 'message', ...pending.last_error });
  }
  // Delay restoration until the StrictMode hook has replaced its disposed controller.
  useEffect(() => {
    const abort = new AbortController();
    life.current = abort;
    void Promise.resolve().then(async () => {
      if (abort.signal.aborted) return;
      if (latest.current.pending_request?.state === 'processing') {
        const current = await synchronize(abort.signal);
        if (current) restorePending(current);
      } else restorePending(latest.current);
    });
    return () => abort.abort();
  }, [id]);

  const replies = history.turns.map(asReply);
  const reply = replies.find((r) => r.id === (selected ?? snapshot.turnId)) ?? replies[replies.length - 1] ?? null;
  const guard = (action: () => void) => { if (!blocked.current) action(); };
  return <VincentView state={snapshot} reply={reply} enabled actions={{
    startRecording: () => guard(() => { setSelected(null); void voice.startRecording(); }),
    stopRecording: () => voice.stopRecording(),
    playTurn: (turnId) => guard(() => { setSelected(turnId); void voice.playTurn(turnId); }),
    stopPlayback: () => voice.stopPlayback(), retryTranscription: () => guard(() => { void voice.retryTranscription(); }),
    retryMessage: () => guard(() => { void voice.retryMessage(); }),
    submit: (question) => guard(() => { void voice.submitText(question); }),
  }} interactionBlocked={checking} unsent={snapshot.unsent} notice={notice ? <aside className="vincent-unavailable" role="status">
    <p>{notice}</p>
    <button className="btn btn-quiet" disabled={syncBusy} onClick={() => {
      const signal = life.current?.signal;
      if (signal && !signal.aborted) void synchronize(signal).then((current) => { if (current) restorePending(current); });
    }}>Check again</button>
  </aside> : undefined} history={replies} selectTurn={(turnId) => {
    voice.stopPlayback(); setSelected(turnId);
  }} />;
}
