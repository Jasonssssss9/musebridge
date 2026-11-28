import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { webcrypto } from 'node:crypto';
import { createServer } from 'vite';
import { JSDOM } from 'jsdom';
import { completed, ID, emptyHistory, savedHistory, pendingHistory, message } from './vincent-fixtures.mjs';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://localhost:8080/a/getty-103jnh/vincent' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLTextAreaElement', 'Event', 'MouseEvent']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
}
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLMediaElement.prototype.play = async () => {};
dom.window.HTMLMediaElement.prototype.pause = () => {};
const { act, createElement, StrictMode } = await import('react');
const { createRoot } = await import('react-dom/client');
const vite = await createServer({ configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
const { default: Live } = await vite.ssrLoadModule('/src/vincent/Live.tsx');
let root, container;
async function flush() { for (let i = 0; i < 6; i++) await act(async () => { await setImmediate(); }); }
async function render() {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  await act(async () => root.render(createElement(StrictMode, null, createElement(Live)))); await flush();
}
async function leave() {
  if (root) await act(async () => root.unmount());
  root = null; container?.remove();
}
afterEach(leave);
after(async () => { await vite.close(); dom.window.close(); });
const button = (label) => [...container.querySelectorAll('button')].find((b) => b.textContent.trim().includes(label));
async function click(label) { assert.ok(button(label), label); await act(async () => button(label).click()); await flush(); }
async function ask(question) {
  await act(async () => {
    const field = container.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(field, question);
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  await flush();
}
const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
function harness(t, initial = emptyHistory()) {
  const h = { current: structuredClone(initial), calls: [], audios: [], tracks: [], micRequests: 0, now: 0, speechFails: false, next: null };
  class FakeAudio {
    constructor() { this.listeners = {}; this.src = ''; this.paused = true; h.audios.push(this); }
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    emit(name) { (this.listeners[name] ?? []).forEach((fn) => fn()); }
    play() {
      if (this.src.startsWith('blob:') && h.blockAutoplay) return Promise.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' }));
      this.paused = false; return Promise.resolve();
    }
    pause() { this.paused = true; }
    removeAttribute() {}
  }
  class FakeRecorder {
    static isTypeSupported() { return true; }
    constructor() { this.state = 'inactive'; this.mimeType = 'audio/mp4'; this.listeners = {}; }
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive'; queueMicrotask(() => {
        (this.listeners.dataavailable ?? []).forEach((fn) => fn({ data: new Blob(['test recording'], { type: 'audio/mp4' }) }));
        (this.listeners.stop ?? []).forEach((fn) => fn());
      });
    }
  }
  Object.defineProperty(globalThis, 'Audio', { configurable: true, value: FakeAudio });
  Object.defineProperty(globalThis, 'MediaRecorder', { configurable: true, value: FakeRecorder });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia: async () => {
      h.micRequests++;
      if (h.denyMic) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' });
      const track = { stopped: false, stop() { this.stopped = true; } }; h.tracks.push(track);
      return { getTracks: () => [track] };
    },
  } });
  t.mock.method(performance, 'now', () => h.now);
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : init.body;
    h.calls.push({ url, method: init.method, body, signal: init.signal });
    if (url === '/api/conversations') return Response.json({ conversation: h.current.conversation, created: true });
    assert.ok(url.startsWith(`/api/conversations/${ID}/`));
    if (url.endsWith('/transcriptions')) return Response.json({ text: 'Why is that?', language: 'en' });
    if (url.endsWith('/speech')) {
      if (h.speechFails) return Response.json({ error: { code: 'speech_unavailable', message: 'Audio is unavailable.', retryable: true } }, { status: 503 });
      return new Response(new Blob(['fixture audio'], { type: 'audio/mpeg' }));
    }
    if (init.method === 'GET') {
      if (h.historyUnavailable) throw new TypeError('offline');
      return Response.json(h.current);
    }
    const action = h.next; h.next = null;
    if (action === 'busy') return Response.json({ error: { code: 'agent_busy', message: 'Please retry.', retryable: true } }, { status: 503 });
    if (action === 'conflict') {
      h.current = savedHistory();
      return Response.json({ error: { code: 'conversation_state_conflict', message: 'The conversation changed. Review your question.', retryable: false } }, { status: 409 });
    }
    if (typeof action === 'function') return action(body, init);
    assert.equal(body.expected_revision, h.current.conversation.revision);
    const turn = { ...structuredClone(completed.turn), question: body.question, client_message_id: body.client_message_id,
      turn_id: uuid(100 + body.expected_revision), sequence: body.expected_revision + 1, language_override: body.language_override };
    h.current = { conversation: { ...h.current.conversation, revision: turn.sequence, last_answer_language: 'en' }, turns: [...h.current.turns, turn], pending_request: null };
    if (action === 'dropSaved') throw new TypeError('lost response');
    return Response.json({ conversation: h.current.conversation, turn });
  });
  h.posts = () => h.calls.filter((c) => c.method === 'POST' && c.url.endsWith('/messages'));
  h.speeches = () => h.calls.filter((c) => c.url.endsWith('/speech'));
  return h;
}

