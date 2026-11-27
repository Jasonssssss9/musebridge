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
}

export interface VoiceOptions {
  /**
   * Sends one transcribed question through the existing chat client, with one
   * client_message_id and the current expected_revision, and resolves with the
   * committed turn (after history recovery if the response was lost). The chat
   * client keeps its own retry rules; reject to report a message failure.
   */
  submit: (question: string) => Promise<{ turnId: string }>;
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
const CACHED_ANSWERS = 6;

/** Preferred capture formats, in order. Safari records MP4/AAC; Chrome and Firefox WebM/Opus. */
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
  private readonly audio: HTMLAudioElement;
  private disposed = false;
  /** Bumped by each new capture and by dispose; stale async results check it and stop. */
  private epoch = 0;
  private playbackToken = 0;
  private playback: Playback = null;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private recordType = '';
  private recordStarted = 0;
  private autoStop: unknown = null;
  private ticker: unknown = null;
  /** The last unsubmitted clip, kept only while transcription can be retried. */
  private clip: Blob | null = null;
  private readonly answers = new Map<string, string>(); // turnId -> object URL
  private readonly aborts = new Set<AbortController>();

  constructor(private readonly conversationId: string, private readonly options: VoiceOptions) {
    this.env = { ...browserEnvironment(), ...options.env };
    this.snapshot = {
      conversationId, phase: 'ready', transcript: null, turnId: null, error: null, recordingSeconds: 0, welcome: false,
    };
    this.audio = this.env.createAudio();
    this.audio.preload = 'auto';
    this.audio.addEventListener('playing', () => this.onPlaying());
    this.audio.addEventListener('ended', () => this.onEnded());
    this.audio.addEventListener('error', () => this.onAudioError());
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

  /** Call from the Record tap. Stops any speech first, then opens the microphone. */
  async startRecording(): Promise<void> {
    const phase = this.snapshot.phase;
    if (this.disposed || phase === 'recording' || phase === 'transcribing' || phase === 'generating') return;
    this.stopAudio();
    this.unlockAudio();
    const epoch = ++this.epoch;
    this.clip = null; // a new recording replaces an unsubmitted clip
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
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    });
    recorder.addEventListener('stop', () => { void this.onRecorderStop(epoch); });
    recorder.start();
    this.recordStarted = this.env.now();
    this.set({ phase: 'recording', error: null, transcript: null, turnId: null, recordingSeconds: 0, welcome: false });
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
    this.unlockAudio(); // still inside the visitor's tap
    this.clearTimers();
    this.set({ phase: 'transcribing', recordingSeconds: (this.env.now() - this.recordStarted) / 1000 });
    try {
      if (this.recorder.state !== 'inactive') this.recorder.stop();
      else void this.onRecorderStop(this.epoch);
    } catch {
      void this.onRecorderStop(this.epoch);
    }
    this.releaseMicrophone();
  }

  private async onRecorderStop(epoch: number): Promise<void> {
    if (epoch !== this.epoch || this.disposed) return;
    const recorder = this.recorder;
    this.recorder = null;
    this.releaseMicrophone();
    const elapsed = this.env.now() - this.recordStarted;
    const type = uploadType(recorder?.mimeType ?? '', this.recordType);
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
    if (epoch !== this.epoch || this.disposed) return; // never submit a late transcript
    this.clip = null;
    this.set({ phase: 'generating', transcript: text, error: null });
    await this.ask(epoch, text);
  }

  private async ask(epoch: number, question: string): Promise<void> {
    let turnId: string;
    try {
      ({ turnId } = await this.options.submit(question));
    } catch (error) {
      if (epoch !== this.epoch || this.disposed) return;
      const e = error as { code?: unknown; retryable?: unknown; message?: unknown };
      this.fail(failure('message',
        typeof e.message === 'string' && e.message ? e.message : 'Your question could not be sent. Try again.',
        e.retryable === true, typeof e.code === 'string' ? e.code : null));
      return;
    }
    if (epoch !== this.epoch || this.disposed) return;
    await this.playTurn(turnId);
  }

  // ---- speaking -----------------------------------------------------------------------

  /**
   * Speaks a committed turn: fetches it once, then replays from the local copy.
   * Also the Play button after a blocked autoplay, and the retry after a speech failure.
   */
  async playTurn(turnId: string): Promise<void> {
    if (this.disposed || this.snapshot.phase === 'recording' || this.snapshot.phase === 'transcribing') return;
    this.stopAudio();
    let url = this.answers.get(turnId);
    if (!url) {
      const epoch = this.epoch;
      this.set({ phase: 'preparing_audio', turnId, error: null, welcome: false });
      let audio: Blob;
      try {
        const response = await this.media('speech', `/api/conversations/${this.conversationId}/turns/${turnId}/speech`, { method: 'POST' });
        audio = await response.blob();
        if (audio.size === 0) throw new MediaFailure(failure('speech', 'The spoken answer is unavailable right now. You can still read it.', true, null));
      } catch (error) {
        if (epoch !== this.epoch || this.disposed) return;
        this.fail(error instanceof MediaFailure ? error.failure : unexpected('speech'));
        this.set({ turnId });
        return;
      }
      if (epoch !== this.epoch || this.disposed) return;
      url = this.env.createObjectURL(audio.type ? audio : new Blob([audio], { type: 'audio/mpeg' }));
      this.remember(turnId, url);
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
    if (!this.playback) return;
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

  private onPlaying(): void {
    if (!this.playback) return;
    // "playing" comes from the media element, so captions start with the voice.
    this.set({ phase: 'playing', turnId: this.playback.turnId ?? this.snapshot.turnId, welcome: this.playback.kind === 'welcome' });
  }

  private onEnded(): void {
    if (!this.playback) return;
    this.playback = null;
    this.set({ phase: 'ready', welcome: false });
  }

  private onAudioError(): void {
    const current = this.playback;
    if (!current) return;
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
      this.audio.pause();
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
    this.autoStop = this.ticker = null;
  }

  private releaseMicrophone(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  /** Call when leaving the page: stops capture and speech and drops every late result. */
  dispose(): void {
    if (this.disposed) return;
    this.epoch++;
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
