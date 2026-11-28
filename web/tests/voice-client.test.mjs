import assert from 'node:assert/strict';
import { after, test } from 'node:test';

// Loads src/voice/vincentClient.ts through Vite, or VOICE_CLIENT_MODULE when
// Vite is not installed.
let clientModule;
if (process.env.VOICE_CLIENT_MODULE) {
  clientModule = await import(process.env.VOICE_CLIENT_MODULE);
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({
    configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  });
  after(() => vite.close());
  clientModule = await vite.ssrLoadModule('/src/voice/vincentClient.ts');
}
const { createVincentClient } = clientModule;

const { opened, completed, ID, message, emptyHistory, savedHistory, pendingHistory } = await import('./vincent-fixtures.mjs');
const turn = completed.turn;
const conversation = (revision) => structuredClone(revision ? completed.conversation : opened.conversation);

function server(script) {
  const calls = [];
  const fetcher = async (path, init) => {
    calls.push({ path, method: init.method, body: init.body });
    const step = script.shift();
    if (!step) throw new Error('unexpected call ' + init.method + ' ' + path);
    if (step === 'drop') throw new TypeError('network');
    return Response.json(step.body, { status: step.status ?? 200 });
  };
  return { calls, client: createVincentClient(fetcher, async () => {}) };
}

test('open creates or restores the Vincent conversation', async () => {
  const { calls, client } = server([{ body: { conversation: conversation(0), created: true } }]);
  const opened = await client.open();
  assert.equal(opened.conversation_id, ID);
  assert.deepEqual(JSON.parse(calls[0].body), { contract_version: 'agent-demo-v0.4', artwork_id: 'getty-103jnh', conversation_mode: 'vincent' });
});

test('a guide conversation is never used as Vincent', async () => {
  const { client } = server([{ body: { conversation: { ...conversation(0), conversation_mode: 'guide', reference_pack_version: null }, created: true } }]);
  await assert.rejects(client.open());
});

test('a lost response is recovered from history without a second POST', async () => {
  const { calls, client } = server([
    'drop',
    { body: { conversation: conversation(1), turns: [turn], pending_request: null } },
  ]);
  const result = await client.submit(ID, message);
  assert.equal(result.turn.turn_id, turn.turn_id);
  assert.equal(result.conversation.revision, 1);
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET']);
});

test('a lost request with no saved turn is resent identically', async () => {
  const { calls, client } = server([
    'drop',
    { body: emptyHistory() },
    { body: { conversation: conversation(1), turn } },
  ]);
  await client.submit(ID, message);
  const posts = calls.filter((c) => c.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].body, posts[1].body);
  assert.deepEqual(JSON.parse(posts[0].body), {
    contract_version: 'agent-demo-v0.4', client_message_id: message.clientMessageId, question: message.question,
    language_override: null, expected_revision: 0,
  });
});

test('a revision conflict is reported and never retried', async () => {
  const { calls, client } = server([
    { status: 409, body: { request_id: 'r', error: { code: 'conversation_state_conflict', message: 'The conversation state changed.', retryable: false } } },
  ]);
  await assert.rejects(client.submit(ID, message), (error) => error.code === 'conversation_state_conflict' && error.retryable === false);
  assert.equal(calls.length, 1);
});

test('processing after a dropped POST is polled with GET only', async () => {
  const { calls, client } = server(['drop', { body: pendingHistory() }, { body: pendingHistory() }, { body: savedHistory() }]);
  assert.equal((await client.submit(ID, message)).turn.turn_id, turn.turn_id);
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET', 'GET', 'GET']);
});

test('refresh recovery never submits a question', async () => {
  const { calls, client } = server([{ body: pendingHistory() }, { body: savedHistory() }]);
  assert.equal((await client.recover(ID)).turns.length, 1);
  assert.deepEqual(calls.map((c) => c.method), ['GET', 'GET']);
});