test('two typed turns use one Vincent session; only actual playback moves the painting; refresh restores sources silently', async (t) => {
  const h = harness(t); await render();
  assert.equal(h.calls.filter((c) => c.url === '/api/conversations').length, 1, 'StrictMode shares the create');
  assert.equal(button('Start recording').disabled, false);
  await ask(message.question);
  assert.equal(h.posts().length, 1); assert.equal(h.speeches().length, 1);
  assert.equal(container.querySelector('.vincent-painting'), null, 'download/play() alone is not playback');
  assert.equal(container.querySelector('.vincent-answer-text').textContent, completed.turn.answer.text);
  assert.equal(container.querySelector('blockquote').textContent, completed.turn.answer.citations[0].excerpt);
  await act(async () => h.audios[0].emit('playing'));
  assert.equal(container.querySelector('.vincent-painting').style.animationPlayState, 'running');
  await click('Stop playback');
  assert.equal(container.querySelector('.vincent-painting').style.animationPlayState, 'paused');
  await ask('Why is that?');
  assert.equal(h.posts()[1].body.expected_revision, 1);
  assert.notEqual(h.posts()[0].body.client_message_id, h.posts()[1].body.client_message_id);
  await click('Stop playback');
  assert.ok(container.querySelector('.vincent-history'));
  await click('Read reply and sources');
  await click('Play answer');
  assert.equal(h.posts().length, 2, 'replay never submits a model question');
  assert.equal(h.speeches().length, 2, 'cached first answer needs no new speech request');
  await leave(); const speechCount = h.speeches().length; await render();
  assert.ok(container.querySelector('.vincent-history'));
  assert.equal(h.speeches().length, speechCount, 'restored answers are not automatically read aloud');
  assert.equal(h.current.conversation.revision, 2);
});

test('recorded transcript and typed follow-up share identity/revision, and leaving releases microphone', async (t) => {
  const h = harness(t); await render(); await click('Start recording');
  assert.equal(h.micRequests, 1);
  h.now = 1700; await click('Stop recording');
  assert.equal(h.tracks[0].stopped, true);
  assert.match(container.querySelector('.vincent-question').textContent, /Why is that/);
  assert.equal(h.posts()[0].body.question, 'Why is that?');
  await ask('What about the flowers?');
  assert.equal(h.posts()[1].body.expected_revision, 1);
  await click('Start recording'); await leave();
  assert.ok(h.tracks.every((track) => track.stopped));
  assert.ok(h.audios.every((audio) => audio.paused));
});

test('lost submission response recovers the saved turn exactly once', async (t) => {
  const h = harness(t); await render(); h.next = 'dropSaved'; await ask(message.question);
  assert.equal(h.posts().length, 1); assert.equal(h.current.turns.length, 1);
  assert.equal(container.querySelector('.vincent-answer-text').textContent, completed.turn.answer.text);
});

test('stale revision refreshes history, preserves the draft and requires an explicit new submission', async (t) => {
  const h = harness(t); await render(); h.next = 'conflict'; await ask('Why is that?');
  assert.match(container.querySelector('[role=alert]').textContent, /Why is that/);
  assert.equal(button('Retry same question'), undefined);
  assert.equal(h.posts().length, 1);
  await click('Review question');
  assert.equal(container.querySelector('textarea').value, 'Why is that?');
  await click('Send question');
  assert.equal(h.posts().length, 2);
  assert.equal(h.posts()[1].body.expected_revision, 1);
  assert.notEqual(h.posts()[1].body.client_message_id, h.posts()[0].body.client_message_id);
  assert.equal(h.current.turns.length, 2);
});

