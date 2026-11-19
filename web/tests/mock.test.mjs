import assert from 'node:assert/strict';
import { after, beforeEach, test } from 'node:test';
import { webcrypto } from 'node:crypto';
import { createServer } from 'vite';

const storage = new Map();
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
} });
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
});
const { mockFetch, resetMockServer, setFault } = await vite.ssrLoadModule('/src/api/mock.ts');
const { parseConversation, parseHistory, parseTurn } = await vite.ssrLoadModule('/src/api/types.ts');
after(() => vite.close());
beforeEach(() => resetMockServer());
const CONTRACT = 'agent-demo-v0.3';
const id = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
async function request(path, body) {
  const res = await mockFetch(path, body ? { method: 'POST', body: JSON.stringify(body) } : {});
  return { status: res.status, body: await res.json() };
}
async function create(artwork = 'aic-28560') {
  const response = await request('/api/conversations', { contract_version: CONTRACT, artwork_id: artwork });
  assert.equal(response.status, 200);
  return parseConversation(response.body.conversation);
}
function payload(n, question, expected_revision = 0, language_override = null) {
  return { contract_version: CONTRACT, client_message_id: id(n), question, expected_revision, language_override };
}
const path = (conversation) => `/api/conversations/${conversation.conversation_id}/messages`;

test('scripted visitor examples produce all six valid public answer states and both source kinds', async () => {
  const c = await create();
  const cases = [
    ['Who made it, and when?', 'answered'],
    ['What is it made of, and what is that little object for?', 'partially_answered'],
    ['What is that thing for?', 'clarification_needed'],
    ['How many hours did it take to paint?', 'insufficient'],
    ["What's the weather today?", 'out_of_scope'],
    ['Thanks', 'conversational'],
  ];
  for (const [index, [question, expected]] of cases.entries()) {
    const response = await request(path(c), payload(100 + index, question, index));
    assert.equal(response.status, 200);
    const turn = parseTurn(response.body.turn);
    assert.equal(turn.answer.status, expected);
    assert.equal(turn.sequence, index + 1);
    if (index === 0) assert.ok(turn.answer.citations.every((citation) => citation.kind === 'catalog'));
    if (index === 1) assert.ok(turn.answer.citations.some((citation) => citation.kind === 'evidence'));
  }
  const saved = parseHistory((await request(path(c))).body);
  assert.equal(saved.turns.length, 6);
  assert.equal(saved.conversation.agent.revision, 6);
});

test('a live request completes once when the system clock moves backward during its timer', async (t) => {
  const c = await create();
  let wallTime = Date.now();
  t.mock.method(Date, 'now', () => (wallTime -= 1000));
  const message = payload(180, 'Thanks');
  const response = await request(path(c), message);
  assert.equal(response.status, 200);
  const replay = await request(path(c), message);
  assert.deepEqual(replay.body.turn, response.body.turn);
  const history = parseHistory((await request(path(c))).body);
  assert.equal(history.pending_request, null);
  assert.equal(history.turns.length, 1);
});

test('mock replies support four languages and keep one-off translation separate from preference', async () => {
  const c = await create();
  for (const [index, language] of ['en', 'es', 'zh-CN', 'ko'].entries()) {
    const response = await request(path(c), payload(200 + index, 'What is it made of?', index, language));
    assert.equal(parseTurn(response.body.turn).answer_language, language);
    assert.equal(parseConversation(response.body.conversation).agent.language_preference, language);
  }
  const translated = await request(path(c), payload(204, 'Translate that into English', 4));
  assert.equal(parseTurn(translated.body.turn).answer_language, 'en');
  assert.equal(parseConversation(translated.body.conversation).agent.language_preference, 'ko');
  const following = await request(path(c), payload(205, 'Thanks', 5));
  assert.equal(parseTurn(following.body.turn).answer_language, 'ko');
});

test('mock replay preserves the old turn with the latest conversation and rejects changed payloads', async () => {
  const c = await create();
  const firstPayload = payload(300, 'Thanks');
  const first = await request(path(c), firstPayload);
  await request(path(c), payload(301, 'Beautiful!', 1));
  const replay = await request(path(c), firstPayload);
  assert.deepEqual(replay.body.turn, first.body.turn);
  assert.equal(parseConversation(replay.body.conversation).agent.revision, 2);
  assert.equal((await request(path(c), { ...firstPayload, language_override: 'ko' })).body.error.code, 'idempotency_conflict');
  assert.equal((await request(path(c), payload(302, 'Another question.', 0))).body.error.code, 'conversation_state_conflict');
  assert.equal(parseHistory((await request(path(c))).body).turns.length, 2);
});

test('a failed mock attempt does not advance revision and retries retain the logical message', async () => {
  const c = await create();
  const submission = payload(400, 'Thanks');
  setFault('unavailable');
  const failed = await request(path(c), submission);
  assert.equal(failed.status, 503);
  const pending = parseHistory((await request(path(c))).body);
  assert.equal(pending.conversation.agent.revision, 0);
  assert.equal(pending.pending_request.client_message_id, submission.client_message_id);
  assert.equal(pending.pending_request.state, 'failed');
  setFault('none');
  const retry = await request(path(c), submission);
  assert.equal(parseTurn(retry.body.turn).sequence, 1);
  assert.equal(parseTurn(retry.body.turn).client_message_id, submission.client_message_id);
  assert.equal(parseHistory((await request(path(c))).body).pending_request, null);
});

test('mock artwork conversations never share history or citation scope', async () => {
  const bedroom = await create();
  const toilette = await create('aic-11723');
  assert.notEqual(bedroom.conversation_id, toilette.conversation_id);
  for (const [index, c] of [bedroom, toilette].entries()) {
    const response = await request(path(c), payload(500 + index, 'How does the museum describe the brushwork?'));
    const turn = parseTurn(response.body.turn);
    assert.ok(turn.answer.citations.length > 0);
    assert.ok(turn.answer.citations.every((citation) => citation.artwork_id === c.artwork_id));
    const saved = parseHistory((await request(path(c))).body);
    assert.equal(saved.turns.length, 1);
    assert.equal(saved.turns[0].client_message_id, id(500 + index));
  }
});
