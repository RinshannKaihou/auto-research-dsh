import { randomUUID } from 'node:crypto';

function tokenAmount(usage) {
  if (!usage) return null;
  if (Number.isFinite(usage.totalTokens)) return usage.totalTokens;
  const fields = ['inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens'];
  const values = fields.map(key => usage[key]).filter(Number.isFinite);
  return values.length ? values.reduce((total, value) => total + value, 0) : null;
}

function eventFacts(event) {
  const data = event?.data ?? {};
  const facts = {};
  for (const key of ['callId', 'rootCallId', 'name', 'turn', 'step', 'reason']) {
    const value = data[key];
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      facts[key] = value;
    }
  }
  return facts;
}

export function registerResearchEvents(ctx, domain) {
  const memory = new Map();
  const disposers = [];

  disposers.push(ctx.systemPrompt.context({
    name: 'research:memory',
    order: 800,
    text: context => {
      const id = context.agent ? String(context.agent.id) : '';
      const value = memory.get(id);
      return value ? `Associated research project context:\n${value}` : '';
    },
  }));

  disposers.push(ctx.on('agent/pre-step', async ({ agent, turn }, next) => {
    try {
      const state = await domain.request(agent, 'query', {}, `${agent.id}:state:${turn}`);
      if (state.attempt) {
        await domain.request(agent, 'bind_turn', { turn }, `${agent.id}:turn:${turn}`);
      }
      if (state.owned_goal) {
        domain.lastOwnedGoal ??= new Map();
        domain.lastOwnedGoal.set(String(agent.id), state.owned_goal.goal_id);
        const goal = ctx.goals.get(agent);
        if (goal?.phase === 'active' && state.project.control !== 'auto' &&
            goal.id === state.owned_goal.goal_id) {
          await domain.changeGoal(
            agent,
            'pause',
            `${agent.id}:control:${turn}`,
            { projectControl: false },
          );
        }
      }
      const view = await domain.request(agent, 'memory', { max_chars: 12000 }, `${agent.id}:memory:${turn}`);
      memory.set(String(agent.id), view.text);
    } catch {
      memory.delete(String(agent.id));
      const goal = ctx.goals.get(agent);
      if (goal?.phase === 'active') {
        const cached = domain.lastOwnedGoal?.get(String(agent.id));
        if (cached === goal.id) ctx.goals.pause(agent, { id: goal.id, revision: goal.revision });
      }
    }
    return next();
  }));

  disposers.push(ctx.on('agent/inbox/inserted', ({ agent, message }) => {
    if (message.source?.kind !== 'user') return;
    void domain.request(agent, 'query').then(state => {
      const goal = ctx.goals.get(agent);
      if (!goal || goal.phase !== 'active' || state.owned_goal?.goal_id !== goal.id) return;
      domain.lastOwnedGoal ??= new Map();
      domain.lastOwnedGoal.set(String(agent.id), goal.id);
      return domain.changeGoal(
        agent,
        'pause',
        `${agent.id}:human:${message.id}`,
        { projectControl: false },
      );
    }).catch(() => {});
  }));

  disposers.push(ctx.on('session/event', (session, event) => {
    const agent = ctx.agents.get(session.id);
    if (!agent || agent.session !== session) return;
    const sequence = Number(event.seq ?? event.sequence);
    if (!Number.isSafeInteger(sequence)) return;
    void domain.request(agent, 'host_event', {
      event_type: event.type,
      sequence,
      facts: eventFacts(event),
    }, `${session.id}:event:${sequence}`).catch(() => {});
  }));

  disposers.push(ctx.on('llm/stream', function observe(options, next) {
    const sourceKey = `llm:${options.sessionId ?? 'aux'}:${randomUUID()}`;
    const agent = options.sessionId ? ctx.agents.get(options.sessionId) : undefined;
    const identity = options.sessionId
      ? { host_id: domain.hostId, session_id: String(options.sessionId) }
      : undefined;
    let began = false;
    let usage = null;
    let finishReason = 'missing';
    const begin = identity
      ? domain.storage.request('usage_begin', identity, {
          source_key: sourceKey,
          purpose: options.purpose ?? 'conversation',
          provider: options.provider,
          model: options.model,
        }, `${sourceKey}:begin`).then(() => { began = true; }).catch(() => {})
      : Promise.resolve();
    return (async function* () {
      await begin;
      try {
        for await (const chunk of next()) {
          if (chunk.type === 'usage') usage = chunk.usage;
          if (chunk.type === 'finish') finishReason = chunk.reason;
          yield chunk;
        }
      } finally {
        if (!began || !identity) return;
        try {
          await domain.storage.request('usage_finish', identity, {
            source_key: sourceKey,
            amount: tokenAmount(usage),
            completeness: usage ? 'actual' : 'unknown',
            details: { usage: usage ?? {}, finish_reason: finishReason },
          }, `${sourceKey}:finish`);
          const state = await domain.storage.request('query', identity);
          if (agent && state.project.control === 'auto' &&
              (state.usage.unknown_count > 0 || state.usage.remaining <= 0)) {
            await domain.projectGoals(agent, 'pause', `${sourceKey}:budget`);
          }
        } catch {
          if (!agent) return;
          const goal = ctx.goals.get(agent);
          const owned = domain.lastOwnedGoal?.get(String(agent.id));
          if (goal?.phase === 'active' && owned === goal.id) {
            try { ctx.goals.pause(agent, { id: goal.id, revision: goal.revision }); } catch {}
          }
        }
      }
    })();
  }, { global: true }));

  return () => {
    for (const dispose of disposers.reverse()) dispose();
  };
}
