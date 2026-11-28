// Talk with Vincent voice controller (A → B handoff in docs/vincent-voice.md).
//
// One controller is bound to one Vincent conversation. The page calls
// playWelcome(), startRecording(), stopRecording(), retryTranscription(),
// stopPlayback(), playTurn(turnId) and dispose(), and renders getSnapshot().
// The controller owns the microphone and the single audio element, so two
// voices never play at once and the next recording never captures the answer.
//
// Text and citations always come from the committed turn, which the page reads
// from the chat client. The controller only reports which turn is speaking.
import { SILENT_MP3 } from './silence';
import { uuidv4 } from '../api/uuid';

export type VoicePhase =
  | 'ready' | 'recording' | 'transcribing' | 'generating' | 'preparing_audio' | 'playing' | 'awaiting_play' | 'error';

export type VoiceStage = 'capture' | 'transcription' | 'message' | 'speech' | 'playback';

export interface VoiceError {
  stage: VoiceStage;
  /** Human-readable English the page can show as is. */
  message: string;
  retryable: boolean;
  /** Public API error code, or a local capture code such as microphone_denied. */
  code: string | null;
}

export interface VoiceSnapshot {
  conversationId: string;
  phase: VoicePhase;
  /** What the system heard, shown before and after the question is sent. */
  transcript: string | null;
  /** The committed turn being prepared, played or offered for replay. */
  turnId: string | null;
  error: VoiceError | null;
  /** Seconds recorded so far, while phase is recording. */
  recordingSeconds: number;
  /** True while the opening greeting (not an answer) is playing. */
  welcome: boolean;
  /**
   * The recognized question that was not committed, after a message failure.
   * After conversation_state_conflict the page shows it for review and lets the
   * visitor send it again as a new message or record again.
   */
  unsent: string | null;
}

/**
 * One logical spoken question. The ID and revision are fixed when Record is
 * tapped, so a retry or history recovery sends exactly the same message.
 */
export interface VoiceMessage {
  question: string;
  clientMessageId: string;
  expectedRevision: number;
  languageOverride: 'en' | null;
}

export interface VoiceOptions {
  /**
   * Sends one transcribed question through the existing chat client as
   * {contract_version, client_message_id, question, language_override, expected_revision}
   * and resolves with the committed turn. If the response is lost, recover the
   * turn from history by clientMessageId before sending again. Reject with the
   * public error ({code, message, retryable}) to report a message failure.
   */
  submit: (message: VoiceMessage) => Promise<{ turnId: string }>;
  /** The conversation revision the visitor is answering, read when Record is tapped. */
  revision: () => number;
  /** Optional prerecorded greeting in the same voice. It is never a conversation turn. */
  welcomeUrl?: string;
  /** Test seam; defaults to the browser. */
  env?: Partial<VoiceEnvironment>;
}

export interface VoiceEnvironment {
  fetch: typeof fetch;
  getUserMedia: ((constraints: MediaStreamConstraints) => Promise<MediaStream>) | null;
  MediaRecorder: typeof MediaRecorder | null;
  createAudio: () => HTMLAudioElement;
  randomUUID: () => string;
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (url: string) => void;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (handle: unknown) => void;
  now: () => number;
}

/** Limits from the voice contract. */
export const MAX_RECORDING_MS = 20_000;
export const MEDIA_TIMEOUT_MS = 35_000;
const MIN_RECORDING_MS = 400;
const RECORDER_FINISH_MS = 3000;
const CACHED_ANSWERS = 6;

/** Preferred capture formats, in order; use the first supported by this browser. */
export const CAPTURE_TYPES = ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'];

class MediaFailure extends Error {
  constructor(public readonly failure: VoiceError) {
    super(failure.message);
  }
}

const failure = (stage: VoiceStage, message: string, retryable: boolean, code: string | null): VoiceError =>
  ({ stage, message, retryable, code });

function browserEnvironment(): VoiceEnvironment {
  const devices = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
  return {
    fetch: (...args) => fetch(...args),
    getUserMedia: devices?.getUserMedia ? (c) => devices.getUserMedia(c) : null,
    MediaRecorder: typeof MediaRecorder !== 'undefined' ? MediaRecorder : null,
    createAudio: () => new Audio(),
    randomUUID: uuidv4,
    createObjectURL: (blob) => URL.createObjectURL(blob),
    revokeObjectURL: (url) => URL.revokeObjectURL(url),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    now: () => (typeof performance !== 'undefined' ? performance.now() : Date.now()),
  };
}

