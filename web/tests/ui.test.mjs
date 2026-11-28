import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:8080' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLTextAreaElement', 'Event', 'MouseEvent', 'PopStateEvent', 'sessionStorage', 'localStorage']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
}
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.scrollIntoView = () => {};
// JSDOM does not decode media; lifecycle tests dispatch the browser events explicitly.
dom.window.HTMLMediaElement.prototype.play = async () => {};
dom.window.HTMLMediaElement.prototype.pause = () => {};
const { act, createElement } = await import('react');
const { createRoot } = await import('react-dom/client');
const vite = await createServer({
  configFile: false,
  appType: 'custom',
  define: {
    'import.meta.env.VITE_API_MODE': JSON.stringify('live'),
    'import.meta.env.VITE_PUBLIC_CONTRACT': JSON.stringify('agent'),
  },
  optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
const { Chat } = await vite.ssrLoadModule('/src/ui/Chat.tsx');
const { Citation } = await vite.ssrLoadModule('/src/ui/Citation.tsx');
const { QrPage } = await vite.ssrLoadModule('/src/ui/QrPage.tsx');
const { App, navigate } = await vite.ssrLoadModule('/src/ui/App.tsx');
const { ArtworkPage } = await vite.ssrLoadModule('/src/ui/ArtworkPage.tsx');
const { default: VincentPreview } = await vite.ssrLoadModule('/src/vincent/Preview.tsx');
const { SAMPLE_REPLY } = await vite.ssrLoadModule('/src/vincent/previewData.ts');
const { VincentSources } = await vite.ssrLoadModule('/src/vincent/Sources.tsx');
const { VincentPortrait } = await vite.ssrLoadModule('/src/vincent/Portrait.tsx');
const { ARTWORKS, CITATIONS } = await vite.ssrLoadModule('/src/api/mockData.ts');
const artwork = ARTWORKS[0];
const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const createdAt = '2030-01-01T00:00:00Z';
const conversation = (overrides = {}) => ({
  contract_version: 'agent-demo-v0.3', conversation_id: uuid(1), artwork_id: artwork.artwork_id,
  dataset_version: artwork.dataset_version, created_at: createdAt, expires_at: '2030-01-02T00:00:00Z',
  revision: 0, language_preference: 'auto', auto_language: 'en', last_answer_language: null,
  ...overrides,
});
const turn = (sequence, { status = 'conversational', text = 'You are welcome.', citations = [], language = 'en', question = 'Thanks', override = null } = {}) => ({
  turn_id: uuid(100 + sequence), sequence, client_message_id: uuid(200 + sequence), question,
  answer_language: language, language_override: override, dataset_version: artwork.dataset_version,
  created_at: createdAt, answer: { mode: 'llm', status, text, citations },
});
let root;
let container;
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  root = null;
  container?.remove();
  localStorage.clear();
  sessionStorage.clear();
});
after(async () => { await vite.close(); dom.window.close(); });

