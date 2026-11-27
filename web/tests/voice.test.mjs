import assert from 'node:assert/strict';
import { after, test } from 'node:test';

// Loads src/voice/controller.ts through Vite like the other tests. VOICE_MODULE
// can point at a prebuilt ESM bundle when Vite is not installed.
let voiceModule;
if (process.env.VOICE_MODULE) {
  voiceModule = await import(process.env.VOICE_MODULE);
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({
    configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  });
  after(() => vite.close());
  voiceModule = await vite.ssrLoadModule('/src/voice/controller.ts');
}
const { createVoiceController, uploadType, captureType, MAX_RECORDING_MS, MEDIA_TIMEOUT_MS } = voiceModule;

const CONVERSATION = '7b6cd85e-50fb-47df-a567-13cf87751e21';
const TURN = 'd4fc3fee-17cb-44c5-b44e-b64910511600';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };

class FakeAudio {
  constructor() { this.listeners = {}; this.src = ''; this.currentTime = 0; this.paused = true; this.mode = 'ok'; this.plays = []; this.pauses = 0; }
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  emit(name) { (this.listeners[name] ?? []).forEach((fn) => fn()); }
  play() {
    this.plays.push(this.src);
    if (this.src.startsWith('data:')) return Promise.resolve(); // the iOS unlock clip
    if (this.mode === 'blocked') return Promise.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' }));
    this.paused = false;
    queueMicrotask(() => this.emit('playing'));
    return Promise.resolve();
  }
  pause() { this.paused = true; this.pauses++; }
}

function harness({ supported = ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4'], micError = null, routes = {}, submit, welcomeUrl } = {}) {
  const h = { now: 0, timers: [], requests: [], tracks: [], submits: [], messages: [], revoked: [], urls: 0, revision: 1, ids: 0 };
  h.audio = new FakeAudio();
  class FakeRecorder {
    static isTypeSupported(type) { return supported.includes(type); }
    constructor(stream, options) { this.stream = stream; this.mimeType = options?.mimeType ?? ''; this.state = 'inactive'; this.listeners = {}; h.recorder = this; }
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      setTimeout(() => {
        (this.listeners.dataavailable ?? []).forEach((fn) => fn({ data: new Blob([h.clipBytes ?? 'audio-bytes']) }));
        (this.listeners.stop ?? []).forEach((fn) => fn());
      }, 0);
    }
  }
  const respond = async (path, init) => {
    h.requests.push({ path, method: init.method, body: init.body, headers: init.headers ?? {}, credentials: init.credentials });
    const kind = path.endsWith('/transcriptions') ? 'transcribe' : 'speech';
    const queue = routes[kind] ?? [];
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (typeof next === 'function') return next(init);
    if (next) return next;
    return kind === 'transcribe'
      ? Response.json({ text: 'What do you mean by that?', language: 'en' })
      : new Response(new Blob(['ID3-mp3'], { type: 'audio/mpeg' }), { headers: { 'Content-Type': 'audio/mpeg' } });
  };
  h.controller = createVoiceController(CONVERSATION, {
    welcomeUrl,
    submit: async (message) => {
      h.messages.push(message);
      if (submit) return submit(message);
      h.submits.push(message.question);
      return { turnId: TURN };
    },
    revision: () => h.revision,
    env: {
      randomUUID: () => `00000000-0000-4000-8000-${String(++h.ids).padStart(12, '0')}`,
      fetch: respond,
      getUserMedia: async (constraints) => {
        h.constraints = constraints;
        if (micError) throw Object.assign(new Error('mic'), { name: micError });
        const track = { stopped: false, stop() { this.stopped = true; } };
        h.tracks.push(track);
        return { getTracks: () => [track] };
      },
      MediaRecorder: FakeRecorder,
      createAudio: () => h.audio,
      createObjectURL: () => `blob:answer-${++h.urls}`,
      revokeObjectURL: (url) => h.revoked.push(url),
      setTimeout: (fn, ms) => { const timer = { fn, ms, at: h.now + ms }; h.timers.push(timer); return timer; },
      clearTimeout: (timer) => { h.timers = h.timers.filter((t) => t !== timer); },
      now: () => h.now,
    },
  });
  h.phases = [];
  h.controller.subscribe(() => h.phases.push(h.controller.getSnapshot().phase));
  h.advance = (ms) => {
    h.now += ms;
    for (const timer of h.timers.filter((t) => t.at <= h.now)) {
      h.timers = h.timers.filter((t) => t !== timer);
      timer.fn();
    }
  };
  h.snap = () => h.controller.getSnapshot();
  return h;
}

