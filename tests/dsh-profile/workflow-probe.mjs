/** Deterministic native goal/parallelism probe; never calls a network provider. */
import { writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { SessionAdapter } from '../../apps/dsh/plugin/session-adapter.js';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const {LlmAdapter}=await import(pathToFileURL(join(process.env.ARI_DSH_ROOT,'node_modules/@deepseek-ai/dsh-llm/lib/index.js')));
export const name='ari-workflow-probe';
export const inject=['sessionController','sessions','llm','appReady','agents','commands','goals','workspaceRegistry','jobs'];
export function apply(ctx){
 const report={checks:[],calls:[],results:[],real_model_requests:0},steps=new Map(),tasks=[],finished=new Set(),closingNodes=new Set();
 const mainId='ari-workflow-main',crashMainId='ari-crash-main';
 let idleResolve;const done=new Promise(resolve=>{idleResolve=resolve;});
 const resultFor=(agent,tool)=>{const rows=report.results.filter(r=>r.sessionId===agent&&r.name===tool);return rows.at(-1)?.value;};
 const names=new Map();
 ctx.on('session/event',(session,event)=>{
  if(event.type==='tool/call')names.set(event.data.callId,event.data.name);
  if(event.type==='tool/result'){
   const msg=event.data.message,blocks=msg?.content??[];
   for(const block of blocks)if(block.type==='tool-result'){
    let value;try{value=JSON.parse(block.content.find(c=>c.type==='text')?.text);}catch{value=block.content;}
    const name=names.get(block.toolCallId);report.results.push({sessionId:session.id,name,value,isError:block.isError,at:Date.now()});
    if(name==='research_propose'&&value?.branch)tasks.push(value.branch);
    if(name==='research_finish'&&session.id!==mainId)finished.add(session.id);
   }
  }
 });
 class Fixture extends LlmAdapter{
  async *stream(options){
   report.calls.push({sessionId:options.sessionId,purpose:options.purpose,at:Date.now()});
   if(options.purpose){yield{type:'block-end',index:0,block:{type:'text',text:'Fixture'}};yield{type:'usage',usage:{totalTokens:3}};yield{type:'finish',reason:{kind:'stop'}};return;}
   if(options.sessionId==='ari-memory-main'){
    const context=JSON.stringify(options.messages);
    report.checks.push({name:'coordinator-first-request-memory',passed:['LESSON_29','OPEN_QUESTION_29','EARLY_DISPUTE','EARLY_CORRECTION','v29'].every(text=>context.includes(text))});
   }
   const sid=options.sessionId,index=steps.get(sid)??0;steps.set(sid,index+1);
   let call;
   if(sid===crashMainId){
    const proposal=report.results.find(r=>r.sessionId===crashMainId&&r.name==='research_propose')?.value;
    const crashTask=proposal?.branch;
    const closed=crashTask&&resultFor(crashTask.session_id,'research_finish');
    if(!proposal)call={name:'research_propose',arguments:{question:'crash-owned background job',why_now:'exercise cold recovery without replay',plan:'start one attributable background job and request close',root_reason:'isolated crash acceptance root',dispatch:true}};
    else if(!closed)call={name:'research_wait',arguments:{task_ids:[crashTask.task_id]}};
   }else if(sid===mainId){
    const proposals=report.results.filter(r=>r.sessionId===mainId&&r.name==='research_propose').map(r=>r.value);
    const root=proposals[0]?.node;
    const rootTask=tasks[0],rootPublication=rootTask&&resultFor(rootTask.session_id,'research_publish');
    const branchTasks=tasks.slice(1,3);
    const branchPublications=branchTasks.map(task=>resultFor(task.session_id,'research_publish')).filter(Boolean);
    const mergeTask=tasks[3],mergePublication=mergeTask&&resultFor(mergeTask.session_id,'research_publish');
    const specialist=resultFor(mainId,'research_memory')?.review;
    if(!root)call={name:'research_propose',arguments:{question:'SECRET_NODE_CANARY establish the shared root evidence',why_now:'native graph acceptance',plan:'write the common source result',root_reason:'independent acceptance root',dispatch:true}};
    else if(!rootPublication)call={name:'research_wait',arguments:{task_ids:[rootTask.task_id]}};
    else if(branchTasks.length<2){const branch=branchTasks.length;call={name:'research_propose',arguments:{question:`parallel derived question ${branch}`,why_now:'root material is available',plan:`write parallel result ${branch}`,predecessors:[{node_id:root.node_id,relation_type:'depends_on',rationale:'uses the common root publication item',input_refs:[rootPublication.refs[0]]}],dispatch:true}};}
    else if(branchPublications.length<2)call={name:'research_wait',arguments:{task_ids:branchTasks.map(task=>task.task_id)}};
    else if(!mergeTask)call={name:'research_propose',arguments:{question:'merge the two parallel results',why_now:'both branch materials are available',plan:'write a merged result',predecessors:branchTasks.map((task,branch)=>({node_id:task.node_id,relation_type:'depends_on',rationale:`merge branch ${branch}`,input_refs:[branchPublications[branch].refs[0]]})),dispatch:true}};
    else if(!mergePublication)call={name:'research_wait',arguments:{task_ids:[mergeTask.task_id]}};
    else if(!specialist)call={name:'research_memory',arguments:{action:'consolidate',evidence_refs:[branchPublications[0].refs[0]]}};
    else {
     const a=ctx.agents.get(sid),state=await command(a,'/research status');
     const pending=state.workflow.tasks.filter(task=>tasks.some(t=>t.task_id===task.task_id)&&!['finished','failed','cancelled'].includes(task.state));
     if(pending.length)call={name:'research_wait',arguments:{task_ids:pending.map(t=>t.task_id)}};
     else {const g=ctx.goals.get(a);if(g?.phase==='active')ctx.goals.complete(a,{id:g.id,revision:g.revision});}
    }
   }else if(sid.startsWith('discussion-')){
    if(index===0)call={name:'research_note',arguments:{body:'Must be rejected'}};
   }else if(ctx.agents.get(sid)?.session.header.parentSession){
    const context=JSON.stringify(options.messages);
    if(context.includes('blind reviewer')){
     report.blind??={starts:{},ends:{},contexts:[],tools:[]};
     const tools=(options.tools??[]).map(t=>t.name??t.function?.name);
     report.blind.contexts.push(!['SECRET_NODE_CANARY','SECRET_PUBLICATION_CANARY','result.txt','pub/P-'].some(marker=>context.includes(marker)));
     report.blind.tools.push(tools);
     if(index===0){report.blind.starts[sid]=Date.now();call={name:'research_read_input',arguments:{input_id:'input-1',offset:0,limit:8192}};}
     else {await new Promise(r=>setTimeout(r,300));report.blind.ends[sid]=Date.now();}
    }
   }else if(sid.startsWith('research-')){
    const objective=ctx.goals.get(ctx.agents.get(sid))?.objective??'';
    const isCrash=objective.includes('crash-owned background job');
    const isMerge=tasks[3]?.session_id===sid;
    const isRoot=tasks[0]?.session_id===sid;
    const phase=(isMerge&&index>0?index-1:index)-(isRoot&&index>3?1:0);
    if(closingNodes.has(sid)){closingNodes.delete(sid);call={name:'research_close_node',arguments:{node_id:(await command(ctx.agents.get(sid),'/research status')).workflow.session.node_id}};}
    else if(isCrash&&index===0)call={name:'bash',arguments:{description:'Start attributable crash recovery job',command:'printf "%s\\n" "$$" > crash-job.pid; sleep 120',run_in_background:true}};
    else if(isCrash&&index===1)call={name:'research_finish',arguments:{details:{reason:'crash fixture close intent'}}};
    else if(index===0)call={name:'bash',arguments:{description:'Independent native workspace overlap',command:'pwd; sleep 1; printf "native result\\n" > result.txt'}};
    else if(isMerge&&index===1)call={name:'research_consume',arguments:{source_ref:resultFor(tasks[0].session_id,'research_publish').refs[0],use:'late root context used during merge verification',relation_type:'context'}};
    else if(phase===1)call={name:'research_publish',arguments:{status:'partial',summary:'SECRET_PUBLICATION_CANARY early branch evidence',gaps:['still exploring'],items:[{item_id:'result',source_path:'result.txt'}]}};
    else if(phase===2)call={name:'research_snapshot',arguments:{paths:['result.txt']}};
    // Finish is a work-segment intent; do not close a node in the same active turn.
    else if(isRoot&&index===3)call={name:'research_delegate_batch',arguments:{tasks:[1,2].map(n=>({label:`blind-${n}`,question:'Review the assigned sample',purpose:'independent assessment',context_mode:'blind',inputs:[resultFor(sid,'research_publish').refs[0]],deliverable:'Final review',completion_criteria:'Read the sample and report',report_requirements:'State uncertainty'}))}};
    else if(phase===3)call={name:'research_finish',arguments:{details:{reason:'fixture finished'}}};
   }
   if(call){yield{type:'block-end',index:0,block:{type:'tool-call',id:`call-${sid}-${index}`,name:call.name,arguments:JSON.stringify(call.arguments)}};}
   else yield{type:'block-end',index:0,block:{type:'text',text:report.blind?.ends[sid]?'Final blind deliverable. '.repeat(5000):'Fixture response'}};
   yield{type:'usage',usage:{inputTokens:10,outputTokens:5,totalTokens:15}};
   yield{type:'finish',reason:{kind:call?'tool-calls':'stop'}};
  }
 }
 ctx.llm.registerAdapter(['ari-fixture'],new Fixture());
 ctx.on('llm/stream',async function*(options,next){if(options.provider!=='ari-fixture')throw Error('NO_PAID_REQUESTS');yield*next();});
 const command=async(agent,text)=>{const r=await ctx.commands.execute(agent,text,[],new AbortController().signal);if(r.result.kind==='error')throw Error(r.result.text);try{return JSON.parse(r.result.text);}catch{return r.result.text;}};
 const wait=async(fn,label)=>{const until=Date.now()+15000;while(!fn()){if(Date.now()>until)throw Error(`Timeout: ${label}`);await new Promise(r=>setTimeout(r,25));}};
 ctx.appReady.onReady(async()=>{
  try{
   const existence = new SessionAdapter(ctx);
   report.checks.push({name:'native-explicit-absence',passed:(await existence.exists('ari-061-never-created'))===false});
   if(process.env.ARI_PROBE_PHASE==='restart'){
   const a=(await ctx.sessionController.resolveAgent(mainId)).agent;
    const before=report.calls.length;const state=await command(a,'/research status');
    await new Promise(r=>setTimeout(r,250));
    report.checks.push({name:'cold-no-model',passed:report.calls.length===before,goal:ctx.goals.get(a),state:state.runtime.state});
    const crashMain=(await ctx.sessionController.resolveAgent(crashMainId)).agent;
    const crashState=await command(crashMain,'/research status');
    const crashRow=crashState.workflow.sessions.find(row=>row.role==='node_core');
    const crashChild=(await ctx.sessionController.resolveAgent(crashRow.session_id)).agent;
    const recovered=await command(crashChild,'/research status');
    let processState='unknown';
    const pidPath=join(crashChild.session.header.cwd,'crash-job.pid');
    let crashPid;
    try{crashPid=Number(readFileSync(pidPath,'utf8').trim());process.kill(crashPid,0);processState='alive';}catch(error){if(error?.code==='ESRCH')processState='exited';}
    let verifyBlocked=false,resumeBlocked=false;
    try{await command(crashMain,`/research verify-close --session ${crashChild.id}`);}catch{verifyBlocked=true;}
    try{await command(crashMain,`/research resume --session ${crashChild.id}`);}catch{resumeBlocked=true;}
    await new Promise(r=>setTimeout(r,250));
    const savedJobs=recovered.workflow.intents.find(intent=>intent.intent_id===`jobs:${crashChild.id}`)?.details?.jobs??[];
    const liveJobs=ctx.jobs.list(crashChild);
    report.checks.push({name:'crash-job-classified-no-replay',passed:['alive','exited'].includes(processState)&&savedJobs.some(job=>['running','stopping'].includes(job.status))&&liveJobs.length===0&&recovered.workflow.session.close_state==='unverified'&&!!recovered.attempt&&verifyBlocked&&resumeBlocked&&report.calls.length===before,processState,savedJobs,liveJobs,closeState:recovered.workflow.session.close_state,verifyBlocked,resumeBlocked});
    if(Number.isSafeInteger(crashPid)&&crashPid>1){try{process.kill(crashPid,'SIGTERM');}catch{}}
   }else{
    const cwd=join(process.env.DSH_HOME,'workflow-project');mkdirSync(cwd,{recursive:true});
    const ws=await ctx.workspaceRegistry.create(cwd);await ctx.sessionController.create({sessionId:mainId,cwd});await ws.attachSession(mainId);
    await ctx.sessionController.selectModel({sessionId:mainId,provider:'ari-fixture',model:'deterministic'});
    const a=ctx.agents.get(mainId);await command(a,'/research init Native workflow acceptance');
    await command(a,'/research auto');await wait(()=>finished.size===4,'root, parallel branches and merge finish');
    await wait(()=>ctx.goals.get(a)?.phase==='complete','parent resumes and completes');await a.whenIdle();
    for(const sid of finished){const child=ctx.agents.get(sid);await child.whenIdle();closingNodes.add(sid);await ctx.sessionController.prompt({sessionId:sid,requestId:`close-${sid}`,content:[{type:'text',text:'Close the completed research node'}]},new AbortController().signal);await child.whenIdle();}
    let state=await command(a,'/research status');
    report.checks.push({name:'typed-root-parallel-merge',passed:state.counts.tasks===4&&state.counts.dependencies===4&&state.counts.consumptions===1,state});
    const batch=resultFor(tasks[0].session_id,'research_delegate_batch');
    const blind=report.blind,ids=Object.keys(blind?.starts??{});
    report.checks.push({name:'blind-real-first-context-and-tool-scope',passed:ids.length===2&&blind.contexts.every(Boolean)&&blind.tools.every(names=>names.length===1&&names[0]==='research_read_input'),blind});
    report.checks.push({name:'blind-native-overlap-and-large-artifacts',passed:ids.length===2&&Math.max(...Object.values(blind.starts))<Math.min(...Object.values(blind.ends))&&batch?.tasks?.length===2&&batch.tasks.every(t=>t.state==='completed'&&t.exit_verified&&t.result?.artifact)&&JSON.stringify(batch).length<20000,batch});
    const specialist=resultFor(mainId,'research_memory')?.review;
    report.checks.push({name:'native-specialist',passed:specialist?.state==='completed'&&specialist?.exit_verified===true&&state.counts.specialists===3,specialist});
    const execution=tasks.slice(1,3).map(t=>ctx.agents.get(t.session_id));
    const toolBounds=execution.map(child=>{const es=child.session.events;const c=es.find(e=>e.type==='tool/call'&&e.data.name==='bash');const r=es.find(e=>e.type==='tool/result'&&e.data.message.source.callId===c.data.callId);return{cwd:child.session.header.cwd,start:c.time,end:r.time};});
    report.checks.push({name:'independent-overlap',passed:toolBounds[0].cwd!==toolBounds[1].cwd&&Math.max(...toolBounds.map(b=>b.start))<Math.min(...toolBounds.map(b=>b.end)),toolBounds});
    const sourceSnapshot=[...finished].map(sid=>resultFor(sid,'research_snapshot')).find(Boolean);
    const beforeRestoreCalls=report.calls.length;
    const preview=await command(a,`/research restore ${sourceSnapshot.snapshot_id}`);
    const handoff=await command(a,`/research restore ${sourceSnapshot.snapshot_id} --preview-id ${preview.preview_id}`);
    const h=ctx.agents.get(handoff.sessionId);
    const hs=await command(h,'/research status');
    report.checks.push({name:'closed-node-handoff',passed:preview.eligible&&hs.workflow.session.role==='handoff'&&!hs.attempt&&!ctx.goals.get(h)&&readFileSync(join(h.session.header.cwd,'result.txt'),'utf8').includes('native result')&&!report.calls.slice(beforeRestoreCalls).some(c=>c.sessionId===h.id),preview,handoff});
    const beforeCalls=report.calls.length;const discussion=await command(a,'/research discuss X-001');
    report.checks.push({name:'discussion-no-model',passed:!report.calls.slice(beforeCalls).some(c=>c.sessionId===discussion.sessionId),discussion});
    const d=ctx.agents.get(discussion.sessionId);
    for(let turn=0;turn<3;turn++){await ctx.sessionController.prompt({sessionId:d.id,requestId:`discussion-${turn}`,content:[{type:'text',text:'Discuss the frozen result'}]},new AbortController().signal);await d.whenIdle();}
    const denied=report.results.find(r=>r.sessionId===d.id&&r.name==='research_note');
    report.checks.push({name:'discussion-write-denied',passed:denied?.isError===true});
    const repeat=await command(a,'/research discuss X-001');report.checks.push({name:'discussion-reused',passed:repeat.sessionId===d.id});
    state=await command(a,'/research status');report.checks.push({name:'discussion-no-attempt',passed:state.counts.attempts===5&&state.usage.discussion>0,usage:state.usage});
    for(const row of state.workflow.sessions){const live=ctx.agents.get(row.session_id);if(live)await ctx.sessions.flush(live.session);}
    const noticeDeadline=Date.now()+3000;
    while(state.counts.notifications<8&&Date.now()<noticeDeadline){await new Promise(r=>setTimeout(r,25));state=await command(a,'/research status');}
    report.checks.push({name:'native-notifications',passed:state.counts.notifications===8,notificationCount:state.counts.notifications});
    const memoryCwd=join(process.env.DSH_HOME,'coordinator-project');mkdirSync(memoryCwd,{recursive:true});
    await ctx.sessionController.create({sessionId:'ari-memory-main',cwd:memoryCwd});
    await ctx.sessionController.selectModel({sessionId:'ari-memory-main',provider:'ari-fixture',model:'deterministic'});
    const coordinator=ctx.agents.get('ari-memory-main');await command(coordinator,'/research init Coordinator memory acceptance');
    execFileSync('python3',[fileURLToPath(new URL('./seed-coordinator.py',import.meta.url)),memoryCwd],{env:{...process.env,PYTHONPATH:process.env.ARI_PLUGIN_PYTHON}});
    await ctx.sessionController.prompt({sessionId:coordinator.id,requestId:'coordinator-first-turn',content:[{type:'text',text:'Review the current research agenda'}]},new AbortController().signal);
    await coordinator.whenIdle();await ctx.sessions.flush(coordinator.session);
    if(!report.checks.some(c=>c.name==='coordinator-first-request-memory'))throw Error('Coordinator provider was not called');
    const acceptCwd=join(process.env.DSH_HOME,'accept-067');mkdirSync(acceptCwd,{recursive:true});
    const verdict=JSON.parse(execFileSync('python3',[fileURLToPath(new URL('./verify-067.py',import.meta.url)),acceptCwd],{env:{...process.env,PYTHONPATH:process.env.ARI_PLUGIN_PYTHON}}).toString());
    report.checks.push({name:'epistemic-closed-loop-0.6.7',passed:verdict.passed===true,findings:verdict.findings});
    report.checks.push({name:'a2-refs-provenance-hints-0.6.8',...verdict.a2,passed:verdict.a2?.passed===true});
    report.checks.push({name:'b-field-check-0.6.9',...verdict.b,passed:verdict.b?.passed===true});
    const crashCwd=join(process.env.DSH_HOME,'crash-project');mkdirSync(crashCwd,{recursive:true});
    const crashWs=await ctx.workspaceRegistry.create(crashCwd);await ctx.sessionController.create({sessionId:crashMainId,cwd:crashCwd});await crashWs.attachSession(crashMainId);
    await ctx.sessionController.selectModel({sessionId:crashMainId,provider:'ari-fixture',model:'deterministic'});
    const crashMain=ctx.agents.get(crashMainId);await command(crashMain,'/research init Crash recovery acceptance');await command(crashMain,'/research auto');
    await wait(()=>report.results.some(r=>r.sessionId===crashMainId&&r.name==='research_propose'),'crash node proposal');
    const crashTask=report.results.find(r=>r.sessionId===crashMainId&&r.name==='research_propose').value.branch;
    await wait(()=>resultFor(crashTask.session_id,'research_finish'),'crash node close intent');
    const crashChild=ctx.agents.get(crashTask.session_id);await crashChild.whenIdle();
    let crashState,crashDeadline=Date.now()+5000;
    do{crashState=await command(crashChild,'/research status');if(crashState.workflow.session.close_state==='unverified')break;await new Promise(r=>setTimeout(r,25));}while(Date.now()<crashDeadline);
    const crashJobs=ctx.jobs.list(crashChild);
    await ctx.sessions.flush(crashChild.session);await ctx.sessions.flush(crashMain.session);
    report.checks.push({name:'crash-job-ready',passed:crashState.workflow.session.close_state==='unverified'&&!!crashState.attempt&&crashJobs.some(job=>job.status==='running'),sessionId:crashChild.id,jobs:crashJobs});
    report.crash_ready=true;
   }
   if(report.checks.some(c=>!c.passed))throw Error('One or more acceptance checks failed');
  }catch(error){report.error={message:error.message,stack:error.stack};}
  report.done=true;writeFileSync(process.env.ARI_PROBE_OUTPUT,JSON.stringify(report,null,2));
 });
}
