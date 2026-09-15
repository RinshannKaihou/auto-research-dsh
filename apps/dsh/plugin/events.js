import { randomUUID } from 'node:crypto';

export function tokenAmount(usage) {
  if (!usage) return null;
  if (Number.isFinite(usage.totalTokens)) return usage.totalTokens;
  const values = ['inputTokens','outputTokens','cacheReadTokens','cacheWriteTokens'].map(k => usage[k]).filter(Number.isFinite);
  return values.length ? values.reduce((a,b) => a+b,0) : null;
}
const lifecycle = /^(turn\/(start|end)|step\/(start|end)|tool\/(call|result)|approval\/(asked|decided)|goal\/change|assistant\/message|agent\/error)$/;
export function eventFacts(event) {
  const data = event?.data ?? {}, facts = {};
  for (const key of ['id','callId','rootCallId','name','toolName','turn','step','reason','outcome']) {
    if (['string','number','boolean'].includes(typeof data[key])) facts[key] = data[key];
  }
  if (event.type === 'assistant/message' && data.usage) facts.usage = data.usage;
  return facts;
}
export function registerResearchEvents(ctx, domain) {
  const memory = new Map(), queues = new Map(), disposers = [], humans = new Set(), replayed = new Set();
  let disposed = false, timer;
  domain.clearMemory = id => memory.delete(id);
  domain.contextCache = memory;
  const contain = (agent, promise) => promise.catch(error => {
    if (!/disconnected|unavailable|channel closed/i.test(error.message)) return;
    domain.faults.set(agent.id, 'storage: 研究状态存储不可用');
    const goal = ctx.goals.get(agent);
    if (goal?.phase === 'active' && domain.lastOwnedGoal.get(agent.id) === goal.id) ctx.goals.pause(agent, { id: goal.id, revision: goal.revision });
  });
  async function flush() {
    timer = undefined;
    if (disposed) return;
    for (const [id, queue] of queues) {
      if (!queue.events.length || queue.flushing) continue;
      queue.flushing = true;
      const events = queue.events.splice(0,128);
      try { await domain.request(queue.agent, 'host_events', { events, cursor: events.at(-1).sequence }, `${id}:projection:${events[0].sequence}:${events.at(-1).sequence}`); }
      catch (error) { if (!/not associated/.test(error.message)) queue.events.unshift(...events); }
      finally { queue.flushing = false; }
    }
    if ([...queues.values()].some(q => q.events.length)) timer = setTimeout(flush,200);
  }
  function enqueue(agent, event) {
    if (!lifecycle.test(event.type)) return;
    const sequence = Number(event.seq ?? event.sequence);
    if (!Number.isSafeInteger(sequence)) return;
    const queue = queues.get(agent.id) ?? { agent, events: [], flushing: false };
    queues.set(agent.id, queue);
    queue.events.push({ event_type: event.type, sequence, facts: eventFacts(event) });
    if (!timer) timer = setTimeout(flush,50);
  }
  async function assembledMemory(agent, purpose) {
    if (!agent?.id) return '';
    const timeoutMs = domain.memoryReadTimeoutMs ?? 2000;
    let timer;
    try {
      const view = await Promise.race([
        domain.request(agent, 'memory_context', { max_chars: 12000 }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('research memory timeout')), timeoutMs); }),
      ]);
      clearTimeout(timer);
      const text = `Research context:\n${view.text}`;
      memory.set(agent.id, { text, sourceDigest: view.source_digest, stale: false });
      void domain.request(agent, 'context_record', { fields: {
        body: view.text, source_sequence: view.source_sequence,
        turn: domain.turns.get(agent.id), step: domain.steps?.get(agent.id), purpose,
        policy_version: 'memory-v2', dependencies: view.dependencies ?? [], selection: { source_digest: view.source_digest, omitted: view.omitted ?? [] },
      } }, `${agent.id}:context:${randomUUID()}`).catch(() => {});
      return text;
    } catch (error) {
      clearTimeout(timer);
      const saved = memory.get(agent.id);
      if (saved) {
        if (!saved.stale) memory.set(agent.id, { ...saved, stale: true,
          text: `${saved.text}\n\n[Research memory is stale: storage did not answer in time.]` });
        return memory.get(agent.id).text;
      }
      if (/not associated|not currently attached/i.test(error instanceof Error ? error.message : String(error))) return '';
      return domain.associatedSessions?.has(agent.id)
        ? 'Research context unavailable for this associated session. Continue manual conversation; autonomous continuation is blocked until storage recovers.'
        : '';
    }
  }
  disposers.push(ctx.on('system-prompt/assemble', async (_assembly, context, next) => {
    const assembled = await next();
    const agent = context?.agent;
    if (!agent?.id) return assembled;
    const purpose = context?.purpose ?? context?.request?.purpose ?? context?.signal?.purpose;
    if (typeof purpose === 'string' && /title|compress|compact/i.test(purpose)) return assembled;
    await domain.ensureSpecialist(agent);
    const text = await assembledMemory(agent, purpose);
    if (!text) return assembled;
    return { ...assembled, contexts: [
      ...assembled.contexts.filter(item => item.name !== 'research:memory'),
      { name: 'research:memory', text },
    ] };
  }, { global: true }));
  disposers.push(ctx.on('agent/pre-step', async ({agent,turn,step}, next) => {
    domain.turns.set(agent.id,turn);
    domain.steps ??= new Map(); domain.steps.set(agent.id,step);
    let reject = false, blockAuto = false;
    try {
      const state = await domain.state(agent), row = state.workflow.session;
      if (!replayed.has(agent.id)) {
        for (const event of agent.session.events ?? []) enqueue(agent,event);
        replayed.add(agent.id);
      }
      if (state.attempt) await domain.request(agent,'bind_turn',{turn},`${agent.id}:turn:${turn}`);
      const goal = domain.owned(agent,state);
      if (goal) domain.lastOwnedGoal.set(agent.id,goal.id);
      const human = humans.has(agent.id);
      blockAuto = step === 1 && !!goal && !human && (state.workflow.run.state !== 'running' || !!row.pause_reason);
      if (human && goal?.phase === 'active') await domain.pauseSession(agent,'human');
      // A user's turn may proceed even when plugin continuation is paused.
      reject = !human && ['wait','finished'].includes(row?.pause_reason);
      if (!human && goal?.phase === 'active' && (state.workflow.run.state !== 'running' || row.pause_reason)) {
        ctx.goals.pause(agent,{id:goal.id,revision:goal.revision});
      }
    } catch (error) {
      memory.delete(agent.id);
      await contain(agent,Promise.reject(error));
    }
    if (reject) return {kind:'reject'};
    const decision = await next();
    if (blockAuto && decision.kind === 'enter' && !decision.messages.some(m=>m.source?.kind==='user')) return {kind:'reject'};
    return decision;
  }));
  disposers.push(ctx.on('agent/inbox/inserted', ({agent,message}) => {
    if (message.source?.kind !== 'user') return;
    humans.add(agent.id);
    void contain(agent, domain.state(agent).then(async state => {
      if (domain.owned(agent,state)) await domain.pauseSession(agent,'human',`${agent.id}:human:${message.id}`);
    }));
  }));
  disposers.push(ctx.on('agent/inbox/claimed', ({agent,message}) => {
    if (message.source?.kind === 'plugin' && message.source.plugin === 'auto-research-v5' && message.id.startsWith('notice-')) {
      void contain(agent,domain.workflow(agent,'notification_state',{notification_id:message.id,state:'claimed'},`${message.id}:claimed`));
    }
  }));
  disposers.push(ctx.on('agent/turn-stopping', async ({agent,signal}) => {
    if (signal.aborted && !domain.stopping.has(agent.id)) {
      try { const state=await domain.state(agent); if (domain.owned(agent,state)) await domain.pauseSession(agent,'native_stop'); } catch {}
    }
  }));
  disposers.push(ctx.on('agent/status', ({agent,status}) => {
    if (status !== 'idle') return;
    humans.delete(agent.id);
    void contain(agent,domain.idle(agent));
  }));
  disposers.push(ctx.on('agent/error', ({agent,error}) => {
    void contain(agent,domain.state(agent).then(state => {
      if (state.workflow.session?.role === 'specialist') return undefined;
      return domain.pauseSession(agent,'fault').then(() => domain.progress(agent,'failed',{summary:String(error)},`fault:${randomUUID()}`));
    }));
  }));
  disposers.push(ctx.on('goal/changed', ({agent,change}) => {
    if (!change.goal || domain.lastOwnedGoal.get(agent.id) !== change.goal.id) return;
    if (change.goal.phase === 'complete') void contain(agent,domain.state(agent).then(async state => {
      await domain.workflow(agent,'session',{pause_reason:'complete'});
      if (state.workflow.session?.role === 'main') await domain.workflow(agent,'run',{state:'complete'});
      await domain.progress(agent,'goal_complete',{},`goal:${change.goal.id}:complete`);
    }));
    if (change.goal.phase === 'blocked') void contain(agent,domain.pauseSession(agent,'host_limit'));
  }));
  if (ctx.jobs) disposers.push(ctx.jobs.onJobsChanged(owner => {
    if (owner) void contain(owner,domain.workflow(owner,'intent',{
      intent_id:`jobs:${owner.id}`,kind:'jobs',state:'observed',
      details:{jobs:domain.nativeJobs(owner).map(j=>({id:j.id,status:j.status}))},
    }).then(()=>domain.idle(owner)));
  }));
  disposers.push(ctx.on('session/event', (session,event) => {
    const agent = ctx.agents.get(session.id);
    if (agent?.session === session) enqueue(agent,event);
  }));
  disposers.push(ctx.on('llm/stream', function observe(options,next) {
    const sourceKey = `llm:${options.sessionId ?? 'aux'}:${randomUUID()}`;
    const agent = options.sessionId ? ctx.agents.get(options.sessionId) : undefined;
    let began = false, usage = null, reason = 'missing';
    const begin = agent ? domain.request(agent,'usage_begin',{
      source_key:sourceKey,purpose:options.purpose ?? 'conversation',provider:options.provider,model:options.model,
      turn: options.purpose && options.purpose !== 'conversation' ? undefined : domain.turns.get(agent.id),
      step: options.purpose && options.purpose !== 'conversation' ? undefined : domain.steps?.get(agent.id),
    },`${sourceKey}:begin`).then(() => {began=true;}).catch(() => {}) : Promise.resolve();
    return (async function* () {
      await begin;
      try {
        for await (const chunk of next()) {
          if (chunk.type === 'usage') usage=chunk.usage;
          if (chunk.type === 'finish') reason=chunk.reason;
          yield chunk;
        }
      } finally {
        // No return in finally: provider errors and cancellation must reach DSH.
        if (began) await contain(agent,domain.request(agent,'usage_finish',{
          source_key:sourceKey,amount:tokenAmount(usage),completeness:usage?'actual':'unknown',details:{usage:usage??{},finish_reason:reason},
        },`${sourceKey}:finish`));
      }
    })();
  },{global:true}));
  return () => {disposed=true;clearTimeout(timer);for(const dispose of disposers.reverse())dispose();};
}
