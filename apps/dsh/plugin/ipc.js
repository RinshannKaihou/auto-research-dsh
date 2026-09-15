import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

export function bundledPythonPath() {
  const bundled = join(here, 'python');
  if (existsSync(join(bundled, 'auto_research', 'service.py'))) return bundled;
  return join(here, '..', '..', '..', 'src');
}

/** One private inherited channel, bounded pending calls and frames; no model API. */
export class StorageClient {
  constructor({ python = 'python3', pythonModulePath, registryPath, timeoutMs = 15000 } = {}) {
    const version = spawnSync(python, ['-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], {
      encoding: 'utf8',
      timeout: 5000,
    });
    if (version.status !== 0 || !/^3\.(?:1[1-9]|[2-9]\d)$/.test(version.stdout.trim())) {
      throw new Error(`Auto Research requires local Python 3.11+ (${python} is unavailable or too old)`);
    }
    this.pending = new Map();
    this.queue = [];
    this.closed = false;
    this.buffer = Buffer.alloc(0);
    this.timeoutMs = timeoutMs;
    const modulePath = pythonModulePath ?? bundledPythonPath();
    const registry = registryPath ?? join(homedir(), '.dsh', 'auto-research-v5', 'registry.sqlite3');
    this.child = spawn(python, ['-m', 'auto_research.service', '--registry', registry], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { PATH: process.env.PATH, PYTHONIOENCODING: 'utf-8', PYTHONPATH: modulePath },
    });
    this.child.stdout.on('data', data => this.receive(data));
    // Do not send host/Python diagnostics containing configuration into browser responses.
    this.child.stderr.on('data', () => {});
    this.child.on('error', () => this.fail(new Error('Storage service unavailable')));
    this.child.on('exit', () => this.fail(new Error('Storage service disconnected')));
    this.child.stdin.on('error', () => this.fail(new Error('Storage channel closed')));
  }
  receive(data) {
    this.buffer = Buffer.concat([this.buffer, data]);
    while (true) {
      const end = this.buffer.indexOf(10);
      if (end < 0) break;
      if (end > 4 * 1024 * 1024) return this.close();
      let response;
      try { response = JSON.parse(this.buffer.subarray(0, end).toString('utf8')); }
      catch { return this.close(); }
      this.buffer = this.buffer.subarray(end + 1);
      const pending = this.pending.get(response.request_id);
      if (!pending) continue;
      this.pending.delete(response.request_id);
      clearTimeout(pending.timer);
      response.ok ? pending.resolve(response.value) : pending.reject(new Error(response.error?.message ?? 'Storage request failed'));
      this.pump();
    }
    if (this.buffer.length > 4 * 1024 * 1024) this.close();
  }
  request(method, identity = {}, fields = {}, operationId = randomUUID()) {
    if (this.closed) return Promise.reject(new Error('Storage service disconnected'));
    if (this.queue.length >= 4096) return Promise.reject(new Error('Storage queue capacity exceeded; retry with the same operation ID'));
    const requestId = randomUUID();
    const line = JSON.stringify({
      ...fields,
      ...identity,
      transport_id: requestId,
      operation_id: operationId,
      method,
    }) + '\n';
    if (Buffer.byteLength(line) > 64 * 1024) return Promise.reject(new Error('Request too large'));
    return new Promise((resolve, reject) => {
      const priority = /^(usage_|workflow|own_goal|finish|bind_turn)/.test(method) ? 0 : method === 'host_events' ? 2 : 1;
      this.queue.push({ requestId, line, resolve, reject, priority, retries: 0 });
      this.queue.sort((a,b) => a.priority-b.priority);
      this.pump();
    });
  }
  pump() {
    while (!this.closed && this.pending.size < 4 && this.queue.length) {
      const entry = this.queue.shift();
      entry.timer = setTimeout(() => {
        this.pending.delete(entry.requestId);
        if (++entry.retries <= 2) this.queue.unshift(entry);
        else entry.reject(new Error('Storage service timed out; last view may be stale'));
        this.pump();
      }, this.timeoutMs);
      this.pending.set(entry.requestId, entry);
      this.child.stdin.write(entry.line);
    }
  }

  fail(error) {
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    for (const entry of this.queue.splice(0)) entry.reject(error);
  }
  close() {
    this.fail(new Error('Storage service closed'));
    this.child.stdin.destroy();
    this.child.kill('SIGTERM');
  }
}
