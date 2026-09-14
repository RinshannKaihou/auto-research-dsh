import { randomUUID } from 'node:crypto';

export function agentIdentity(agent, hostId = 'local') {
  return { host_id: hostId, session_id: String(agent.id ?? agent.session?.header?.id) };
}

export function operationId(agent, suffix = randomUUID()) {
  return `${String(agent.id ?? agent.session?.header?.id)}:${suffix}`;
}

export class ResearchDomain {
  constructor(ctx, storage, adapter, config = {}) {
    this.ctx = ctx;
    this.storage = storage;
    this.adapter = adapter;
    this.hostId = config.hostId ?? 'local';
    this.maxGoalRounds = config.maxGoalRounds ?? 20;
    this.autonomousConcurrency = config.autonomousConcurrency ?? 2;
  }

  identity(agent) { return agentIdentity(agent, this.hostId); }

  request(agent, method, fields = {}, id) {
    return this.storage.request(method, this.identity(agent), fields, id ?? operationId(agent));
  }

  async open(agent, { goal, budget = 0, root } = {}, id) {
    const projectRoot = root ?? agent.session.header.cwd;
    if (!projectRoot) throw new Error('The native session has no working directory');
    return this.request(agent, 'open', { root: projectRoot, goal, budget }, id);
  }

  async autonomousStatus(agent) {
    const state = await this.request(agent, 'query');
    if (state.usage.unknown_count > 0) {
      throw new Error('Unknown model usage must be reconciled before autonomous research');
    }
    if (!(state.usage.budget > 0)) throw new Error('Set a positive project budget before /research auto');
    if (state.usage.remaining <= 0) throw new Error('Project budget is exhausted; manual research remains available');
    return state;
  }

  async focus(agent, nodeId, id) {
    const state = await this.request(agent, 'query', {}, `${id}:query`);
    if (agent.status !== 'idle') {
      return this.request(agent, 'focus', {
        node_id: nodeId,
        defer: true,
        mode: state.project.control === 'auto' ? 'auto' : 'manual',
      }, id);
    }
    if (state.attempt) {
      await this.request(agent, 'finish', {
        state: 'finished',
        details: { reason: 'focus-switch' },
      }, `${id}:finish`);
    }
    return this.request(agent, 'focus', {
      node_id: nodeId,
      mode: state.project.control === 'auto' ? 'auto' : 'manual',
    }, `${id}:focus`);
  }

  async detach(agent, id) {
    const state = await this.request(agent, 'query', {}, `${id}:query`);
    if (state.attempt) {
      await this.request(agent, 'finish', {
        state: 'finished',
        details: { reason: 'session-detach' },
      }, `${id}:finish`);
    }
    return this.request(agent, 'detach', {}, `${id}:detach`);
  }

  async auto(agent, id, budget) {
    if (budget !== undefined) {
      if (!Number.isFinite(budget) || budget <= 0) throw new Error('Autonomous budget must be positive');
      await this.request(agent, 'budget', { budget }, `${id}:budget`);
    }
    let state = await this.autonomousStatus(agent);
    if (state.attempt?.mode === 'manual') {
      const prior = state.attempt;
      await this.request(agent, 'finish', {
        state: 'finished', details: { reason: 'autonomous-mode-enabled' },
      }, `${id}:finish-manual`);
      await this.request(agent, 'focus', {
        node_id: prior.node_id,
        role: prior.role,
        mode: 'auto',
      }, `${id}:focus-auto`);
      state = await this.autonomousStatus(agent);
    }
    const current = this.ctx.goals.get(agent);
    const owned = state.owned_goal;
    if (current && (!owned || current.id !== owned.goal_id) && current.phase !== 'complete') {
      throw new Error('This session already has a non-plugin native goal');
    }
    const objective = [
      `Advance research project: ${state.project.goal}`,
      'Use native DSH tools for the work and research_* tools for notes, snapshots, publications, relations, and work-segment completion.',
      'Continue within the current native session. Stop the goal when the research objective is met; do not infer publication or node closure from goal completion.',
    ].join('\n');
    const goal = current?.phase === 'complete'
      ? this.ctx.goals.create(agent, { objective, maxGoalRounds: this.maxGoalRounds })
      : current ?? this.ctx.goals.create(agent, { objective, maxGoalRounds: this.maxGoalRounds });
    await this.request(agent, 'own_goal', {
      goal_id: goal.id,
      revision: goal.revision,
      phase: goal.phase,
    }, `${id}:goal`);
    this.lastOwnedGoal ??= new Map();
    this.lastOwnedGoal.set(String(agent.id), goal.id);
    await this.request(agent, 'control', { control: 'auto' }, `${id}:control`);
    return goal;
  }

  async changeGoal(agent, action, id, { projectControl = true } = {}) {
    const state = action === 'resume' ? await this.autonomousStatus(agent) : await this.request(agent, 'query');
    const current = this.ctx.goals.get(agent);
    if (!current || !state.owned_goal || current.id !== state.owned_goal.goal_id) {
      throw new Error('No plugin-owned native goal is active in this session');
    }
    let goal = current;
    if (action === 'pause' && current.phase === 'active') {
      goal = this.ctx.goals.pause(agent, { id: current.id, revision: current.revision });
    } else if (action === 'resume' && (current.phase === 'paused' || current.phase === 'blocked' || current.activation === 'disarmed')) {
      goal = this.ctx.goals.resume(agent, { id: current.id, revision: current.revision });
    }
    await this.request(agent, 'own_goal', {
      goal_id: goal.id,
      revision: goal.revision,
      phase: goal.phase,
    }, `${id}:goal`);
    this.lastOwnedGoal ??= new Map();
    this.lastOwnedGoal.set(String(agent.id), goal.id);
    if (projectControl) {
      await this.request(agent, 'control', {
        control: action === 'resume' ? 'auto' : 'paused',
      }, `${id}:control`);
    }
    return goal;
  }

