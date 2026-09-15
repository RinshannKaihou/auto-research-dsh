import { randomUUID, createHash } from 'node:crypto';

export function agentIdentity(agent, hostId = 'local') {
  return { host_id: hostId, session_id: String(agent.id ?? agent.session?.header?.id) };
}
export function operationId(agent, suffix = randomUUID()) { return `${agent.id}:${suffix}`; }
const managed = row => !!row && !row.detached && ['main', 'node_core', 'exploration'].includes(row.role);
const terminal = state => ['finished', 'failed', 'cancelled'].includes(state);

/** Domain orchestration runs exclusively on native DSH agents, goals and jobs. */
export class ResearchDomain {
  constructor(ctx, storage, adapter, config = {}) {
    Object.assign(this, { ctx, storage, adapter });
    this.hostId = config.hostId ?? 'local';
    this.maxGoalRounds = config.maxGoalRounds;
    this.memoryReadTimeoutMs = config.memoryReadTimeoutMs ?? 2000;
    this.autonomousConcurrency = config.autonomousConcurrency ?? 2;
    this.specialistFanout = config.specialistFanout ?? 2;
    this.subagentProvider = config.subagentProvider ?? 'spawn';
    this.loaded = new Map();
    this.locks = new Map();
    this.lastOwnedGoal = new Map();
    this.faults = new Map();
    this.turns = new Map();
    this.stopping = new Set();
    this.pendingSpecialists = new Map();
    this.specialistHandles = new Map();
    this.associatedSessions = new Set();
    this.sessionRoles = new Map();
  }
  identity(agent) { return agentIdentity(agent, this.hostId); }
  request(agent, method, fields = {}, id) {
    return this.storage.request(method, this.identity(agent), fields, id ?? operationId(agent));
  }
  workflow(agent, action, fields = {}, id) { return this.request(agent, 'workflow', { action, fields }, id); }
  async state(agent, { all = true } = {}) {
    let state = await this.request(agent, 'control_state');
    this.associatedSessions.add(agent.id);
    const key = state.project.project_id;
    if (!this.loaded.has(key)) {
      const cold = this.workflow(agent, 'cold', {}, `boot:${randomUUID()}`);
      this.loaded.set(key, cold);
    }
    await this.loaded.get(key);
    state = await this.request(agent, 'control_state');
    if (state.workflow.session && !state.workflow.session.detached) {
      this.sessionRoles.set(agent.id, state.workflow.session.role);
    } else this.sessionRoles.delete(agent.id);
    let cursors = state.workflow.cursors ?? {};
    while (all && Object.keys(cursors).length) {
      const page = await this.request(agent, 'workflow_page', { cursors });
      const next = {};
      for (const key of Object.keys(cursors)) {
        state.workflow[key].push(...page[key]);
        if (page.cursors[key]) next[key] = page.cursors[key];
      }
      cursors = next;
    }
    this.associatedSessions.add(agent.id);
    return state;
  }
  managedRole(agent) {
    const role = this.sessionRoles.get(agent?.id);
    return ['main', 'node_core', 'exploration'].includes(role) ? role : null;
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
    const state = await this.state(agent, { all: false });
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
      specialist_count: sessions.filter(s => s.role === 'specialist' && s.native_status === 'running').length,
      pending_approvals: sessions.some(s => managed(s) && s.pending_approvals === null) ? null : sessions.filter(managed).reduce((n,s) => n+s.pending_approvals,0),
      current: sessions.find(s => s.session_id === agent.id) };
    return state;
  }
  page(agent, collection, cursor = {}, limit = 50) {
    if (collection === 'usage') return this.request(agent, 'usage_page', { ...cursor, limit });
    if (collection === 'changes') return this.request(agent, 'changes_page', { ...cursor, limit });
    return this.request(agent, 'history_page', { collection, ...cursor, limit });
  }
  lookup(agent, fields = {}) { return this.request(agent, 'query', fields); }
  guidanceStatus(agent) { return this.request(agent, 'guidance_status'); }
  guidanceRegister(agent, path, version, id = operationId(agent)) {
    return this.request(agent, 'guidance_register', { path, ...(version ? { version } : {}) }, id);
  }
  async contextPreview(agent) {
    try {
      const view = await this.request(agent, 'memory_context', { max_chars: 12000 });
      return { ...view, status: 'fresh' };
    } catch (error) {
      const cached = this.contextCache?.get(agent.id);
      if (cached) return { text: cached.text, source_digest: cached.sourceDigest, status: 'stale', error: error.message };
      return { text: '', status: 'unavailable', error: error instanceof Error ? error.message : String(error) };
    }
  }
  async ensureSpecialist(agent) {
    const parentId = agent?.session?.header?.parentSession;
    if (!parentId) return null;
    const pending = this.pendingSpecialists.get(parentId);
    if (!pending) return null;
    if (pending.childSessionId && pending.childSessionId !== agent.id) {
      throw new Error('Specialist identity does not match the durable delegation intent');
    }
    if (!pending.bound) {
      const parent = this.ctx.agents.get(parentId);
      if (!parent) throw new Error('Specialist parent session is unavailable');
      pending.childSessionId = agent.id;
      pending.bound = this.request(parent, 'specialist_bind_child', {
        task_id: pending.task.task_id,
        child_session_id: agent.id,
        node_id: pending.task.node_id,
        cwd: agent.session.header.cwd,
      }, `${pending.operationId}:bind`);
    }
    await pending.bound;
    return pending.task;
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
    const state = await this.request(agent, 'control_state');
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
    if (!state.attempt) await this.request(agent, 'focus', { node_id: row.node_id, role: ['node_core','exploration'].includes(row.role) ? 'core' : 'planner', mode: 'auto' }, `${id}:attempt`);
    const taskRow = state.workflow.tasks.find(t => t.session_id === agent.id);
    const task = taskRow ? { ...taskRow, context: await this.request(agent, 'task_context', { task_id: taskRow.task_id }) } : null;
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
    await this.request(agent, 'own_goal', {
      goal_id: goal.id,
      revision: goal.revision,
      phase: goal.phase,
      activation: goal.activation,
      change_reason: restart ? 'restart' : 'arm',
    }, `${id}:goal`);
    return goal;
  }
  async auto(agent, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const state = await this.state(agent), run = state.workflow.run;
      if (run.state === 'running') {
        const main = await this.adapter.resolve(run.main_session_id);
        const current = await this.state(main), row = current.workflow.session;
        const goal = this.owned(main, current);
        if (!row?.pause_reason && goal?.phase === 'active' && goal.activation !== 'disarmed') {
          return { message: '自主研究已在运行；未创建重复 goal 或工作段', state: run.state, sessionId: main.id };
        }
        return { message: `项目允许自主推进，但主会话实际处于暂停状态：${row?.pause_reason ?? goal?.phase ?? 'unknown'}。请使用 /research resume --session ${main.id}`, state: run.state, sessionId: main.id, pause_reason: row?.pause_reason ?? null };
      }
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
            const current = await this.state(target);
            if (row.pause_reason === 'cold' && !this.recoveryClear(target, current)) throw new Error('冷恢复发现未核实工具或作业；不会重跑命令');
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
  async resumeSession(agent, sessionId, id = operationId(agent), { retry = false } = {}) {
    return this.serial(agent, async () => {
      const receipt = await this.request(agent, 'recovery_receipt', { key: id });
      if (receipt) return receipt;
      const project = await this.state(agent);
      let row = project.workflow.sessions.find(item => item.session_id === sessionId);
      if (!row) {
        try { const selected = await this.lookup(agent, { ref: sessionId }); if (selected.kind === 'session') row = selected.value; } catch {}
      }
      if (!managed(row)) throw new Error('目标会话不是本项目的受管理研究执行会话');
      const target = await this.adapter.resolve(sessionId);
      let current = await this.state(target);
      if (this.ctx.goals.get(target) && !this.owned(target, current)) throw new Error('This session already has a non-plugin native goal');
      if (!this.quiet(target)) throw new Error('目标会话仍在执行或存在运行中的归属作业');
      if (!this.recoveryClear(target, current)) throw new Error('工具结果或作业退出状态尚未核实；不会重跑命令');
      const reason = current.workflow.session?.pause_reason;
      if (retry && !['fault', 'unverified', 'host_limit'].includes(reason)) {
        throw new Error(`会话当前暂停原因是 ${reason ?? 'none'}，不符合故障重试条件`);
      }
      if (!retry && !['human', 'native_stop', 'finished', 'complete', 'fault', 'host_limit'].includes(reason)) {
        throw new Error(`会话当前暂停原因是 ${reason ?? 'none'}；项目级暂停请使用 /research resume`);
      }
      const previous = current.attempt;
      const task = current.workflow.tasks.find(t => t.session_id === target.id);
      if (task && ['failed','unverified','finished','cancelled'].includes(task.state)) {
        // Reserve this node before arming the native goal. The unique live-task
        // index rejects a concurrent dispatch before any new execution begins.
        await this.workflow(target, 'task_retry', { task_id: task.task_id }, `${id}:reserve-task`);
      }
      if (retry && previous) {
        await this.request(target, 'finish', {
          state: 'stopped', details: {
            reason: 'explicit-retry-after-exit-verification', retry_of: previous.attempt_id,
            original_details: previous.details ?? {},
          },
        }, `${id}:finish-prior`);
        current = await this.state(target);
      }
      if (['stopped', 'complete', 'cold'].includes(project.workflow.run.state)) {
        await this.workflow(agent, 'run', { state: 'running', new_generation: true }, `${id}:run`);
      }
      this.faults.delete(target.id);
      let goal;
      try { goal = await this.arm(target, current, `${id}:arm`, { restart: reason === 'complete' }); }
      catch (error) {
        await this.pauseSession(target, 'unverified');
        if (task) await this.workflow(target, 'task_state', { task_id: task.task_id, state: 'unverified', error: error.message });
        throw error;
      }
      const refreshed = await this.state(target);
      if (task) {
        await this.workflow(target, 'task_resumed', { task_id: task.task_id, attempt_id: refreshed.attempt?.attempt_id, retry_of: previous?.attempt_id }, `${id}:task-running`);
        await this.workflow(target, 'intent', { intent_id: `task-attempt:${task.task_id}`, kind: 'task-attempt', state: 'bound', details: { task_id: task.task_id, attempt_id: refreshed.attempt?.attempt_id, retry_of: previous?.attempt_id } }, `${id}:attempt-binding`);
      }
      const result = { message: retry ? '故障退出已核实，已在原目录创建恢复工作段' : '已显式继续该研究会话', session_id: target.id, attempt_id: refreshed.attempt?.attempt_id ?? null, retry_of: previous?.attempt_id ?? null, goal_id: goal.id };
      await this.workflow(agent, 'intent', { intent_id: id, kind: 'recovery-receipt', state: 'complete', details: result }, `${id}:result`);
      return result;
    });
  }
  async retryTask(agent, taskId, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const prior = await this.request(agent, 'recovery_receipt', { key: id });
      if (prior) return prior;
      const project = await this.state(agent);
      const selected = await this.lookup(agent, { ref: taskId });
      if (selected.kind !== 'exploration-task') throw new Error('不是探索任务');
      const task = selected.value;
      if (!['unverified','failed'].includes(task.state)) throw new Error('任务不处于待核实或故障状态');
      if (project.workflow.run.state !== 'running') throw new Error('请先显式继续项目，再重试任务');
      if (!this.adapter.exists) throw new Error('宿主不提供会话存在性核实');
      // Existence errors alone never authorize replay after registered execution.
      const clean = await this.request(agent, 'task_creation_clear', { task_id: taskId });
      if (!clean.clear) throw new Error(`已有执行登记，请使用 /research retry --session ${task.session_id}；缺失会话不能证明退出`);
      if (await this.adapter.exists(task.session_id)) {
        const child = await this.adapter.resolve(task.session_id);
        if (!this.quiet(child) || this.ctx.goals.get(child) || (child.session.events ?? []).some(e => e.type === 'tool/call' || e.type === 'agent/start')) throw new Error('创建中的原生会话已有执行事实，保留待核实');
      }
      await this.workflow(agent, 'task_retry', { task_id: taskId, absent_verified: true }, `${id}:requeue`);
      await this.schedule(agent);
      const result = { task_id: taskId, message: '已核实任务尚未开始执行；使用原任务、原会话 ID 和原目录重试创建', state: (await this.lookup(agent, { ref: taskId })).value.state };
      await this.workflow(agent, 'intent', { intent_id: id, kind: 'recovery-receipt', state: 'complete', details: result }, `${id}:result`);
      return result;
    });
  }
  async verifySpecialist(agent, taskId, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const task = await this.request(agent, 'specialist_get', { task_id: taskId });
      if (task.state !== 'unverified') return task;
      if (!task.child_session_id) throw new Error('专家身份缺失，无法证明退出；保留待核实');
      if ([...this.pendingSpecialists.values()].some(p => p.task.task_id === taskId)) throw new Error('原生专家调用尚未返回');
      let child;
      try { child = await this.adapter.resolve(task.child_session_id); }
      catch { throw new Error('无法读取专家原生会话，保留待核实'); }
      const current = await this.state(child);
      if (!this.quiet(child) || !this.recoveryClear(child, current)) throw new Error('专家工具或作业退出仍未核实');
      const handle = this.specialistHandles.get(taskId);
      if (handle) { await handle.dispose(); this.specialistHandles.delete(taskId); }
      const parent = await this.adapter.resolve(task.parent_session_id);
      return this.request(parent, 'specialist_finish', { fields: { task_id: taskId, state: 'incomplete', result: task.result ?? {}, error: task.error, exit_verified: true } }, `${id}:verified`);
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
      await this.settleStops(agent, false);
      state = await this.state(agent);
      return { message: state.workflow.run.state === 'stopped' ? '研究已停止' : '停止待核实；请查看原生会话与作业状态', state: state.workflow.run.state };
    });
  }
  async settleStops(agent, verify = false) {
    const state = await this.state(agent);
    if (!['stopping','unverified'].includes(state.workflow.run.state)) return;
    let complete = true;
    for (const task of state.workflow.tasks.filter(t => !terminal(t.state) && !state.workflow.sessions.some(s => s.session_id === t.session_id))) {
      if (task.state === 'queued') {
        await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'cancelled' });
        continue;
      }
      // A failed create may have succeeded in the host before losing its receipt.
      // Only explicit absence, with no registered execution, permits settlement.
      try {
        if (!verify || !this.adapter.exists || await this.adapter.exists(task.session_id)) { complete = false; continue; }
        const clean = await this.request(agent, 'task_creation_clear', { task_id: task.task_id });
        if (!clean.clear) { complete = false; continue; }
        await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'cancelled' });
      } catch { complete = false; }
    }
    for (const row of state.workflow.sessions.filter(managed)) {
      const target = this.ctx.agents.get(row.session_id);
      if (!target || !this.quiet(target)) { complete = false; continue; }
      const current = await this.state(target);
      if (current.attempt?.state === 'unknown' || !this.recoveryClear(target,current)) { complete = false; continue; }
      if (row.pause_reason === 'unverified' && !verify) { complete = false; continue; }
      if (row.pause_reason === 'unverified') {
        this.faults.delete(target.id);
        await this.workflow(target, 'session', { pause_reason: 'stop' }, `verify-stop:${target.id}:reason`);
      }
      if (current.attempt) await this.request(target, 'finish', { state: 'stopped', details: { reason: 'project-stop-confirmed' } });
      const task = state.workflow.tasks.find(t => t.session_id === target.id && !terminal(t.state));
      if (task) await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'cancelled' });
      this.stopping.delete(target.id);
    }
    await this.workflow(agent, 'run', { state: complete ? 'stopped' : 'unverified' });
  }
  async verifyStop(agent, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const state = await this.state(agent);
      if (!['stopping', 'unverified'].includes(state.workflow.run.state)) {
        return { message: '项目当前没有待核实的停止操作', state: state.workflow.run.state };
      }
      await this.workflow(agent, 'intent', { intent_id: id, kind: 'verify-stop', state: 'requested' }, `${id}:intent`);
      await this.settleStops(agent, true);
      const final = await this.state(agent);
      return { message: final.workflow.run.state === 'stopped' ? '所有受管理执行均已核实退出，研究已停止' : '仍有执行、工具或作业无法确认退出', state: final.workflow.run.state };
    });
  }
  async dispatch(agent, nodeId, id) {
    return this.serial(agent, async () => {
      const task = await this.workflow(agent, 'task', { node_id: nodeId }, id);
      await this.schedule(agent);
      // Terminal tasks leave the control view, but their retry receipts remain valid.
      return (await this.lookup(agent, { ref: task.task_id })).value;
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
  async requestClose(agent, details = {}, id = operationId(agent), { allowMain = false } = {}) {
    const state = await this.state(agent), row = state.workflow.session;
    if (!managed(row)) throw new Error('Only managed research executors have work segments');
    if (row.role === 'main' && !allowMain) {
      throw new Error('The main coordinator cannot use research_finish; publish/checkpoint, wait for nodes, or complete its native goal.');
    }
    if (!state.attempt) {
      await this.workflow(agent, 'session', { close_state: 'closed', close_attempt_id: null, close_reason: JSON.stringify(details) }, `${id}:closed-empty`);
      return { close_state: 'closed', attempt_id: null, message: 'No active research work segment' };
    }
    await this.workflow(agent, 'session', {
      close_state: 'requested', close_attempt_id: state.attempt.attempt_id,
      close_reason: JSON.stringify(details),
    }, `${id}:intent`);
    // Disarm only the node executor's owned continuation after the durable
    // intent exists.  Otherwise an active native goal can start the next step
    // before agent/idle gets a chance to verify and close the segment.
    if (row.role !== 'main') await this.pauseSession(agent, 'closing', `${id}:closing`);
    return { close_state: 'requested', attempt_id: state.attempt.attempt_id, message: 'Close requested; finalization waits for native tools and owned jobs to exit.' };
  }
  async finalizeClose(agent, verify = false, id = operationId(agent)) {
    let state = await this.state(agent), row = state.workflow.session;
    if (!row || !['requested','unverified'].includes(row.close_state)) return null;
    if (!this.quiet(agent) || !this.recoveryClear(agent, state)) {
      if (row.close_state !== 'unverified') {
        await this.workflow(agent, 'session', { close_state: 'unverified' }, `${id}:unverified`);
      }
      return { close_state: 'unverified', attempt_id: row.close_attempt_id };
    }
    if (row.close_state === 'unverified' && !verify) {
      return { close_state: 'unverified', attempt_id: row.close_attempt_id };
    }
    if (state.attempt && state.attempt.attempt_id !== row.close_attempt_id) {
      await this.workflow(agent, 'session', { close_state: 'unverified', close_reason: 'attempt identity changed before close verification' }, `${id}:identity`);
      return { close_state: 'unverified', attempt_id: row.close_attempt_id };
    }
    if (row.role !== 'main') await this.pauseSession(agent, 'segment_complete', `${id}:pause-core`);
    if (state.attempt) {
      await this.request(agent, 'finish', { state: 'finished', details: { reason: 'verified-work-segment-close', close_reason: row.close_reason } }, `${id}:finish`);
    }
    await this.workflow(agent, 'session', {
      close_state: 'closed', close_attempt_id: row.close_attempt_id,
      pause_reason: row.role === 'main' ? 'complete' : 'segment_complete',
    }, `${id}:closed`);
    state = await this.state(agent);
    return { close_state: 'closed', attempt_id: row.close_attempt_id, task: state.workflow.tasks.find(t => t.session_id === agent.id) ?? null };
  }
  async verifyClose(agent, sessionId, id = operationId(agent)) {
    return this.serial(agent, async () => {
      const target = await this.adapter.resolve(sessionId);
      const state = await this.state(target);
      if (!managed(state.workflow.session)) throw new Error('Target is not a managed research executor');
      if (!['requested','unverified'].includes(state.workflow.session.close_state)) throw new Error('Target has no close intent to verify');
      const result = await this.finalizeClose(target, true, id);
      if (result?.close_state !== 'closed') throw new Error('Native tools or owned jobs have not exited; close remains unverified');
      await this.schedule(agent);
      return result;
    });
  }
  async schedule(agent) {
    let state = await this.state(agent);
    const taskSessions = new Set(state.workflow.tasks.map(task => task.session_id));
    let slots = state.workflow.tasks.filter(t => ['starting','running','waiting','stopping','unverified'].includes(t.state)).length;
    for (const row of state.workflow.sessions.filter(managed)) {
      if (taskSessions.has(row.session_id)) continue;
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
      await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'starting' }, `${task.task_id}:${task.updated_at}:starting`);
      try {
        let child, workspace;
        if (task.cwd) {
          child = await this.adapter.resolve(task.session_id);
          workspace = task.cwd;
          const current = await this.state(child);
          if (child.session.header.cwd !== workspace || !this.quiet(child) || !this.recoveryClear(child, current)) {
            throw new Error('Persistent node core is not quiet in its recorded workspace');
          }
        } else {
          const prepared = await this.request(parent, 'prepare_branch', { node_id: task.node_id }, `${task.task_id}:prepare`);
          workspace = prepared.workspace;
          child = await this.createSession(parent, task.session_id, workspace);
          const context = await this.request(parent, 'task_context', { task_id: task.task_id });
          await this.request(child, 'open', { root: state.project_root, cwd: workspace, session_role: 'node_core', node_id: task.node_id, context }, `${task.task_id}:open`);
        }
        await this.workflow(child, 'task_state', { task_id: task.task_id, state: 'running', cwd: workspace }, `${task.task_id}:${task.updated_at}:running`);
        await this.arm(child, await this.state(child), `${task.task_id}:arm:${task.updated_at}`);
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
    if (kind === 'finished' && task) await this.pauseSession(agent, 'segment_complete');
    if (task && !terminal(task.state)) {
      await this.workflow(agent, 'notify', { key: value.publication_id ?? value.attempt_id ?? id, kind, reference: value.publication_id ? `pub/${value.publication_id}` : null, summary: value.summary ?? '', gaps: value.gaps ?? [] }, `${id}:notice`);
      if (kind === 'failed') await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'failed', error: value.summary }, `${id}:failed`);
      if (kind === 'finished') {
        await this.workflow(agent, 'task_state', { task_id: task.task_id, state: 'finished' }, `${id}:task`);
      }
    }
    await this.serial(agent, () => this.schedule(agent));
  }
  async delegate(agent, args, exec, id, purpose = 'domain') {
    if (!this.ctx.subagents?.start) throw new Error('Native DSH subagent service is unavailable');
    const state = await this.state(agent), row = state.workflow.session;
    if (!managed(row)) throw new Error('Only managed research agents may delegate specialists');
    if (agent.session.header.parentSession) throw new Error('Specialists cannot recursively delegate');
    let task = await this.request(agent, 'specialist_create', { fields: {
      purpose, label: args.label ?? (purpose === 'review' ? '整理与复核' : '节点专家'),
      prompt: args.prompt, inputs: args.inputs ?? [], node_id: args.node_id ?? state.attempt?.node_id ?? row.node_id,
      fanout_limit: this.specialistFanout,
    }, model_call: true }, id);
    task = await this.request(agent, 'specialist_get', { task_id: task.task_id });
    if (['completed', 'incomplete', 'cancelled'].includes(task.state) && task.exit_verified) {
      return task;
    }
    if (['running', 'unverified'].includes(task.state)) return task;
    const pending = { task, operationId: id, bound: null, childSessionId: null };
    this.pendingSpecialists.set(agent.id, pending);
    let run, result, disposed = false;
    try {
      const toolFilter = Array.isArray(args.tool_scope) && args.tool_scope.length
        ? { allow: [...new Set([...args.tool_scope, 'research_query'])] }
        : { deny: [
          'research_dispatch', 'research_wait', 'research_propose', 'research_note',
          'research_memory', 'research_snapshot', 'research_publish', 'research_relate',
          'research_finish', 'research_close_node', 'research_delegate',
        ] };
      run = await this.ctx.subagents.start(this.subagentProvider, {
        label: task.label,
        prompt: [{ type: 'text', text: task.prompt }],
        parent: agent,
        signal: exec.signal,
        maxDepth: 1,
        toolFilter,
        persona: purpose === 'review'
          ? 'Act as an independent research consolidation reviewer. Read the fixed research context and return conflicts, applicable conditions, early negative results, and proposed revisions with sources. Do not mutate the research ledger.'
          : 'Act as a bounded domain specialist. Answer only the fixed question from the supplied research context, preserve uncertainty and disagreements, and do not mutate the research ledger.',
      });
      if (!run.localAgent) throw new Error('The configured provider did not create a local native specialist session');
      this.specialistHandles.set(task.task_id, run);
      const nativeResult = Promise.resolve(run.result).then(
        value => ({ ok: true, value }), error => ({ ok: false, error }),
      );
      pending.childSessionId = run.localAgent.id;
      await this.ensureSpecialist(run.localAgent);
      const settled = await nativeResult;
      if (!settled.ok) throw settled.error;
      const native = settled.value;
      result = {
        stop_reason: native.stopReason,
        output: native.output ?? [],
        ...(native.structured === undefined ? {} : { structured: native.structured }),
        ...(native.diagnostic ? { diagnostic: native.diagnostic } : {}),
      };
      await run.dispose(); disposed = true; this.specialistHandles.delete(task.task_id);
      const stateName = native.stopReason === 'completed' ? 'completed'
        : native.stopReason === 'aborted' ? 'cancelled' : 'incomplete';
      return await this.request(agent, 'specialist_finish', { fields: {
        task_id: task.task_id, state: stateName, result,
        error: native.diagnostic ?? null, exit_verified: true,
      } }, `${id}:finish`);
    } catch (error) {
      let verified = !run;
      if (run && !disposed) {
        try { await run.dispose(); verified = true; this.specialistHandles.delete(task.task_id); } catch { verified = false; }
      }
      return this.request(agent, 'specialist_finish', { fields: {
        task_id: task.task_id,
        state: verified ? (exec.signal.aborted ? 'cancelled' : 'incomplete') : 'unverified',
        result: result ?? {}, error: error instanceof Error ? error.message : String(error),
        exit_verified: verified,
      } }, `${id}:finish-error`);
    } finally {
      this.pendingSpecialists.delete(agent.id);
    }
  }
  async idle(agent) {
    return this.serial(agent, async () => {
      await this.settleStops(agent, false);
      await this.finalizeClose(agent, false);
      await this.schedule(agent);
    });
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
