import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';

import { StorageClient } from '../../apps/dsh/plugin/ipc.js';
import { SessionAdapter } from '../../apps/dsh/plugin/session-adapter.js';
import { ResearchDomain } from '../../apps/dsh/plugin/domain.js';
import { defineResearchTool } from '../../apps/dsh/plugin/tool-definition.js';

const python = process.env.ARI_TEST_PYTHON ?? 'python3';
const pythonModulePath = resolve('src');

test('private IPC initializes schema 9 and deduplicates business operations', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'ari-ipc-test-'));
  const root = join(directory, 'project');
  const client = new StorageClient({
    python,
    pythonModulePath,
    registryPath: join(directory, 'registry.sqlite3'),
  });
  const identity = { host_id: 'host', session_id: 'session' };
  try {
    const opened = await client.request('open', identity, {
      root,
      goal: '中文 fixture',
    }, 'open');
    assert.equal(opened.schema_version, 9);
    assert.equal(opened.project.goal, '中文 fixture');
    const first = await client.request('focus', identity, {
      node_id: null,
      role: 'planner',
      mode: 'manual',
    }, 'focus');
    const retry = await client.request('focus', identity, {
      node_id: null,
      role: 'planner',
      mode: 'manual',
    }, 'focus');
    assert.deepEqual(retry, first);
    const values = await Promise.all(Array.from({ length: 5 }, () => (
      client.request('query', identity)
    )));
    assert(values.every(value => value.project.goal === '中文 fixture'));
    const exit = once(client.child, 'exit');
    client.close();
    await exit;
    await assert.rejects(client.request('query', identity), /disconnected/);
  } finally {
    client.close();
    rmSync(directory, { recursive: true, force: true });
  }
});

test('generated client exposes blank-session research command receipt',()=>{
  const client=readFileSync(resolve('apps/dsh/plugin/client.js'),'utf8');
  assert.match(client,/conversation\.input\.dock/);
  assert.match(client,/command\/executed/);
  assert.match(client,/research-receipt/);
  assert.match(client,/useReceipt\(value=>value\)/);
});

test('session adapter uses ordinary native sessions and preserves model selection', async () => {
  const calls = [];
  const controller = {
    inspect: async sessionId => ({ sessionId }),
    create: async request => { calls.push(['create', request]); return { sessionId: 'child' }; },
    selectModel: async request => { calls.push(['model', request]); return { selected: request }; },
    cancel: request => { calls.push(['cancel', request]); return { accepted: true }; },
  };
  const adapter = new SessionAdapter({ sessionController: controller });
  assert.deepEqual(await adapter.inspect('native-session'), { sessionId: 'native-session' });
  await adapter.create({ cwd: '/tmp/work', agentPreset: 'standard' });
  await adapter.selectModel('child', { provider: 'fixture', model: 'deterministic' });
  await adapter.cancel('child');
  assert.deepEqual(calls, [
    ['create', { cwd: '/tmp/work', agentPreset: 'standard' }],
    ['model', { sessionId: 'child', provider: 'fixture', model: 'deterministic' }],
    ['cancel', { sessionId: 'child' }],
  ]);
});

test('native session absence is distinguished from IO failure', async () => {
  class ApiSessionNotFound extends Error {}
  const controller = { inspect: async () => { throw new ApiSessionNotFound('absent'); } };
  const adapter = new SessionAdapter({ sessionController: controller });
  assert.equal(await adapter.exists('missing'), false);
  controller.inspect = async () => { throw new Error('disk unavailable'); };
  await assert.rejects(adapter.exists('unknown'), /disk unavailable/);
  controller.inspect = async () => ({meta:{id:'present'},events:[]});
  assert.equal(await adapter.exists('present'), true);
});

test('local tool definitions expose JSON schema and validate required arguments', async () => {
  const definition = defineResearchTool({
    name: 'sample',
    description: 'sample',
    parameters: { text: { type: 'string', required: true } },
    output: {
      schema: { type: 'object', additionalProperties: true, properties: {} },
      render: () => [],
    },
    execute: async args => ({ text: args.text }),
  });
  assert.deepEqual(definition.parameters.required, ['text']);
  await assert.rejects(definition.execute({}, {}), /required/);
  assert.deepEqual(await definition.execute({ text: 'ok' }, {}), { text: 'ok' });
});
