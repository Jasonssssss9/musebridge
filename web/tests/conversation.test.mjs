import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createServer } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:8080' });
for (const key of ['window', 'document', 'sessionStorage', 'localStorage']) {
  Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
}
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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
const { useConversation } = await vite.ssrLoadModule('/src/chat/useConversation.ts');
const { saveDraft, loadDraft } = await vite.ssrLoadModule('/src/chat/drafts.ts');
after(async () => { await vite.close(); dom.window.close(); });
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

const CONTRACT = 'agent-demo-v0.3';
const BEDROOM = 'aic-28560';
const TOILETTE = 'aic-11723';
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const convId = (artwork) => id(artwork === BEDROOM ? 1 : 2);
function conversation(revision = 0, artwork = BEDROOM, language = 'en') {
  return {
    contract_version: CONTRACT, conversation_id: convId(artwork), artwork_id: artwork,
    dataset_version: 'aic-seed-v0.1', created_at: '2026-11-14T10:00:00Z', expires_at: '2026-11-15T10:00:00Z',
    revision, language_preference: 'auto', auto_language: 'en', last_answer_language: revision ? language : null,
  };
}
function makeTurn(s, sequence = s.expected_revision + 1, language = 'en') {
  return {
    turn_id: id(100 + sequence), sequence, client_message_id: s.client_message_id, question: s.question,
    language_override: s.language_override, answer_language: language, dataset_version: 'aic-seed-v0.1',
    created_at: '2026-11-14T10:01:00Z',
    answer: { status: 'conversational', mode: 'llm', text: 'Let us keep exploring.', citations: [] },
  };
}
function history(turns = [], artwork = BEDROOM, pending_request = null) {
  return { conversation: conversation(turns.length, artwork, turns.at(-1)?.answer_language), turns, pending_request };
}
function error(code, retryable, status = 409) {
  return Response.json({ request_id: 'test-request', error: { code, message: code, retryable } }, { status });
}
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}
async function mount(t, fetcher, artwork = BEDROOM) {
  t.mock.method(globalThis, 'fetch', fetcher);
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  let view;
  function Probe({ artworkId }) { view = useConversation(artworkId); return null; }
  const render = async (artworkId) => { await act(async () => { root.render(React.createElement(Probe, { artworkId })); await setImmediate(); }); };
  t.after(async () => { await act(async () => root.unmount()); container.remove(); });
  await render(artwork);
  return {
    get view() { return view; }, render,
    async do(fn) { await act(async () => { fn(view); await setImmediate(); }); },
    async until(predicate) {
      for (let n = 0; n < 20 && !predicate(view); n++) await act(async () => { await setImmediate(); });
      assert.ok(predicate(view), 'expected conversation state was not reached');
    },
  };
}

for (const brokenReply of ['lost', 'malformed']) {
  test(`recovers a ${brokenReply} success response from history without another POST`, async (t) => {
    let saved = history();
    let posts = 0;
    const hook = await mount(t, async (path, options) => {
      if (path === '/api/conversations') return Response.json({ conversation: saved.conversation, created: true });
      if (options.method === 'GET') return Response.json(saved);
      posts++;
      const s = JSON.parse(options.body);
      saved = history([makeTurn(s)]);
      if (brokenReply === 'lost') throw new TypeError('Connection lost after commit');
      return Response.json({ broken: true });
    });
    assert.equal(hook.view.status, 'ready');
    await hook.do((view) => view.send('Beautiful!', null));
    await hook.until((view) => view.turns.length === 1 && view.pending === null);
    assert.equal(posts, 1);
    assert.equal(hook.view.conversation.agent.revision, 1);
    assert.equal(loadDraft(convId(BEDROOM)), null);
  });
}

test('a deliberate retry preserves the complete original submission', async (t) => {
  const posts = [];
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: true });
    if (options.method === 'GET') return Response.json(history());
    const s = JSON.parse(options.body); posts.push(s);
    if (posts.length === 1) return error('generation_unavailable', true, 503);
    return Response.json({ conversation: conversation(1, BEDROOM, 'ko'), turn: makeTurn(s, 1, 'ko') });
  });
  await hook.do((view) => view.send('Beautiful!', 'ko'));
  assert.equal(hook.view.pending.phase, 'failed');
  assert.equal(posts.length, 1);
  await hook.do((view) => view.retry());
  assert.equal(hook.view.pending, null);
  assert.deepEqual(posts[1], posts[0]);
  assert.equal(hook.view.turns.length, 1);
});