test('concurrent opens share a request and cancellation does not lose its cookie response', async () => {
  let finish, posts = 0;
  const client = createVincentClient(() => { posts++; return new Promise((resolve) => { finish = resolve; }); });
  const abort = new AbortController();
  const abandoned = client.open({ signal: abort.signal });
  const active = client.open();
  abort.abort();
  await assert.rejects(abandoned, { name: 'AbortError' });
  finish(Response.json(opened));
  assert.equal((await active).conversation_id, ID);
  assert.equal(posts, 1);
});

test('wrong release, pack, language, identity and malformed fields are rejected before display', async () => {
  for (const patch of [
    { dataset_version: 'getty-pilot-v1' }, { reference_pack_version: null }, { language_preference: 'es' },
    { last_answer_language: 'en' }, { conversation_id: 'not-a-uuid' }, { created_at: 'yesterday' },
  ]) {
    const { client } = server([{ body: { ...opened, conversation: { ...opened.conversation, ...patch } } }]);
    await assert.rejects(client.open(), (e) => e.code === 'invalid_response' && !e.retryable);
  }
});

test('foreign evidence, unsafe sources, missing citations and mismatched receipts are rejected', async () => {
  for (const mutate of [
    (r) => { r.turn.answer.citations[0].artwork_id = 'getty-103jng'; },
    (r) => { r.turn.answer.citations[0].source_url = 'javascript:alert(1)'; },
    (r) => { r.turn.answer.citations[0].source_kind = 'invented'; },
    (r) => { r.turn.answer.citations = []; },
    (r) => { r.turn.question = 'A different question'; },
    (r) => { r.turn.sequence = 2; },
    (r) => { r.turn.client_message_id = ID; },
    (r) => { r.turn.language_override = 'en'; },
  ]) {
    const result = structuredClone(completed); mutate(result);
    const { calls, client } = server([{ body: result }]);
    await assert.rejects(client.submit(ID, message), (e) => e.code === 'invalid_response' && !e.retryable);
    assert.equal(calls.length, 1);
  }
});

test('history validates scope, ordering, pending state and duplicates', async () => {
  for (const mutate of [
    (h) => { h.conversation.conversation_id = message.clientMessageId; },
    (h) => { h.conversation.revision = 2; },
    (h) => { h.turns[0].sequence = 2; },
    (h) => { h.turns.push(h.turns[0]); h.conversation.revision = 2; },
    (h) => { h.pending_request = pendingHistory().pending_request; },
  ]) {
    const body = savedHistory(); mutate(body);
    const { client } = server([{ body }]);
    await assert.rejects(client.history(ID), (e) => e.code === 'invalid_response');
  }
});

test('invalid history during recovery is not swallowed or resent', async () => {
  const body = savedHistory(); body.turns[0].answer.citations[0].artwork_id = 'other';
  const { calls, client } = server(['drop', { body }]);
  await assert.rejects(client.submit(ID, message), (e) => e.code === 'invalid_response');
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET']);
});

test('abort cancels a pending recovery wait without another POST', async () => {
  const abort = new AbortController();
  const { calls, client } = server(['drop', { body: pendingHistory() }]);
  await assert.rejects(client.submit(ID, message, { signal: abort.signal, onHistory: () => abort.abort() }), { name: 'AbortError' });
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET']);
});

test('an explicit retry reads history first and reuses the original message', async () => {
  const { calls, client } = server([
    { status: 503, body: { error: { code: 'agent_busy', message: 'Please try again.', retryable: true } } },
    { body: emptyHistory() }, { body: completed },
  ]);
  await assert.rejects(client.submit(ID, message));
  await client.submit(ID, message);
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET', 'POST']);
  assert.equal(calls[0].body, calls[2].body);
});

test('recovery and submission honor an already exhausted shared deadline without network work', async () => {
  const { calls, client } = server([]);
  const options = { deadline: Date.now() - 1 };
  await assert.rejects(client.submit(ID, message, options), (e) => e.code === 'request_timeout');
  await assert.rejects(client.recover(ID, options), (e) => e.code === 'request_timeout');
  assert.equal(calls.length, 0);
});
