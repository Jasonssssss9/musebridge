import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import type { VoiceSnapshot } from '../voice/controller';
import { Link } from '../ui/App';
import { VincentSources, type VincentCitation } from './Sources';
import { VincentPortrait } from './Portrait';
import './styles.css';

export type VincentVoiceState = Pick<VoiceSnapshot, 'phase' | 'transcript' | 'turnId' | 'error' | 'recordingSeconds' | 'welcome'>;
export const INITIAL_VOICE_STATE: VincentVoiceState = {
  phase: 'ready', transcript: null, turnId: null, error: null, recordingSeconds: 0, welcome: false,
};

export interface VincentReply {
  id: string;
  question: string;
  text: string;
  citations: VincentCitation[];
}

export interface VincentActions {
  startRecording(): void;
  stopRecording(): void;
  playTurn(turnId: string): void;
  stopPlayback(): void;
  retryTranscription(): void;
  retryMessage?(): void;
  submit(question: string): void;
}

const statusText = {
  ready: 'Ready when you are.',
  recording: 'Listening. Tap Stop recording when you’re done.',
  transcribing: 'Turning your recording into words…',
  generating: 'Vincent is preparing a reply…',
  preparing_audio: 'Your reply is ready. Preparing the voice…',
  playing: 'Vincent is speaking.',
  awaiting_play: 'Your reply is ready. Tap Play answer to listen.',
  error: 'Let’s try that again.',
};

