/** Blank-session browser acceptance. No provider is ever allowed to run. */
import {writeFileSync,mkdirSync} from 'node:fs';
import {dirname,join} from 'node:path';
export const name='ari-blank-probe';
export const inject=['sessionController','workspaceRegistry','llm','appReady'];
export function apply(ctx){
  const report={done:false,model_attempts:0,real_model_requests:0,turn_starts:0,events:[],sessions:[]};
  const save=()=>writeFileSync(process.env.ARI_PROBE_OUTPUT,JSON.stringify(report,null,2));
  ctx.on('llm/stream',async function*(){report.model_attempts++;save();throw Error('BLANK_TEST_FORBIDS_ALL_MODELS');});
  ctx.on('session/event',(session,event)=>{
    if(event.type==='turn/start')report.turn_starts++;
    report.events.push({sessionId:session.id,type:event.type});save();
  });
  ctx.appReady.onReady(async()=>{
    try{
      const workspaces=['blank-research','unrelated-workspace'].map(name=>({name,cwd:join(dirname(process.env.ARI_PROBE_OUTPUT),name)}));
      if(process.env.ARI_SAMPLE_PROJECT)workspaces.push({name:'sample-research',cwd:process.env.ARI_SAMPLE_PROJECT});
      for(const {name,cwd} of workspaces){
        if(name!=='sample-research')mkdirSync(cwd,{recursive:true});
        const workspace=await ctx.workspaceRegistry.create(cwd),sessionId=`ari-${name}`;
        await ctx.sessionController.create({sessionId,cwd});await workspace.attachSession(sessionId);
        report.sessions.push({sessionId,cwd});
      }
      report.done=true;
    }catch(error){report.error={message:error.message,stack:error.stack};}
    save();
  });
}
