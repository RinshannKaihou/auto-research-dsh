import { randomUUID, createHash } from 'node:crypto';

export function agentIdentity(agent, hostId = 'local') {
  return { host_id: hostId, session_id: String(agent.id ?? agent.session?.header?.id) };
}
export function operationId(agent, suffix = randomUUID()) { return `${agent.id}:${suffix}`; }
const managed = row => !row.detached && ['main', 'exploration'].includes(row.role);
const terminal = state => ['finished', 'failed', 'cancelled'].includes(state);

/** Domain orchestration runs exclusively on native DSH agents, goals and jobs. */
export class ResearchDomain {
  constructor(ctx, storage, adapter, config = {}) {
    Object.assign(this, { ctx, storage, adapter });
    this.hostId = config.hostId ?? 'local';
    this.maxGoalRounds = config.maxGoalRounds;
    this.autonomousConcurrency = config.autonomousConcurrency ?? 2;
    this.loaded = new Map();
    this.locks = new Map();
    this.lastOwnedGoal = new Map();
    this.faults = new Map();
    this.turns = new Map();
    this.stopping = new Set();
  }
  identity(agent) { return agentIdentity(agent, this.hostId); }
  request(agent, method, fields = {}, id) {
    return this.storage.request(method, this.identity(agent), fields, id ?? operationId(agent));
  }
  workflow(agent, action, fields = {}, id) { return this.request(agent, 'workflow', { action, fields }, id); }
  async state(agent) {
    let state = await this.request(agent, 'query');
    const key = state.project.project_id;
    if (!this.loaded.has(key)) {
      const cold = this.workflow(agent, 'cold', {}, `boot:${randomUUID()}`);
      this.loaded.set(key, cold);
    }
    await this.loaded.get(key);
    return this.request(agent, 'query');
  }
  async serial(agent, fn) {
    const state = await this.state(agent), key = state.project.project_id;
    const previous = this.locks.get(key) ?? Promise.resolve();
    const work = previous.catch(() => {}).then(fn);
    this.locks.set(key, work);
    try { return await work; } finally { if (this.locks.get(key) === work) this.locks.delete(key); }
  }
  nativeJobs(agent) {
    if (!this.ctx.jobs) throw new Error('Native job ownership cannot be verified');
    return this.ctx.jobs.list(agent).filter(job => job.ownerSession === agent.id);
  }
  recoveryClear(agent, state) {
    const calls = new Set();
    for (const event of agent.session.events ?? []) {
      if (event.type === 'tool/call') calls.add(event.data.callId ?? event.data.call?.id);
      if (event.type === 'tool/result') {
        calls.delete(event.data.callId ?? event.data.message?.source?.callId);
      }
    }
    calls.delete(undefined);
    const saved = state.workflow.intents?.find(i => i.intent_id === `jobs:${agent.id}`);
    const known = this.nativeJobs(agent);
    const unknown = saved?.details.jobs?.some(j => ['running','stopping'].includes(j.status) && !known.some(k => k.id === j.id && ['completed','killed'].includes(k.status)));
    return !calls.size && !unknown;
  }
  quiet(agent) {
    return agent.status === 'idle' && !this.nativeJobs(agent).some(j => ['running', 'stopping'].includes(j.status));
  }
  owned(agent, state) {
    const goal = this.ctx.goals.get(agent);
    const owner = state.workflow?.sessions.find(s => s.session_id === agent.id)?.goal_id ?? state.owned_goal?.goal_id;
    return goal && (goal.id === owner || goal.id === this.lastOwnedGoal.get(agent.id)) ? goal : null;
  }
  async query(agent) {
    const state = await this.state(agent);
    const sessions = state.workflow.sessions.map(row => {
      const target = this.ctx.agents.get(row.session_id);
      const goal = target ? this.ctx.goals.get(target) : null;
      let jobs = null;
      try { if (target) jobs = this.nativeJobs(target); } catch {}
      return { ...row, context: undefined, name: target?.session.header.title ?? row.session_id,
        native_status: target?.status ?? 'unloaded', goal,
        pending_approvals: target ? (() => {
          const pending = new Set();
          for (const e of target.session.events ?? []) {
            if (e.type === 'approval/asked') pending.add(e.data.id);
            if (e.type === 'approval/decided') pending.delete(e.data.id);
          }
          return pending.size;
        })() : null,
        jobs, pause_reason: this.faults.get(row.session_id) ?? row.pause_reason,
        turn: this.turns.get(row.session_id) ?? null };
    });
    state.runtime = { ...state.workflow.run, sessions,
      running_count: sessions.filter(s => managed(s) && s.native_status === 'running').length,
      pending_approvals: sessions.some(s => managed(s) && s.pending_approvals === null) ? null : sessions.filter(managed).reduce((n,s) => n+s.pending_approvals,0),
      current: sessions.find(s => s.session_id === agent.id) };
    return state;
  }
  async open(agent, { goal, root } = {}, id = operationId(agent)) {
    const projectRoot = root ?? agent.session.header.cwd;
    if (!projectRoot) throw new Error('The native session has no working directory');
    const state = await this.request(agent, 'open', { root: projectRoot, cwd: agent.session.header.cwd, goal }, id);
    // New manual projects are already unarmed; existing projects must pass cold recovery.
    if (state.workflow.run.state === 'manual') this.loaded.set(state.project.project_id, Promise.resolve());
    return this.query(agent);
  }
  focus() { throw new Error('聚焦已退役。选择节点只查看详情；研究方向由 Agent 决定。'); }
  branch() { throw new Error('创建分支已退役。请使用“围绕此节点讨论”；并行探索由研究 Agent 派发。'); }
  async detach(agent, id) {
    return this.serial(agent, async () => {
      const state = await this.state(agent), row = state.workflow.session;
      if (row?.role === 'main' && !['manual','stopped','complete'].includes(state.workflow.run.state)) throw new Error('移出主会话前须先停止项目');
      if (managed(row) && (!this.quiet(agent) || state.attempt)) throw new Error('须先停止并核实此执行会话');
      await this.pauseSession(agent, 'detached');
      await this.workflow(agent, 'session', { detached: 1 }, `${id}:role`);
      await this.request(agent, 'detach', {}, `${id}:detach`);
      this.clearMemory?.(agent.id);
      return { message: '已将此会话移出项目；原对话和材料保留' };
    });
  }
  async pauseSession(agent, reason, id = operationId(agent)) {
    const state = await this.request(agent, 'query');
    const goal = this.owned(agent, state);
    await this.workflow(agent, 'session', { pause_reason: reason }, `${id}:reason`);
    if (goal?.phase === 'active') this.ctx.goals.pause(agent, { id: goal.id, revision: goal.revision });
  }
  async arm(agent, state, id, { restart = false } = {}) {
    const row = state.workflow.session;
    if (row?.cwd && row.cwd !== agent.session.header.cwd) throw new Error('Native cwd differs from the recorded workspace; recovery requires verification');
    if (!managed(row)) throw new Error('This session is not a managed research executor');
    const current = this.ctx.goals.get(agent), owned = this.owned(agent, state);
    if (current && !owned) throw new Error('This session already has a non-plugin native goal');
    if (state.attempt?.state === 'unknown') throw new Error('旧执行尚未核实退出');
    if (!state.attempt) await this.request(agent, 'focus', { node_id: row.node_id, role: row.role === 'exploration' ? 'branch' : 'planner', mode: 'auto' }, `${id}:attempt`);
    const task = state.workflow.tasks.find(t => t.session_id === agent.id);
    const objective = task ? `Research task ${task.task_id}: ${task.context.question}\nPlan: ${task.context.plan}\nFixed inputs: ${JSON.stringify(task.context.inputs)}\nReport partial publications, gaps, and completion to the initiating agent.` : `Advance research project: ${state.project.goal}`;
    const instructions = '\nUse native tools in your own cwd. Use research_propose and research_dispatch for independent exploration, research_wait to wait without polling, research_publish for partial findings, and research_finish when the work segment ends. Goal completion does not imply publication, node closure, or scientific success.';
    await this.workflow(agent, 'session', { pause_reason: null }, `${id}:clear`);
    let goal;
    if (restart && current && current.phase !== 'complete') this.ctx.goals.clear(agent, { id: current.id, revision: current.revision });
    if (!current || restart || current.phase === 'complete') {
      goal = this.ctx.goals.create(agent, { objective: objective + instructions, ...(this.maxGoalRounds ? { maxGoalRounds: this.maxGoalRounds } : {}) });
    } else if (current.phase !== 'active' || current.activation === 'disarmed') {
      goal = this.ctx.goals.resume(agent, { id: current.id, revision: current.revision });
    } else goal = current;
    this.lastOwnedGoal.set(agent.id, goal.id);
    await this.workflow(agent, 'session', { goal_id: goal.id, goal_revision: goal.revision }, `${id}:owner`);
    await this.request(agent, 'own_goal', { goal_id: goal.id, revision: goal.revision, phase: goal.phase }, `${id}:goal`);
    return goal;
  }
  async auto(agent, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const state = await this.state(agent), run = state.workflow.run;
      if (run.state === 'running') return { message: '自主研究已开始', state: run.state };
      if (['stopping','unverified'].includes(run.state)) throw new Error('停止待核实，尚不能再次开始');
      if (['paused','cold'].includes(run.state)) throw new Error('请使用“继续自主研究”并查看暂停原因');
      const main = await this.adapter.resolve(run.main_session_id);
      let mainState = await this.state(main);
      const existing = this.ctx.goals.get(main);
      if (existing && !this.owned(main, mainState)) throw new Error('This session already has a non-plugin native goal');
      if (!this.quiet(main)) throw new Error('研究主会话仍在执行，请等待当前轮结束');
      if (mainState.attempt && (run.state === 'complete' || mainState.attempt.mode !== 'auto')) {
        await this.request(main, 'finish', { state: 'finished', details: { reason: run.state === 'complete' ? 'explicit-restart-after-goal-completion' : 'explicit-switch-to-autonomous' } }, `${id}:prior-segment`);
        mainState = await this.state(main);
      }
      await this.workflow(agent, 'run', { state: 'running', new_generation: true }, `${id}:run`);
      try { await this.arm(main, mainState, `${id}:main`, { restart: ['stopped','complete'].includes(run.state) }); }
      catch (error) { await this.workflow(agent, 'run', { state: 'paused' }); throw error; }
      return { message: '已开始自主研究', sessionId: main.id };
    });
  }
  async projectGoals(agent, action, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const state = await this.state(agent), run = state.workflow.run;
      if (['stopping','unverified','stopped','complete'].includes(run.state)) throw new Error('请先完成停止核实，或使用“再次开始研究”');
      await this.workflow(agent, 'run', { state: action === 'pause' ? 'paused' : 'running' }, `${id}:run`);
      const results = [];
      for (const row of state.workflow.sessions.filter(managed)) {
        try {
          const target = await this.adapter.resolve(row.session_id);
          if (action === 'pause') {
            if (!row.pause_reason || row.pause_reason === 'wait') await this.pauseSession(target, row.pause_reason === 'wait' ? 'project_wait' : 'project');
          } else if (['project','project_wait','cold'].includes(row.pause_reason)) {
            if (!this.quiet(target)) throw new Error('当前轮或作业尚未收尾');
            if (row.pause_reason === 'cold' && !this.recoveryClear(target, state)) throw new Error('冷恢复发现未核实工具或作业；不会重跑命令');
            const current = await this.state(target);
            if (current.attempt?.state === 'unknown') throw new Error('旧执行待核实');
            if (row.waiting.length) await this.workflow(target, 'session', { pause_reason: 'wait' });
            else await this.arm(target, current, `${id}:${target.id}`);
          }
          results.push({ session_id: target.id, pause_reason: (await this.state(target)).workflow.session.pause_reason });
        } catch (error) { results.push({ session_id: row.session_id, error: error.message }); }
      }
      if (action === 'resume') await this.schedule(agent);
      return { message: action === 'pause' ? '已暂停后续续轮；当前轮允许收尾' : '已恢复项目暂停的执行；其他暂停原因保持不变', sessions: results };
    });
  }
  async stopProject(agent, id = operationId(agent)) {
    return this.serial(agent, async () => {
      let state = await this.state(agent);
      await this.workflow(agent, 'intent', { intent_id: id, kind: 'stop', state: 'requested' }, `${id}:intent`);
      await this.workflow(agent, 'run', { state: 'stopping' }, `${id}:run`);
      for (const row of state.workflow.sessions.filter(managed)) {
        try {
          const target = await this.adapter.resolve(row.session_id), current = await this.state(target);
          if (!this.owned(target, current) && this.ctx.goals.get(target)) continue;
          this.stopping.add(target.id);
          await this.pauseSession(target, 'stop');
          if (target.status !== 'idle') await this.adapter.cancel(target.id);
          for (const job of this.nativeJobs(target).filter(j => ['running','stopping'].includes(j.status))) this.ctx.jobs.kill(job.id, target, 'research-project-stop');
        } catch (error) {
          this.faults.set(row.session_id, `停止待核实：${error.message}`);
          await this.workflow({ id: row.session_id }, 'session', { pause_reason: 'unverified' });
        }
      }
      await this.settleStops(agent);
      state = await this.state(agent);
      return { message: state.workflow.run.state === 'stopped' ? '研究已停止' : '停止待核实；请查看原生会话与作业状态', state: state.workflow.run.state };
    });
  }
  async settleStops(agent) {
    const state = await this.state(agent);
    if (!['stopping','unverified'].includes(state.workflow.run.state)) return;
    let complete = true;
    for (const row of state.workflow.sessions.filter(managed)) {
      const target = this.ctx.agents.get(row.session_id);
      if (!target || row.pause_reason === 'unverified' || !this.quiet(target)) { complete = false; continue; }
      const current = await this.state(target);
      if (current.attempt?.state === 'unknown' || !this.recoveryClear(target,current)) { complete = false; continue; }
      if (current.attempt) await this.request(target, 'finish', { state: 'stopped', details: { reason: 'project-stop-confirmed' } });
      const task = state.workflow.tasks.find(t => t.session_id === target.id && !terminal(t.state));
      if (task) await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'cancelled' });
      this.stopping.delete(target.id);
    }
    await this.workflow(agent, 'run', { state: complete ? 'stopped' : 'unverified' });
  }
  async dispatch(agent, nodeId, id) {
    return this.serial(agent, async () => {
      const task = await this.workflow(agent, 'task', { node_id: nodeId }, id);
      await this.schedule(agent);
      return (await this.state(agent)).workflow.tasks.find(t => t.task_id === task.task_id);
    });
  }
  async wait(agent, taskIds, id) {
    return this.serial(agent, async () => {
      const state = await this.state(agent);
      if (!managed(state.workflow.session) || state.workflow.run.state !== 'running') throw new Error('Only an autonomous research agent can wait');
      if (!taskIds.length || taskIds.some(taskId => !state.workflow.tasks.some(t => t.task_id === taskId && t.parent_session_id === agent.id))) throw new Error('Wait requires tasks dispatched by this agent');
      await this.workflow(agent, 'session', { waiting: taskIds }, `${id}:tasks`);
      await this.pauseSession(agent, 'wait', id);
      return { waiting: taskIds, message: 'Native continuation paused. The current turn ends at the next step boundary; progress arrives through the plugin inbox.' };
    });
  }
  async schedule(agent) {
    let state = await this.state(agent);
    let slots = 0;
    for (const row of state.workflow.sessions.filter(managed)) {
      const target = this.ctx.agents.get(row.session_id);
      if (!target) { if (!row.pause_reason) slots++; continue; }
      if (!this.quiet(target) || (!row.pause_reason && this.owned(target,state)?.phase === 'active')) slots++;
    }
    // Deliver before dispatch; waiting coordinators need a free execution slot too.
    for (const notice of state.workflow.notifications.filter(n => n.state === 'pending')) {
      const row = state.workflow.sessions.find(s => s.session_id === notice.recipient);
      const target = this.ctx.agents.get(notice.recipient);
      if (!target || !managed(row)) continue;
      if (!this.hasMessage(target, notice.notification_id)) target.send({ id: notice.notification_id, role: 'user', source: { kind: 'plugin', plugin: 'auto-research-v5', form: 'notice', summary: `Research ${notice.kind}: ${notice.task_id}` }, content: [{ type: 'text', text: JSON.stringify(notice.payload) }] }, 'next-step', false);
      await this.workflow(agent, 'notification_state', { notification_id: notice.notification_id, state: 'delivered' }, `${notice.notification_id}:delivered`);
    }
    state = await this.state(agent);
    if (state.workflow.run.state !== 'running') return;
    for (const row of state.workflow.sessions.filter(s => managed(s) && s.pause_reason === 'wait')) {
      const target = this.ctx.agents.get(row.session_id);
      const relevant = state.workflow.notifications.some(n => n.recipient === row.session_id && n.state === 'delivered' && row.waiting.includes(n.task_id));
      if (relevant && target && this.quiet(target) && slots < this.autonomousConcurrency) {
        await this.workflow(target, 'session', { waiting: [] });
        await this.arm(target, await this.state(target), operationId(target));
        slots++;
      }
    }
    for (const task of state.workflow.tasks.filter(t => t.state === 'queued')) {
      if (slots >= this.autonomousConcurrency) break;
      const parent = await this.adapter.resolve(task.parent_session_id);
      await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'starting' }, `${task.task_id}:starting`);
      try {
        const prepared = await this.request(parent, 'prepare_branch', { node_id: task.node_id }, `${task.task_id}:prepare`);
        const child = await this.createSession(parent, task.session_id, prepared.workspace);
        await this.request(child, 'open', { root: state.project_root, cwd: prepared.workspace, session_role: 'exploration', node_id: task.node_id, context: task.context }, `${task.task_id}:open`);
        await this.workflow(child, 'task_state', { task_id: task.task_id, state: 'running', cwd: prepared.workspace }, `${task.task_id}:running`);
        await this.arm(child, await this.state(child), `${task.task_id}:arm`);
        slots++;
      } catch (error) {
        await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'unverified', error: error.message });
        // Never re-create an uncertain session or re-run its command on recovery.
        slots++;
      }
    }
  }
  hasMessage(agent, id) {
    const entries = agent.session.events ?? agent.session.log ?? [];
    return JSON.stringify(entries).includes(`"${id}"`) || [...(agent.inbox?.nextStep ?? []), ...(agent.inbox?.nextTurn ?? [])].some(m => m.id === id);
  }
  async progress(agent, kind, value, id) {
    const state = await this.state(agent);
    const task = state.workflow.tasks.find(t => t.session_id === agent.id);
    if (kind === 'published') value = state.publications.find(p => p.publication_id === value.publication_id) ?? value;
    if (kind === 'finished') await this.pauseSession(agent, 'finished');
    if (task) {
      await this.workflow(agent, 'notify', { key: value.publication_id ?? value.attempt_id ?? (kind === 'finished' ? state.attempts.filter(a => state.associations.some(s => s.association_id === a.association_id && s.session_id === agent.id)).at(-1)?.attempt_id : null) ?? id, kind, reference: value.publication_id ? `pub/${value.publication_id}` : null, summary: value.summary ?? '', gaps: value.gaps ?? [] }, `${id}:notice`);
      if (kind === 'failed') await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'failed', error: value.summary }, `${id}:failed`);
      if (kind === 'finished') {
        await this.pauseSession(agent, 'finished');
        await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'finished' }, `${id}:task`);
      }
    }
    await this.serial(agent, () => this.schedule(agent));
  }
  async idle(agent) {
    return this.serial(agent, async () => { await this.settleStops(agent); await this.schedule(agent); });
  }
  async createSession(parent, sessionId, cwd) {
    let child = this.ctx.agents.get(sessionId);
    if (!child) {
      try { child = await this.adapter.resolve(sessionId); } catch {}
    }
    if (!child) { await this.adapter.create({ sessionId, cwd, agentPreset: this.ctx.sessionProjections?.stateOf(parent.session, 'agentPreset') ?? parent.session.header.agentPreset }); child = await this.adapter.resolve(sessionId); }
    const selection = this.ctx.sessionProjections?.stateOf(parent.session, 'modelSelection');
    const options = selection?.pending ?? selection?.lastUsed ?? parent.session.requestHeader?.()?.config ?? parent.options;
    await this.adapter.selectModel(sessionId, options);
    return child;
  }
  async discuss(agent, nodeId, id, fresh = false) {
    return this.serial(agent, async () => {
      const state = await this.state(agent);
      const prepared = await this.request(agent, 'discussion_prepare', { node_id: nodeId, fresh }, `${id}:prepare`);
      if (!prepared.existing) {
        const child = await this.createSession(agent, prepared.session_id, prepared.workspace);
        await this.request(child, 'open', { root: state.project_root, cwd: prepared.workspace, session_role: 'discussion', node_id: nodeId, context: prepared.context }, `${id}:open`);
      }
      return { sessionId: prepared.session_id, message: '背景已准备，可开始提问', existing: prepared.existing };
    });
  }
  restorePreview(agent, snapshotId) { return this.request(agent, 'restore_preview', { snapshot_id: snapshotId }); }
  async restore(agent, snapshotId, id, previewId) {
    if (!previewId) return this.restorePreview(agent, snapshotId);
    return this.serial(agent, async () => {
      const stable = `restore:${previewId}`;
      const prepared = await this.request(agent, 'prepare_restore', { snapshot_id: snapshotId, preview_id: previewId }, stable);
      const sid = 'handoff-' + createHash('sha256').update(stable).digest('hex').slice(0,24);
      const child = await this.createSession(agent, sid, prepared.workspace);
      await this.request(child, 'open', { root: prepared.project_root, cwd: prepared.workspace, session_role: 'handoff', node_id: prepared.node_id, context: prepared.context }, `${stable}:open`);
      await this.request(child, 'record_restore', { snapshot_id: snapshotId, source_attempt_id: prepared.source_attempt_id, workspace: prepared.workspace }, `${stable}:record`);
      return { sessionId: sid, message: '接手会话已准备，等待人工输入', ...prepared };
    });
  }
}
