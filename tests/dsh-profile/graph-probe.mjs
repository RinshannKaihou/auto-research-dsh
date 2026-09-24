/** Run only in an isolated official Web profile. A deterministic adapter emits
 * native tool calls; all other provider streams are blocked before dispatch. */
import {writeFileSync, mkdirSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const {LlmAdapter} = await import(pathToFileURL(join(process.env.ARI_DSH_ROOT,'node_modules/@deepseek-ai/dsh-llm/lib/index.js')));
export const name = 'ari-profile-probe';
export const inject = ['sessionController', 'sessionQuery', 'sessions', 'llm', 'appReady', 'agents', 'commands', 'goals', 'workspaceRegistry'];
export function apply(ctx) {
  const report = {profile:'base+web+standard', real_model_requests:0, calls:[], checks:[], tool_events:[]};
  const save = () => writeFileSync(process.env.ARI_PROBE_OUTPUT, JSON.stringify(report,null,2));
  const step = new Map();
  let resolveAutoStarted;
  const autoStarted = new Promise(resolve => { resolveAutoStarted = resolve; });
  class Fixture extends LlmAdapter {
    async *stream(options) {
      report.calls.push({sessionId:options.sessionId, purpose:options.purpose ?? 'conversation', at:Date.now()});
      if (options.purpose) {
        yield {type:'block-end', index:0, block:{type:'text',text:'Native fixture session'}};
        yield {type:'usage',usage:{inputTokens:10,outputTokens:4,totalTokens:14}};
        yield {type:'finish',reason:{kind:'stop'}};
        return;
      }
      const index = step.get(options.sessionId) ?? 0;
      step.set(options.sessionId,index+1);
      if (options.sessionId === 'ari-profile-probe-auto') {
        if (index === 0) {
          resolveAutoStarted();
          await new Promise(resolve => setTimeout(resolve,150));
        }
        yield {type:'block-end',index:0,block:{type:'text',text:index === 0 ? 'Native goal round started.' : 'Human intervention processed.'}};
        yield {type:'usage',usage:{inputTokens:20,outputTokens:5,totalTokens:25}};
        yield {type:'finish',reason:{kind:'stop'}};
        return;
      }
      const calls = [
        {name:'bash',arguments:JSON.stringify({description:'Check native workspace and concurrent process output',command:'pwd; printf "NATIVE_START\\n"; sleep 1; printf "NATIVE_END\\n"'})},
        {name:'research_note',arguments:JSON.stringify({body:'Native deterministic progress',kind:'progress'})},
        {name:'research_publish',arguments:JSON.stringify({status:'partial',summary:'Partial fixture publication',gaps:['No scientific evaluation'],items:[{item_id:'sentinel',kind:'fixture',source_path:'sentinel.txt'}]})},
        {name:'research_publish',arguments:JSON.stringify({status:'partial',summary:'Second fixture publication',gaps:[],items:[{item_id:'sentinel',kind:'fixture',content:{revision:2}}]})},
        {name:'research_propose',arguments:JSON.stringify({question:'Establish a transparent baseline',why_now:'Graph fixture',plan:'Measure baseline',inputs:[]})},
        {name:'research_propose',arguments:JSON.stringify({question:'Validate the revised detector',why_now:'Follow the baseline',plan:'Audit and compare',inputs:['pub/P-001#sentinel','pub/P-002#sentinel']})},
        {name:'research_propose',arguments:JSON.stringify({question:'Independent alternative',why_now:'Disconnected fixture',plan:'Explore',inputs:[]})},
        {name:'research_relate',arguments:JSON.stringify({source_ref:'X-001',target_ref:'X-002',label:'supports',note:'Explicit native tool relation'})},
        {name:'research_relate',arguments:JSON.stringify({source_ref:'X-002',target_ref:'X-001',label:'revisits',note:'Cycle is legal'})},
      ];
      if (index < calls.length) {
        yield {type:'block-end',index:0,block:{type:'tool-call',id:`fixture-${options.sessionId}-${index}`,...calls[index]}};
        yield {type:'usage',usage:{inputTokens:20,outputTokens:5,totalTokens:25}};
        yield {type:'finish',reason:{kind:'tool-calls'}};
      } else {
        yield {type:'block-end',index:0,block:{type:'text',text:'Native tool probe complete; no scientific claim.'}};
        yield {type:'usage',usage:{inputTokens:20,outputTokens:5,totalTokens:25}};
        yield {type:'finish',reason:{kind:'stop'}};
      }
    }
  }
  ctx.llm.registerAdapter(['ari-fixture'],new Fixture());
  ctx.on('llm/stream', async function* (options,next) {
    if (options.provider !== 'ari-fixture') throw new Error('PROFILE_PROBE_NO_PAID_REQUESTS');
    yield* next();
  });
  ctx.on('session/event',(session,event)=>{
    if (event.type?.startsWith('tool/') || event.kind?.startsWith('tool/')) report.tool_events.push({sessionId:session.id,event,at:Date.now()});
  });
  ctx.appReady.onReady(async () => {
    try {
      if (process.env.ARI_PROBE_PHASE === 'restart') {
        for (const suffix of ['a','b','auto']) {
          const sessionId = `ari-profile-probe-${suffix}`;
          const before = !!ctx.agents.get(sessionId);
          const history = await ctx.sessionController.inspect(sessionId);
          const resolution = await ctx.sessionController.resolveAgent(sessionId);
          if (resolution.error) throw resolution.error;
          const restored = resolution.agent;
          report.checks.push({
            sessionId,
            cold:!before,
            history,
            restoredGoal:restored ? ctx.goals.get(restored) : null,
          });
        }
      } else {
        for (const suffix of ['a','b']) {
          const cwd = join(process.env.DSH_HOME,`workspace-${suffix}`);
          mkdirSync(cwd,{recursive:true});
          const workspace = await ctx.workspaceRegistry.create(cwd);
          writeFileSync(join(cwd,'sentinel.txt'),`PRIVATE_SENTINEL_${suffix.toUpperCase()}\n`);
          const sessionId = `ari-profile-probe-${suffix}`;
          const created = await ctx.sessionController.create({sessionId,cwd});
          await workspace.attachSession(sessionId);
          const flushed = await ctx.sessions.flush(ctx.sessions.get(sessionId));
          const beforePrompt = await ctx.sessionController.inspect(sessionId);
          report.checks.push({sessionId,created,flushed,beforePrompt});
          await ctx.sessionController.selectModel({sessionId,provider:'ari-fixture',model:'deterministic'});
          const agent = ctx.agents.get(sessionId);
          const init = await ctx.commands.execute(
            agent,
            '/research init Deterministic native research',
            [],
            new AbortController().signal,
          );
          const focus = await ctx.commands.execute(
            agent,
            '/research focus planning',
            [],
            new AbortController().signal,
          );
          report.checks.push({sessionId,init,focus});
        }
        await Promise.all(['a','b'].map(async suffix=>{
          const sessionId = `ari-profile-probe-${suffix}`;
          const agent = ctx.agents.get(sessionId);
          for (let turn = 1; turn <= 3; turn += 1) {
            await ctx.sessionController.prompt({sessionId,requestId:`probe-${sessionId}-${turn}`,content:[{type:'text',text:`Run deterministic research turn ${turn}.`}]},new AbortController().signal);
            await agent.whenIdle();
          }
          await ctx.sessions.flush(agent.session);
          const status = await ctx.commands.execute(
            agent,
            '/research status',
            [],
            new AbortController().signal,
          );
          report.checks.push({sessionId,status,after:await ctx.sessionController.inspect(sessionId)});
        }));
        const autoSessionId = 'ari-profile-probe-auto';
        const autoCwd = join(process.env.DSH_HOME,'workspace-auto');
        mkdirSync(autoCwd,{recursive:true});
        const autoWorkspace = await ctx.workspaceRegistry.create(autoCwd);
        await ctx.sessionController.create({sessionId:autoSessionId,cwd:autoCwd});
        await autoWorkspace.attachSession(autoSessionId);
        await ctx.sessionController.selectModel({sessionId:autoSessionId,provider:'ari-fixture',model:'deterministic'});
        const autoAgent = ctx.agents.get(autoSessionId);
        await ctx.commands.execute(
          autoAgent,
          '/research init Deterministic autonomous research',
          [],
          new AbortController().signal,
        );
        const createdGoal = await ctx.commands.execute(
          autoAgent,
          '/research auto',
          [],
          new AbortController().signal,
        );
        await Promise.race([
          autoStarted,
          new Promise((_,reject) => setTimeout(() => reject(new Error('native goal round did not start')),5000)),
        ]);
        await ctx.sessionController.prompt({
          sessionId:autoSessionId,
          requestId:'probe-human-intervention',
          content:[{type:'text',text:'Pause autonomous work and process this human correction.'}],
        },new AbortController().signal);
        await autoAgent.whenIdle();
        for (let retry = 0; retry < 80 && ctx.goals.get(autoAgent)?.phase !== 'paused'; retry += 1) {
          await new Promise(resolve => setTimeout(resolve,25));
        }
        const autoStatus = await ctx.commands.execute(
          autoAgent,
          '/research status',
          [],
          new AbortController().signal,
        );
        report.checks.push({
          sessionId:autoSessionId,
          createdGoal,
          goalAfterHuman:ctx.goals.get(autoAgent),
          status:autoStatus,
          after:await ctx.sessionController.inspect(autoSessionId),
        });
      }
    } catch(error) { report.error = {message:error.message,stack:error.stack,detail:JSON.parse(JSON.stringify(error))}; }
    report.done = true;
    save();
  });
}
