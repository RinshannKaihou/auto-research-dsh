#!/usr/bin/env node
/** One process / one DSH Agent. No user profile is created or modified. */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { spawn, execFile, execFileSync } from 'node:child_process';

const objectSchema = { type: 'object', additionalProperties: true };
const outputDefinition = { schema: objectSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] };
const inside = (base, candidate) => candidate === base || candidate.startsWith(base + path.sep);
const log = (type, detail = {}) => process.stdout.write(JSON.stringify({ type, t: new Date().toISOString(), ...detail }) + '\n');

/** Resolve symlinks even when the final file does not exist. */
export function canonical(candidate) {
  const tail = [];
  let current = path.resolve(candidate);
  while (true) {
    try { fs.lstatSync(current); break; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    const parent = path.dirname(current);
    if (parent === current) throw new Error('PATH_NOT_FOUND');
    tail.unshift(path.basename(current)); current = parent;
  }
  return path.join(fs.realpathSync(current), ...tail);
}

export function normalizeRequest(raw) {
  if (!raw || !['worker', 'coordinator'].includes(raw.role) || typeof raw.prompt !== 'string') throw new Error('INVALID_REQUEST');
  const workspace = fs.realpathSync(raw.workspace);
  if (!fs.statSync(workspace).isDirectory()) throw new Error('INVALID_WORKSPACE');
  const writeRoots = (raw.write_roots ?? [workspace]).map(canonical);
  if (writeRoots.length !== 1 || writeRoots[0] !== workspace) throw new Error('WRITE_ROOT_MUST_EQUAL_WORKSPACE');
  const readRoots = [...new Set((raw.read_roots ?? [workspace, raw.history_dir].filter(Boolean)).map(canonical))];
  if (!readRoots.includes(workspace)) readRoots.push(workspace);
  const readonlyRoots = (raw.readonly_roots ?? []).map(canonical);
  if (raw.history_dir) readonlyRoots.push(canonical(raw.history_dir));
  if (readonlyRoots.some(root => inside(root, workspace))) throw new Error('WORKSPACE_INSIDE_READONLY_ROOT');
  const timeout = raw.timeout_s ?? 600;
  if (!(Number.isFinite(timeout) && timeout > 0 && timeout <= 86400)) throw new Error('INVALID_TIMEOUT');
  if (!raw.output_schema || raw.output_schema.type !== 'object') throw new Error('OUTPUT_SCHEMA_MUST_BE_OBJECT');
  if (raw.dsh_config !== undefined && (typeof raw.dsh_config !== 'string' || !fs.existsSync(raw.dsh_config))) throw new Error('INVALID_DSH_CONFIG');
  return { ...raw, workspace, read_roots: readRoots, write_roots: writeRoots, readonly_roots: [...new Set(readonlyRoots)], timeout_s: timeout,
    ...(raw.dsh_config ? { dsh_config: fs.realpathSync(raw.dsh_config) } : {}),
  };
}

export function allowedPath(request, candidate, write = false) {
  const resolved = canonical(path.resolve(request.workspace, candidate));
  const roots = write ? request.write_roots : request.read_roots;
  if (!roots.some(root => inside(root, resolved)) || (write && request.readonly_roots.some(root => inside(root, resolved)))) {
    const error = new Error(write ? 'WRITE_DENIED' : 'READ_DENIED'); error.code = error.message; throw error;
  }
  return resolved;
}

/** Read user-selected materials without asking the model to locate a PDF runtime. */
export async function readMaterial(request, candidate, { offset = 0, limit = 48000, signal } = {}) {
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 48000) throw new Error('INVALID_READ_RANGE');
  const filename = allowedPath(request, candidate);
  const stat = await fsp.stat(filename);
  if (stat.isDirectory()) {
    const entries = (await fsp.readdir(filename, { withFileTypes: true }))
      .map(entry => ({ name: entry.name, kind: entry.isSymbolicLink() ? 'symlink' : entry.isDirectory() ? 'directory' : 'file' }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return { path: filename, format: 'directory', entries: entries.slice(0, 1000), truncated: entries.length > 1000 };
  }
  const handle = await fsp.open(filename, 'r');
  const header = Buffer.alloc(5);
  try { await handle.read(header, 0, 5, 0); } finally { await handle.close(); }
  let content, format = 'text';
  if (header.toString() === '%PDF-') {
    const binary = (process.env.PATH ?? '/usr/local/bin:/opt/homebrew/bin:/usr/bin').split(path.delimiter)
      .map(directory => path.join(directory, 'pdftotext')).find(file => {
        try { fs.accessSync(file, fs.constants.X_OK); return fs.statSync(file).isFile(); } catch { return false; }
      });
    if (!binary) throw new Error('PDF_TEXT_EXTRACTOR_UNAVAILABLE');
    const profile = seatbeltProfile(request, [path.dirname(fs.realpathSync(binary))]);
    content = await new Promise((resolve, reject) => {
      execFile('/usr/bin/sandbox-exec', ['-p', profile, binary, '-layout', '-enc', 'UTF-8', filename, '-'], {
        encoding: 'utf8', maxBuffer: 8 * 1024 * 1024, timeout: 30000, signal,
        env: { PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: path.join(request.workspace, '.home'), TMPDIR: path.join(request.workspace, '.tmp'), LANG: 'en_US.UTF-8' },
      }, (error, stdout) => error ? reject(new Error('PDF_TEXT_EXTRACTION_FAILED')) : resolve(stdout));
    });
    format = 'pdf-text';
    if (!content.trim()) throw new Error('PDF_HAS_NO_EXTRACTABLE_TEXT');
  } else {
    if (stat.size > 8 * 1024 * 1024) throw new Error('TEXT_FILE_TOO_LARGE');
    content = await fsp.readFile(filename, 'utf8');
    if (content.includes('\u0000')) throw new Error('BINARY_FILE_NOT_SUPPORTED');
  }
  return { path: filename, format, content: content.slice(offset, offset + limit), total_characters: content.length,
    next_offset: offset + limit < content.length ? offset + limit : null };
}

function findDshPackage() {
  const candidates = [process.env.ARI_DSH_PACKAGE];
  for (const entry of (process.env.PATH ?? '').split(path.delimiter)) {
    const bin = path.join(entry, 'dsh');
    try { candidates.push(path.resolve(fs.realpathSync(bin), '../../package.json')); } catch {}
  }
  candidates.push(path.join(os.homedir(), '.hermes/node/lib/node_modules/@deepseek-ai/dsh/package.json'));
  for (const candidate of candidates.filter(Boolean)) {
    try { if (JSON.parse(fs.readFileSync(candidate, 'utf8')).name === '@deepseek-ai/dsh') return fs.realpathSync(candidate); } catch {}
  }
  throw new Error('DSH_NOT_FOUND: install DSH or set ARI_DSH_PACKAGE to its package.json');
}

/** Kernel confines arbitrary shell code; the file tools separately check canonical paths. */
export function seatbeltProfile(request, runtimeRoots = []) {
  if (process.platform !== 'darwin') throw new Error('SANDBOX_UNSUPPORTED: v0.1 DSH shell backend requires macOS Seatbelt');
  const systemRoots = ['/System', '/usr', '/bin', '/sbin', '/Library', '/private/etc', '/private/var/db/dyld', '/dev'];
  const reads = [...new Set([...systemRoots, ...runtimeRoots, ...request.read_roots].filter(p => fs.existsSync(p)).map(canonical))];
  const subpath = p => `(subpath ${JSON.stringify(p)})`;
  return [
    '(version 1)', '(allow default)', '(deny file-read-data)', '(deny file-write*)',
    `(allow file-read-data (literal "/") ${reads.map(subpath).join(' ')})`,
    `(allow file-write* ${request.write_roots.map(subpath).join(' ')})`,
    '(allow file-write-data (literal "/dev/null"))',
    ...request.readonly_roots.map(root => `(deny file-write* ${subpath(root)})`),
  ].join('\n');
}

function descendants(pid) {
  try {
    const rows = execFileSync('/bin/ps', ['-axo', 'pid=,ppid='], { encoding: 'utf8' }).trim().split('\n').map(line => line.trim().split(/\s+/).map(Number));
    const found = new Set([pid]);
    for (let changed = true; changed;) { changed = false; for (const [child, parent] of rows) if (found.has(parent) && !found.has(child)) { found.add(child); changed = true; } }
    return [...found].reverse();
  } catch { return [pid]; }
}

export function makeShell(request, runtimeRoots = []) {
  const children = new Set();
  const profile = seatbeltProfile(request, runtimeRoots);
  const temp = path.join(request.workspace, '.tmp');
  const home = path.join(request.workspace, '.home');
  fs.mkdirSync(temp, { recursive: true }); fs.mkdirSync(home, { recursive: true });
  const execute = (command, signal, timeoutMs = request.timeout_s * 1000) => new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('CANCELLED')); return; }
    // No inherited API-key environment, shell startup files, or detached process group.
    const child = spawn('/usr/bin/sandbox-exec', ['-p', profile, '/bin/bash', '--noprofile', '--norc', '-c', command], {
      cwd: request.workspace, env: { PATH: process.env.PATH ?? '/usr/bin:/bin', HOME: home, TMPDIR: temp, TMP: temp, TEMP: temp, LANG: 'en_US.UTF-8' },
      stdio: ['ignore', 'pipe', 'pipe'], detached: false,
    });
    let finish;
    const done = new Promise(resolve => { finish = resolve; });
    let stdout = '', stderr = '', aborted = false, escalation, closed, failure;
    const stop = () => {
      if (aborted) return;
      aborted = true;
      const pids = descendants(child.pid);
      for (const pid of pids) { try { process.kill(pid, 'SIGTERM'); } catch {} }
      escalation = setTimeout(() => {
        for (const pid of pids) { try { process.kill(pid, 'SIGKILL'); } catch {} }
        escalation = undefined; if (closed) settle();
      }, 1000);
    };
    const record = { stop, done }; children.add(record);
    const timer = setTimeout(stop, Math.max(1, Math.min(timeoutMs, request.timeout_s * 1000)));
    signal?.addEventListener('abort', stop, { once: true });
    child.stdout.on('data', chunk => { stdout = (stdout + chunk).slice(-24000); });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-12000); });
    child.on('error', error => { failure = error; });
    child.on('close', (code, exitSignal) => { closed = { code, exitSignal }; if (!escalation) settle(); });
    function settle() {
      clearTimeout(timer); signal?.removeEventListener('abort', stop); children.delete(record); finish();
      if (failure) reject(failure);
      else resolve({ exit_code: closed.code, signal: closed.exitSignal, cancelled: aborted, stdout, stderr, cwd: request.workspace });
    }
  });
  return { execute, async close() { const active = [...children]; for (const child of active) child.stop(); await Promise.all(active.map(child => child.done)); }, children };
}

