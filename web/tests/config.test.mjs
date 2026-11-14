import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { loadConfigFromFile } from 'vite';

const original = process.env.PUBLIC_CONTRACT;
after(() => {
  if (original === undefined) delete process.env.PUBLIC_CONTRACT;
  else process.env.PUBLIC_CONTRACT = original;
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
