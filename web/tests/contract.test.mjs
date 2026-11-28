import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';

const servers = [];
async function load(contract) {
  const vite = await createServer({
    configFile: false, appType: 'custom',
    define: {
      'import.meta.env.VITE_API_MODE': JSON.stringify('live'),
      'import.meta.env.VITE_PUBLIC_CONTRACT': JSON.stringify(contract),
    },
    optimizeDeps: { noDiscovery: true },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  });
  servers.push(vite);
  return {
    types: await vite.ssrLoadModule('/src/api/types.ts'),
    client: await vite.ssrLoadModule('/src/api/client.ts'),
  };
}
const { types, client } = await load('agent');
const { vincentEntryUrl } = await servers[0].ssrLoadModule('/src/vincent.ts');
const fixture = await load('fixture');
after(() => Promise.all(servers.map((s) => s.close())));

const id = (n) => `${String(n).padStart(8, '0')}-2222-4222-8222-222222222222`;
const scope = { artwork_id: 'aic-28560', dataset_version: 'aic-seed-v0.1' };
const conversation = (revision = 0) => ({
  ...scope, contract_version: 'agent-demo-v0.3', conversation_id: id(1),
  created_at: '2026-11-13T20:00:00Z', expires_at: '2026-11-14T20:00:00Z',
  revision, language_preference: 'auto', auto_language: 'en', last_answer_language: revision ? 'en' : null,
});
const evidence = () => ({
  chunk_id: 'aic-28560-medium', artwork_id: scope.artwork_id, source_id: 'aic-api-28560',
  source_title: 'The Bedroom', source_url: 'https://api.artic.edu/api/v1/artworks/28560',
  source_language: 'en', source_locator: 'medium_display', excerpt: 'Oil on canvas',
  license: 'CC0-1.0', license_url: 'https://creativecommons.org/publicdomain/zero/1.0/',
  attribution: 'Art Institute of Chicago', normalization: 'Whitespace normalized.',
});
const catalog = () => ({
  kind: 'catalog', fact_id: 'aic-28560-catalog-artist', ...scope, field: 'artist', value: 'Vincent van Gogh',
  source_id: 'aic-api-28560', source_title: 'The Bedroom', source_url: 'https://api.artic.edu/api/v1/artworks/28560',
  source_locator: 'artist_display', attribution: 'Art Institute of Chicago',
  normalization: 'Released catalog metadata; not a verbatim museum excerpt.',
});
const turn = (sequence = 1) => ({
  turn_id: id(sequence + 10), sequence, client_message_id: id(sequence + 20), question: 'What is it made of?',
  answer_language: 'en', language_override: null, dataset_version: scope.dataset_version,
  created_at: '2026-11-13T20:00:01Z',
  answer: { mode: 'llm', status: 'answered', text: 'It is oil on canvas.', citations: [evidence()] },
});
const history = (count = 1) => ({
  conversation: conversation(count), turns: Array.from({ length: count }, (_, i) => turn(i + 1)), pending_request: null,
});