export async function createRuntime(request, { event = log } = {}) {
  const dshPackage = findDshPackage();
  const require = createRequire(dshPackage);
  const load = name => import(pathToFileURL(require.resolve(name)).href);
  const [{ Context }, spine, settings, credentials, defaults, deepseek, pi, extensions, { validateJsonSchemaValue }, llm] = await Promise.all([
    load('@deepseek-ai/cordis'), load('@deepseek-ai/dsh-agent-spine-demo'), load('@deepseek-ai/dsh-settings-file'),
    load('@deepseek-ai/dsh-credentials-local'), load('@deepseek-ai/dsh-agent-default-model'), load('@deepseek-ai/dsh-llm-deepseek'),
    load('@deepseek-ai/dsh-llm-pi-ai'), load('@deepseek-ai/dsh-deepseek-llm-api-extensions'), load('@deepseek-ai/dsh-tools'), load('@deepseek-ai/dsh-llm'),
  ]);
  const root = new Context();
  const assertValid = (schema, value) => {
    if (validateJsonSchemaValue(schema, value).length) throw new Error('INVALID_SCHEMA');
  };
  let dshHome = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
  let settingsPath;
  if (request.dsh_config) {
    const selected = fs.realpathSync(request.dsh_config);
    if (fs.statSync(selected).isDirectory()) dshHome = selected;
    else settingsPath = selected;
  }
  const runtimeRoot = path.dirname(path.dirname(path.dirname(dshPackage)));
  const shell = makeShell(request, [path.dirname(process.execPath), runtimeRoot]);
  await root.plugin(spine, { agents: [], skills: { enabled: false }, workspaceContext: false, toolBash: false, toolJobs: false, goals: false,
    includeHarnessIdentity: false, includeRuntimeContext: false,
    persona: `You are a research ${request.role}. Your actual working directory is ${request.workspace}. Use the provided tools for files and commands. Call submit_result with your final structured answer, then finish. Do not start background daemons.`,
  });
  await root.plugin(settings.default, { dshHome, ...(settingsPath ? { path: settingsPath } : {}), watch: false });
  await root.plugin(credentials.default, { dshHome, watch: false });
  const configured = (await root.get('settings').load())['agent-default-model'];
  if (!configured || typeof configured.provider !== 'string' || typeof configured.model !== 'string') { await root.fiber.dispose(); throw new Error('NO_CONFIGURED_DSH_MODEL'); }
  await root.plugin(defaults.default, { provider: configured.provider, model: configured.model });
  await root.plugin(extensions.default);
  await root.plugin(deepseek, {});
  await root.plugin(pi, {});

  const tools = root.get('tools');
  let submitted;
  const argsSchema = properties => ({ type: 'object', additionalProperties: false, properties, required: Object.keys(properties) });
  const string = { type: 'string' };
  const register = (name, description, parameters, execute) => tools.register({ name, description, parameters, output: outputDefinition,
    execute: async (args, exec) => { assertValid(parameters, args); event('tool_started', { name }); try { const value = await execute(args, exec); event('tool_finished', { name, ...(name === 'run' ? { exit_code: value.exit_code, cancelled: value.cancelled } : {}) }); return value; } catch (error) { event('tool_denied_or_failed', { name, code: error.code ?? error.message }); throw error; } },
  });
  // These canonical checks are the tool's mandatory dispatch guard, not prompt advice.
  register('read', 'Read an allowed text file or extract PDF text; a directory returns its entries. Use next_offset to read later text. Source files stay unchanged.', { type: 'object', additionalProperties: false, properties: { path: string, offset: { type: 'integer', minimum: 0 }, limit: { type: 'integer', minimum: 1, maximum: 48000 } }, required: ['path'] }, (args, exec) => readMaterial(request, args.path, { ...args, signal: exec.signal }));
  register('write', 'Write a UTF-8 file only inside this workspace; inputs and history are read-only.', argsSchema({ path: string, content: string }), async args => {
    const target = allowedPath(request, args.path, true); await fsp.mkdir(path.dirname(target), { recursive: true });
    const checked = allowedPath(request, target, true);
    const temporary = allowedPath(request, path.join(path.dirname(checked), `.ari-write-${randomUUID()}`), true);
    try { await fsp.writeFile(temporary, args.content, { encoding: 'utf8', flag: 'wx' }); await fsp.rename(temporary, allowedPath(request, checked, true)); }
    finally { await fsp.rm(temporary, { force: true }); }
    return { written: checked };
  });
  register('run', 'Run a foreground bash command in the private workspace. Kernel sandbox enforces the same file boundaries. No background daemons.', argsSchema({ command: string }), (args, exec) => shell.execute(args.command, exec.signal));
  register('submit_result', 'Submit the final structured result after saving all products and progress. Call exactly once, then finish.', request.output_schema, async args => { submitted = structuredClone(args); return { accepted: true }; });
  const selection = root.get('agentDefaultModel').currentSelection();
  const handle = await root.get('agents').create({ sessionId: `ari-${randomUUID()}`, meta: { cwd: request.workspace }, agentOptions: { ...selection,
    ...(process.env.ARI_DSH_MAX_TOKENS ? { maxTokens: Number(process.env.ARI_DSH_MAX_TOKENS) } : {}),
  } });
  const agent = handle.agent;
  await agent.whenIdle();
  let usage = null;
  root.on('session/event', (session, evt) => {
    if (session !== agent.session) return;
    if (evt.type === 'assistant/message') {
      const u = evt.data.usage ?? evt.data.message?.usage;
      if (u && [u.totalTokens, u.inputTokens, u.outputTokens].some(value => typeof value === 'number')) usage = (usage ?? 0) + (u.totalTokens ?? ((u.inputTokens ?? 0) + (u.outputTokens ?? 0) + (u.cacheReadTokens ?? 0) + (u.cacheWriteTokens ?? 0)));
    }
    if (['turn/start', 'turn/end'].includes(evt.type)) event(evt.type.replace('/', '_'), { session_id: session.id, ...(evt.type === 'turn/end' ? { reason: evt.data.reason?.kind, tokens: usage } : {}) });
  });
  event('ready', { cwd: process.cwd(), workspace: request.workspace, session_id: agent.session.id, provider: selection.provider, model: selection.model });
  return { root, agent, shell, tools, selection, validateJsonSchemaValue, getUsage: () => usage,
    async run() {
      const firstSeq = agent.session.seq;
      agent.followup(llm.createUserMessage({ content: [{ type: 'text', text: request.prompt + '\n\nSubmit the final answer through submit_result after completing the requested work. Required result JSON schema:\n' + JSON.stringify(request.output_schema) }], source: { kind: 'user' } }));
      await agent.whenIdle();
      const events = agent.session.events.filter(evt => evt.seq >= firstSeq);
      const ending = events.filter(evt => evt.type === 'turn/end').at(-1);
      if (ending?.data.reason?.kind !== 'completed') throw new Error('AGENT_' + (ending?.data.reason?.kind ?? 'NO_COMPLETION').toUpperCase());
      const messages = events.filter(evt => evt.type === 'assistant/message');
      const text = messages.map(evt => evt.data.message.content.filter(block => block.type === 'text').map(block => block.text).join('')).filter(Boolean).at(-1) ?? '';
      const stripped = text.trim().replace(/^```(?:json)?\s*/, '').replace(/\s*```$/, '');
      const result = submitted ?? JSON.parse(stripped); assertValid(request.output_schema, result);
      return { result, usage: { tokens: usage }, session_id: agent.session.id };
    },
    async close() { await shell.close(); await handle.dispose(); await root.fiber.dispose(); },
  };
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 2) throw new Error('Usage: node apps/dsh/worker.mjs request.json output.json');
  const input = path.resolve(argv[0]), output = path.resolve(argv[1]);
  let runtime, timer, cancelled = false;
  const publish = async result => {
    await fsp.mkdir(path.dirname(output), { recursive: true });
    const tmp = `${output}.${process.pid}.tmp`; await fsp.writeFile(tmp, JSON.stringify(result) + '\n', { mode: 0o600 }); await fsp.rename(tmp, output);
  };
  const cancel = () => { cancelled = true; runtime?.agent.cancel('external-cancel'); void runtime?.shell.close(); };
  process.once('SIGTERM', cancel); process.once('SIGINT', cancel);
  try {
    const request = normalizeRequest(JSON.parse(await fsp.readFile(input, 'utf8')));
    process.chdir(request.workspace);
    timer = setTimeout(cancel, request.timeout_s * 1000);
    runtime = await createRuntime(request);
    if (cancelled) throw new Error('CANCELLED');
    const result = await runtime.run();
    if (cancelled) throw new Error('CANCELLED');
    await publish(result);
    log('completed', { session_id: result.session_id, tokens: result.usage.tokens });
  } catch (error) {
    await publish({ result: null, usage: { tokens: runtime?.getUsage() ?? null }, session_id: runtime?.agent.session.id ?? null,
      error: { code: /^[A-Z_]+$/.test(error.message) ? error.message : 'BACKEND_ERROR' } });
    throw error;
  } finally {
    clearTimeout(timer); await runtime?.close(); process.removeListener('SIGTERM', cancel); process.removeListener('SIGINT', cancel);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { log('failed', { code: /^[A-Z_]+$/.test(error.message) ? error.message : 'BACKEND_ERROR', error_type: error.name }); process.exitCode = 1; });
}