/** Picks the first capture format this browser can record, or null. */
export function captureType(recorder: typeof MediaRecorder | null): string | null {
  if (!recorder) return null;
  if (typeof recorder.isTypeSupported !== 'function') return '';
  return CAPTURE_TYPES.find((type) => recorder.isTypeSupported(type)) ?? null;
}

/** Normalizes the recorder's type to one the server accepts, e.g. "audio/webm;codecs=opus". */
export function uploadType(recorded: string, negotiated: string): string {
  const type = (recorded || negotiated || '').replace(/\s+/g, '').toLowerCase();
  if (type.startsWith('audio/mp4') || type.startsWith('video/mp4')) {
    return type.includes('codecs=') ? type.replace('video/mp4', 'audio/mp4') : 'audio/mp4';
  }
  if (type.startsWith('audio/webm') || type.startsWith('video/webm')) {
    return type.includes('opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  }
  return type || 'audio/mp4';
}

type Playback = { token: number; kind: 'answer' | 'welcome'; turnId: string | null } | null;

export class VoiceController {
  private readonly env: VoiceEnvironment;
  private snapshot: VoiceSnapshot;
  private readonly listeners = new Set<() => void>();
  private element: HTMLAudioElement | null = null;
  private disposed = false;
  /** Bumped by each new capture and by dispose; stale async results check it and stop. */
  private epoch = 0;
  private playbackToken = 0;
  private playback: Playback = null;
  /** Bumped by every Play, Stop, Record and dispose. Only the latest request may start audio. */
  private playRequest = 0;
  /** The message bound to the current recording; resent unchanged by retryMessage(). */
  private message: VoiceMessage | null = null;
  private pendingQuestion = false;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private recordType = '';
  private recordStarted = 0;
  private recordStopped: number | null = null;
  private autoStop: unknown = null;
  private ticker: unknown = null;
  private recorderFinish: unknown = null;
  /** The last unsubmitted clip, kept only while transcription can be retried. */
  private clip: Blob | null = null;
  private bound: { clientMessageId: string; expectedRevision: number } | null = null;
  private readonly answers = new Map<string, string>(); // turnId -> object URL
  private readonly aborts = new Set<AbortController>();

  // The constructor has no side effects (React may construct and discard one).
  // The audio element is created on first use.
  constructor(readonly conversationId: string, private readonly options: VoiceOptions) {
    this.env = { ...browserEnvironment(), ...options.env };
    this.snapshot = {
      conversationId, phase: 'ready', transcript: null, turnId: null, error: null, recordingSeconds: 0, welcome: false, unsent: null,
    };
  }

  get isDisposed(): boolean {
    return this.disposed;
  }

  private get audio(): HTMLAudioElement {
    if (!this.element) {
      const element = this.env.createAudio();
      element.preload = 'auto';
      element.addEventListener('playing', () => this.onPlaying(element));
      element.addEventListener('ended', () => this.onEnded(element));
      element.addEventListener('error', () => this.onAudioError(element));
      this.element = element;
    }
    return this.element;
  }

  // ---- state for the page -----------------------------------------------------------

  getSnapshot = (): VoiceSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(patch: Partial<VoiceSnapshot>): void {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private fail(error: VoiceError): void {
    this.set({ phase: 'error', error, welcome: false });
  }

  // ---- welcome ------------------------------------------------------------------------

  /** Plays the greeting once per entry. A blocked autoplay is ignored: the page still works silently. */
  playWelcome(): void {
    const url = this.options.welcomeUrl;
    if (!url || this.disposed || this.snapshot.phase !== 'ready') return;
    const token = this.startPlayback('welcome', null, url);
    this.audio.play().catch(() => {
      if (this.playback?.token === token) {
        this.playback = null;
        this.set({ phase: 'ready', welcome: false });
      }
    });
  }

  // ---- recording ----------------------------------------------------------------------

  /** Typed questions use exactly the same identity, retry and speech path as recordings. */
  async submitText(question: string): Promise<void> {
    if (this.disposed || ['recording', 'transcribing', 'generating'].includes(this.snapshot.phase)) return;
    question = question.trim();
    if (!question || [...question].length > 500) return;
    this.stopAudio();
    this.playRequest++;
    this.unlockAudio();
    const epoch = ++this.epoch;
    this.clip = null;
    this.bound = null;
    this.message = { question, clientMessageId: this.env.randomUUID(), expectedRevision: this.options.revision(), languageOverride: null };
    this.pendingQuestion = true;
    this.set({ phase: 'generating', transcript: question, turnId: null, error: null, welcome: false, unsent: null });
    await this.ask(epoch);
  }

  /** Restore a failed request after refresh, without silently submitting it. */
  restoreMessage(message: VoiceMessage, error: VoiceError): void {
    if (this.disposed || this.pendingQuestion) return;
    this.message = error.retryable ? message : null;
    this.set({ phase: 'error', transcript: message.question, unsent: message.question, error });
  }

  /** A later history check found the original turn. Clear its error without autoplay. */
  restoreTurn(turnId: string, question: string): void {
    if (this.disposed || this.pendingQuestion) return;
    this.stopAudio();
    this.playRequest++;
    this.message = null;
    this.set({ phase: 'ready', transcript: question, turnId, unsent: null, error: null, welcome: false });
  }

  /** Call from the Record tap. Stops any speech first, then opens the microphone. */
  async startRecording(): Promise<void> {
    const phase = this.snapshot.phase;
    if (this.disposed || phase === 'recording' || phase === 'transcribing' || phase === 'generating') return;
    this.stopAudio();
    this.playRequest++;
    // Do not start even silent playback while acquiring or using the microphone.
    // If the later answer cannot autoplay, the existing Play action resumes it.
    const epoch = ++this.epoch;
    this.clip = null; // a new recording replaces an unsubmitted clip
    this.bound = null;
    this.message = null; // and is a new logical question
    let bound: { clientMessageId: string; expectedRevision: number };
    try {
      bound = { clientMessageId: this.env.randomUUID(), expectedRevision: this.options.revision() };
    } catch {
      this.fail(failure('capture', 'The conversation is not ready yet. Try again in a moment.', true, null));
      return;
    }
    const type = captureType(this.env.MediaRecorder);
    if (!this.env.getUserMedia) {
      this.fail(failure('capture', 'Voice needs the secure HTTPS page. You can type your question instead.', false, 'microphone_requires_https'));
      return;
    }
    if (type === null || !this.env.MediaRecorder) {
      this.fail(failure('capture', 'This browser cannot record audio here. You can type your question instead.', false, 'recorder_unsupported'));
      return;
    }
    let stream: MediaStream;
    try {
      stream = await this.env.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    } catch (error) {
      if (epoch !== this.epoch || this.disposed) return;
      const name = (error as { name?: string })?.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.fail(failure('capture',
          'Microphone access is blocked. In Safari, tap aA, then Website Settings, and allow the microphone. You can also type your question.',
          false, 'microphone_denied'));
      } else {
        this.fail(failure('capture', 'No microphone is available. You can type your question instead.', false, 'microphone_unavailable'));
      }
      return;
    }
    if (epoch !== this.epoch || this.disposed) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    let recorder: MediaRecorder;
    try {
      recorder = type ? new this.env.MediaRecorder(stream, { mimeType: type }) : new this.env.MediaRecorder(stream);
    } catch {
      stream.getTracks().forEach((track) => track.stop());
      this.fail(failure('capture', 'Recording could not start. You can type your question instead.', false, 'recorder_unsupported'));
      return;
    }
    this.stream = stream;
    this.recorder = recorder;
    this.chunks = [];
    this.recordType = type;
    recorder.addEventListener('dataavailable', (event: BlobEvent) => {
      if (epoch !== this.epoch || this.recorder !== recorder || this.disposed) return;
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    });
    recorder.addEventListener('stop', () => { void this.onRecorderStop(epoch, recorder); });
    recorder.addEventListener('error', () => this.failCapture(epoch, recorder));
    this.bound = bound;
    this.recordStopped = null;
    try {
      // Keep every fragment and the final tail; upload only after the stop event.
      recorder.start(1000);
    } catch {
      this.failCapture(epoch, recorder);
      return;
    }
    this.recordStarted = this.env.now();
    this.set({ phase: 'recording', error: null, transcript: null, turnId: null, recordingSeconds: 0, welcome: false, unsent: null });
    this.tick(epoch);
    this.autoStop = this.env.setTimeout(() => {
      if (epoch === this.epoch) this.stopRecording();
    }, MAX_RECORDING_MS);
  }

  private tick(epoch: number): void {
    this.ticker = this.env.setTimeout(() => {
      if (epoch !== this.epoch || this.snapshot.phase !== 'recording') return;
      this.set({ recordingSeconds: Math.min(MAX_RECORDING_MS, this.env.now() - this.recordStarted) / 1000 });
      this.tick(epoch);
    }, 250);
  }

  /** Call from the Stop tap (also runs automatically at 20 seconds). Releases the microphone. */
  stopRecording(): void {
    if (this.snapshot.phase !== 'recording' || !this.recorder) return;
    const recorder = this.recorder;
    const epoch = this.epoch;
    this.clearTimers();
    this.recordStopped = this.env.now();
    this.set({ phase: 'transcribing', recordingSeconds: (this.recordStopped - this.recordStarted) / 1000 });
    this.recorderFinish = this.env.setTimeout(() => this.failCapture(epoch, recorder), RECORDER_FINISH_MS);
    try {
      if (recorder.state !== 'inactive') recorder.stop();
      // An inactive recorder may still have its final data/stop events queued.
    } catch {
      this.failCapture(epoch, recorder);
    }
    // Keep the tracks alive until the recorder has delivered the final data.
  }

  private failCapture(epoch: number, recorder: MediaRecorder): void {
    if (epoch !== this.epoch || this.recorder !== recorder || this.disposed) return;
    this.clearTimers();
    this.recorder = null;
    try { if (recorder.state !== 'inactive') recorder.stop(); } catch { /* release below */ }
    this.releaseMicrophone();
    this.chunks = [];
    this.bound = null;
    this.fail(failure('capture', 'The recording did not finish correctly. Record your question again.', false, 'recording_failed'));
  }

  private async onRecorderStop(epoch: number, recorder: MediaRecorder): Promise<void> {
    if (epoch !== this.epoch || this.recorder !== recorder || this.disposed) return;
    this.clearTimers();
    this.recorder = null;
    this.releaseMicrophone();
    const elapsed = (this.recordStopped ?? this.env.now()) - this.recordStarted;
    const type = uploadType(recorder.mimeType, this.recordType);
    const clip = new Blob(this.chunks, { type });
    this.chunks = [];
    if (clip.size === 0 || elapsed < MIN_RECORDING_MS) {
      this.fail(failure('capture', 'That recording was too short. Hold Record and ask your question again.', false, 'recording_empty'));
      return;
    }
    this.clip = clip;
    await this.transcribe(epoch);
  }

  /** Re-sends the kept clip after a retryable transcription failure. */
  async retryTranscription(): Promise<void> {
    const error = this.snapshot.error;
    if (!this.clip || this.snapshot.phase !== 'error' || error?.stage !== 'transcription' || !error.retryable) return;
    this.set({ phase: 'transcribing', error: null });
    await this.transcribe(this.epoch);
  }

  private async transcribe(epoch: number): Promise<void> {
    const clip = this.clip;
    if (!clip) return;
    let text: string;
    try {
      const response = await this.media('transcription', `/api/conversations/${this.conversationId}/transcriptions`, {
        method: 'POST', body: clip, headers: { 'Content-Type': clip.type },
      });
      const body = (await response.json()) as { text?: unknown; language?: unknown };
      if (typeof body.text !== 'string' || body.text.trim() === '' || body.language !== 'en') {
        throw new MediaFailure(failure('transcription', 'Speech recognition returned an unexpected reply. Try again.', true, null));
      }
      text = body.text;
    } catch (error) {
      if (epoch !== this.epoch || this.disposed) return;
      this.fail(error instanceof MediaFailure ? error.failure : unexpected('transcription'));
      return;
    }
    if (epoch !== this.epoch || this.disposed || !this.bound) return; // never submit a late transcript
    this.clip = null;
    // The transcript becomes one logical message, bound to the Record tap.
    this.message = { question: text, ...this.bound, languageOverride: null };
    this.bound = null;
    this.set({ phase: 'generating', transcript: text, error: null, unsent: null });
    await this.ask(epoch);
  }

  /**
   * Resends the same logical message (same ID, question and revision) after a
   * retryable message failure such as a lost response. Not used after a
   * revision conflict: that question is kept in snapshot.unsent for review.
   */
  async retryMessage(): Promise<void> {
    const error = this.snapshot.error;
    if (!this.message || this.pendingQuestion || this.snapshot.phase !== 'error' || error?.stage !== 'message' || !error.retryable) return;
    this.set({ phase: 'generating', error: null, unsent: null });
    await this.ask(this.epoch);
  }

  private async ask(epoch: number): Promise<void> {
    const message = this.message;
    if (!message) return;
    let turnId: string;
    this.pendingQuestion = true;
    try {
      ({ turnId } = await this.options.submit({ ...message }));
    } catch (error) {
      if (epoch !== this.epoch || this.disposed) return;
      const e = error as { code?: unknown; retryable?: unknown; message?: unknown };
      const code = typeof e.code === 'string' ? e.code : null;
      const retryable = e.retryable === true;
      if (!retryable) this.message = null; // a retry would be rejected; the words stay in unsent
      this.fail(failure('message',
        typeof e.message === 'string' && e.message ? e.message : 'Your question could not be sent. Try again.',
        retryable, code));
      this.set({ unsent: message.question });
      return;
    } finally {
      this.pendingQuestion = false;
    }
    if (epoch !== this.epoch || this.disposed) return;
    this.message = null;
    await this.playTurn(turnId);
  }

  // ---- speaking -----------------------------------------------------------------------

  /**
   * Speaks a committed turn: fetches it once, then replays from the local copy.
   * Also the Play button after a blocked autoplay, and the retry after a speech failure.
   */
  async playTurn(turnId: string): Promise<void> {
    const phase = this.snapshot.phase;
    if (this.disposed || phase === 'recording' || phase === 'transcribing' || (phase === 'generating' && this.pendingQuestion)) return;
    this.stopAudio();
    // The latest Play or Stop wins: an older download that finishes later stays silent.
    const request = ++this.playRequest;
    let url = this.answers.get(turnId);
    if (!url) {
      this.set({ phase: 'preparing_audio', turnId, error: null, welcome: false });
      let audio: Blob;
      try {
        const response = await this.media('speech', `/api/conversations/${this.conversationId}/turns/${turnId}/speech`, { method: 'POST' });
        audio = await response.blob();
        if (audio.size === 0) throw new MediaFailure(failure('speech', 'The spoken answer is unavailable right now. You can still read it.', true, null));
      } catch (error) {
        if (request !== this.playRequest || this.disposed) return;
        this.fail(error instanceof MediaFailure ? error.failure : unexpected('speech'));
        this.set({ turnId });
        return;
      }
      if (this.disposed) return;
      // Keep the clip for a later Replay even if this request was superseded.
      url = this.answers.get(turnId) ?? this.env.createObjectURL(audio.type ? audio : new Blob([audio], { type: 'audio/mpeg' }));
      this.remember(turnId, url);
      if (request !== this.playRequest) return;
    }
    const token = this.startPlayback('answer', turnId, url);
    this.set({ phase: 'preparing_audio', turnId, error: null, welcome: false });
    try {
      await this.audio.play();
    } catch (error) {
      if (this.playback?.token !== token) return;
      if ((error as { name?: string })?.name === 'NotAllowedError') {
        this.set({ phase: 'awaiting_play' }); // the page shows Play, which calls playTurn again
      } else {
        this.playback = null;
        this.fail(failure('playback', 'The answer could not be played. Tap Play to try again.', true, null));
        this.set({ turnId });
      }
    }
  }

  /** Stops the answer or greeting. The answer and sources stay on screen; the page can offer replay. */
  stopPlayback(): void {
    const phase = this.snapshot.phase;
    const downloading = phase === 'preparing_audio' || phase === 'awaiting_play';
    if (!this.playback && !downloading) return;
    this.playRequest++; // a download still in flight must not start afterwards
    this.stopAudio();
    this.set({ phase: 'ready', welcome: false });
  }

  private startPlayback(kind: 'answer' | 'welcome', turnId: string | null, url: string): number {
    const token = ++this.playbackToken;
    this.playback = { token, kind, turnId };
    this.audio.src = url;
    this.audio.currentTime = 0;
    return token;
  }

  private onPlaying(element: HTMLAudioElement): void {
    if (!this.playback || element !== this.element) return;
    // "playing" comes from the media element, so captions start with the voice.
    this.set({ phase: 'playing', turnId: this.playback.turnId ?? this.snapshot.turnId, welcome: this.playback.kind === 'welcome' });
  }

  private onEnded(element: HTMLAudioElement): void {
    if (!this.playback || element !== this.element) return;
    this.playback = null;
    this.set({ phase: 'ready', welcome: false });
  }

  private onAudioError(element: HTMLAudioElement): void {
    const current = this.playback;
    if (!current || element !== this.element) return;
    this.playback = null;
    if (current.kind === 'welcome') {
      this.set({ phase: 'ready', welcome: false });
      return;
    }
    this.fail(failure('playback', 'The answer could not be played. Tap Play to try again.', true, null));
    this.set({ turnId: current.turnId });
  }

  private stopAudio(): void {
    this.playback = null;
    this.playbackToken++;
    try {
      this.element?.pause();
    } catch { /* not playing */ }
  }

  /** Plays a short silence on the shared element during a tap so iOS allows the answer later. */
  private unlockAudio(): void {
    if (this.playback) return;
    try {
      this.audio.src = SILENT_MP3;
      const attempt = this.audio.play();
      if (attempt && typeof attempt.catch === 'function') attempt.catch(() => {});
    } catch { /* best effort */ }
  }

  private remember(turnId: string, url: string): void {
    this.answers.set(turnId, url);
    while (this.answers.size > CACHED_ANSWERS) {
      const [oldest, oldUrl] = this.answers.entries().next().value as [string, string];
      this.answers.delete(oldest);
      this.env.revokeObjectURL(oldUrl);
    }
  }

  // ---- transport ----------------------------------------------------------------------

  private async media(stage: VoiceStage, path: string, init: RequestInit): Promise<Response> {
    const abort = new AbortController();
    this.aborts.add(abort);
    const timer = this.env.setTimeout(() => abort.abort(), MEDIA_TIMEOUT_MS);
    try {
      let response: Response;
      try {
        response = await this.env.fetch(path, { ...init, credentials: 'same-origin', signal: abort.signal });
      } catch {
        throw new MediaFailure(abort.signal.aborted && !this.disposed
          ? failure(stage, 'The voice service took too long. Try again.', true, 'voice_timeout')
          : failure(stage, 'The connection dropped. Check your network and try again.', true, null));
      }
      if (response.ok) return response;
      let body: { error?: { code?: unknown; message?: unknown; retryable?: unknown } } = {};
      try {
        body = await response.json();
      } catch { /* not the JSON envelope */ }
      const code = typeof body.error?.code === 'string' ? body.error.code : null;
      const message = typeof body.error?.message === 'string' && body.error.message
        ? body.error.message
        : stage === 'speech' ? 'The spoken answer is unavailable right now. You can still read it.' : 'Speech recognition failed. Try again.';
      throw new MediaFailure(failure(stage, message, body.error?.retryable === true, code));
    } finally {
      this.env.clearTimeout(timer);
      this.aborts.delete(abort);
    }
  }

  // ---- cleanup ------------------------------------------------------------------------

  private clearTimers(): void {
    if (this.autoStop !== null) this.env.clearTimeout(this.autoStop);
    if (this.ticker !== null) this.env.clearTimeout(this.ticker);
    if (this.recorderFinish !== null) this.env.clearTimeout(this.recorderFinish);
    this.autoStop = this.ticker = this.recorderFinish = null;
  }

  private releaseMicrophone(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  /** Call when leaving the page: stops capture and speech and drops every late result. */
  dispose(): void {
    if (this.disposed) return;
    this.epoch++;
    this.playRequest++;
    this.clearTimers();
    try {
      if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    } catch { /* already stopped */ }
    this.recorder = null;
    this.releaseMicrophone();
    this.stopAudio();
    this.aborts.forEach((abort) => abort.abort());
    this.answers.forEach((url) => this.env.revokeObjectURL(url));
    this.answers.clear();
    this.clip = null;
    this.bound = null;
    this.message = null;
    if (this.element) this.element.removeAttribute?.('src');
    this.listeners.clear();
    this.disposed = true;
  }
}

function unexpected(stage: VoiceStage): VoiceError {
  return failure(stage, stage === 'speech'
    ? 'The spoken answer is unavailable right now. You can still read it.'
    : 'Speech recognition failed. Try again.', true, null);
}

export function createVoiceController(conversationId: string, options: VoiceOptions): VoiceController {
  return new VoiceController(conversationId, options);
}