  async projectGoals(agent, action, id) {
    if (action === 'resume') await this.autonomousStatus(agent);
    const sessions = await this.storage.request('project_sessions', this.identity(agent));
    const results = [];
    for (const row of sessions) {
      let target;
      try {
        target = await this.adapter.resolve(row.session_id);
        const state = await this.storage.request('query', {
          host_id: row.host_id,
          session_id: row.session_id,
        });
        const goal = this.ctx.goals.get(target);
        if (!goal || state.owned_goal?.goal_id !== goal.id) continue;
        const changed = await this.changeGoal(
          target,
          action,
          `${id}:${row.session_id}`,
          { projectControl: false },
        );
        results.push({ session_id: row.session_id, goal: changed });
      } catch (error) {
        results.push({
          session_id: row.session_id,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    await this.request(agent, 'control', {
      control: action === 'resume' ? 'auto' : 'paused',
    }, `${id}:control`);
    return { action, sessions: results };
  }

  async stopProject(agent, id) {
    const paused = await this.projectGoals(agent, 'pause', `${id}:pause`);
    const sessions = await this.storage.request('project_sessions', this.identity(agent));
    const stopped = [];
    for (const row of sessions) {
      try {
        const identity = { host_id: row.host_id, session_id: row.session_id };
        const state = await this.storage.request('query', identity);
        if (state.attempt) {
          await this.storage.request('finish', identity, {
            state: 'stopped', details: { reason: 'project-stop' },
          }, `${id}:${row.session_id}:finish`);
        }
        const target = await this.adapter.resolve(row.session_id);
        if (target.status !== 'idle') await this.adapter.cancel(row.session_id);
        stopped.push({ session_id: row.session_id, status: 'stop-requested' });
      } catch (error) {
        stopped.push({
          session_id: row.session_id,
          status: 'unverified',
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    await this.request(agent, 'control', { control: 'stopped' }, `${id}:control`);
    return { paused, stopped };
  }

  async branch(agent, nodeId, id) {
    const state = await this.request(agent, 'query');
    if (!state.nodes.some(node => node.node_id === nodeId && node.status !== 'closed')) {
      throw new Error(`Unknown or closed research node: ${nodeId}`);
    }
    if (state.project.control === 'auto') {
      const active = state.attempts.filter(
        attempt => attempt.mode === 'auto' && attempt.ended_at === null,
      ).length;
      if (active >= this.autonomousConcurrency) {
        throw new Error(`Autonomous concurrency limit ${this.autonomousConcurrency} is already in use`);
      }
    }
    const prepared = await this.request(agent, 'prepare_branch', {
      node_id: nodeId,
    }, `${id}:prepare`);
    const projectRoot = prepared.project_root;
    const cwd = prepared.workspace;
    const child = await this.adapter.create({
      cwd,
      agentPreset: agent.session.header.agentPreset,
    });
    await this.adapter.selectModel(child.sessionId, agent.options);
    const identity = { host_id: this.hostId, session_id: String(child.sessionId) };
    await this.storage.request('open', identity, { root: projectRoot }, `${id}:open`);
    await this.storage.request('focus', identity, {
      node_id: nodeId,
      role: 'branch',
      mode: state.project.control === 'auto' ? 'auto' : 'manual',
    }, `${id}:focus`);
    if (state.project.control === 'auto') {
      const target = await this.adapter.resolve(child.sessionId);
      await this.auto(target, `${id}:auto`);
    }
    return { sessionId: child.sessionId, cwd, nodeId, inputs: prepared.inputs };
  }

  async restore(agent, snapshotId, id) {
    const prepared = await this.request(agent, 'prepare_restore', {
      snapshot_id: snapshotId,
    }, `${id}:prepare`);
    const child = await this.adapter.create({
      cwd: prepared.workspace,
      agentPreset: agent.session.header.agentPreset,
    });
    await this.adapter.selectModel(child.sessionId, agent.options);
    const identity = { host_id: this.hostId, session_id: String(child.sessionId) };
    await this.storage.request('open', identity, {
      root: prepared.project_root,
    }, `${id}:open`);
    if (prepared.node_id) {
      await this.storage.request('focus', identity, {
        node_id: prepared.node_id,
        role: 'handoff',
        mode: 'manual',
      }, `${id}:focus`);
    } else {
      await this.storage.request('focus', identity, {
        node_id: null,
        role: 'handoff',
        mode: 'manual',
      }, `${id}:focus`);
    }
    await this.storage.request('record_restore', identity, {
      snapshot_id: snapshotId,
      source_attempt_id: prepared.source_attempt_id,
      workspace: prepared.workspace,
    }, `${id}:record`);
    return { sessionId: child.sessionId, ...prepared };
  }
}