async function flush() {
  for (let i = 0; i < 4; i++) await act(async () => { await setImmediate(); });
}
async function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(element));
  await flush();
}
async function choose(value) {
  await act(async () => {
    const select = container.querySelector('select');
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
async function ask(question = 'Thanks') {
  await act(async () => {
    const textarea = container.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, question);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => container.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  await flush();
}

/** Controlled public HTTP responses; no model or retrieval service participates in UI tests. */
function server(t, initial = conversation(), initialTurns = []) {
  let current = structuredClone(initial);
  const turns = structuredClone(initialTurns);
  const requests = [];
  const waiting = [];
  t.mock.method(globalThis, 'fetch', async (_url, init = {}) => {
    if (init.method === 'GET') return Response.json({ conversation: current, turns, pending_request: null });
    const body = JSON.parse(init.body);
    if (!('question' in body)) return Response.json({ conversation: current, created: false });
    requests.push(body);
    return new Promise((resolve) => waiting.push({ body, resolve }));
  });
  return {
    requests,
    async finish({ language = 'en', preference, autoLanguage = 'en', text = 'You are welcome.' } = {}) {
      const { body, resolve } = waiting.shift();
      const next = turn(turns.length + 1, { language, text, question: body.question, override: body.language_override });
      next.client_message_id = body.client_message_id;
      turns.push(next);
      current = {
        ...current, revision: next.sequence, last_answer_language: language, auto_language: autoLanguage,
        language_preference: preference ?? body.language_override ?? current.language_preference,
      };
      await act(async () => resolve(Response.json({ conversation: current, turn: next })));
      await flush();
    },
    async fail() {
      const { resolve } = waiting.shift();
      await act(async () => resolve(Response.json({
        request_id: 'ui-test', error: { code: 'generation_unavailable', message: 'Unavailable', retryable: true },
      }, { status: 503 })));
      await flush();
    },
  };
}

test('preserves a next-message language selection across an earlier response and consumes it once', async (t) => {
  const api = server(t);
  await render(createElement(Chat, { artwork }));
  assert.deepEqual([...container.querySelectorAll('select option')].map((o) => o.value), ['auto', 'en', 'es', 'zh-CN', 'ko']);
  await ask();
  assert.equal(api.requests[0].language_override, null);
  await choose('ko');
  assert.match(container.textContent, /Applies from your next message/);
  await api.finish();
  assert.equal(container.querySelector('select').value, 'ko');
  await ask('Please continue');
  assert.equal(api.requests[1].language_override, 'ko');
  assert.equal(api.requests[1].expected_revision, 1);
  await api.finish({ language: 'ko' });
  await ask('Another thought');
  assert.equal(api.requests[2].language_override, null);
  await api.finish({ language: 'ko' });
});

test('an explicit Auto selection while a fixed-language request is pending survives its commit', async (t) => {
  const api = server(t);
  await render(createElement(Chat, { artwork }));
  await choose('es');
  await ask();
  assert.equal(container.querySelector('select').value, 'es');
  await choose('auto');
  await api.finish({ language: 'es' });
  assert.equal(container.querySelector('select').value, 'auto');
  await ask('Hello again');
  assert.equal(api.requests[1].language_override, 'auto');
  await api.finish();
});

test('retry preserves the submitted language while a newer selection remains for the next message', async (t) => {
  const api = server(t);
  await render(createElement(Chat, { artwork }));
  await choose('es');
  await ask();
  await choose('ko');
  await api.fail();
  assert.equal(container.querySelector('select').value, 'ko');
  const retry = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Try again');
  await act(async () => retry.click());
  await flush();
  assert.deepEqual(api.requests[1], api.requests[0]);
  await api.finish({ language: 'es' });
  assert.equal(container.querySelector('select').value, 'ko');
  await ask('New question');
  assert.equal(api.requests[2].language_override, 'ko');
  await api.finish({ language: 'ko' });
});

test('a failed choice is not presented as a saved preference for a new message', async (t) => {
  const api = server(t);
  await render(createElement(Chat, { artwork }));
  await choose('es');
  await ask();
  await api.fail();
  assert.equal(container.querySelector('select').value, 'auto');
  await ask('A different question');
  assert.equal(api.requests[1].language_override, null);
  await api.finish();
});

test('Auto suggestions use the persistent automatic language after a temporary translation', async (t) => {
  const translated = turn(1, { language: 'ko', text: '감사합니다.' });
  server(t, conversation({ revision: 1, auto_language: 'zh-CN', last_answer_language: 'ko' }), [translated]);
  await render(createElement(Chat, { artwork }));
  assert.equal(container.querySelector('select').value, 'auto');
  assert.ok([...container.querySelectorAll('.chip')].every((chip) => chip.lang === 'zh-CN'));
  assert.equal(container.querySelector('.answer-text').lang, 'ko');
});

test('renders all six answer states, mixed clarification and distinct source cards without hiding the response', async (t) => {
  const values = Object.values(CITATIONS).filter((c) => c.artwork_id === artwork.artwork_id);
  const evidence = values.find((c) => c.kind === undefined);
  const catalog = values.find((c) => c.kind === 'catalog');
  assert.ok(evidence && catalog);
  const texts = ['Supported answer.', 'A sourced answer. Which object do you mean?', 'Which detail do you mean?',
    'The record does not identify the pigment. We can discuss its colors.', 'This guide covers the selected artwork.', 'What draws your attention?'];
  const statuses = ['answered', 'partially_answered', 'clarification_needed', 'insufficient', 'out_of_scope', 'conversational'];
  const turns = statuses.map((status, i) => turn(i + 1, {
    status, text: texts[i], citations: i === 0 ? [catalog] : i === 1 ? [evidence] : [],
  }));
  server(t, conversation({ revision: 6, last_answer_language: 'en' }), turns);
  await render(createElement(Chat, { artwork }));
  assert.deepEqual([...container.querySelectorAll('.answer-text')].map((p) => p.textContent), texts);
  assert.equal(container.querySelectorAll('.cites').length, 2);
  assert.equal(container.querySelectorAll('blockquote').length, 1);
  assert.equal(container.querySelector('.cite-catalog').querySelector('blockquote'), null);
  assert.match(container.querySelector('.cite-catalog').textContent, /Catalog record/);
  assert.equal(container.querySelector('.answer-conversational').querySelector('.cites'), null);
  assert.match(container.querySelector('.answer-partially_answered').textContent, /Which object do you mean/);
});

test('source text stays literal and only HTTPS source and license URLs become links', async () => {
  const excerpt = '<img src=x onerror=alert(1)> Original museum text.';
  const evidence = {
    kind: 'evidence', chunk_id: 'test', artwork_id: artwork.artwork_id, source_id: 'test', source_title: 'Source',
    source_url: 'javascript:alert(1)', source_language: 'en', source_locator: 'description', excerpt,
    license: 'License', license_url: 'data:text/html,unsafe', attribution: 'Museum', normalization: 'none',
  };
  await render(createElement('ul', null, createElement(Citation, { c: evidence })));
  assert.equal(container.querySelector('blockquote').textContent, excerpt);
  assert.equal(container.querySelectorAll('img, script, a').length, 0);
  await act(async () => root.render(createElement('ul', null, createElement(Citation, { c: {
    ...evidence, source_url: 'https://www.artic.edu/artworks/28560', license_url: 'https://creativecommons.org/publicdomain/zero/1.0/',
  } }))));
  const links = [...container.querySelectorAll('a')];
  assert.equal(links.length, 2);
  assert.ok(links.every((a) => a.protocol === 'https:' && a.rel === 'noopener noreferrer'));
});

test('empty conversation explains sourced answers without promising citations for every reply', async (t) => {
  server(t);
  await render(createElement(Chat, { artwork }));
  assert.match(container.textContent, /Sourced answers include links/);
  assert.doesNotMatch(container.textContent, /each one shows its source/);
});


test('Getty gallery, image enlargement, suggested questions and QR cards use the released artwork data', async (t) => {
  const { artworks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-pilot-v1.json', import.meta.url)));
  const active = artworks[1];
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  dom.window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  const scoped = conversation({ artwork_id: active.artwork_id, dataset_version: active.dataset_version });
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (url === '/api/artworks') return Response.json({ artworks });
    if (url === `/api/artworks/${active.slug}`) return Response.json({ artwork: active });
    if (init.method === 'POST') return Response.json({ conversation: scoped, created: true });
    return Response.json({ conversation: scoped, turns: [], pending_request: null });
  });
  window.history.replaceState(null, '', '/');
  await render(createElement(App));
  assert.deepEqual([...container.querySelectorAll('.workcard img')].map((i) => i.getAttribute('src')), artworks.map((a) => a.image_url));
  await act(async () => container.querySelectorAll('.workcard')[1].click());
  await flush();
  assert.equal(container.querySelector('.image-preview img').alt, active.image_alt);
  assert.equal(container.querySelector('figcaption a').href, active.image_source_url);
  await act(async () => container.querySelector('.image-preview').click());
  assert.ok(container.querySelector('dialog').open);
  await act(async () => container.querySelector('dialog button').click());
  assert.equal(container.querySelector('dialog').open, false);
  assert.deepEqual([...container.querySelectorAll('.chips button')].map((el) => el.textContent), active.suggested_questions);
  await act(async () => navigate('/qr'));
  await flush();
  assert.deepEqual([...container.querySelectorAll('svg.qr')].map((el) => new URL(el.getAttribute('aria-label')).pathname), artworks.map((a) => `/a/${a.slug}`));
});

test('only enabled Irises artwork responses display the Vincent entry', async (t) => {
  const { artworks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-gallery-v2.json', import.meta.url)));
  const irises = artworks.find((a) => a.artwork_id === 'getty-103jnh');
  let active;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    if (url === `/api/artworks/${active.slug}`) return Response.json({ artwork: active });
    const scoped = conversation({ artwork_id: active.artwork_id, dataset_version: active.dataset_version });
    if (init.method === 'POST') return Response.json({ conversation: scoped, created: false });
    return Response.json({ conversation: scoped, turns: [], pending_request: null });
  });
  const cases = [
    [irises, false],
    [{ ...irises, conversation_modes: ['guide'] }, false],
    [{ ...irises, conversation_modes: ['guide', 'vincent'] }, true],
    [{ ...irises, dataset_version: 'getty-pilot-v1', conversation_modes: ['guide', 'vincent'] }, false],
    [{ ...artworks.find((a) => a.artwork_id !== irises.artwork_id), conversation_modes: ['guide', 'vincent'] }, false],
  ];
  for (const [index, [record, enabled]] of cases.entries()) {
    active = record;
    const page = createElement(ArtworkPage, { key: index, slug: active.slug });
    if (!root) await render(page);
    else { await act(async () => root.render(page)); await flush(); }
    const entry = [...container.querySelectorAll('a')].find((a) => a.textContent === 'Talk with Vincent');
    assert.equal(Boolean(entry), enabled);
    if (entry) assert.equal(entry.href, 'http://localhost:8080/a/getty-103jnh/vincent');
    assert.ok(container.querySelector('.composer'), 'the ordinary guide stays available');
  }
});

test('the Vincent destination has an honest landing page and returns to the ordinary guide', async (t) => {
  const { artworks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-gallery-v2.json', import.meta.url)));
  const irises = artworks.find((a) => a.artwork_id === 'getty-103jnh');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push(url);
    if (url === `/api/artworks/${irises.slug}`) return Response.json({ artwork: irises });
    const scoped = conversation({ artwork_id: irises.artwork_id, dataset_version: irises.dataset_version });
    if (init.method === 'POST') return Response.json({ conversation: scoped, created: false });
    return Response.json({ conversation: scoped, turns: [], pending_request: null });
  });
  window.history.replaceState(null, '', '/a/getty-103jnh/vincent');
  await render(createElement(App));
  assert.equal(document.title, 'Talk with Vincent · MuseBridge');
  assert.match(container.textContent, /not available yet/);
  assert.ok(container.querySelector('textarea').disabled);
  assert.ok([...container.querySelectorAll('button')].every((button) => button.disabled));
  assert.equal(container.querySelector('.vincent-preview'), null);
  assert.deepEqual(requests, [], 'the landing page must not create an ordinary guide conversation');
  const back = [...container.querySelectorAll('a')].find((a) => a.textContent.endsWith('Back to artwork'));
  await act(async () => back.click());
  await flush();
  assert.equal(window.location.pathname, '/a/getty-103jnh');
  assert.equal(document.title, 'Irises · MuseBridge');
  assert.ok(container.querySelector('.composer'));
  await act(async () => navigate('/a/getty-103jnh/vincent/'));
  await flush();
  assert.match(container.textContent, /not available yet/);
  assert.equal(container.querySelector('.composer'), null);
});

async function previewState(value) {
  await act(async () => {
    const select = container.querySelector('.vincent-preview select');
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await flush();
}

async function previewClick(text) {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent.trim().endsWith(text));
  assert.ok(button && !button.disabled, `the ${text} action must be available`);
  await act(async () => button.click());
  await flush();
}

test('the Vincent preview walks through capture and playback without services or saved history', async (t) => {
  const network = t.mock.method(globalThis, 'fetch', () => { throw new Error('The preview must stay offline.'); });
  await render(createElement(VincentPreview));
  assert.match(container.querySelector('.vincent-preview').textContent, /No microphone, audio or AI services/);
  await previewClick('Start recording');
  assert.match(container.querySelector('.vincent-status').textContent, /Listening/);
  assert.ok(container.querySelector('.vincent-timer'));
  await previewClick('Stop recording');
  assert.match(container.querySelector('.vincent-status').textContent, /Turning your recording into words/);
  await previewClick('Next step');
  assert.match(container.querySelector('.vincent-question').textContent, /What color did you call the irises/);
  await previewClick('Next step');
  assert.equal(container.querySelector('.vincent-answer-text').textContent, SAMPLE_REPLY.text);
  assert.equal(container.querySelector('.vincent-playback button').disabled, true, 'reply is readable while audio prepares');
  await previewClick('Next step');
  await previewClick('Play answer');
  assert.equal(container.querySelector('.vincent-painting').style.animationPlayState, 'running');
  await previewClick('Stop playback');
  assert.equal(container.querySelector('.vincent-painting').style.animationPlayState, 'paused');
  assert.equal(container.querySelector('.vincent-answer-text').textContent, SAMPLE_REPLY.text);
  await previewClick('Replay answer');
  await previewClick('Start recording');
  assert.equal(container.querySelector('.vincent-painting'), null);
  assert.ok(container.querySelector('.vincent-portrait'));
  assert.equal(container.querySelector('.vincent-greeting'), null, 'recording never restarts the opening animation');
  assert.equal(network.mock.calls.length, 0);
  assert.equal(localStorage.length, 0);
  assert.equal(sessionStorage.length, 0);
});

test('Vincent greeting ends at the listening portrait and does not replay across turns', async () => {
  await render(createElement(VincentPreview));
  const video = container.querySelector('.vincent-greeting');
  assert.ok(video.muted && video.playsInline && video.autoplay);
  assert.equal(video.loop, false);
  assert.equal(video.getAttribute('poster'), '/vincent/greeting-poster.webp');
  assert.equal(container.querySelector('.vincent-portrait').getAttribute('src'), '/vincent/listening.webp');
  await act(async () => video.dispatchEvent(new Event('ended')));
  assert.equal(container.querySelector('video'), null);
  await previewState('playing');
  assert.ok(container.querySelector('.vincent-character').hidden);
  await previewClick('Stop playback');
  assert.equal(container.querySelector('.vincent-painting').style.animationPlayState, 'paused');
  await previewClick('Start recording');
  assert.equal(container.querySelector('.vincent-character').hidden, false);
  assert.equal(container.querySelector('video'), null);
  await previewClick('Stop recording');
  await previewState('ready');
  assert.equal(container.querySelector('video'), null);
  await previewClick('Reset preview');
  assert.ok(container.querySelector('video'), 'the explicit preview reset simulates a new page entry');
});

test('recording or stopping the welcome interrupts the greeting and pauses its video', async (t) => {
  const paused = t.mock.method(window.HTMLMediaElement.prototype, 'pause', () => {});
  await render(createElement(VincentPreview));
  const first = container.querySelector('video');
  await previewClick('Start recording');
  assert.equal(container.querySelector('video'), null);
  assert.ok(paused.mock.calls.some((call) => call.this === first));
  await previewState('welcome');
  const welcome = container.querySelector('video');
  assert.ok(welcome);
  await previewClick('Stop greeting');
  assert.equal(container.querySelector('video'), null);
  assert.ok(paused.mock.calls.some((call) => call.this === welcome));
});

test('blocked autoplay and a failed video both leave the listening portrait usable', async (t) => {
  const playing = t.mock.method(window.HTMLMediaElement.prototype, 'play', async () => { throw new Error('Autoplay blocked'); });
  await render(createElement(VincentPreview));
  assert.equal(container.querySelector('video'), null);
  assert.ok(container.querySelector('.vincent-portrait'));
  assert.equal([...container.querySelectorAll('button')].find((b) => b.textContent.includes('Start recording')).disabled, false);
  playing.mock.restore();
  await previewClick('Reset preview');
  await act(async () => container.querySelector('video').dispatchEvent(new Event('error')));
  assert.equal(container.querySelector('video'), null);
  assert.ok(container.querySelector('.vincent-portrait'));
  await act(async () => container.querySelector('.vincent-portrait').dispatchEvent(new Event('error')));
  assert.match(container.querySelector('.vincent-portrait-placeholder').textContent, /could not be loaded/);
});

test('reduced motion skips the greeting and applies immediately during playback', async (t) => {
  const listeners = new Set();
  const preference = {
    matches: true,
    addEventListener(_event, listener) { listeners.add(listener); },
    removeEventListener(_event, listener) { listeners.delete(listener); },
  };
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => preference });
  t.after(() => { delete window.matchMedia; });
  await render(createElement(VincentPreview));
  assert.equal(container.querySelector('video'), null);
  preference.matches = false;
  await previewClick('Reset preview');
  assert.ok(container.querySelector('video'));
  await act(async () => {
    preference.matches = true;
    for (const listener of listeners) listener();
  });
  assert.equal(container.querySelector('video'), null);
  assert.equal(listeners.size, 0, 'the completed greeting removes its preference listener');
});