async function recordQuestion(h, ms = 2000) {
  await h.controller.startRecording();
  assert.equal(h.snap().phase, 'recording');
  h.advance(ms);
  h.controller.stopRecording();
  await settle();
}

test('one recording becomes one question, then the committed answer is spoken', async () => {
  const h = harness();
  await recordQuestion(h);
  assert.deepEqual(h.constraints.audio.echoCancellation, true);
  assert.ok(h.tracks.every((t) => t.stopped), 'the microphone is released after stop');

  const [upload, speech] = h.requests;
  assert.equal(upload.path, `/api/conversations/${CONVERSATION}/transcriptions`);
  assert.equal(upload.method, 'POST');
  assert.equal(upload.headers['Content-Type'], 'audio/mp4;codecs=mp4a.40.2');
  assert.ok(upload.body instanceof Blob);
  assert.equal(upload.credentials, 'same-origin');
  assert.deepEqual(h.submits, ['What do you mean by that?']);
  assert.equal(speech.path, `/api/conversations/${CONVERSATION}/turns/${TURN}/speech`);
  assert.equal(speech.body, undefined, 'the speech request has no body');
  assert.equal(speech.headers['Content-Type'], undefined);

  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.snap().turnId, TURN);
  assert.equal(h.snap().transcript, 'What do you mean by that?');
  assert.deepEqual(h.phases.filter((p, i, all) => p !== all[i - 1]),
    ['recording', 'transcribing', 'generating', 'preparing_audio', 'playing']);

  h.audio.emit('ended');
  assert.equal(h.snap().phase, 'ready');
  assert.equal(h.snap().turnId, TURN, 'the turn stays available for replay');
});

test('replay plays the local copy without asking the server again', async () => {
  const h = harness();
  await recordQuestion(h);
  h.controller.stopPlayback();
  assert.equal(h.snap().phase, 'ready');
  await h.controller.playTurn(TURN);
  await settle();
  assert.equal(h.requests.filter((r) => r.path.endsWith('/speech')).length, 1);
  assert.equal(h.snap().phase, 'playing');
});

test('recording again stops the answer before opening the microphone', async () => {
  const h = harness();
  await recordQuestion(h);
  assert.equal(h.snap().phase, 'playing');
  const pausesBefore = h.audio.pauses;
  await h.controller.startRecording();
  assert.ok(h.audio.pauses > pausesBefore, 'speech stopped');
  assert.equal(h.snap().phase, 'recording');
  assert.equal(h.snap().turnId, null);
  h.audio.emit('playing'); // a late event from the old answer is ignored
  assert.equal(h.snap().phase, 'recording');
});

test('desktop browsers upload WebM with Opus', async () => {
  assert.equal(captureType({ isTypeSupported: (t) => t.startsWith('audio/webm') }), 'audio/webm;codecs=opus');
  assert.equal(captureType(null), null);
  assert.equal(uploadType('audio/webm; codecs=opus', ''), 'audio/webm;codecs=opus');
  assert.equal(uploadType('video/webm;codecs=opus', ''), 'audio/webm;codecs=opus');
  assert.equal(uploadType('', 'audio/mp4'), 'audio/mp4');
  assert.equal(uploadType('audio/mp4;codecs=avc1,mp4a.40.2', ''), 'audio/mp4;codecs=avc1,mp4a.40.2');
  const h = harness({ supported: ['audio/webm;codecs=opus'] });
  await recordQuestion(h);
  assert.equal(h.requests[0].headers['Content-Type'], 'audio/webm;codecs=opus');
});

