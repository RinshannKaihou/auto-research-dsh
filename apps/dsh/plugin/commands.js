function parseInit(input) {
  const match = input.trim().match(/^(.*?)(?:\s+--budget\s+(\d+(?:\.\d+)?))?$/);
  return { goal: match?.[1]?.trim(), budget: match?.[2] === undefined ? 0 : Number(match[2]) };
}

function result(value) {
  return { kind: 'success', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) };
}

export function registerResearchCommand(ctx, domain) {
  return ctx.commands.register({
    name: 'research',
    description: 'Manage the research project associated with this native session',
    input: { hint: 'init|open|status|focus|auto|pause|resume|stop|branch|restore|detach …' },
    async handler(invocation) {
      const input = invocation.rawInput.trim();
      const [action = 'status', ...rest] = input.split(/\s+/);
      const arg = rest.join(' ').trim();
      const id = `${invocation.agent.id}:${invocation.commandId}`;
      try {
        if (action === 'init') {
          const options = parseInit(arg);
          if (!options.goal) throw new Error('Usage: /research init <goal> [--budget <amount>]');
          return result(await domain.open(invocation.agent, options, id));
        }
        if (action === 'open') return result(await domain.open(invocation.agent, {}, id));
        if (action === 'status') return result(await domain.request(invocation.agent, 'query', {}, id));
        if (action === 'focus') {
          const nodeId = arg === 'planning' || arg === '' ? null : arg;
          return result(await domain.focus(invocation.agent, nodeId, id));
        }
        if (action === 'auto') {
          const requestedBudget = arg ? Number(arg) : undefined;
          return result(await domain.auto(invocation.agent, id, requestedBudget));
        }
        if (action === 'pause') return result(await domain.projectGoals(invocation.agent, 'pause', id));
        if (action === 'resume') return result(await domain.projectGoals(invocation.agent, 'resume', id));
        if (action === 'stop') {
          return result(await domain.stopProject(invocation.agent, id));
        }
        if (action === 'branch') {
          if (!arg) throw new Error('Usage: /research branch <node-id>');
          return result(await domain.branch(invocation.agent, arg, id));
        }
        if (action === 'restore') {
          if (!arg) throw new Error('Usage: /research restore <snapshot-id>');
          return result(await domain.restore(invocation.agent, arg, id));
        }
        if (action === 'detach') {
          return result(await domain.detach(invocation.agent, id));
        }
        throw new Error(`Unknown /research action: ${action}`);
      } catch (error) {
        return { kind: 'error', text: error instanceof Error ? error.message : String(error) };
      }
    },
  });
}
