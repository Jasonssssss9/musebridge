import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, test } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { createServer } from 'vite';

const vite = await createServer({
  configFile: false,
  appType: 'custom',
  define: { 'import.meta.env.VITE_API_MODE': JSON.stringify('live') },
  optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, hmr: false, watch: null },
});
after(() => vite.close());

test('serializes conversation creation across artworks and coalesces the same artwork', async (t) => {
  const { openConversation } = await vite.ssrLoadModule('/src/api/client.ts');
  const requests = [];
  let cookieEstablished = false;
  let releaseFirst;
  const firstResponse = new Promise((resolve) => { releaseFirst = resolve; });
  t.mock.method(globalThis, 'fetch', async (_path, options) => {
    const artworkId = JSON.parse(options.body).artwork_id;
    requests.push({ artworkId, cookieEstablished });
    if (artworkId === 'aic-28560') {
      await firstResponse;
      cookieEstablished = true;
    }
    return Response.json({
      conversation: {
        conversation_id: artworkId + '-conversation',
        artwork_id: artworkId,
        dataset_version: 'aic-seed-v0.1',
        created_at: '2026-11-07T18:00:00Z',
        expires_at: '2026-11-08T18:00:00Z',
      },
    });
  });

  const bedroom = openConversation('aic-28560');
  const duplicate = openConversation('aic-28560');
  const toilette = openConversation('aic-11723');
  try {
    await setImmediate();
    assert.equal(bedroom, duplicate);
    assert.deepEqual(requests, [{ artworkId: 'aic-28560', cookieEstablished: false }]);
  } finally {
    releaseFirst();
    await Promise.all([bedroom, duplicate, toilette]);
  }
  assert.deepEqual(requests, [
    { artworkId: 'aic-28560', cookieEstablished: false },
    { artworkId: 'aic-11723', cookieEstablished: true },
  ]);
});

test('keeps an edited draft when an earlier submission finishes', async (t) => {
  const { clearDraft, loadDraft, saveDraft, stageDraft } = await vite.ssrLoadModule('/src/chat/drafts.ts');
  const storage = new Map();
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original);
    else delete globalThis.sessionStorage;
  });

  const conversationId = 'aic-28560-conversation';
  const first = { client_message_id: 'first', question: 'Original question?', language_override: null, expected_revision: 2 };
  const edited = { client_message_id: 'second', question: 'Edited question?', language_override: 'ko', expected_revision: 2 };
  saveDraft(conversationId, first);
  stageDraft(conversationId, edited);
  stageDraft(conversationId, edited);
  assert.deepEqual(loadDraft(conversationId), { ...edited, previous: first });

  clearDraft(conversationId, first.client_message_id);
  assert.deepEqual(loadDraft(conversationId), { ...edited, previous: undefined });
  clearDraft(conversationId, edited.client_message_id);
  assert.equal(loadDraft(conversationId), null);
});

test('reads the shared agent-demo-v0.3 public examples the way the contract labels them', async () => {
  const { parseAnswer, parseConversation, parseHistory, parseTurn } = await vite.ssrLoadModule('/src/api/types.ts');
  const parsers = {
    PublicAnswer: parseAnswer,
    PublicConversation: parseConversation,
    PublicTurn: parseTurn,
    PublicHistoryResponse: parseHistory,
    PublicConversationResponse: (payload) => parseConversation(payload.conversation),
    PublicSubmissionResponse: (payload) => [parseConversation(payload.conversation), parseTurn(payload.turn)],
  };
  const examples = JSON.parse(readFileSync(new URL('../../docs/contracts/agent-v2.examples.json', import.meta.url), 'utf8'));
  const cases = examples.schema_cases.filter((c) => parsers[c.definition]);
  assert.ok(cases.length >= 6, 'expected the public response examples to be present');
  for (const c of cases) {
    let accepted = true;
    try { parsers[c.definition](c.payload); } catch { accepted = false; }
    assert.equal(accepted, c.valid, c.definition + ' / ' + c.name);
  }
});

test('never shows an unknown status, a mislabelled catalog fact or a stale revision as a normal answer', async () => {
  const { parseAnswer, parseHistory } = await vite.ssrLoadModule('/src/api/types.ts');
  const catalog = {
    kind: 'catalog', fact_id: 'aic-28560-catalog-artist', artwork_id: 'aic-28560', dataset_version: 'aic-seed-v0.1',
    field: 'artist', value: 'Vincent van Gogh', source_id: 'aic-api-28560',
    source_title: 'The Bedroom — Art Institute of Chicago API record', source_url: 'https://api.artic.edu/api/v1/artworks/28560',
    source_locator: 'artworks[artwork_id=aic-28560].artist', attribution: 'Art Institute of Chicago',
    normalization: 'Released catalog metadata; not a verbatim museum excerpt.',
  };
  const answer = (status, citations) => ({ mode: 'llm', status, text: 'Text.', citations });
  assert.equal(parseAnswer(answer('partially_answered', [catalog])).citations[0].kind, 'catalog');
  assert.throws(() => parseAnswer(answer('surprising_new_status', [])));
  assert.throws(() => parseAnswer(answer('clarification_needed', [catalog])));
  assert.throws(() => parseAnswer(answer('partially_answered', [])));
  assert.throws(() => parseAnswer(answer('answered', [{ ...catalog, kind: 'something_else' }])));
  assert.throws(() => parseAnswer({ ...answer('answered', [catalog]), mode: 'fixture' }));

  const conversation = {
    contract_version: 'agent-demo-v0.3', conversation_id: '22222222-2222-4222-8222-222222222222', artwork_id: 'aic-28560',
    dataset_version: 'aic-seed-v0.1', created_at: '2026-11-13T20:00:00Z', expires_at: '2026-11-14T20:00:00Z',
    revision: 3, language_preference: 'auto', auto_language: 'en', last_answer_language: 'en',
  };
  assert.throws(() => parseHistory({ conversation, turns: [], pending_request: null }));
  assert.throws(() => parseHistory({ conversation: { ...conversation, contract_version: 'agent-demo-v9' }, turns: [], pending_request: null }));
});
