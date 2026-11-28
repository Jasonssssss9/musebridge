import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { loadConfigFromFile } from 'vite';

const original = process.env.PUBLIC_CONTRACT;
const originalBase = process.env.PUBLIC_BASE_URL;
after(() => {
  if (original === undefined) delete process.env.PUBLIC_CONTRACT;
  else process.env.PUBLIC_CONTRACT = original;
  if (originalBase === undefined) delete process.env.PUBLIC_BASE_URL;
  else process.env.PUBLIC_BASE_URL = originalBase;
});
const configPath = fileURLToPath(new URL('../vite.config.ts', import.meta.url));
const config = async (mode = 'production') => {
  const loaded = await loadConfigFromFile({ command: 'build', mode }, configPath, undefined, 'silent');
  assert.ok(loaded, 'the checked-in Vite configuration must load');
  return loaded.config;
};

test('live builds default to the fixture contract and explicitly opt into the agent contract', async () => {
  delete process.env.PUBLIC_CONTRACT;
  const fixture = await config();
  assert.equal(JSON.parse(fixture.define['import.meta.env.VITE_PUBLIC_CONTRACT']), 'fixture');
  assert.equal(JSON.parse(fixture.define['import.meta.env.VITE_API_MODE']), 'live');
  process.env.PUBLIC_CONTRACT = 'agent';
  const agent = await config();
  assert.equal(JSON.parse(agent.define['import.meta.env.VITE_PUBLIC_CONTRACT']), 'agent');
  assert.equal(JSON.parse(agent.define['import.meta.env.VITE_API_MODE']), 'live');
  assert.equal(agent.server.proxy['/api'].timeout, 75_000);
  assert.equal(agent.server.proxy['/api'].proxyTimeout, 75_000);
  const mock = await config('mock');
  assert.equal(JSON.parse(mock.define['import.meta.env.VITE_API_MODE']), 'mock');
});

test('an invalid build contract fails instead of silently producing a fixture deployment', async () => {
  process.env.PUBLIC_CONTRACT = 'agnt';
  await assert.rejects(config(), /PUBLIC_CONTRACT must be fixture or agent/);
});

test('the Vincent entry receives only the configured public origin', async () => {
  process.env.PUBLIC_CONTRACT = 'agent';
  delete process.env.PUBLIC_BASE_URL;
  assert.equal(JSON.parse((await config()).define['import.meta.env.VITE_PUBLIC_ORIGIN']), '');
  process.env.PUBLIC_BASE_URL = 'https://vincent.example/';
  const configured = await config();
  assert.equal(JSON.parse(configured.define['import.meta.env.VITE_PUBLIC_ORIGIN']), 'https://vincent.example');
  assert.deepEqual(configured.server.allowedHosts, ['vincent.example']);
  for (const invalid of ['https://user:secret@vincent.example', 'https://vincent.example/?key=secret',
    'https://vincent.example/path', 'https://vincent.example/#fragment', 'javascript:alert(1)']) {
    process.env.PUBLIC_BASE_URL = invalid;
    await assert.rejects(config(), /PUBLIC_BASE_URL must be an HTTP\(S\) origin/);
  }
});
