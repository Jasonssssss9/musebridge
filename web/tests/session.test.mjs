import assert from 'node:assert/strict';
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
  const first = { client_message_id: 'first', question: 'Original question?', answer_language: 'en' };
  const edited = { client_message_id: 'second', question: 'Edited question?', answer_language: 'zh-CN' };
  saveDraft(conversationId, first);
  stageDraft(conversationId, edited);
  stageDraft(conversationId, edited);
  assert.deepEqual(loadDraft(conversationId), { ...edited, previous: first });

  clearDraft(conversationId, first.client_message_id);
  assert.deepEqual(loadDraft(conversationId), { ...edited, previous: undefined });
  clearDraft(conversationId, edited.client_message_id);
  assert.equal(loadDraft(conversationId), null);
});