test('microphone denial offers typed input and sends nothing', async () => {
  const h = harness({ micError: 'NotAllowedError' });
  await h.controller.startRecording();
  assert.equal(h.snap().phase, 'error');
  assert.deepEqual({ ...h.snap().error, message: undefined },
    { stage: 'capture', code: 'microphone_denied', retryable: false, message: undefined });
  assert.match(h.snap().error.message, /type your question/);
  assert.equal(h.requests.length, 0);
});

test('a too-short recording is rejected locally', async () => {
  const h = harness();
  await recordQuestion(h, 100);
  assert.equal(h.snap().error.code, 'recording_empty');
  assert.equal(h.requests.length, 0);
});

test('recording stops by itself at 20 seconds', async () => {
  const h = harness();
  await h.controller.startRecording();
  h.advance(MAX_RECORDING_MS);
  await settle();
  assert.ok(h.tracks[0].stopped);
  assert.equal(h.requests[0].path.endsWith('/transcriptions'), true);
  assert.ok(h.snap().recordingSeconds <= 20);
});

test('a retryable transcription failure keeps the clip and retries only that stage', async () => {
  const busy = () => Response.json({ request_id: 'r', error: { code: 'voice_busy', message: 'Voice is busy.', retryable: true } }, { status: 429 });
  const h = harness({ routes: { transcribe: [busy, Response.json({ text: 'Why?', language: 'en' })] } });
  await recordQuestion(h);
  assert.equal(h.snap().phase, 'error');
  assert.deepEqual(h.snap().error, { stage: 'transcription', code: 'voice_busy', message: 'Voice is busy.', retryable: true });
  assert.equal(h.submits.length, 0);
  const firstClip = h.requests[0].body;
  await h.controller.retryTranscription();
  await settle();
  assert.equal(h.requests[1].body, firstClip, 'the same clip is resent');
  assert.deepEqual(h.submits, ['Why?']);
});

test('a non-retryable recognition result asks for a new recording', async () => {
  const noSpeech = () => Response.json({ request_id: 'r', error: { code: 'no_speech', message: 'No clear speech was recognized. Record another question.', retryable: false } }, { status: 422 });
  const h = harness({ routes: { transcribe: [noSpeech] } });
  await recordQuestion(h);
  assert.equal(h.snap().error.code, 'no_speech');
  await h.controller.retryTranscription();
  assert.equal(h.requests.length, 1, 'no retry for a clip without speech');
  assert.equal(h.submits.length, 0);
});

test('a speech failure keeps the answer and can be retried', async () => {
  const down = () => Response.json({ request_id: 'r', error: { code: 'speech_unavailable', message: 'The spoken answer is unavailable right now. You can still read it.', retryable: true } }, { status: 503 });
  const h = harness({ routes: { speech: [down, new Response(new Blob(['ID3'], { type: 'audio/mpeg' }))] } });
  await recordQuestion(h);
  assert.equal(h.snap().phase, 'error');
  assert.equal(h.snap().error.stage, 'speech');
  assert.equal(h.snap().turnId, TURN, 'the page can still show and retry this turn');
  assert.equal(h.submits.length, 1);
  await h.controller.playTurn(TURN);
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.submits.length, 1, 'retrying audio never resubmits the question');
});

test('blocked autoplay waits for a Play tap and then uses the local copy', async () => {
  const h = harness();
  h.audio.mode = 'blocked';
  await recordQuestion(h);
  assert.equal(h.snap().phase, 'awaiting_play');
  h.audio.mode = 'ok';
  await h.controller.playTurn(TURN);
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.requests.filter((r) => r.path.endsWith('/speech')).length, 1);
});

