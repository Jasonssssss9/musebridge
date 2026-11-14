import assert from 'node:assert/strict';
import { after, afterEach, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:8080' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLTextAreaElement', 'Event', 'MouseEvent', 'PopStateEvent', 'sessionStorage', 'localStorage']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
}
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.scrollIntoView = () => {};
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
  assert.match(container.textContent, /Answers grounded in museum records include sources/);
  assert.doesNotMatch(container.textContent, /each one shows its source/);
});


test('QR cards require a valid HTTP(S) origin and never print broken or executable addresses', async () => {
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
  assert.equal(document.title, 'QR cards for the demo · MuseBridge');
  assert.equal(container.querySelectorAll('svg.qr').length, 2);
});