test('artwork capabilities default to guide and reject malformed mode declarations', () => {
  const { artworks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-gallery-v2.json', import.meta.url)));
  const irises = artworks.find((a) => a.artwork_id === 'getty-103jnh');
  assert.deepEqual(types.parseArtwork(irises).conversation_modes, ['guide']);
  for (const modes of [['guide'], ['guide', 'vincent']]) {
    assert.deepEqual(types.parseArtwork({ ...irises, conversation_modes: modes }).conversation_modes, modes);
  }
  for (const modes of [null, 'vincent', [], ['vincent'], ['vincent', 'guide'], ['guide', 'guide'], ['guide', 'unknown'], ['guide', 'vincent', 'vincent']]) {
    assert.throws(() => types.parseArtwork({ ...irises, conversation_modes: modes }), types.InvalidResponse);
  }
});

test('Vincent entry requires the advertised release and a secure public destination', () => {
  const { artworks } = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-gallery-v2.json', import.meta.url)));
  const irises = { ...artworks.find((a) => a.artwork_id === 'getty-103jnh'), conversation_modes: ['guide', 'vincent'] };
  const path = '/a/getty-103jnh/vincent';
  assert.equal(vincentEntryUrl(irises, 'https://vincent.example'), `https://vincent.example${path}`);
  for (const origin of ['http://localhost:8080', 'http://127.0.0.1:8080', 'http://[::1]:8080']) {
    assert.equal(vincentEntryUrl(irises, origin), `${origin}${path}`);
  }
  for (const origin of ['', 'http://192.168.1.20:8080', 'http://vincent.example', 'javascript:alert(1)',
    'https://user:secret@vincent.example', 'https://vincent.example/path', 'https://vincent.example/?key=secret', 'https://vincent.example/#fragment']) {
    assert.equal(vincentEntryUrl(irises, origin), null);
  }
  for (const change of [{ conversation_modes: undefined }, { conversation_modes: ['guide'] },
    { artwork_id: 'getty-103qtz' }, { slug: 'getty-103qtz' }, { dataset_version: 'getty-pilot-v1' }]) {
    assert.equal(vincentEntryUrl({ ...irises, ...change }, 'https://vincent.example'), null);
  }
});
const pending = (revision = 1) => ({
  contract_version: 'agent-demo-v0.3', client_message_id: id(99), question: 'Thanks', language_override: null,
  expected_revision: revision, state: 'processing', last_error: null,
});
const submission = () => ({ client_message_id: id(21), question: 'What is it made of?', language_override: null, expected_revision: 0 });
const invalid = (error) => error instanceof client.TransportError && error.kind === 'invalid_response';

function fixtureConversation() {
  const { contract_version, revision, language_preference, auto_language, last_answer_language, ...c } = conversation();
  return c;
}
function fixtureTurn() {
  const { language_override, ...t } = turn();
  t.answer.mode = 'fixture';
  return t;
}

test('Getty and Commons image sources carry their own rights labels', () => {
  const [art] = JSON.parse(readFileSync(new URL('../../knowledge/data/getty-pilot-v1.json', import.meta.url))).artworks;
  const commons = 'https://commons.wikimedia.org/wiki/File:Joshua_Reynolds_-_Portrait_of_Omai.jpg';
  const copy = { ...art, image_license: 'Public Domain Mark 1.0', image_source_url: commons };
  assert.equal(types.parseArtwork(art).image_license, 'CC0-1.0');
  assert.equal(types.parseArtwork(copy).image_source_url, commons);
  for (const bad of [{ ...art, image_source_url: commons }, { ...copy, image_source_url: art.image_source_url },
    { ...copy, image_source_url: 'https://commons.wikimedia.org/wiki/Main_Page' }]) {
    assert.throws(() => types.parseArtwork(bad), types.InvalidResponse);
  }
});

test('valid agent history accepts open metadata and both citation types', () => {
  const h = history();
  h.extra = 'compatible'; h.conversation.extra = true; h.turns[0].answer.extra = { safe: true };
  h.turns[0].answer.citations.push(catalog());
  const parsed = types.parseHistory(h);
  assert.equal(parsed.turns[0].answer.citations[1].kind, 'catalog');
  assert.equal(parsed.conversation.agent.revision, 1);
});

test('response identities, initial language state and UTC dates are validated', () => {
  for (const change of [
    { conversation_id: '../foreign' }, { conversation_id: 'AAAAAAAA-2222-4222-8222-222222222222' },
    { created_at: '2026-02-30T20:00:00Z' }, { expires_at: 'tomorrow' },
    { last_answer_language: 'en' }, { auto_language: 'ko' }, { language_preference: 'es' },
  ]) assert.throws(() => types.parseConversation({ ...conversation(), ...change }), types.InvalidResponse);
  assert.throws(() => types.parseTurn({ ...turn(), client_message_id: 'broken' }), types.InvalidResponse);
});

test('full histories reject gaps, reordered sequences and duplicate turn or message identities', () => {
  for (const change of [
    (h) => h.turns.shift(),
    (h) => h.turns.reverse(),
    (h) => { h.turns[1].sequence = 1; },
    (h) => { h.turns[1].turn_id = h.turns[0].turn_id; },
    (h) => { h.turns[1].client_message_id = h.turns[0].client_message_id; },
    (h) => { h.conversation.revision = 3; },
    (h) => { h.conversation.last_answer_language = 'ko'; },
  ]) { const h = history(2); change(h); assert.throws(() => types.parseHistory(h), types.InvalidResponse); }
});

test('history cannot admit foreign sources or a different release', () => {
  for (const change of [
    (h) => { h.turns[0].dataset_version = 'foreign'; },
    (h) => { h.turns[0].answer.citations[0].artwork_id = 'aic-11723'; },
    (h) => { h.turns[0].answer.citations = [{ ...catalog(), dataset_version: 'foreign' }]; },
    (h) => { h.turns[0].answer.mode = 'fixture'; },
  ]) { const h = history(); change(h); assert.throws(() => types.parseHistory(h), types.InvalidResponse); }
});

test('pending records preserve the current contract, original revision and state/error relationship', () => {
  const h = history(); h.pending_request = pending();
  assert.equal(types.parseHistory(h).pending_request.expected_revision, 1);
  h.pending_request = { ...pending(), state: 'failed', last_error: { code: 'generation_timeout', message: 'Timed out.', retryable: true } };
  assert.equal(types.parseHistory(h).pending_request.state, 'failed');
  for (const change of [
    { contract_version: undefined }, { contract_version: 'agent-demo-v0.2' }, { expected_revision: 0 },
    { client_message_id: id(21) }, { state: 'failed' }, { last_error: undefined },
    { last_error: { code: 'generation_timeout', message: 'Timed out.', retryable: true } },
  ]) {
    h.pending_request = { ...pending(), ...change };
    assert.throws(() => types.parseHistory(h), types.InvalidResponse);
  }
});

test('source URLs and text cannot contain unsafe schemes, credentials or invalid Unicode', () => {
  for (const url of ['javascript:alert(1)', 'http://api.artic.edu/x', 'https://user:pass@api.artic.edu/x', 'not a URL']) {
    const a = turn().answer; a.citations[0].source_url = url;
    assert.throws(() => types.parseAnswer(a), types.InvalidResponse);
  }
  for (const text of ['\u0000bad', '\u0085bad', '\ud800', 'x'.repeat(2001)]) {
    assert.throws(() => types.parseAnswer({ ...turn().answer, text }), types.InvalidResponse);
  }
  assert.equal(types.parseAnswer({ ...turn().answer, text: 'A painting 🎨' }).text, 'A painting 🎨');
});

test('fixture history and pending records retain their existing wire contract', () => {
  const h = {
    conversation: fixtureConversation(), turns: [fixtureTurn()],
    pending_request: { client_message_id: id(99), question: 'Why?', answer_language: 'zh-CN', state: 'processing', last_error: null },
  };
  assert.equal(types.parseHistory(h).pending_request.language_override, 'zh-CN');
  h.pending_request.contract_version = 'agent-demo-v0.3';
  assert.throws(() => types.parseHistory(h), types.InvalidResponse);
});

test('agent POST carries the exact retry payload and allows an old turn with a current projection', async (t) => {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return Response.json({ conversation: { ...conversation(3), last_answer_language: 'ko' }, turn: turn() });
  });
  const s = submission();
  for (let i = 0; i < 2; i++) {
    const result = await client.postMessage(id(1), s, scope);
    assert.equal(result.turn.sequence, 1);
    assert.equal(result.conversation.agent.revision, 3);
  }
  assert.deepEqual(requests[0], { contract_version: 'agent-demo-v0.3', ...s });
  assert.deepEqual(requests[0], requests[1]);
});

