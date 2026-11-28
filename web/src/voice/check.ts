// A's device test page for Talk with Vincent. It drives the real voice
// controller and the v0.4 client against the running gallery, so the iPhone
// checklist can run before B's visitor page exists. Not a visitor page.
import { createVoiceController, type VoiceController, type VoiceSnapshot } from './controller';
import { createVincentClient, type VincentTurn } from './vincentClient';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const client = createVincentClient();
const turns: VincentTurn[] = [];
let revision = 0;
let voice: VoiceController | null = null;

function render(snapshot: VoiceSnapshot): void {
  $('phase').textContent = snapshot.phase + (snapshot.welcome ? ' (greeting)' : '');
  $('seconds').textContent = snapshot.phase === 'recording' ? `${snapshot.recordingSeconds.toFixed(1)} s` : '';
  $('transcript').textContent = snapshot.transcript ?? '–';
  const busy = ['recording', 'transcribing', 'generating'].includes(snapshot.phase);
  ($('record') as HTMLButtonElement).disabled = busy;
  ($('stop') as HTMLButtonElement).disabled = !['recording', 'playing', 'preparing_audio', 'awaiting_play'].includes(snapshot.phase);
  const error = snapshot.error;
  ($('retry') as HTMLButtonElement).disabled = !(error?.retryable && (error.stage === 'transcription' || error.stage === 'message'));
  ($('play') as HTMLButtonElement).disabled = !snapshot.turnId || busy;
  $('play').textContent = snapshot.phase === 'awaiting_play' ? 'Play' : 'Replay';
  $('error').hidden = !error;
  $('error').textContent = error ? `${error.stage}: ${error.message}${error.code ? ` (${error.code})` : ''}` : '';
  const unsent = snapshot.unsent !== null && error?.stage === 'message' && !error.retryable;
  $('unsentBox').hidden = !unsent;
  if (unsent && document.activeElement !== $('unsent')) ($('unsent') as HTMLTextAreaElement).value = snapshot.unsent ?? '';
  for (const element of document.querySelectorAll<HTMLElement>('[data-turn]')) {
    element.style.outline = snapshot.phase === 'playing' && element.dataset.turn === snapshot.turnId ? '2px solid var(--accent)' : '';
  }
}

function showTurns(): void {
  const container = $('turns');
  container.replaceChildren(...turns.map((turn) => {
    const box = document.createElement('div');
    box.className = 'box turn';
    box.dataset.turn = turn.turn_id;
    const question = document.createElement('p');
    question.innerHTML = '<strong>You</strong> ';
    question.append(turn.question);
    const answer = document.createElement('p');
    answer.innerHTML = '<strong>Vincent</strong> ';
    answer.append(turn.answer.text);
    box.append(question, answer);
    for (const citation of turn.answer.citations) {
      const source = document.createElement('p');
      source.className = 'source';
      const link = document.createElement('a');
      link.href = citation.source_url;
      link.textContent = citation.source_title;
      link.target = '_blank';
      source.append(`${citation.kind === 'catalog' ? 'catalog' : citation.source_kind} · `, link, ` · ${citation.attribution}`);
      box.append(source);
    }
    const replay = document.createElement('button');
    replay.textContent = 'Replay this answer';
    replay.onclick = () => { void voice?.playTurn(turn.turn_id); };
    box.append(replay);
    return box;
  }));
}

async function start(): Promise<void> {
  const conversation = await client.open();
  revision = conversation.revision;
  $('conversation').textContent = conversation.conversation_id.slice(0, 8);
  $('revision').textContent = String(revision);
  const history = await client.history(conversation.conversation_id);
  turns.push(...history.turns);
  showTurns();
  voice = createVoiceController(conversation.conversation_id, {
    revision: () => revision,
    submit: async (message) => {
      const { conversation: next, turn } = await client.submit(conversation.conversation_id, message);
      revision = next.revision;
      $('revision').textContent = String(revision);
      if (!turns.some((t) => t.turn_id === turn.turn_id)) turns.push(turn);
      showTurns();
      return { turnId: turn.turn_id };
    },
  });
  voice.subscribe(() => render(voice!.getSnapshot()));
  render(voice.getSnapshot());
  $('record').onclick = () => { void voice!.startRecording(); };
  $('stop').onclick = () => {
    if (voice!.getSnapshot().phase === 'recording') voice!.stopRecording();
    else voice!.stopPlayback();
  };
  $('retry').onclick = () => {
    const stage = voice!.getSnapshot().error?.stage;
    void (stage === 'message' ? voice!.retryMessage() : voice!.retryTranscription());
  };
  $('play').onclick = () => { const id = voice!.getSnapshot().turnId; if (id) void voice!.playTurn(id); };
  $('sendUnsent').onclick = async () => {
    // A reviewed question is a new logical message at the current revision.
    const question = ($('unsent') as HTMLTextAreaElement).value.trim();
    if (!question) return;
    const { conversation: next, turn } = await client.submit(conversation.conversation_id, {
      question, clientMessageId: crypto.randomUUID(), expectedRevision: revision, languageOverride: null,
    });
    revision = next.revision;
    $('revision').textContent = String(revision);
    turns.push(turn);
    showTurns();
    $('unsentBox').hidden = true;
    void voice!.playTurn(turn.turn_id);
  };
  addEventListener('pagehide', () => voice?.dispose());
}

start().catch((error) => {
  $('conversation').textContent = 'unavailable';
  $('error').hidden = false;
  $('error').textContent = `Could not open the Vincent conversation: ${error?.message ?? error}${error?.code ? ` (${error.code})` : ''}`;
});