/** Render controller state; only the caller decides when audio has really started. */
export function VincentView({ state, reply, actions, enabled, previewControls, portraitUrl,
  interactionBlocked = false, notice, unsent, history = [], selectTurn }: {
  state: VincentVoiceState;
  reply: VincentReply | null;
  actions: VincentActions;
  enabled: boolean;
  previewControls?: ReactNode;
  portraitUrl?: string;
  interactionBlocked?: boolean;
  notice?: ReactNode;
  unsent?: string | null;
  history?: VincentReply[];
  selectTurn?: (turnId: string) => void;
}) {
  const [draft, setDraft] = useState('');
  const [showArtwork, setShowArtwork] = useState(state.phase === 'playing' && !state.welcome && state.turnId === reply?.id);
  const [greetingDismissed, setGreetingDismissed] = useState(false);
  const [artworkFailed, setArtworkFailed] = useState(false);
  const recording = state.phase === 'recording';
  const playing = state.phase === 'playing';
  const answerPlaying = playing && !state.welcome && state.turnId === reply?.id;
  const processing = ['transcribing', 'generating', 'preparing_audio'].includes(state.phase);
  const submitting = ['transcribing', 'generating'].includes(state.phase);
  const canStart = enabled && !interactionBlocked && !recording && !submitting;
  const canPlay = enabled && !interactionBlocked && reply !== null && !recording && !processing && !playing;
  const canStop = enabled && ['preparing_audio', 'playing', 'awaiting_play'].includes(state.phase);
  const questionLength = [...draft.trim()].length;

  useEffect(() => {
    if (answerPlaying) setShowArtwork(true);
    else if (recording || processing || state.welcome) setShowArtwork(false);
  }, [answerPlaying, recording, processing, state.welcome]);

  function interruptGreeting(action: () => void) {
    setGreetingDismissed(true);
    action();
  }

  function submitDraft(event: FormEvent) {
    event.preventDefault();
    if (!canStart || questionLength === 0 || questionLength > 500) return;
    interruptGreeting(() => actions.submit(draft.trim()));
    setDraft('');
  }

  const message = !enabled ? 'Voice conversation is not available yet.'
    : state.welcome ? 'Hello. Shall we take a closer look at these irises?' : statusText[state.phase];

  return (
    <main className="page vincent-page" lang="en">
      <nav className="vincent-nav" aria-label="Artwork navigation">
        <Link to="/a/getty-103jnh" className="vincent-back">← Back to artwork</Link>
        <span className="vincent-language">English conversation</span>
      </nav>
      <header className="vincent-heading">
        <p className="vincent-eyebrow">A closer look at Irises</p>
        <h1>Talk with Vincent</h1>
        <p>Share what catches your eye. Take the conversation from there.</p>
      </header>

      {previewControls}
      {notice}
      {!enabled && <aside className="vincent-unavailable" role="status">
        <strong>This conversation is not available yet.</strong>
        <p>You can still explore Irises with the <Link to="/a/getty-103jnh">artwork guide</Link>.</p>
      </aside>}

      <figure className={`vincent-stage${showArtwork ? ' vincent-stage-artwork' : ''}`}>
        <div className="vincent-visual">
          <div className="vincent-character" hidden={showArtwork}>
            <VincentPortrait portraitUrl={portraitUrl} allowGreeting={!greetingDismissed && !showArtwork &&
              (state.phase === 'ready' || (state.phase === 'playing' && state.welcome))} />
          </div>
          {showArtwork && (artworkFailed ? <p className="vincent-image-fallback">The painting could not be loaded. Your reply is still available below.</p>
            : <img key={reply?.id} className="vincent-painting" src="/artworks/getty-103jnh.jpg"
              alt="Blue irises and long green leaves, with one white bloom among the flowers."
              style={{ animationPlayState: answerPlaying ? 'running' : 'paused' }} onError={() => setArtworkFailed(true)} />)}
          <span className="vincent-stage-label">{showArtwork ? 'Irises · 1889' : 'Vincent van Gogh'}</span>
        </div>
        <figcaption>An AI interpretation of Vincent van Gogh, with an AI-generated voice.</figcaption>
        {showArtwork && <p className="vincent-image-credit">Digital image courtesy of Getty’s Open Content Program.{' '}
          <a href="https://media.getty.edu/iiif/image/8c255d80-7382-46db-9fa8-892c0d37247e/full/!1200,1200/0/default.jpg"
            target="_blank" rel="noopener noreferrer">Image source · CC0-1.0</a>
        </p>}
      </figure>

      <section className="vincent-status" aria-label="Conversation status">
        <span className={`vincent-status-dot${recording ? ' is-recording' : ''}`} aria-hidden="true" />
        <p role="status" aria-live="polite">{message}</p>
        {recording && <span className="vincent-timer" aria-label={`${state.recordingSeconds} seconds recorded`}>
          0:{String(Math.floor(state.recordingSeconds)).padStart(2, '0')} / 0:20
        </span>}
      </section>

      {state.error && <section className="failure vincent-error" role="alert">
        <p>{state.error.message}</p>
        {state.error.stage === 'capture' && <p>Allow microphone access in your browser, or type your question below.</p>}
        {state.error.stage === 'speech' && <p>Your answer and sources are still here. You can read them while audio is unavailable.</p>}
        {state.error.retryable && state.error.stage === 'transcription' &&
          <button className="btn btn-quiet" disabled={!enabled} onClick={actions.retryTranscription}>Retry transcription</button>}
        {state.error.retryable && state.error.stage === 'message' && actions.retryMessage &&
          <button className="btn btn-quiet" disabled={!canStart} onClick={actions.retryMessage}>Retry same question</button>}
        {unsent && <><p>Your question: {unsent}</p>
          <p>Review these words before starting a new question. Retrying keeps the original request.</p>
          <button className="btn btn-quiet" disabled={!canStart} onClick={() => setDraft(unsent)}>Review question</button></>}
      </section>}

      <section className="vincent-question" aria-labelledby="vincent-question-heading">
        <h2 id="vincent-question-heading">Your question</h2>
        <p className={state.transcript ? '' : 'muted'}>{state.transcript ?? 'What we hear will appear here. You can also type a question.'}</p>
      </section>

      <section className="vincent-answer" aria-labelledby="vincent-answer-heading">
        <div className="vincent-answer-heading">
          <h2 id="vincent-answer-heading">Vincent’s reply</h2>
          {reply && <span className="vincent-reply-label">{previewControls ? 'Sample reply' : 'AI response'}</span>}
        </div>
        {reply ? <>
          {reply.question !== state.transcript && <p className="muted small">In reply to: {reply.question}</p>}
          <p className="vincent-answer-text">{reply.text}</p>
          <div className="actions vincent-playback" aria-label="Answer playback">
            <button className="btn btn-quiet" disabled={!canPlay} onClick={() => interruptGreeting(() => actions.playTurn(reply.id))}>
              {showArtwork ? 'Replay answer' : 'Play answer'}
            </button>
            <button className="btn btn-quiet" disabled={!canStop} onClick={() => interruptGreeting(actions.stopPlayback)}>Stop playback</button>
          </div>
          {reply.citations.length > 0 ? <VincentSources key={reply.id} citations={reply.citations} />
            : <p className="muted small">No sources are attached to this reply.</p>}
        </> : <p className="muted">Vincent’s reply will appear here. When a reply uses sources, you can open them below.</p>}
      </section>

      {history.length > 1 && <section className="vincent-history" aria-label="Conversation history">
        <h2>Earlier in this conversation</h2>
        <ol>{history.filter((item) => item.id !== reply?.id).map((item) => <li key={item.id}>
          <p>{item.question}</p>
          <button className="btn btn-quiet" disabled={!canStart} onClick={() => selectTurn?.(item.id)}>Read reply and sources</button>
        </li>)}</ol>
      </section>}

      <details className="vincent-write" open={state.error?.stage === 'capture' || unsent ? true : undefined}>
        <summary>Prefer typing?</summary>
        <form onSubmit={submitDraft}>
          <label htmlFor="vincent-draft">Your question in English</label>
          <textarea id="vincent-draft" value={draft} onChange={(event) => setDraft(event.target.value)}
            rows={3} disabled={!enabled} placeholder="What would you like to ask?" aria-invalid={questionLength > 500}
            aria-describedby="vincent-draft-help" />
          <p id="vincent-draft-help" className={questionLength > 500 ? 'error small' : 'muted small'}>
            {questionLength > 500 ? 'Please keep your question to 500 characters.' : 'You can use up to 500 characters.'}
          </p>
          <button className="btn" disabled={!canStart || questionLength === 0 || questionLength > 500}>Send question</button>
        </form>
      </details>

      <footer className="vincent-recording-controls">
        <div className="vincent-record-actions">
          <button className={`btn vincent-record-button${recording ? ' is-recording' : ''}`} disabled={recording ? !enabled : !canStart}
            onClick={() => interruptGreeting(recording ? actions.stopRecording : actions.startRecording)}>
            <span aria-hidden="true">{recording ? '■' : '●'}</span> {recording ? 'Stop recording' : 'Start recording'}
          </button>
          {canStop && <button className="btn btn-quiet" disabled={!enabled} onClick={() => interruptGreeting(actions.stopPlayback)}>
            {state.welcome ? 'Stop greeting' : 'Stop playback'}
          </button>}
        </div>
        <p>{recording ? 'Tap Stop recording when you’re done.' : processing ? 'Your question is being processed.'
          : playing ? 'Stop the reply or start a new recording.' : 'One voice at a time. Speak for up to 20 seconds.'}</p>
      </footer>
    </main>
  );
}
