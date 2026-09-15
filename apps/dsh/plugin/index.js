import { StorageClient } from './ipc.js';
import { SessionAdapter } from './session-adapter.js';
import { ResearchDomain } from './domain.js';
import { registerResearchCommand } from './commands.js';
import { registerResearchTools } from './tools.js';
import { registerResearchEvents } from './events.js';

export const name = 'auto-research-v5';
export const inject = ['commands', 'tools', 'systemPrompt', 'llm', 'goals', 'agents', 'sessionController', 'jobs', 'sessionProjections', 'subagents'];
export const RPC_CHANNEL = '/research-v5';

class UnavailableStorage {
  constructor(error) { this.error = error; }
  request() { return Promise.reject(this.error); }
  close() {}
}

function agentFor(ctx, sessionId) {
  if (typeof sessionId !== 'string' || !sessionId) throw new Error('sessionId is required');
  const agent = ctx.agents.get(sessionId);
  if (!agent) throw new Error('The native session is not currently attached');
  return agent;
}

export function apply(ctx, config = {}) {
  let storage;
  try {
    storage = new StorageClient(config);
  } catch (error) {
    storage = new UnavailableStorage(error instanceof Error ? error : new Error(String(error)));
  }
  const adapter = new SessionAdapter(ctx);
  const domain = new ResearchDomain(ctx, storage, adapter, config);
  const disposers = [];

  disposers.push(ctx.systemPrompt.section({
    name: 'tool:research',
    order: 700,
    text: [
      'When this native session is associated with a research project, use research_* tools to preserve durable research structure.',
      'Use DSH native tools for actual work. Publications may be partial and do not imply scientific validation. Finish work segments and close nodes explicitly.',
      'Do not start autonomous research unless the user enabled it through /research auto.',
      'In manual mode, start a consolidation reviewer only when the user explicitly asks for consolidation or review.',
    ].join('\n'),
  }));
  disposers.push(registerResearchCommand(ctx, domain));
  disposers.push(...registerResearchTools(ctx, domain));
  disposers.push(registerResearchEvents(ctx, domain));

  ctx.inject(['connection', 'webServer'], scoped => {
    scoped.connection.rpc.handle(RPC_CHANNEL, async (endpoint, payload = {}) => {
      try {
        if (endpoint === 'capabilities') {
          const value = await storage.request('capabilities', {}, {}, `capabilities:${payload.sessionId ?? 'none'}`);
          return { ok: true, value };
        }
        const agent = agentFor(ctx, payload.sessionId);
        const id = payload.operationId ?? `${agent.id}:workbench:${Date.now()}`;
        if (endpoint === 'query' || endpoint === 'status') {
          return { ok: true, value: await domain.query(agent) };
        }
        if (endpoint === 'history.page') {
          return { ok: true, value: await domain.page(agent, payload.collection, payload.cursor ?? {}, payload.limit ?? 50) };
        }
        if (endpoint === 'usage.page') {
          return { ok: true, value: await domain.page(agent, 'usage', payload.cursor ?? {}, payload.limit ?? 50) };
        }
        if (endpoint === 'changes.page') {
          return { ok: true, value: await domain.page(agent, 'changes', payload.cursor ?? {}, payload.limit ?? 50) };
        }
        if (endpoint === 'reference.get') {
          return { ok: true, value: await domain.lookup(agent, { ref: payload.ref }) };
        }
        if (endpoint === 'guidance.status') return { ok: true, value: await domain.guidanceStatus(agent) };
        if (endpoint === 'guidance.register') return { ok: true, value: await domain.guidanceRegister(agent, payload.path, payload.version, id) };
        if (endpoint === 'context.preview') return { ok: true, value: await domain.contextPreview(agent) };
        if (endpoint === 'open') {
          return { ok: true, value: await domain.open(agent, {
            goal: payload.goal,
          }, id) };
        }
        if (endpoint === 'auto') return {
          ok: true,
          value: await domain.auto(agent, id),
        };
        if (endpoint === 'pause' || (endpoint === 'resume' && !payload.targetSessionId)) {
          return { ok: true, value: await domain.projectGoals(agent, endpoint, id) };
        }
        if (endpoint === 'resume') return { ok: true, value: await domain.resumeSession(agent, payload.targetSessionId, id) };
        if (endpoint === 'retry') return { ok: true, value: payload.taskId ? await domain.retryTask(agent, payload.taskId, id) : await domain.resumeSession(agent, payload.targetSessionId, id, { retry: true }) };
        if (endpoint === 'verify-specialist') return { ok: true, value: await domain.verifySpecialist(agent, payload.taskId, id) };
        if (endpoint === 'verify-stop') return { ok: true, value: await domain.verifyStop(agent, id) };
        if (endpoint === 'focus') {
          return { ok: true, value: await domain.focus(agent, payload.nodeId ?? null, id) };
        }
        if (endpoint === 'stop') {
          return { ok: true, value: await domain.stopProject(agent, id) };
        }
        if (endpoint === 'branch') {
          return { ok: true, value: await domain.branch(agent, payload.nodeId, id) };
        }
        if (endpoint === 'discussion.open') return { ok: true, value: await domain.discuss(agent, payload.nodeId, id, payload.fresh === true) };
        if (endpoint === 'restore.preview') return { ok: true, value: await domain.restorePreview(agent, payload.snapshotId) };
        if (endpoint === 'restore.create') {
          if (!payload.previewId) throw new Error('Restore requires a preview ID');
          return { ok: true, value: await domain.restore(agent, payload.snapshotId, id, payload.previewId) };
        }
        if (endpoint === 'restore') {
          return { ok: true, value: await domain.restore(agent, payload.snapshotId, id) };
        }
        if (endpoint === 'detach') {
          return { ok: true, value: await domain.detach(agent, id) };
        }
        throw new Error(`Unknown research workbench operation: ${endpoint}`);
      } catch (error) {
        return {
          ok: false,
          error: {
            code: 'research/unavailable',
            message: error instanceof Error ? error.message : String(error),
          },
        };
      }
    }, { authority: 'trusted-host' });
  });

  return () => {
    for (const dispose of disposers.reverse()) dispose();
    storage.close();
  };
}
