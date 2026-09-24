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

const specialistParameters = {
        label: { type: 'string', required: true },
        question: { type: 'string', required: true },
        purpose: { type: 'string', required: true },
        inputs: { type: 'array', items: { type: 'string' } },
        tool_scope: { type: 'array', items: { type: 'string' } },
        node_id: { type: 'string' },
        context_mode: { type: 'string', enum: ['research', 'blind'] },
        deliverable: { type: 'string', required: true },
        completion_criteria: { type: 'string', required: true },
        report_requirements: { type: 'string', required: true },
      };
function specialistArgs(args) {
  const inputDescription = args.context_mode === 'blind'
    ? (args.inputs ?? []).map((_, i) => `input-${i + 1}`)
    : args.inputs ?? [];
  return { ...args, prompt: `Question: ${args.question}\nPurpose: ${args.purpose}\nAssigned inputs: ${JSON.stringify(inputDescription)}\nDeliverable: ${args.deliverable}\nCompletion criteria: ${args.completion_criteria}\nReport requirements: ${args.report_requirements}\nPreserve uncertainty; return incomplete when evidence is insufficient.` };
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
      name: 'research_delegate',
      description: 'Start one bounded native specialist in the current node. It receives fixed research context, reads the ledger, and cannot write research records or delegate recursively.',
      timeoutMs: domain.specialistTimeoutMs + 60000,
      isConcurrencySafe: () => false,
      parameters: specialistParameters,
      invoke: (args, exec, id) => domain.delegate(exec.agent, specialistArgs(args), exec, id),
    }),
    tool(domain, {
      name: 'research_delegate_batch', description: 'Synchronously run a bounded batch of independent read-only specialists in parallel. Returns each result; do not use research_wait for specialists.',
      timeoutMs: domain.specialistTimeoutMs + 60000, isConcurrencySafe: () => false,
      parameters: { tasks: { type: 'array', required: true, items: { type: 'object', properties: specialistParameters, additionalProperties: false } } },
      invoke: (args, exec, id) => domain.delegateBatch(exec.agent, { tasks: args.tasks.map(specialistArgs) }, exec, id),
    }),
    tool(domain, {
      name: 'research_read_input', description: 'Read an assigned frozen input by opaque input_id. Offset and limit count Unicode characters; follow next_offset until null.',
      method: 'specialist_read_input',
      parameters: { input_id: { type: 'string', required: true }, offset: { type: 'number' }, limit: { type: 'number' } },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_verify_specialist', description: 'Verify a stuck specialist against native execution facts and settle it if exited. Never edit the research database to free a slot. Active or uncertain tasks stay unchanged.',
      parameters: { task_id: { type: 'string', required: true } },
      invoke: (args, exec, id) => domain.verifySpecialist(exec.agent, args.task_id, id),
    }),
    tool(domain, {
      name: 'research_verify_task', description: 'Verify an exploration task whose native session creation is uncertain. If native absence and lack of execution facts are confirmed, settle it as failed without restarting research.',
      parameters: { task_id: { type: 'string', required: true } },
      invoke: (args, exec, id) => domain.verifyTask(exec.agent, args.task_id, id),
    }),
    tool(domain, {
      name: 'research_query',
      method: 'query',
      description: 'Read a bounded research summary, retrieve a fixed reference, search knowledge, or page a collection. Use the returned cursor to continue. collection="hints" lists current-state structural candidates; use offset/limit to page and kind to filter the hint class. It does not replay historical boundaries.',
      parameters: {
        ref: { type: 'string' }, query: { type: 'string' }, collection: { type: 'string' },
        node_id: { type: 'string' }, kind: { type: 'string' }, after: { type: 'number' },
        status: { type: 'string' }, revision: { type: 'number' },
        conditions: { type: 'object', additionalProperties: true, properties: {} },
        upper_id: { type: 'number' }, offset: { type: 'number' }, limit: { type: 'number' },
      },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_propose',
      method: 'propose',
      description: 'Atomically propose a node as an independent root with root_reason or a derived node with typed predecessors and fixed input_refs. A real predecessor must be declared; a handoff error does not make the successor a root. depends_on controls scheduling; branches_from and revises are scientific lineage only and may have empty input_refs; depends_on requires material inputs.',
      parameters: {
        question: { type: 'string', required: true },
        why_now: { type: 'string', required: true },
        plan: { type: 'string', required: true },
        inputs: { type: 'array', items: { type: 'string' } },
        purpose: { type: 'string' },
        strategy: { type: 'string', enum: ['continue', 'redirect', 'anchor'] },
        anchor_ref: { type: 'string' },
        question_ref: { type: 'string' },
        root_reason: { type: 'string' },
        predecessors: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: false,
            properties: {
              node_id: { type: 'string', required: true },
              relation_type: { type: 'string', required: true, enum: ['depends_on', 'branches_from', 'revises'] },
              rationale: { type: 'string', required: true },
              input_refs: { type: 'array', required: true, items: { type: 'string' } },
            },
          },
        },
        dispatch: { type: 'boolean' },
      },
      map: args => ({ ...args, dispatch: undefined }),
      async after(node, args, exec, id) {
        if (!args.dispatch) return node;
        const state = await domain.state(exec.agent);
        if (state.project.control !== 'auto') return { node, dispatched: false };
        return { node, dispatched: true, branch: await domain.dispatch(exec.agent, node.node_id, `${id}:dispatch`) };
      },
    }),
    tool(domain, {
      name: 'research_consume',
      method: 'consume',
      description: 'Append an idempotent record that the current node attempt adopted a later immutable research reference. This never rewrites the node proposal or its fixed inputs.',
      parameters: {
        source_ref: { type: 'string', required: true },
        use: { type: 'string', required: true },
        relation_type: { type: 'string', required: true, enum: ['adopts', 'supports', 'contradicts', 'context'] },
      },
      map: args => args,
    }),
    tool(domain, {
      name: 'research_memory',
      description: 'Field checks compare only declared fields with a frozen file and do not prove the conclusion. A consistent result does not prove the claim. On revise use changes.checks; omitting checks inherits the declarations. Record or revise sourced knowledge, checkpoint the current node, dispose of an impact, or run consolidation. Revise never inherits execution_refs: omitting it stores []; supply the references again even when the result sources are unchanged. For record, omitted node_id defaults to the current work segment node. Project-wide placement requires visibility="project" explicitly; without a current node supply node_id or project visibility. Placement is retrieval context, not access isolation; keep scientific applicability in conditions/scope. In manual mode consolidate only when the user explicitly asks. Claims, observations, and lessons require evidence_refs. S-xxx#path identifies an entry inside a frozen snapshot. Bare S-xxx evidence on claims or observations produces an advisory candidate hint. Retracting requires change_kind="retract" together with affected_scope_mode="versions" naming the version withdrawn. Every revise must declare affected_scope_mode: "versions" with the exact versions it invalidates, "none" if it invalidates nothing, or "unknown" if you cannot tell. Use relations[] with grounded_in for what a statement rests on (it becomes the basis and propagates), and motivated_by for what merely prompted the work (it does not propagate); for an open question, a prior finding is grounded_in when the question presupposes it and motivated_by when it only explains why this node was chosen now.',
      parameters: {
        action: { type: 'string', required: true, enum: ['record', 'revise', 'checkpoint', 'consolidate', 'dispose', 'narrow_scope'] },
        kind: { type: 'string', enum: ['observation', 'hypothesis', 'lesson', 'decision', 'open_question', 'claim'] },
        statement: { type: 'string' }, node_id: { type: 'string' }, status: { type: 'string' },
        visibility: { type: 'string', enum: ['node', 'project'] },
        scope: { type: 'object', additionalProperties: true, properties: {} },
        conditions: { type: 'object', additionalProperties: true, properties: {} },
        evidence_refs: { type: 'array', items: { type: 'string' } },
        dependencies: { type: 'array', items: { type: 'string' } },
        checks: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
          ref: { type: 'string', required: true }, path: { type: 'string', required: true },
          op: { type: 'string', required: true, enum: ['eq','approx','lt','le','gt','ge'] },
          value: { required: true }, tolerance: { type: 'number' },
        } } },
        execution_refs: { type: 'array', items: { type: 'string' }, description: 'Optional execution references for this write: session:, attempt:, event:, or frozen object references. Unresolved references are retained as unlinked. On revise, omission stores an empty list, never inherited; explicitly supply execution_refs again if the result sources are unchanged. asserted_at is server-managed.' },
        motivated_by: { type: 'array', items: { type: 'string' } },
        relations: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              type: { type: 'string', enum: ['grounded_in', 'answers', 'supersedes', 'complements', 'challenges'] },
              target: { type: 'string' },
            },
          },
        },
        affected_scope_mode: { type: 'string', enum: ['versions', 'none', 'unknown'] },
        affected_scope: { type: 'array', items: { type: 'string' } },
        change_kind: { type: 'string', enum: ['retract', 'correct', 'narrow', 'reword'] },
        change_id: { type: 'string' }, affected_version: { type: 'string' },
        disposition_kind: { type: 'string', enum: ['unresolved', 'retained_with_evidence', 'revised', 'retracted'] },
        replacement_ref: { type: 'string' },
        ref: { type: 'string' }, expected_revision: { type: 'number' }, reason: { type: 'string' },
        changes: { type: 'object', additionalProperties: true, properties: {} },
        state: { type: 'object', additionalProperties: true, properties: {} },
      },
      async invoke(args, exec, id) {
        const fields = Object.fromEntries(Object.entries(args).filter(([key, value]) => key !== 'action' && value !== undefined));
        if (args.action !== 'consolidate') {
          return domain.request(exec.agent, 'memory_write', { action: args.action, fields, model_call: true }, id);
        }
        const state = await domain.state(exec.agent);
        if (!['main','node_core','exploration'].includes(state.workflow.session?.role)) throw new Error('This session cannot start a consolidation reviewer');
        const nodeId = state.workflow.session?.role === 'main' ? null : state.attempt?.node_id ?? state.workflow.session?.node_id;
        // Consolidation takes its work from the impact ledger: review_todos is
        // keyed by the triggering revision and cannot address one affected version.
        const impact = await domain.request(exec.agent, 'impact_next', { node_id: nodeId });
        const todo = await domain.request(exec.agent, 'review_todo_next', { node_id: nodeId });
        if (impact) await domain.request(exec.agent, 'impact_review_state', { change_id: impact.change_id, affected_version: impact.affected_version, state: 'running' }, `${id}:impact-running`);
        if (todo) await domain.request(exec.agent, 'review_todo_state', { todo_id: todo.todo_id, state: 'running' }, `${id}:todo-running`);
        const inputs = [...new Set([...(args.evidence_refs ?? []), ...(impact ? [impact.affected_version] : []), ...(todo ? [todo.trigger_ref] : [])])];
        let review;
        try {
          review = await domain.delegate(exec.agent, {
            label: '整理与复核', node_id: nodeId, inputs,
            prompt: `Review node ${nodeId ?? 'project planning'}. Fixed sources: ${JSON.stringify(inputs)}. Find relevant early negative results and conditions; identify conflicts; return proposed revisions with exact sources, reasons, and uncovered scope. Do not write research records.`,
          }, exec, id, 'review');
        } catch (error) {
          if (impact) await domain.request(exec.agent, 'impact_review_state', { change_id: impact.change_id, affected_version: impact.affected_version, state: 'pending' }, `${id}:impact-pending`);
          if (todo) await domain.request(exec.agent, 'review_todo_state', { todo_id: todo.todo_id, state: 'pending' }, `${id}:todo-pending`);
          throw error;
        }
        // A returned proposal means the materials are ready, nothing more. It
        // records no disposition and clears no risk; only an explicit
        // research_memory(action="dispose") changes how a problem stands.
        const parked = review.state === 'completed' ? 'proposal_ready' : 'pending';
        if (impact) await domain.request(exec.agent, 'impact_review_state', { change_id: impact.change_id, affected_version: impact.affected_version, state: parked }, `${id}:impact-${parked}`);
        if (todo) await domain.request(exec.agent, 'review_todo_state', { todo_id: todo.todo_id, state: parked }, `${id}:todo-${parked}`);
        return { impact, todo, review };
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
      description: 'Publish an immutable partial or complete stage. Zero-experiment and empty-findings publications are valid. When possible include display: a short title, overview and grouped points in the research goal language; distinguish findings, untested hypotheses and limitations. Keep the detailed account in summary/report; do not call another model to format it. Display is optional for compatibility.',
      parameters: {
        status: { type: 'string', required: true, enum: ['partial', 'complete'] },
        summary: { type: 'string', required: true },
        display: { type: 'object', properties: {
          title: { type: 'string', required: true, description: 'Short plain-text title, at most 80 characters.' },
          overview: { type: 'string', required: true, description: 'Brief plain-text summary, at most 400 characters.' },
          sections: { type: 'array', description: 'At most 4 groups; separate results, hypotheses and limitations as appropriate.', items: {type:'object',properties:{
            heading: {type:'string',required:true,description:'At most 80 characters.'},
            items: {type:'array',required:true,description:'1–4 plain-text points, each at most 240 characters.',items:{type:'string'}},
          }}},
          primary_item_id: { type:'string', description:'Optional item_id of a file report in this publication; never an inferred prose path.' },
        }},
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
              knowledge_refs: { type: 'array', items: { type: 'string' } },
            },
          },
        },
        knowledge_refs: { type: 'array', items: { type: 'string' } },
      },
      map: args => ({ ...args, gaps: args.gaps ?? [], items: args.items ?? [], knowledge_refs: args.knowledge_refs ?? [] }),
    }),
    tool(domain, {
      name: 'research_relate',
      method: 'relate',
      description: 'Record a research relation between nodes or publications, or a revision rationale. This stores evidence structure and does not judge scientific truth. The reserved knowledge labels grounded_in, answers, supersedes, complements and challenges are refused here when both sides are knowledge versions: write those through research_memory relations[] so they are stored with the revision.',
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
      description: 'Request verified closure of the current node-core work segment after native tools and owned jobs exit. The main coordinator must instead publish/checkpoint, wait, or complete its native goal.',
      parameters: {
        state: { type: 'string', enum: ['finished', 'stopped', 'unknown'] },
        details: { type: 'object', additionalProperties: true, properties: {} },
      },
      invoke: (args, exec, id) => domain.requestClose(exec.agent, { state: args.state ?? 'finished', ...(args.details ?? {}) }, id),
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