test('successful POST cannot silently change submitted text, language, revision or scope', async (t) => {
  let response;
  t.mock.method(globalThis, 'fetch', async () => Response.json(response));
  for (const change of [
    (r) => { r.turn.question = 'A different question'; },
    (r) => { r.turn.language_override = 'ko'; },
    (r) => { r.turn.sequence = 2; },
    (r) => { r.turn.client_message_id = id(90); },
    (r) => { r.turn.answer.citations[0].artwork_id = 'aic-11723'; },
    (r) => { r.conversation.artwork_id = 'aic-11723'; },
    (r) => { r.conversation.dataset_version = 'foreign'; },
    (r) => { r.conversation.conversation_id = id(2); },
    (r) => { r.conversation.last_answer_language = 'ko'; },
    (r) => { r.conversation = conversation(); },
  ]) {
    response = { conversation: conversation(1), turn: turn() }; change(response);
    await assert.rejects(client.postMessage(id(1), submission(), scope), invalid);
  }
});

test('history and artwork HTTP responses are bound to the requested identity', async (t) => {
  let response = history();
  t.mock.method(globalThis, 'fetch', async () => Response.json(response));
  await assert.rejects(client.getHistory(id(2), { expectedScope: scope }), invalid);
  await assert.rejects(client.getHistory(id(1), { expectedScope: { ...scope, artwork_id: 'aic-11723' } }), invalid);
  const art = {
    ...scope, slug: 'aic-11723', title: 'The Bedroom', artist: 'Vincent van Gogh', date_display: '1889',
    museum: 'Art Institute of Chicago', museum_record_id: 28560, accession_number: '1926.417',
    source_url: 'https://api.artic.edu/api/v1/artworks/28560', image_url: null,
  };
  response = { artwork: art };
  await assert.rejects(client.getArtwork('aic-28560'), invalid);
});

test('malformed successful envelopes are classified consistently for reconciliation', async (t) => {
  let response;
  t.mock.method(globalThis, 'fetch', async () => Response.json(response));
  for (response of [null, [], {}, { turn: null }]) {
    await assert.rejects(client.getArtwork('aic-28560'), invalid);
    await assert.rejects(client.openConversation('aic-28560'), invalid);
    await assert.rejects(client.postMessage(id(1), submission(), scope), invalid);
  }
});

test('fixture POST retains its payload and rejects a changed requested answer language', async (t) => {
  let reply = fixtureTurn(); let sent;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    sent = JSON.parse(options.body); return Response.json({ turn: reply });
  });
  const s = { ...submission(), language_override: 'en' };
  assert.equal((await fixture.client.postMessage(id(1), s, scope)).conversation, null);
  assert.deepEqual(sent, { client_message_id: s.client_message_id, question: s.question, answer_language: 'en' });
  reply.answer_language = 'zh-CN';
  await assert.rejects(fixture.client.postMessage(id(1), s, scope), (e) => e.kind === 'invalid_response');
});