test('leaving during autoplay ignores its late rejection and pauses the old video', async (t) => {
  let rejectPlay;
  t.mock.method(window.HTMLMediaElement.prototype, 'play', () => new Promise((_resolve, reject) => { rejectPlay = reject; }));
  const paused = t.mock.method(window.HTMLMediaElement.prototype, 'pause', () => {});
  await render(createElement(VincentPortrait, { allowGreeting: true }));
  const video = container.querySelector('video');
  await act(async () => root.unmount());
  root = null;
  await act(async () => { rejectPlay(new Error('The old page was closed')); });
  assert.ok(paused.mock.calls.some((call) => call.this === video));
  assert.equal(container.textContent, '');
});

test('Vincent media error previews offer stage-specific recovery and retain the reply', async () => {
  await render(createElement(VincentPreview));
  await previewState('microphone_denied');
  assert.ok(container.querySelector('.vincent-write').open);
  assert.match(container.querySelector('[role="alert"]').textContent, /type your question/);
  await previewState('no_speech');
  assert.match(container.querySelector('[role="alert"]').textContent, /Record another question/);
  assert.equal([...container.querySelectorAll('button')].some((b) => b.textContent === 'Retry transcription'), false);
  await previewState('transcription_error');
  await previewClick('Retry transcription');
  assert.equal(container.querySelector('[role="alert"]'), null);
  assert.match(container.querySelector('.vincent-status').textContent, /Turning your recording into words/);
  await previewState('speech_error');
  assert.equal(container.querySelector('.vincent-answer-text').textContent, SAMPLE_REPLY.text);
  assert.ok(container.querySelector('.vincent-sources'));
  await previewClick('Play answer');
  assert.equal(container.querySelector('[role="alert"]'), null);
  assert.equal(container.querySelector('.vincent-answer-text').textContent, SAMPLE_REPLY.text);
});

