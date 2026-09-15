/** Native controller boundary. Legacy session strings never become bindings. */
export class SessionAdapter {
  constructor(ctx) { this.ctx = ctx; }
  async inspect(sessionId) {
    return this.ctx.sessionController.inspect(sessionId);
  }
  async exists(sessionId) {
    try { await this.inspect(sessionId); return true; }
    catch (error) {
      // DSH 0.1.2 exposes this explicit absence type; IO errors are not absence.
      if (error?.constructor?.name === 'ApiSessionNotFound') return false;
      throw error;
    }
  }
  async resolve(sessionId) {
    const result = await this.ctx.sessionController.resolveAgent(sessionId);
    if (result.error) throw result.error;
    return result.agent;
  }
  async create({ cwd, sessionId, agentPreset }) {
    return this.ctx.sessionController.create({
      cwd,
      ...(sessionId ? { sessionId } : {}),
      ...(agentPreset ? { agentPreset } : {}),
    });
  }
  async cancel(sessionId) {
    return this.ctx.sessionController.cancel({ sessionId });
  }
  async selectModel(sessionId, options = {}) {
    if (!options.provider || !options.model) return undefined;
    return this.ctx.sessionController.selectModel({
      sessionId,
      provider: options.provider,
      model: options.model,
      ...(options.reasoningEffort ? { reasoningEffort: options.reasoningEffort } : {}),
    });
  }
}