test('retryable submission uses the same ID, body and revision; speech failure retains answer and sources', async (t) => {
  const h = harness(t); await render(); h.next = 'busy'; await ask(message.question);
  assert.ok(button('Retry same question'));
  h.speechFails = true; await click('Retry same question');
  assert.deepEqual(h.posts()[1].body, h.posts()[0].body);
  assert.ok(container.querySelector('.vincent-answer-text'));
  assert.ok(container.querySelector('.vincent-sources'));
  assert.match(container.querySelector('[role=alert]').textContent, /Audio is unavailable/);
  h.speechFails = false; h.blockAutoplay = true; await click('Play answer');
  assert.match(container.querySelector('.vincent-status').textContent, /Tap Play answer/);
  h.blockAutoplay = false; await click('Play answer');
  assert.equal(h.current.turns.length, 1);
  assert.equal(h.posts().length, 2);
});

test('refresh restores a failed request for explicit retry without creating a fresh ID', async (t) => {
  const h = harness(t, pendingHistory({ code: 'agent_busy', message: 'Please retry.', retryable: true }));
  await render(); assert.equal(h.posts().length, 0); assert.ok(button('Retry same question'));
  await click('Retry same question');
  assert.equal(h.posts()[0].body.client_message_id, message.clientMessageId);
  assert.equal(h.current.turns.length, 1);
});

test('refresh while pending uses GET and shows the completed turn without automatic speech', async (t) => {
  const h = harness(t, pendingHistory()); await render();
  assert.equal(button('Start recording').disabled, true);
  assert.equal(h.posts().length, 0);
  h.current = savedHistory();
  await act(async () => { await new Promise((r) => setTimeout(r, 1050)); }); await flush();
  assert.equal(button('Start recording').disabled, false);
  assert.ok(container.querySelector('.vincent-answer-text'));
  assert.equal(h.posts().length, 0); assert.equal(h.speeches().length, 0);
});

test('microphone denial opens typing; leaving drops late results and cancels the request', async (t) => {
  const h = harness(t); h.denyMic = true; await render(); await click('Start recording');
  assert.ok(container.querySelector('.vincent-write').open);
  let resolve;
  h.next = () => new Promise((r) => { resolve = r; });
  await ask(message.question);
  assert.equal(button('Start recording').disabled, true);
  const post = h.posts()[0]; await leave(); assert.ok(post.signal.aborted);
  resolve(Response.json(completed)); await flush();
  assert.equal(h.speeches().length, 0);
});

test('failed history check blocks new questions; checking again clears a recovered request error without replay', async (t) => {
  const h = harness(t, pendingHistory());
  const originalFetch = globalThis.fetch;
  let gets = 0;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (init.method === 'GET' && ++gets === 2) throw new TypeError('offline');
    return originalFetch(url, init);
  });
  await render();
  assert.equal(button('Start recording').disabled, true);
  assert.match(container.textContent, /Use Check again/);
  h.current = savedHistory(); await click('Check again');
  assert.equal(button('Start recording').disabled, false);
  assert.equal(container.querySelector('[role=alert]'), null);
  assert.match(container.querySelector('.vincent-question').textContent, /what did you paint these irises on/);
  assert.equal(h.posts().length, 0); assert.equal(h.speeches().length, 0);
});

test('a later successful Check again removes the original submission error without another POST', async (t) => {
  const h = harness(t); await render(); h.next = 'busy'; h.historyUnavailable = true;
  await ask('Why is that?');
  assert.ok(container.querySelector('[role=alert]'));
  assert.equal(button('Start recording').disabled, true);
  const original = h.posts()[0].body;
  h.historyUnavailable = false;
  h.current = savedHistory();
  Object.assign(h.current.turns[0], { client_message_id: original.client_message_id, question: original.question });
  await click('Check again');
  assert.equal(container.querySelector('[role=alert]'), null);
  assert.equal(button('Start recording').disabled, false);
  assert.equal(h.posts().length, 1); assert.equal(h.speeches().length, 0);
});

test('typing still works when the browser lacks secure-context randomUUID', async (t) => {
  const h = harness(t);
  const originalCrypto = globalThis.crypto;
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { getRandomValues: originalCrypto.getRandomValues.bind(originalCrypto) } });
  t.after(() => Object.defineProperty(globalThis, 'crypto', { configurable: true, value: originalCrypto }));
  await render(); await ask(message.question);
  assert.equal(h.posts().length, 1);
  assert.match(h.posts()[0].body.client_message_id, /^[0-9a-f-]{36}$/);
});
