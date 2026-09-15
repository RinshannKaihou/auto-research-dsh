import { defineResearchTool } from './tool-definition.js';

const output = {
  schema: { type: 'object', additionalProperties: true, properties: {} },
  render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
};

function tool(domain, definition) {
  return defineResearchTool({
    ...definition,
    output,
    async execute(args, exec) {
      if (!exec.agent) throw new Error('research tools require an owning native agent');
      const id = `${exec.agent.id}:${exec.callId}:${definition.name}`;
      const value = definition.invoke
        ? await definition.invoke(args, exec, id)
        : await domain.request(exec.agent, definition.method, { ...definition.map(args), model_call: true }, id);
      if (definition.method === 'publish') await domain.progress(exec.agent, 'published', value, id);
      if (definition.method === 'finish') await domain.progress(exec.agent, 'finished', value, id);
      return definition.after ? definition.after(value, args, exec, id) : value;
    },
  });
}

export function registerResearchTools(ctx, domain) {
  const definitions = [
    tool(domain, {
      name: 'research_dispatch', description: 'Dispatch an existing research node as an independent native exploration task. Capacity queues tasks durably. Only enabled autonomous research agents may dispatch.',
      parameters: { node_id: { type: 'string', required: true } },
      invoke: (args, exec, id) => domain.dispatch(exec.agent, args.node_id, id),
    }),
    tool(domain, {
      name: 'research_wait', description: 'Wait for progress from your dispatched tasks. Pause native continuation and release capacity after the current turn and jobs finish. No polling is needed.',
      parameters: { task_ids: { type: 'array', required: true, items: { type: 'string' } } },
      invoke: (args, exec, id) => domain.wait(exec.agent, args.task_ids, id),
    }),
    tool(domain, {
      name: 'research_query',
      method: 'query',
      description: 'Read the associated research project, nodes, immutable publications, relations, work segments, snapshots, and usage.',
      parameters: { ref: { type: 'string' } },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_propose',
      method: 'propose',
      description: 'Propose a research node with fixed publication inputs. In manual mode this records the proposal without dispatching work.',
      parameters: {
        question: { type: 'string', required: true },
        why_now: { type: 'string', required: true },
        plan: { type: 'string', required: true },
        inputs: { type: 'array', items: { type: 'string' } },
        purpose: { type: 'string' },
        strategy: { type: 'string', enum: ['continue', 'redirect', 'anchor'] },
        anchor_ref: { type: 'string' },
        dispatch: { type: 'boolean' },
      },
      map: args => ({ ...args, dispatch: undefined }),
      async after(node, args, exec, id) {
        if (!args.dispatch) return node;
        const state = await domain.request(exec.agent, 'query');
        if (state.project.control !== 'auto') return { node, dispatched: false };
        return { node, dispatched: true, branch: await domain.dispatch(exec.agent, node.node_id, `${id}:dispatch`) };
      },
    }),
    tool(domain, {
      name: 'research_note',
      method: 'note',
      description: 'Record progress, conditions, gaps, or a human correction in the current research work segment.',
      parameters: {
        body: { type: 'string', required: true },
        kind: { type: 'string', enum: ['progress', 'condition', 'gap', 'correction'] },
      },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_snapshot',
      method: 'snapshot',
      description: 'Freeze selected project files or directories as a handoff snapshot. Runtime control files, credentials, and Git metadata are rejected.',
      parameters: {
        paths: { type: 'array', required: true, items: { type: 'string' } },
      },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_publish',
      method: 'publish',
      description: 'Publish an immutable partial or complete stage. Zero-experiment and empty-findings publications are valid.',
      parameters: {
        status: { type: 'string', required: true, enum: ['partial', 'complete'] },
        summary: { type: 'string', required: true },
        gaps: { type: 'array', items: { type: 'string' } },
        items: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: true,
            properties: {
              item_id: { type: 'string', required: true },
              kind: { type: 'string' },
              content: { type: 'object', additionalProperties: true, properties: {} },
              source_path: { type: 'string' },
            },
          },
        },
      },
      map: args => ({ ...args, gaps: args.gaps ?? [], items: args.items ?? [] }),
    }),
    tool(domain, {
      name: 'research_relate',
      method: 'relate',
      description: 'Record a research relation or revision rationale. This stores evidence structure and does not judge scientific truth.',
      parameters: {
        source_ref: { type: 'string', required: true },
        target_ref: { type: 'string', required: true },
        label: { type: 'string', required: true },
        note: { type: 'string', required: true },
      },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_finish',
      method: 'finish',
      description: 'Finish the current research work segment without publishing or closing its node.',
      parameters: {
        state: { type: 'string', enum: ['finished', 'stopped', 'unknown'] },
        details: { type: 'object', additionalProperties: true, properties: {} },
      },
      map: args => ({ state: args.state ?? 'finished', details: args.details ?? {} }),
    }),
    tool(domain, {
      name: 'research_close_node',
      method: 'close_node',
      description: 'Explicitly close a research node after its active work segment has ended. A successful publication is not required.',
      parameters: { node_id: { type: 'string', required: true } },
      map: args => args,
    }),
  ];
  return definitions.map(definition => ctx.tools.register(definition));
}