test('a revision conflict restores the draft and requires a new deliberate submission', async (t) => {
  let saved = history();
  const posts = [];
  const other = makeTurn({ client_message_id: id(30), question: 'Another tab.', language_override: null, expected_revision: 0 });
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: saved.conversation, created: false });
    if (options.method === 'GET') return Response.json(saved);
    const s = JSON.parse(options.body); posts.push(s);
    if (posts.length === 1) { saved = history([other]); return error('conversation_state_conflict', false); }
    const turn = makeTurn(s); saved = history([other, turn]);
    return Response.json({ conversation: saved.conversation, turn });
  });
  await hook.do((view) => view.send('What about this?', null));
  await hook.until((view) => view.status === 'ready' && view.conversation.agent.revision === 1);
  assert.equal(posts.length, 1);
  assert.equal(hook.view.restoredText, 'What about this?');
  assert.equal(hook.view.conversationChanged, true);
  assert.equal(hook.view.pending, null);
  await hook.do((view) => view.send(view.restoredText, null));
  assert.equal(posts[1].expected_revision, 1);
  assert.notEqual(posts[1].client_message_id, posts[0].client_message_id);
  assert.equal(hook.view.turns.length, 2);
});

test('refresh resumes a processing request through history without submitting it again', async (t) => {
  const s = { client_message_id: id(31), question: 'Keep looking.', language_override: null, expected_revision: 0 };
  saveDraft(convId(BEDROOM), s);
  let reads = 0;
  let posts = 0;
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: false });
    if (options.method === 'POST') { posts++; throw new Error('Unexpected resubmission'); }
    reads++;
    return Response.json(reads === 1
      ? history([], BEDROOM, { contract_version: CONTRACT, ...s, state: 'processing', last_error: null })
      : history([makeTurn(s)]));
  });
  await hook.until((view) => view.pending === null && view.turns.length === 1);
  assert.equal(posts, 0);
  assert.equal(reads, 2);
});

test('an expired wait budget makes no extra history read until Check again', async (t) => {
  let clock = 0;
  t.mock.method(performance, 'now', () => clock);
  let reads = 0;
  let saved = history();
  let posts = 0;
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: true });
    if (options.method === 'GET') { reads++; return Response.json(saved); }
    posts++;
    saved = history([makeTurn(JSON.parse(options.body))]);
    clock = 70_001;
    throw new TypeError('The browser exhausted its wait');
  });
  await hook.do((view) => view.send('Beautiful!', null));
  assert.equal(hook.view.pending.phase, 'stalled');
  assert.equal(reads, 1, 'only the initial page history read is allowed');
  assert.equal(hook.view.canSend, false);
  await hook.do((view) => view.checkAgain());
  assert.equal(reads, 2);
  assert.equal(posts, 1);
  assert.equal(hook.view.pending, null);
  assert.equal(hook.view.turns.length, 1);
});

test('stale saved drafts are returned for review after another tab advances the revision', async (t) => {
  const draft = { client_message_id: id(32), question: 'What does that mean?', language_override: null, expected_revision: 0 };
  saveDraft(convId(BEDROOM), draft);
  const other = makeTurn({ client_message_id: id(33), question: 'Different topic.', language_override: null, expected_revision: 0 });
  const saved = history([other]);
  let posts = 0;
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: saved.conversation, created: false });
    if (options.method === 'POST') posts++;
    return Response.json(saved);
  });
  assert.equal(posts, 0);
  assert.equal(hook.view.pending, null);
  assert.equal(hook.view.restoredText, draft.question);
  assert.equal(hook.view.conversationChanged, true);
  assert.equal(loadDraft(convId(BEDROOM)), null);
});

test('an older history snapshot cannot roll back the displayed turns or language revision', async (t) => {
  const turn = makeTurn({ client_message_id: id(34), question: 'Keep looking.', language_override: null, expected_revision: 0 });
  let reads = 0;
  const hook = await mount(t, async (path) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(1), created: false });
    reads++;
    return Response.json(reads === 1 ? history([turn]) : history());
  });
  await hook.do((view) => view.reopen());
  assert.equal(hook.view.status, 'ready');
  assert.equal(hook.view.conversation.agent.revision, 1);
  assert.deepEqual(hook.view.turns.map((item) => item.turn_id), [turn.turn_id]);
});