test('a media request that hangs times out as voice_timeout', async () => {
  const h = harness({ routes: { transcribe: [(init) => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))))] } });
  await h.controller.startRecording();
  h.advance(2000);
  h.controller.stopRecording();
  await settle();
  h.advance(MEDIA_TIMEOUT_MS);
  await settle();
  assert.deepEqual({ ...h.snap().error, message: undefined }, { stage: 'transcription', code: 'voice_timeout', retryable: true, message: undefined });
});

test('a message failure is reported without speaking anything', async () => {
  const h = harness({ submit: async () => { throw Object.assign(new Error('Vincent is busy. Try again.'), { code: 'conversation_busy', retryable: true }); } });
  await recordQuestion(h);
  assert.deepEqual(h.snap().error, { stage: 'message', code: 'conversation_busy', message: 'Vincent is busy. Try again.', retryable: true });
  assert.equal(h.requests.filter((r) => r.path.endsWith('/speech')).length, 0);
});

test('leaving the page drops a late transcript and releases everything', async () => {
  let release;
  const h = harness({ routes: { transcribe: [() => new Promise((resolve) => { release = resolve; })] } });
  await h.controller.startRecording();
  h.advance(2000);
  h.controller.stopRecording();
  await settle();
  const seen = h.phases.length;
  h.controller.dispose();
  release(Response.json({ text: 'Late question', language: 'en' }));
  await settle();
  assert.equal(h.submits.length, 0, 'a late transcript is never submitted');
  assert.equal(h.phases.length, seen, 'no updates after dispose');
  assert.ok(h.tracks.every((t) => t.stopped));
});

test('the greeting plays once and recording cancels it', async () => {
  const h = harness({ welcomeUrl: '/vincent/welcome.mp3' });
  h.controller.playWelcome();
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.snap().welcome, true);
  assert.equal(h.snap().turnId, null);
  await h.controller.startRecording();
  assert.equal(h.snap().phase, 'recording');
  assert.equal(h.snap().welcome, false);
  assert.ok(h.audio.paused);

  const blocked = harness({ welcomeUrl: '/vincent/welcome.mp3' });
  blocked.audio.mode = 'blocked';
  blocked.controller.playWelcome();
  await settle();
  assert.equal(blocked.snap().phase, 'ready', 'a blocked greeting is skipped quietly');
});

// ---- recording-to-message handoff ---------------------------------------------------

test('a recording is bound to the message ID and revision at the Record tap', async () => {
  let release;
  const h = harness({ routes: { transcribe: [() => new Promise((resolve) => { release = resolve; })] } });
  h.revision = 3;
  await h.controller.startRecording();
  h.revision = 4; // the conversation advances while the visitor is speaking
  h.advance(2000);
  h.controller.stopRecording();
  await settle();
  release(Response.json({ text: 'Why?', language: 'en' }));
  await settle();
  assert.deepEqual(h.messages, [{
    question: 'Why?', clientMessageId: '00000000-0000-4000-8000-000000000001', expectedRevision: 3, languageOverride: null,
  }]);
});

test('a lost response is retried with the identical logical message', async () => {
  let calls = 0;
  const h = harness({
    submit: async () => {
      if (++calls === 1) throw Object.assign(new Error('The connection dropped. Try again.'), { code: null, retryable: true });
      return { turnId: TURN };
    },
  });
  await recordQuestion(h);
  assert.equal(h.snap().phase, 'error');
  assert.equal(h.snap().error.stage, 'message');
  assert.equal(h.snap().unsent, 'What do you mean by that?');
  await h.controller.retryMessage();
  await settle();
  assert.equal(h.messages.length, 2);
  assert.deepEqual(h.messages[1], h.messages[0], 'same ID, question, revision and override');
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.snap().unsent, null);
});

