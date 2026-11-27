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

const ID = '22222222-2222-4222-8222-222222222222';
const conversation = (revision) => ({
  contract_version: 'agent-demo-v0.4', conversation_id: ID, artwork_id: 'getty-103jnh', dataset_version: 'getty-gallery-v2',
  conversation_mode: 'vincent', reference_pack_version: 'vincent-irises-v1', revision, language_preference: 'en',
});
const message = { question: 'Why do they look blue today?', clientMessageId: '33333333-3333-4333-8333-333333333333', expectedRevision: 1, languageOverride: null };
const turn = { turn_id: '44444444-4444-4444-8444-444444444444', sequence: 2, client_message_id: message.clientMessageId, question: message.question,
  answer: { mode: 'llm', status: 'answered', text: 'The red has faded.', citations: [] } };

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
    { body: { conversation: conversation(2), turns: [turn], pending_request: null } },
  ]);
  const result = await client.submit(ID, message);
  assert.equal(result.turn.turn_id, turn.turn_id);
  assert.equal(result.conversation.revision, 2);
  assert.deepEqual(calls.map((c) => c.method), ['POST', 'GET']);
});

test('a lost request with no saved turn is resent identically', async () => {
  const { calls, client } = server([
    'drop',
    { body: { conversation: conversation(1), turns: [], pending_request: null } },
    { body: { conversation: conversation(2), turn } },
  ]);
  await client.submit(ID, message);
  const posts = calls.filter((c) => c.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].body, posts[1].body);
  assert.deepEqual(JSON.parse(posts[0].body), {
    contract_version: 'agent-demo-v0.4', client_message_id: message.clientMessageId, question: message.question,
    language_override: null, expected_revision: 1,
  });
});

test('a revision conflict is reported and never retried', async () => {
  const { calls, client } = server([
    { status: 409, body: { request_id: 'r', error: { code: 'conversation_state_conflict', message: 'The conversation state changed.', retryable: false } } },
  ]);
  await assert.rejects(client.submit(ID, message), (error) => error.code === 'conversation_state_conflict' && error.retryable === false);
  assert.equal(calls.length, 1);
});