test('a late answer after artwork navigation cannot alter the new artwork conversation', async (t) => {
  const held = deferred();
  let original;
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') {
      const artwork = JSON.parse(options.body).artwork_id;
      return Response.json({ conversation: conversation(0, artwork), created: true });
    }
    if (options.method === 'GET') return Response.json(history([], path.includes(convId(TOILETTE)) ? TOILETTE : BEDROOM));
    original = JSON.parse(options.body);
    return held.promise;
  });
  await hook.do((view) => view.send('Beautiful!', null));
  assert.equal(hook.view.pending.phase, 'sending');
  await hook.render(TOILETTE);
  assert.equal(hook.view.conversation.artwork_id, TOILETTE);
  await act(async () => {
    held.resolve(Response.json({ conversation: conversation(1), turn: makeTurn(original) }));
    await setImmediate();
  });
  assert.equal(hook.view.conversation.artwork_id, TOILETTE);
  assert.equal(hook.view.conversation.agent.revision, 0);
  assert.equal(hook.view.turns.length, 0);
  assert.equal(hook.view.pending, null);
  assert.equal(hook.view.canSend, true);
});

test('a completed replay with a newer conversation loads intervening turns before continuing', async (t) => {
  const s = { client_message_id: id(35), question: 'Original.', language_override: null, expected_revision: 0 };
  saveDraft(convId(BEDROOM), s);
  const first = makeTurn(s);
  const second = makeTurn({ client_message_id: id(36), question: 'Later.', language_override: null, expected_revision: 1 });
  let reads = 0;
  let posts = 0;
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: false });
    if (options.method === 'GET') { reads++; return Response.json(reads === 1 ? history() : history([first, second])); }
    posts++;
    return Response.json({ conversation: conversation(2), turn: first });
  });
  assert.equal(hook.view.pending.phase, 'failed');
  await hook.do((view) => view.retry());
  await hook.until((view) => view.pending === null && view.turns.length === 2);
  assert.equal(posts, 1);
  assert.equal(hook.view.conversation.agent.revision, 2);
});


test('recovery history receives only the original wait budget remaining after the POST', async (t) => {
  let clock = 0;
  t.mock.method(performance, 'now', () => clock);
  const realSetTimeout = globalThis.setTimeout;
  const timeouts = [];
  t.mock.method(globalThis, 'setTimeout', (fn, milliseconds, ...args) => {
    timeouts.push(milliseconds);
    return realSetTimeout(fn, milliseconds, ...args);
  });
  let saved = history();
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: true });
    if (options.method === 'GET') return Response.json(saved);
    saved = history([makeTurn(JSON.parse(options.body))]);
    clock = 69_800;
    throw new TypeError('Connection lost near deadline');
  });
  timeouts.length = 0;
  await hook.do((view) => view.send('Beautiful!', null));
  assert.deepEqual(timeouts.filter((milliseconds) => milliseconds > 0), [70_000, 200]);
  assert.equal(hook.view.turns.length, 1);
  assert.equal(hook.view.pending, null);
});

test('a superseded history opening cannot replace the latest opening result', async (t) => {
  const oldRead = deferred();
  const newRead = deferred();
  let reads = 0;
  const turn = makeTurn({ client_message_id: id(40), question: 'Newer context.', language_override: null, expected_revision: 0 });
  const hook = await mount(t, async (path) => {
    if (path === '/api/conversations') return Response.json({ conversation: conversation(), created: false });
    reads++;
    if (reads === 1) return Response.json(history());
    return reads === 2 ? oldRead.promise : newRead.promise;
  });
  await hook.do((view) => view.reopen());
  await hook.do((view) => view.reopen());
  await act(async () => { newRead.resolve(Response.json(history([turn]))); await setImmediate(); });
  assert.equal(hook.view.conversation.agent.revision, 1);
  await act(async () => { oldRead.resolve(Response.json(history())); await setImmediate(); });
  assert.equal(hook.view.conversation.agent.revision, 1);
  assert.equal(hook.view.turns[0].question, 'Newer context.');
  assert.equal(hook.view.status, 'ready');
});

test('an expired session opens a new conversation and preserves the question without resending', async (t) => {
  let creates = 0;
  let posts = 0;
  const fresh = { ...conversation(), conversation_id: id(99) };
  const hook = await mount(t, async (path, options) => {
    if (path === '/api/conversations') {
      creates++;
      return Response.json({ conversation: creates === 1 ? conversation() : fresh, created: true });
    }
    if (options.method === 'GET') return Response.json({ ...history(), conversation: creates === 1 ? conversation() : fresh });
    posts++;
    return error('conversation_not_found', false, 404);
  });
  await hook.do((view) => view.send('What is this detail?', null));
  await hook.until((view) => view.status === 'ready' && view.conversation.conversation_id === fresh.conversation_id);
  assert.equal(posts, 1);
  assert.equal(hook.view.restoredText, 'What is this detail?');
  assert.equal(hook.view.sessionEnded, true);
  assert.equal(hook.view.pending, null);
  assert.equal(loadDraft(convId(BEDROOM)), null);
});
