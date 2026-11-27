import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { JSDOM } from 'jsdom';

// Mounts the real useVincentVoice hook under React StrictMode, which runs the
// effect cleanup once right after the first mount (the demo build uses it).
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://localhost:8080' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'Event']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { act, createElement, StrictMode } = await import('react');
const { createRoot } = await import('react-dom/client');

let hook;
if (process.env.VOICE_HOOK_MODULE) {
  hook = await import(process.env.VOICE_HOOK_MODULE);
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({
    configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  });
  after(() => vite.close());
  hook = await vite.ssrLoadModule('/src/voice/useVincentVoice.ts');
}
after(() => dom.window.close());
const { useVincentVoice } = hook;

const CONVERSATION = '7b6cd85e-50fb-47df-a567-13cf87751e21';

function fakeBrowser() {
  const h = { micRequests: 0, tracks: [], audios: 0 };
  class FakeRecorder {
    static isTypeSupported() { return true; }
    constructor() { this.state = 'inactive'; this.mimeType = 'audio/mp4'; }
    addEventListener() {}
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; }
  }
  h.env = {
    getUserMedia: async () => {
      h.micRequests++;
      const track = { stopped: false, stop() { this.stopped = true; } };
      h.tracks.push(track);
      return { getTracks: () => [track] };
    },
    MediaRecorder: FakeRecorder,
    createAudio: () => {
      h.audios++;
      return { addEventListener() {}, play: () => Promise.resolve(), pause() {}, removeAttribute() {}, src: '', currentTime: 0 };
    },
    fetch: async () => { throw new Error('no network in this test'); },
    randomUUID: () => '00000000-0000-4000-8000-000000000001',
  };
  return h;
}

function Probe({ conversationId, env, expose }) {
  const { voice, snapshot } = useVincentVoice(conversationId, {
    submit: async () => ({ turnId: 'unused' }), revision: () => 0, env,
  });
  expose.voice = voice;
  return createElement('button', { 'data-phase': snapshot.phase, onClick: () => { void voice.startRecording(); } }, 'Record');
}

async function flush() {
  for (let i = 0; i < 4; i++) await act(async () => { await setImmediate(); });
}

test('under StrictMode the first Record tap opens the microphone exactly once', async () => {
  const h = fakeBrowser();
  const expose = {};
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(createElement(StrictMode, null, createElement(Probe, { conversationId: CONVERSATION, env: h.env, expose }))));
  await flush();
  assert.equal(expose.voice.isDisposed, false, 'the rendered controller is live after StrictMode remounts');

  const button = container.querySelector('button');
  await act(async () => { button.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  await flush();
  assert.equal(h.micRequests, 1, 'one tap, one microphone request');
  assert.equal(container.querySelector('button').dataset.phase, 'recording');

  // Leaving the page releases the microphone.
  await act(async () => root.unmount());
  assert.ok(h.tracks.length === 1 && h.tracks[0].stopped, 'microphone released on unmount');
  assert.equal(expose.voice.isDisposed, true);
  container.remove();
});

test('a new conversation ID gets a new controller and releases the old one', async () => {
  const h = fakeBrowser();
  const expose = {};
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  const render = (id) => act(async () => root.render(createElement(StrictMode, null, createElement(Probe, { conversationId: id, env: h.env, expose }))));
  await render(CONVERSATION);
  await flush();
  const first = expose.voice;
  await act(async () => { container.querySelector('button').dispatchEvent(new window.MouseEvent('click', { bubbles: true })); });
  await flush();
  await render('9c3d1a40-7d0e-4c4e-9f53-5a1e2b3c4d5e');
  await flush();
  assert.notEqual(expose.voice, first);
  assert.equal(first.isDisposed, true);
  assert.equal(expose.voice.isDisposed, false);
  assert.equal(expose.voice.conversationId, '9c3d1a40-7d0e-4c4e-9f53-5a1e2b3c4d5e');
  assert.ok(h.tracks[0].stopped, 'the old recording stopped');
  await act(async () => root.unmount());
  container.remove();
});