test('the typed preview keeps submitted words literal and does not invent sources for arbitrary questions', async () => {
  await render(createElement(VincentPreview));
  const textarea = container.querySelector('textarea');
  const send = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Send question');
  assert.ok(send.disabled);
  async function type(value) {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, value);
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
  await type('x'.repeat(501));
  assert.ok(send.disabled);
  assert.equal(textarea.getAttribute('aria-invalid'), 'true');
  await type('🌻'.repeat(500));
  assert.equal(send.disabled, false, 'the limit counts Unicode characters');
  const question = '<img src=x onerror=alert(1)> Why is that?';
  await type(question);
  await previewClick('Send question');
  assert.ok(container.querySelector('.vincent-question').textContent.includes(question));
  assert.equal(container.querySelector('.vincent-question img'), null);
  await previewClick('Next step');
  assert.match(container.querySelector('.vincent-answer-text').textContent, /sample reply for checking the layout/);
  assert.equal(container.querySelector('.vincent-sources'), null);
});

test('Vincent preview source cards retain the pinned wording, edition credit and license', async () => {
  const pack = JSON.parse(readFileSync(new URL('../../knowledge/data/reference-packs/vincent-irises-v1.json', import.meta.url)));
  for (const { kind, ...citation } of SAMPLE_REPLY.citations) {
    assert.equal(kind, 'evidence');
    assert.deepEqual(citation, pack.records.find((record) => record.chunk_id === citation.chunk_id));
  }
  await render(createElement(VincentPreview));
  await previewState('awaiting_play');
  const sources = container.querySelector('.vincent-sources');
  assert.equal(sources.open, false);
  await act(async () => sources.querySelector('summary').click());
  assert.equal(sources.open, true);
  assert.deepEqual([...sources.querySelectorAll('.cite-kind')].map((el) => el.textContent), ['Artist’s letter', 'Museum source']);
  assert.deepEqual([...sources.querySelectorAll('blockquote')].map((el) => el.textContent), SAMPLE_REPLY.citations.map((c) => c.excerpt));
  assert.ok(sources.querySelector('a[href="https://creativecommons.org/licenses/by-nc-sa/4.0/"]'));
  await previewState('long_reply');
  assert.ok(container.querySelector('.vincent-answer-text').textContent.length > 1000);
});