test('a revision conflict keeps the transcript for review and never resends it', async () => {
  const h = harness({
    submit: async () => { throw Object.assign(new Error('The conversation changed.'), { code: 'conversation_state_conflict', retryable: false }); },
  });
  await recordQuestion(h);
  assert.deepEqual({ ...h.snap().error, message: undefined }, { stage: 'message', code: 'conversation_state_conflict', retryable: false, message: undefined });
  assert.equal(h.snap().unsent, 'What do you mean by that?');
  assert.equal(h.snap().transcript, 'What do you mean by that?');
  await h.controller.retryMessage();
  assert.equal(h.messages.length, 1, 'no automatic resend after a conflict');
  assert.equal(h.requests.filter((r) => r.path.endsWith('/speech')).length, 0);
});

test('a fresh recording is a new logical question', async () => {
  const h = harness();
  await recordQuestion(h);
  h.revision = 2;
  await recordQuestion(h);
  assert.equal(h.messages.length, 2);
  assert.notEqual(h.messages[0].clientMessageId, h.messages[1].clientMessageId);
  assert.equal(h.messages[1].expectedRevision, 2);
});

// ---- the latest Play or Stop wins ---------------------------------------------------

const TURN_2 = 'd4fc3fee-17cb-44c5-b44e-b64910511601';
function deferredSpeech() {
  const pending = [];
  const route = () => new Promise((resolve) => pending.push(resolve));
  const reply = (index, label) => pending[index](new Response(new Blob([label], { type: 'audio/mpeg' })));
  return { route, reply, pending };
}

test('an older speech response that arrives last stays silent', async () => {
  const speech = deferredSpeech();
  const h = harness({ routes: { speech: [speech.route] } });
  const first = h.controller.playTurn(TURN);
  const second = h.controller.playTurn(TURN_2);
  await settle();
  assert.equal(speech.pending.length, 2);
  speech.reply(1, 'second'); // the selected answer arrives first
  await second;
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.snap().turnId, TURN_2);
  const playing = h.audio.src;
  const plays = h.audio.plays.length;
  speech.reply(0, 'first'); // then the older request finishes
  await first;
  await settle();
  assert.equal(h.audio.src, playing, 'the older clip never replaces the selected answer');
  assert.equal(h.audio.plays.length, plays, 'and is never played');
  assert.equal(h.snap().turnId, TURN_2);
  assert.equal(h.snap().phase, 'playing');
});

test('Stop while the answer is downloading keeps it silent', async () => {
  const speech = deferredSpeech();
  const h = harness({ routes: { speech: [speech.route] } });
  const play = h.controller.playTurn(TURN);
  await settle();
  assert.equal(h.snap().phase, 'preparing_audio');
  h.controller.stopPlayback();
  assert.equal(h.snap().phase, 'ready');
  speech.reply(0, 'late');
  await play;
  await settle();
  assert.equal(h.snap().phase, 'ready');
  assert.deepEqual(h.audio.plays.filter((src) => !src.startsWith('data:')), [], 'nothing was played');
  // The downloaded clip is kept, so Replay needs no new speech request.
  await h.controller.playTurn(TURN);
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(speech.pending.length, 1);
});

test('a speech failure for an older request does not replace the selected answer', async () => {
  const speech = deferredSpeech();
  const h = harness({ routes: { speech: [speech.route] } });
  const first = h.controller.playTurn(TURN);
  const second = h.controller.playTurn(TURN_2);
  await settle();
  speech.reply(1, 'second');
  await second;
  await settle();
  speech.pending[0](Response.json({ request_id: 'r', error: { code: 'speech_unavailable', message: 'Unavailable.', retryable: true } }, { status: 503 }));
  await first;
  await settle();
  assert.equal(h.snap().phase, 'playing');
  assert.equal(h.snap().error, null);
  assert.equal(h.snap().turnId, TURN_2);
});

test('constructing a controller has no side effects until it is used', () => {
  let created = 0;
  createVoiceController(CONVERSATION, {
    submit: async () => ({ turnId: TURN }), revision: () => 0,
    env: { createAudio: () => { created++; return new FakeAudio(); } },
  });
  assert.equal(created, 0);
});