test('Vincent sources distinguish guide notes and leave unsafe links and HTML inert', async () => {
  const c = { ...SAMPLE_REPLY.citations[0], source_kind: 'guide_note',
    excerpt: '<script>not executable</script>', source_url: 'javascript:alert(1)', license_url: 'data:text/html,unsafe' };
  await render(createElement(VincentSources, { citations: [c] }));
  assert.equal(container.querySelector('.cite-kind').textContent, 'MuseBridge guide');
  assert.equal(container.querySelector('blockquote').textContent, c.excerpt);
  assert.equal(container.querySelectorAll('a,script').length, 0);
});

test('Getty museum passages and MuseBridge guide notes display distinct author and destination labels', async () => {
  const { sources, chunks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-pilot-v1.json', import.meta.url)));
  const citations = sources.slice(0, 2).map((src) => ({ kind: 'evidence', ...chunks.find((c) => c.source_id === src.source_id), ...src }));
  await render(createElement('ul', null, ...citations.map((c) => createElement(Citation, { key: c.source_id, c }))));
  const cards = [...container.querySelectorAll('.cite')];
  assert.deepEqual(cards.map((li) => li.querySelector('.cite-kind').textContent), ['Getty Museum', 'MuseBridge Guide']);
  assert.match(cards[1].querySelector('.cite-link').textContent, /Underlying Getty record/);
  assert.equal(cards[1].querySelector('blockquote').textContent, citations[1].excerpt);
});

test('QR cards require a valid HTTP(S) origin and never print broken or executable addresses', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ artworks: ARTWORKS }));
  await render(createElement(QrPage));
  assert.equal(container.querySelectorAll('svg.qr').length, 2);
  assert.match(container.textContent, /A phone cannot open/);
  async function address(value) {
    await act(async () => {
      const input = container.querySelector('input');
      Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
  for (const invalid of ['javascript:alert(1)', 'http://192.168.1.20:8080/qr', 'https://name:secret@example.com', 'https://example.com?token=value']) {
    await address(invalid);
    assert.equal(container.querySelectorAll('svg.qr').length, 0, invalid);
    assert.equal(container.querySelector('button').disabled, true, invalid);
    assert.equal(container.querySelector('input').getAttribute('aria-invalid'), 'true');
    assert.match(container.textContent, /Enter an HTTP or HTTPS address/);
  }
  await address('http://192.168.1.20:8080/');
  assert.equal(container.querySelector('button').disabled, false);
  assert.deepEqual([...container.querySelectorAll('svg.qr')].map((svg) => svg.getAttribute('aria-label')), [
    'http://192.168.1.20:8080/a/aic-28560', 'http://192.168.1.20:8080/a/aic-11723',
  ]);
  assert.doesNotMatch(container.textContent, /A phone cannot open/);
});


test('navigation updates home, artwork and QR titles and opens each artwork in its own scope', async (t) => {
  const scopes = [];
  const byId = new Map(ARTWORKS.map((a, i) => [uuid(i + 1), conversation({ conversation_id: uuid(i + 1), artwork_id: a.artwork_id })]));
  t.mock.method(globalThis, 'fetch', async (url, init = {}) => {
    if (url === '/api/artworks') return Response.json({ artworks: ARTWORKS });
    if (url.startsWith('/api/artworks/')) {
      return Response.json({ artwork: ARTWORKS.find((a) => a.slug === url.split('/').at(-1)) });
    }
    if (init.method === 'POST') {
      const body = JSON.parse(init.body);
      assert.equal('question' in body, false);
      scopes.push(body.artwork_id);
      return Response.json({ conversation: [...byId.values()].find((c) => c.artwork_id === body.artwork_id), created: false });
    }
    return Response.json({ conversation: byId.get(url.split('/')[3]), turns: [], pending_request: null });
  });
  window.history.replaceState(null, '', `/a/${ARTWORKS[0].slug}`);
  await render(createElement(App));
  assert.equal(document.title, `${ARTWORKS[0].title} · MuseBridge`);
  assert.equal(container.querySelector('h1').textContent, ARTWORKS[0].title);
  await act(async () => container.querySelector('a.brand').click());
  await flush();
  assert.equal(document.title, 'MuseBridge');
  assert.ok(container.querySelector('.home'));
  await act(async () => navigate(`/a/${ARTWORKS[1].slug}`));
  await flush();
  assert.equal(document.title, `${ARTWORKS[1].title} · MuseBridge`);
  assert.equal(container.querySelector('h1').textContent, ARTWORKS[1].title);
  assert.deepEqual(scopes, ARTWORKS.map((a) => a.artwork_id));
  await act(async () => navigate('/qr'));
  await flush();
  assert.equal(document.title, 'Artwork QR cards · MuseBridge');
  assert.equal(container.querySelectorAll('svg.qr').length, 2);
});
