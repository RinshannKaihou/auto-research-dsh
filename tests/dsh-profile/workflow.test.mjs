import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { StorageClient } from '../../apps/dsh/plugin/ipc.js';
import { ResearchDomain } from '../../apps/dsh/plugin/domain.js';
import { eventFacts, registerResearchEvents } from '../../apps/dsh/plugin/events.js';
import { registerNativeSubagentGuard } from '../../apps/dsh/plugin/index.js';

async function fixture() {
  const dir=mkdtempSync(join(tmpdir(),'ari-workflow-'));
  const storage=new StorageClient({pythonModulePath:resolve('src'),registryPath:join(dir,'registry.sqlite3')});
  const agents=new Map(), goals=new Map(), jobs=[], events=new Map(), cancelled=[], models=[];
  const specialistRuns=[];
  let next=0;
  const ctx={agents:{get:id=>agents.get(id)},jobs:{list:()=>jobs,kill(id){jobs.find(j=>j.id===id).status='stopping';},onJobsChanged:()=>()=>{}},
    on(name,fn){events.set(name,fn);return()=>events.delete(name);},systemPrompt:{context:()=>()=>{}},
    goals:{get:a=>goals.get(a.id),create(a,r){if(goals.get(a.id)?.phase&&goals.get(a.id).phase!=='complete')throw Error('existing');const g={id:`g-${++next}`,revision:1,phase:'active',activation:'armed',...r};goals.set(a.id,g);return g;},
      resume(a){const g={...goals.get(a.id),revision:goals.get(a.id).revision+1,phase:'active',activation:'armed'};goals.set(a.id,g);return g;},
      pause(a){const g={...goals.get(a.id),revision:goals.get(a.id).revision+1,phase:'paused',activation:'disarmed'};goals.set(a.id,g);return g;},clear(a){goals.delete(a.id);}}};
  const add=(id,cwd)=>{const a={id,status:'idle',options:{provider:'fixture',model:'test'},session:{header:{cwd,agentPreset:'standard'},events:[]},inbox:{nextStep:[],nextTurn:[]},send(m,target,wake){this.inbox.nextStep.push(m);this.session.events.push({type:'inbox/splice',data:{inserted:[m]}});assert.equal(wake,false);}};agents.set(id,a);return a;};
  ctx.subagents={async start(provider,request){
    const child=add(`specialist-${specialistRuns.length+1}`,request.parent.session.header.cwd);
    child.session.header.parentSession=request.parent.id;
    const record={provider,request,child,disposed:false};specialistRuns.push(record);
    return{id:child.id,localAgent:child,result:Promise.resolve({stopReason:'completed',output:[{type:'text',text:'reviewed'}]}),async dispose(){record.disposed=true;}};
  }};
  const adapter={async exists(id){return agents.has(id);},async resolve(id){if(!agents.has(id))throw Error('not found');return agents.get(id);},async create({sessionId,cwd}){add(sessionId,cwd);return{sessionId};},async selectModel(...args){models.push(args);},async cancel(id){cancelled.push(id);}};
  const domain=new ResearchDomain(ctx,storage,adapter);
  const main=add('main',join(dir,'project'));
  await domain.open(main,{goal:'Research fixture'},'open');
  return {dir,storage,domain,main,agents,goals,jobs,ctx,events,cancelled,models,specialistRuns,close(){storage.close();rmSync(dir,{recursive:true,force:true});}};
}
const propose=(f,id)=>f.domain.request(f.main,'propose',{question:`question ${id}`,why_now:'independent',plan:`plan ${id}`},`node-${id}`);
const page=async(f,collection,agent=f.main)=>{
 const rows=[];let cursor={};
 do{const value=await f.domain.page(agent,collection,cursor,200);rows.push(...value.items);cursor=value.cursor??null;}while(cursor);
 return rows;
};

test('0.6.3 managed executors cannot bypass durable specialist delegation',()=>{
  let guard;
  const disposed=[];
  const ctx={tools:{guard(fn){guard=fn;return()=>disposed.push(true);}}};
  const domain={managedRole:agent=>agent.id==='main'?'main':agent.id==='core'?'node_core':null};
  const dispose=registerNativeSubagentGuard(ctx,domain);
  assert.match(guard({name:'subagent',agent:{id:'main'}}),/research_delegate/);
  assert.match(guard({name:'subagent',agent:{id:'core'}}),/research_delegate/);
  assert.equal(guard({name:'subagent',agent:{id:'discussion'}}),undefined);
  assert.equal(guard({name:'research_delegate',agent:{id:'main'}}),undefined);
  dispose();assert.equal(disposed.length,1);
});

test('0.6.3 a node keeps one native core session and cwd across work segments',async()=>{
  const f=await fixture();try{
    const node=await propose(f,'persistent-core');
    await f.domain.auto(f.main,'auto');
    const first=await f.domain.dispatch(f.main,node.node_id,'dispatch-core-1');
    const child=f.agents.get(first.session_id);
    const initial=(await f.domain.state(child)).attempt;
    const cwd=child.session.header.cwd;
    assert.equal((await f.domain.state(child)).workflow.session.role,'node_core');
    await f.domain.request(child,'finish',{state:'finished'},'finish-core-1');
    await f.domain.progress(child,'finished',{},'finish-core-1');
    const second=await f.domain.dispatch(f.main,node.node_id,'dispatch-core-2');
    const resumed=await f.domain.state(child);
    assert.equal(second.state,'running',second.error);
    assert.equal(second.task_id,first.task_id);
    assert.equal(second.session_id,first.session_id);
    assert.equal(child.session.header.cwd,cwd);
    assert.notEqual(resumed.attempt.attempt_id,initial.attempt_id);
    assert.equal(resumed.attempt.node_id,node.node_id);
    assert.equal((await page(f,'tasks')).length,1);
  }finally{f.close();}
});

test('0.6.3 finish is a verified close intent and never pauses the main coordinator',async()=>{
  const f=await fixture();try{
    const node=await propose(f,'verified-close');
    await f.domain.auto(f.main,'auto');
    await assert.rejects(f.domain.requestClose(f.main,{},'main-finish'),/coordinator/);
    const task=await f.domain.dispatch(f.main,node.node_id,'dispatch-close');
    const child=f.agents.get(task.session_id);
    f.jobs.push({id:'owned-job',ownerSession:child.id,status:'running'});
    const receipt=await f.domain.requestClose(child,{reason:'stage complete'},'close-request');
    assert.equal(receipt.close_state,'requested');
    assert.equal(f.goals.get(child.id).phase,'paused');
    assert.equal(f.goals.get('main').phase,'active');
    await f.domain.idle(child);
    let state=await f.domain.state(child);
    assert.equal(state.workflow.session.close_state,'unverified');
    assert.ok(state.attempt);
    f.jobs[0].status='completed';
    const closed=await f.domain.verifyClose(f.main,child.id,'verify-close');
    assert.equal(closed.close_state,'closed');
    state=await f.domain.state(child);
    assert.equal(state.attempt,null);
    assert.equal((await f.domain.lookup(f.main,{ref:task.task_id})).value.state,'finished');
    assert.equal(f.goals.get('main').phase,'active');
  }finally{f.close();}
});

test('0.6.3 nested goal facts and usage registration gaps stay observable',async()=>{
  assert.deepEqual(eventFacts({type:'goal/change',data:{reason:'resume',goal:{id:'g',revision:4,phase:'active',activation:'armed'}}}),{
    reason:'resume',goal:{id:'g',revision:4,phase:'active',activation:'armed'},
  });
  const f=await fixture();let dispose;try{
    dispose=registerResearchEvents(f.ctx,f.domain);
    const original=f.domain.request.bind(f.domain);let failed=false;
    f.domain.request=(agent,method,fields,id)=>{
      if(method==='usage_begin'&&!failed){failed=true;return Promise.reject(Error('fixture registration failed'));}
      return original(agent,method,fields,id);
    };
    const stream=f.events.get('llm/stream')({sessionId:f.main.id,purpose:'conversation'},async function*(){yield{type:'usage',usage:{totalTokens:9}};});
    for await(const _ of stream){}
    const gaps=await page(f,'usage_gaps');
    assert.equal(gaps.length,1);
    assert.match(gaps[0].reason,/registration failed/);
    assert.equal((await f.domain.query(f.main)).usage.coverage_incomplete,1);
  }finally{dispose?.();f.close();}
});

test('0.6.1 creation failure can be explicitly retried with stable identity, never on an IO error',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');
  const node=await propose(f,'creation');
  const create=f.domain.adapter.create;
  f.domain.adapter.create=async()=>{throw Error('fixture creation failed');};
  const task=await f.domain.dispatch(f.main,node.node_id,'dispatch');
  assert.equal(task.state,'unverified');
  await assert.rejects(f.domain.resumeSession(f.main,task.session_id,'bad',{retry:true}),/受管理/);
  f.domain.adapter.exists=async()=>{throw Error('fixture IO unavailable');};
  await assert.rejects(f.domain.retryTask(f.main,task.task_id,'io'),/IO unavailable/);
  f.domain.adapter.exists=async id=>f.agents.has(id);
  f.domain.adapter.create=create;
  const result=await f.domain.retryTask(f.main,task.task_id,'retry-task');
  assert.equal(result.state,'running');
  assert.deepEqual(await f.domain.retryTask(f.main,task.task_id,'retry-task'),result);
  assert.equal((await page(f,'tasks')).length,1);
  assert.ok(f.agents.get(task.session_id));
 }finally{f.close();}
});

test('0.6.6 material preparation failure is failed before native creation and releases capacity',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'prepare-failure');
  const original=f.domain.request.bind(f.domain);
  f.domain.request=(agent,method,fields,id)=>method==='prepare_branch'?Promise.reject(Error('frozen object missing')):original(agent,method,fields,id);
  const task=await f.domain.dispatch(f.main,node.node_id,'dispatch-prepare-failure');
  assert.equal(task.state,'failed');assert.match(task.error,/frozen object missing/);
  assert.equal(f.agents.has(task.session_id),false);
  assert.equal((await page(f,'tasks')).filter(t=>['starting','running','waiting','stopping','unverified'].includes(t.state)).length,0);
 }finally{f.close();}
});

test('0.6.6 verify-task settles absent legacy creation without restarting a complete project',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'verify-task');
  const task=await f.domain.workflow(f.main,'task',{node_id:node.node_id},'legacy-task');
  await f.domain.workflow(f.main,'task_state',{task_id:task.task_id,state:'unverified',error:'historical create uncertainty'},'legacy-unverified');
  await f.domain.workflow(f.main,'run',{state:'complete'},'complete-project');
  const verified=await f.domain.verifyTask(f.main,task.task_id,'verify-legacy');
  assert.equal(verified.state,'failed');assert.equal(verified.error,'historical create uncertainty');
  const repeated=await f.domain.verifyTask(f.main,task.task_id,'verify-legacy-again');
  assert.equal(repeated.state,'failed');
  assert.equal(f.agents.has(task.session_id),false);assert.equal(f.goals.has(task.session_id),false);
 }finally{f.close();}
});

test('0.6.6 approval status is durable, visible, and does not wake the coordinator',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'approval');
  const task=await f.domain.dispatch(f.main,node.node_id,'dispatch-approval'),child=f.agents.get(task.session_id);
  const asked={type:'approval/asked',data:{id:'approval-1',callId:'call-1',reason:'访问只读数据目录'}};
  child.session.events.push(asked);await f.domain.recordApproval(child,asked);
  let state=await f.domain.query(f.main);
  assert.equal(state.runtime.pending_approvals,1);assert.equal(state.runtime.waiting_approval_count,1);
  assert.equal(state.runtime.sessions.find(s=>s.session_id===child.id).pending_approvals,1);
  assert.equal(f.main.inbox.nextStep.length,0);
  const notices=await page(f,'notifications');
  assert.equal(notices.filter(n=>n.kind==='approval_requested').length,1);assert.equal(notices.find(n=>n.kind==='approval_requested').state,'claimed');
  await f.domain.recordApproval(child,asked);assert.equal((await page(f,'notifications')).filter(n=>n.kind==='approval_requested').length,1);
  const decided={type:'approval/decided',data:{id:'approval-1',outcome:'allowed'}};
  child.session.events.push(decided);await f.domain.recordApproval(child,decided);
  state=await f.domain.query(f.main);assert.equal(state.runtime.pending_approvals,0);assert.equal(f.main.inbox.nextStep.length,0);
 }finally{f.close();}
});

test('0.6.1 stop retains failed creation until native absence is explicitly verified',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'stop-create');
  f.domain.adapter.create=async()=>{throw Error('create unavailable');};
  const task=await f.domain.dispatch(f.main,node.node_id,'dispatch');
  assert.equal((await f.domain.stopProject(f.main,'stop')).state,'unverified');
  f.domain.adapter.exists=async()=>{throw Error('IO unavailable');};
  assert.equal((await f.domain.verifyStop(f.main,'verify-io')).state,'unverified');
  f.domain.adapter.exists=async id=>f.agents.has(id);
  assert.equal((await f.domain.verifyStop(f.main,'verify')).state,'stopped');
  assert.equal((await page(f,'tasks'))[0].state,'cancelled');
  assert.equal(f.agents.has(task.session_id),false);
 }finally{f.close();}
});

test('0.6.1 retry reserves the failed node and binds the new attempt without double execution',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'retry');
  const task=await f.domain.dispatch(f.main,node.node_id,'dispatch');
  const child=f.agents.get(task.session_id), prior=(await f.domain.state(child)).attempt;
  await f.domain.pauseSession(child,'fault');
  await f.domain.progress(child,'failed',{summary:'provider failure'},'fault');
  assert.equal((await page(f,'tasks'))[0].state,'failed');
  const result=await f.domain.resumeSession(f.main,child.id,'retry',{retry:true});
  assert.deepEqual(await f.domain.resumeSession(f.main,child.id,'retry',{retry:true}),result);
  const current=await f.domain.state(child);
  assert.equal(current.attempt.details.retry_of,prior.attempt_id);
  assert.equal(current.workflow.tasks[0].state,'running');
  const again=await f.domain.dispatch(f.main,node.node_id,'dispatch-again');
  assert.equal(again.task_id,task.task_id);
  assert.equal((await page(f,'tasks')).length,1);
  await f.domain.request(child,'finish',{state:'finished'},'done');
  await f.domain.progress(child,'finished',{},'done');
  assert.equal((await f.domain.dispatch(f.main,node.node_id,'dispatch')).state,'finished');
 }finally{f.close();}
});

test('0.6.1 unverified specialists retain slots until native tools and jobs are verified',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');const node=await propose(f,'specialist');
  const start=f.ctx.subagents.start;
  let failDispose=true;
  f.ctx.subagents.start=async(...args)=>{
   const run=await start(...args),dispose=run.dispose;
   run.dispose=async()=>{if(failDispose)throw Error('dispose failed');return dispose();};return run;
  };
  const dispatched=await f.domain.dispatch(f.main,node.node_id,'dispatch-expert');
  const core=f.agents.get(dispatched.session_id);
  const task=await f.domain.delegate(core,{node_id:node.node_id,prompt:'check'}, {signal:new AbortController().signal}, 'expert');
  assert.equal(task.state,'unverified');
  const child=f.agents.get(task.child_session_id);
  child.session.events.push({type:'tool/call',data:{callId:'pending'}});
  assert.equal((await f.domain.verifySpecialist(f.main,task.task_id,'verify1')).state,'unverified');
  child.session.events.push({type:'tool/result',data:{callId:'pending'}});
  failDispose=false;
  const verified=await f.domain.verifySpecialist(f.main,task.task_id,'verify2');
  assert.equal(verified.state,'incomplete');assert.equal(verified.exit_verified,true);
  assert.equal(f.goals.get('main').phase,'active');
 }finally{f.close();}
});

test('repeated start and discussion do not duplicate attempts or sessions; discussions are read-only with separate usage',async()=>{
 const f=await fixture();try{
  const node=await propose(f,'one');
  await Promise.all([f.domain.auto(f.main,'start'),f.domain.auto(f.main,'start2')]);
  assert.equal((await f.domain.query(f.main)).counts.attempts,1);
  const a=await f.domain.discuss(f.main,node.node_id,'d1'),b=await f.domain.discuss(f.main,node.node_id,'d2');
  assert.equal(a.sessionId,b.sessionId);assert.equal(f.agents.size,2);
  const d=f.agents.get(a.sessionId);d.status='running';
  await assert.rejects(f.domain.request(d,'note',{body:'illegal',model_call:true},'illegal'),/Discussion/);
  await assert.rejects(f.domain.dispatch(d,node.node_id,'illegal-dispatch'),/Only research agents/);
  await f.domain.request(d,'usage_begin',{source_key:'d-usage'},'d-begin');
  await f.domain.request(d,'usage_finish',{source_key:'d-usage',amount:17,completeness:'actual'},'d-finish');
  await f.domain.projectGoals(d,'pause','pause');
  assert.equal(f.goals.get('main').phase,'paused');assert.equal(f.goals.has(d.id),false);
  await f.domain.stopProject(d,'stop');
  assert.equal(f.cancelled.includes(d.id),false);
  const state=await f.domain.query(d);assert.equal(state.runtime.state,'stopped');assert.equal(state.usage.discussion,17);assert.equal(state.counts.attempts,1);
  assert.equal((await page(f,'usage',d))[0].node_id,null);
  await f.domain.auto(d,'restart');assert.equal((await f.domain.query(d)).counts.attempts,2);
 }finally{f.close();}
});

test('user goals remain untouched; pauses retain their source',async()=>{
 const f=await fixture();try{
  f.goals.set('main',{id:'user',phase:'complete'});
  await assert.rejects(f.domain.auto(f.main,'start'),/non-plugin/);
  assert.equal(f.goals.get('main').id,'user');f.goals.delete('main');
  await f.domain.auto(f.main,'start2');
  await f.domain.pauseSession(f.main,'human');
  await f.domain.projectGoals(f.main,'pause','pause');
  await f.domain.projectGoals(f.main,'resume','resume');
  assert.equal(f.goals.get('main').phase,'paused');
  assert.equal((await f.domain.state(f.main)).workflow.session.pause_reason,'human');
 }finally{f.close();}
});

test('two queued exploration tasks overlap after the main agent waits; publication notifications are deduplicated',async()=>{
 const f=await fixture();try{
  const n1=await propose(f,'one'),n2=await propose(f,'two');
  await f.domain.auto(f.main,'auto');f.main.status='running';
  const t1=await f.domain.dispatch(f.main,n1.node_id,'dispatch1');
  const t2=await f.domain.dispatch(f.main,n2.node_id,'dispatch2');
  assert.equal(t1.state,'running');assert.equal(t2.state,'queued');
  assert.equal((await f.domain.state(f.main)).counts.attempts,2);
  assert.equal((await f.domain.dispatch(f.main,n1.node_id,'dispatch1')).session_id,t1.session_id);
  await f.domain.wait(f.main,[t1.task_id,t2.task_id],'wait');f.main.status='idle';
  await f.domain.idle(f.main);
  const state=await f.domain.state(f.main);assert.equal(state.workflow.tasks.filter(t=>t.state==='running').length,2);
  const c1=f.agents.get(t1.session_id),c2=f.agents.get(t2.session_id);
  assert.notEqual(c1.session.header.cwd,c2.session.header.cwd);
  for(const c of [c1,c2])writeFileSync(join(c.session.header.cwd,'result.txt'),c.id);
  assert.match(f.goals.get(c1.id).objective,/question one/);assert.match(f.goals.get(c2.id).objective,/question two/);
  const pub=await f.domain.request(c1,'publish',{status:'partial',summary:'early result',gaps:['needs validation'],items:[{item_id:'r',source_path:'result.txt'}]},'pub');
  await f.domain.progress(c1,'published',pub,'pub');await f.domain.progress(c1,'published',pub,'pub');
  assert.equal(f.main.inbox.nextStep.length,1);assert.equal(f.goals.get('main').phase,'paused');
  await f.domain.request(c1,'finish',{state:'finished'},'finish');
  await f.domain.progress(c1,'finished',{},'finish');
  assert.equal(f.goals.get('main').phase,'active');
  assert.equal(f.main.inbox.nextStep.length,2);
  await f.domain.pauseSession(f.main,'native_stop');
  await f.domain.progress(c2,'published',{publication_id:'later'},'later');
  assert.equal(f.goals.get('main').phase,'paused');
  assert.equal((await f.domain.state(f.main)).workflow.session.pause_reason,'native_stop');
 }finally{f.close();}
});

test('stop waits for turns and owned jobs; never cancels unowned jobs or discussion',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');f.main.status='running';
  f.jobs.push({id:'ours',ownerSession:'main',status:'running'},{id:'unowned',status:'running'});
  await f.domain.stopProject(f.main,'stop');
  let state=await f.domain.query(f.main);assert.equal(state.runtime.state,'unverified');assert.equal(state.attempt.ended_at,null);
  assert.equal(f.jobs[0].status,'stopping');assert.equal(f.jobs[1].status,'running');
  await assert.rejects(f.domain.auto(f.main,'bad'),/停止待核实/);
  f.jobs[0].status='killed';f.main.status='idle';await f.domain.idle(f.main);
  state=await f.domain.query(f.main);assert.equal(state.runtime.state,'stopped');assert.equal(state.attempt,null);
 }finally{f.close();}
});

test('preview is read-only and handoff retains closed-node provenance without an attempt',async()=>{
 const f=await fixture();try{
  const node=await propose(f,'one');
  await f.domain.request(f.main,'focus',{node_id:node.node_id,mode:'manual'},'focus');
  writeFileSync(join(f.main.session.header.cwd,'draft.txt'),'frozen draft');
  const snapshot=await f.domain.request(f.main,'snapshot',{paths:['draft.txt']},'snapshot');
  await f.domain.request(f.main,'finish',{state:'finished'},'finish');
  await f.domain.request(f.main,'close_node',{node_id:node.node_id},'close');
  const before=await f.domain.state(f.main);
  const preview=await f.domain.restorePreview(f.main,snapshot.snapshot_id);
  assert.equal(f.agents.size,1);assert.equal((await f.domain.state(f.main)).counts.attempts,before.counts.attempts);
  const created=await f.domain.restore(f.main,snapshot.snapshot_id,'restore',preview.preview_id);
  const again=await f.domain.restore(f.main,snapshot.snapshot_id,'restore2',preview.preview_id);
  assert.equal(again.sessionId,created.sessionId);assert.equal(f.agents.size,2);
  assert.equal(readFileSync(join(created.workspace,'draft.txt'),'utf8'),'frozen draft');
  const handoffAgent=f.agents.get(created.sessionId);const handoff=await f.domain.state(handoffAgent);assert.equal(handoff.attempt,null);assert.equal(handoff.workflow.session.role,'handoff');assert.equal((await f.domain.lookup(handoffAgent,{ref:node.node_id})).value.status,'closed');
 }finally{f.close();}
});

test('stream chunk storm does not exhaust IPC; provider failures still propagate',async()=>{
 const f=await fixture();let dispose;try{
  dispose=registerResearchEvents(f.ctx,f.domain);
  for(let seq=0;seq<10000;seq++)f.events.get('session/event')(f.main.session,{seq,type:'assistant/chunk',data:{}});
  const responses=await Promise.all(Array.from({length:100},()=>f.domain.request(f.main,'query')));
  assert.equal(responses.length,100);
  const stream=f.events.get('llm/stream')({sessionId:f.main.id},async function*(){yield{type:'usage',usage:{totalTokens:12}};throw Error('provider failed');});
  await assert.rejects(async()=>{for await(const _ of stream){}},/provider failed/);
  assert.equal((await f.domain.state(f.main)).usage.actual,12);
 }finally{dispose?.();f.close();}
});

test('late notices never rearm human, native Stop, fault, completed, project-paused or cold sessions', async()=>{
 for (const reason of ['human','native_stop','fault','complete','project','cold']) {
  const f=await fixture();try {
   const n=await propose(f,reason); await f.domain.auto(f.main,'auto');
   const task=await f.domain.dispatch(f.main,n.node_id,'dispatch');
   await f.domain.wait(f.main,[task.task_id],'wait');
   await f.domain.pauseSession(f.main,reason);
   if(reason==='project')await f.domain.workflow(f.main,'run',{state:'paused'});
   if(reason==='cold')await f.domain.workflow(f.main,'cold',{});
   if(reason==='complete'){f.goals.get('main').phase='complete';await f.domain.workflow(f.main,'run',{state:'complete'});}
   const child=f.agents.get(task.session_id);
   const publication=await f.domain.request(child,'publish',{items:[],summary:'partial',gaps:['not final']},'pub');
   await f.domain.progress(child,'publication',publication,'pub');
   assert.equal((await f.domain.state(f.main)).workflow.session.pause_reason,reason);
   assert.notEqual(f.goals.get('main').phase,'active');
   assert.equal(f.main.inbox.nextStep.length,1);
   await f.domain.idle(child);assert.equal(f.main.inbox.nextStep.length,1);
  }finally{f.close();}
 }
});

test('cancel failure remains unverified until verify-stop confirms exit',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');f.main.status='running';
  f.domain.adapter.cancel=async()=>{throw Error('native cancel unavailable');};
  const result=await f.domain.stopProject(f.main,'stop');
  assert.equal(result.state,'unverified');
  assert.equal((await f.domain.state(f.main)).attempt.state,'open');
  await assert.rejects(f.domain.auto(f.main,'restart'),/待核实/);
  f.main.status='idle';f.domain.adapter.cancel=async()=>{};
  await f.domain.verifyStop(f.main,'verify');
  assert.equal((await f.domain.state(f.main)).workflow.run.state,'stopped');
  await f.domain.auto(f.main,'restart2');
  assert.equal((await f.domain.state(f.main)).counts.attempts,2);
 }finally{f.close();}
});

test('explicit session resume and fault retry preserve cwd and create segments only when required',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');
  const cwd=f.main.session.header.cwd;
  await f.domain.pauseSession(f.main,'human','pause-human');
  const resumed=await f.domain.resumeSession(f.main,'main','resume-human');
  assert.equal(resumed.session_id,'main');assert.equal(f.goals.get('main').phase,'active');
  assert.equal((await f.domain.state(f.main)).counts.attempts,1);
  await f.domain.pauseSession(f.main,'fault','pause-fault');
  const retried=await f.domain.resumeSession(f.main,'main','retry-fault',{retry:true});
  assert.equal(retried.retry_of,'A-001');assert.equal(retried.attempt_id,'A-002');
  assert.equal(f.main.session.header.cwd,cwd);
  const attempts=await page(f,'attempts');
  assert.equal(attempts[0].details.reason,'explicit-retry-after-exit-verification');
  assert.equal(attempts[0].details.retry_of,'A-001');
 }finally{f.close();}
});

test('project pause allows current-turn tools to finish but refuses a new autonomous turn',async()=>{
 const f=await fixture();let dispose;try{
  await f.domain.auto(f.main,'auto');dispose=registerResearchEvents(f.ctx,f.domain);
  await f.domain.projectGoals(f.main,'pause','pause');
  const hook=f.events.get('agent/pre-step');const next=async()=>({kind:'enter',messages:[]});
  assert.equal((await hook({agent:f.main,turn:1,step:2},next)).kind,'enter');
  assert.equal((await hook({agent:f.main,turn:2,step:1},next)).kind,'reject');
  await f.domain.workflow(f.main,'session',{pause_reason:'wait'});
  assert.equal((await hook({agent:f.main,turn:2,step:3},next)).kind,'reject');
 }finally{dispose?.();f.close();}
});

test('explicit manual-to-auto transition closes manual work once, then repeat starts reuse auto work',async()=>{
 const f=await fixture();try{
  await f.domain.request(f.main,'note',{body:'manual preparation',model_call:true},'manual');
  await f.domain.auto(f.main,'auto');await f.domain.auto(f.main,'repeat');
  const state=await f.domain.state(f.main);
  assert.equal(state.attempt.mode,'auto');assert.equal(state.counts.attempts,2);
  assert.equal((await page(f,'attempts'))[0].details.reason,'explicit-switch-to-autonomous');
 }finally{f.close();}
});

test('native specialists are registered before use, remain read-only, and retries do not spawn twice',async()=>{
 const f=await fixture();try{
  const node=await propose(f,'specialist');
  await f.domain.auto(f.main,'auto');
  const dispatched=await f.domain.dispatch(f.main,node.node_id,'dispatch-review');
  const core=f.agents.get(dispatched.session_id);
  const args={label:'review',node_id:node.node_id,inputs:[node.question_ref],prompt:'review fixed sources',tool_scope:['research_query']};
  const exec={signal:new AbortController().signal};
  const first=await f.domain.delegate(core,args,exec,'specialist-op','review');
  assert.equal(first.state,'completed');assert.equal(first.exit_verified,true);
  assert.equal(f.specialistRuns.length,1);assert.equal(f.specialistRuns[0].disposed,true);
  assert.deepEqual(f.specialistRuns[0].request.toolFilter,{allow:['research_query']});
  const child=f.specialistRuns[0].child;
  const childState=await f.domain.state(child);
  assert.equal(childState.workflow.session.role,'specialist');
  assert.match((await f.domain.request(child,'memory_context')).text,/research-read-only/);
  await assert.rejects(f.domain.request(child,'note',{body:'illegal',model_call:true},'specialist-write'),/cannot modify/);
  const usage=await f.domain.request(child,'usage_begin',{source_key:'specialist-usage'},'specialist-usage');
  assert.equal(usage.attempt_id,(await f.domain.state(core)).attempt.attempt_id);
  const retry=await f.domain.delegate(core,args,exec,'specialist-op','review');
  assert.equal(retry.task_id,first.task_id);assert.equal(f.specialistRuns.length,1);
 }finally{f.close();}
});

test('specialist cancellation is structured and a failed dispose remains unverified without duplicate spawn',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');
  const controller=new AbortController();
  let startedResolve;const started=new Promise(resolve=>{startedResolve=resolve;});
  f.ctx.subagents.start=async(provider,request)=>{
    const child={...f.main,id:'specialist-cancelled',session:{header:{...f.main.session.header,parentSession:f.main.id},events:[]}};
    f.agents.set(child.id,child);
    const result=new Promise(resolve=>request.signal.addEventListener('abort',()=>resolve({stopReason:'aborted',output:[]} ),{once:true}));
    startedResolve();
    return{id:child.id,localAgent:child,result,async dispose(){}};
  };
  const running=f.domain.delegate(f.main,{label:'cancel',prompt:'wait',inputs:[]},{signal:controller.signal},'specialist-cancel','review');
  await started;controller.abort();
  assert.equal((await running).state,'cancelled');

  let starts=0;
  f.ctx.subagents.start=async(provider,request)=>{
    starts++;
    const child={...f.main,id:'specialist-unverified',session:{header:{...f.main.session.header,parentSession:f.main.id},events:[]}};
    f.agents.set(child.id,child);
    return{id:child.id,localAgent:child,result:new Promise((_,reject)=>setTimeout(()=>reject(Error('provider disconnected')),100)),async dispose(){throw Error('exit unknown');}};
  };
  const args={label:'unknown',prompt:'work',inputs:[]},exec={signal:new AbortController().signal};
  const first=await f.domain.delegate(f.main,args,exec,'specialist-unverified','review');
  assert.equal(first.state,'unverified');assert.equal(first.exit_verified,false);
  const retry=await f.domain.delegate(f.main,args,exec,'specialist-unverified','review');
  assert.equal(retry.task_id,first.task_id);assert.equal(starts,1);
  assert.equal(f.goals.get('main').phase,'active');
 }finally{f.close();}
});

test('specialist model failure is an incomplete result and does not fault the parent goal',async()=>{
 const f=await fixture();try{
  await f.domain.auto(f.main,'auto');
  f.ctx.subagents.start=async(provider,request)=>{
    const child={...f.main,id:'specialist-error',session:{header:{...f.main.session.header,parentSession:f.main.id},events:[]}};
    f.agents.set(child.id,child);
    return{id:child.id,localAgent:child,result:Promise.resolve({stopReason:'error',output:[],diagnostic:'fixture failure'}),async dispose(){}};
  };
  const result=await f.domain.delegate(f.main,{label:'review',prompt:'review',inputs:[]},{signal:new AbortController().signal},'specialist-error-op','review');
  assert.equal(result.state,'incomplete');assert.equal(result.error,'fixture failure');
  assert.equal(f.goals.get('main').phase,'active');assert.equal(f.domain.faults.has('main'),false);
 }finally{f.close();}
});

test('assemble injects the first stable context and usage-only changes do not change its bytes',async()=>{
 const f=await fixture();let dispose;try{
  const node=await propose(f,'context');
  await f.domain.request(f.main,'focus',{node_id:node.node_id,role:'planner',mode:'manual'},'focus-context');
  dispose=registerResearchEvents(f.ctx,f.domain);
  const assemble=f.events.get('system-prompt/assemble');
  const base={sections:[],contexts:[],tools:[],variables:{}};
  const first=await assemble(base,{agent:f.main,scope:f.main,signal:new AbortController().signal},async()=>base);
  const firstText=first.contexts.find(row=>row.name==='research:memory')?.text;
  assert.match(firstText,/Research fixture/);assert.match(firstText,/question context/);
  await f.domain.request(f.main,'usage_begin',{source_key:'context-usage'},'context-usage-start');
  await f.domain.request(f.main,'usage_finish',{source_key:'context-usage',amount:99,completeness:'actual'},'context-usage-finish');
  const second=await assemble(base,{agent:f.main,scope:f.main,signal:new AbortController().signal},async()=>base);
  assert.equal(second.contexts.find(row=>row.name==='research:memory')?.text,firstText);
  const title=await assemble(base,{agent:f.main,scope:f.main,purpose:'title',signal:new AbortController().signal},async()=>base);
  assert.equal(title.contexts.some(row=>row.name==='research:memory'),false);
  const unrelated={...f.main,id:'unrelated',session:{header:{...f.main.session.header},events:[]}};f.agents.set(unrelated.id,unrelated);
  const outside=await assemble(base,{agent:unrelated,scope:unrelated,signal:new AbortController().signal},async()=>base);
  assert.equal(outside.contexts.some(row=>row.name==='research:memory'),false);
 }finally{dispose?.();f.close();}
});

test('0.6.5 oversized specialist output is archived without reasoning and settles',async()=>{
  const f=await fixture();try{
    await f.domain.auto(f.main,'auto');
    const start=f.ctx.subagents.start;
    f.ctx.subagents.start=async(...args)=>{const run=await start(...args);run.result=Promise.resolve({stopReason:'completed',output:[{type:'reasoning',text:'private'.repeat(20000)},{type:'text',text:'deliverable'.repeat(12000)}]});return run;};
    const result=await f.domain.delegate(f.main,{prompt:'project review',inputs:[]},{signal:new AbortController().signal},'large-review','review');
    assert.equal(result.state,'completed');assert.equal(result.exit_verified,true);
    assert.ok(result.result.artifact.version);assert.ok(JSON.stringify(result).length<10000);
    const content=readFileSync(join(f.main.session.header.cwd,result.result.artifact.path),'utf8');
    assert(!content.includes('private'));assert(content.includes('deliverable'.repeat(12000)));
  }finally{f.close();}
});

async function specialistCore(f) {
 await f.domain.auto(f.main,'auto');
 const node=await propose(f,'expert-node');
 const task=await f.domain.dispatch(f.main,node.node_id,'dispatch-experts');
 return f.agents.get(task.session_id);
}

test('0.6.5 a batch overlaps execution with distinct first-context and usage identities',async()=>{
 const f=await fixture();let dispose;try{
  const core=await specialistCore(f);dispose=registerResearchEvents(f.ctx,f.domain);
  const start=f.ctx.subagents.start,release=[],bound=[];
  f.ctx.subagents.start=async(...args)=>{
   const run=await start(...args);
   // Model assembly may happen before start returns to the caller.
   const context=await f.events.get('system-prompt/assemble')({}, {agent:run.localAgent}, async()=>({contexts:[]}));
   const state=await f.domain.state(run.localAgent);
   const usage=await f.domain.request(run.localAgent,'usage_begin',{source_key:run.localAgent.id},`${run.localAgent.id}:usage`);
   bound.push({id:run.localAgent.id,context:context.contexts[0].text,usage,state});
   run.result=new Promise(resolve=>release.push(resolve));
   return run;
  };
  const work=f.domain.delegateBatch(core,{tasks:[{label:'one',prompt:'UNIQUE_ONE'},{label:'two',prompt:'UNIQUE_TWO'}]},{signal:new AbortController().signal},'batch');
  for(let i=0;i<100&&release.length<2;i++)await new Promise(r=>setTimeout(r,10));
  assert.equal(release.length,2,'both experts start before either completes');
  assert.match(bound[0].context,/UNIQUE_ONE/);assert(!bound[0].context.includes('UNIQUE_TWO'));
  assert.match(bound[1].context,/UNIQUE_TWO/);assert(!bound[1].context.includes('UNIQUE_ONE'));
  assert.notEqual(bound[0].id,bound[1].id);
  const attempt=(await f.domain.state(core)).attempt;
  assert(bound.every(b=>b.usage.attempt_id===attempt.attempt_id&&b.usage.node_id===attempt.node_id));
  release[0]({stopReason:'error',output:[],diagnostic:'one failed'});
  release[1]({stopReason:'completed',output:[{type:'text',text:'two succeeds'}]});
  const result=await work;
  assert.deepEqual(result.tasks.map(t=>t.state),['incomplete','completed']);
  assert(result.tasks.every(t=>t.exit_verified));
  assert.equal(f.domain.runningSpecialists.size,0);
  const replay=await f.domain.delegateBatch(core,{tasks:[{label:'one',prompt:'UNIQUE_ONE'},{label:'two',prompt:'UNIQUE_TWO'}]},{signal:new AbortController().signal},'batch');
  assert.deepEqual(replay.tasks.map(t=>t.task_id),result.tasks.map(t=>t.task_id));assert.equal(release.length,2);
 }finally{dispose?.();f.close();}
});

test('0.6.5 full-batch capacity is checked before any new native startup',async()=>{
 const f=await fixture();try{
  const core=await specialistCore(f);
  await f.domain.createSpecialist(core,{prompt:'occupied'},'occupied');
  await assert.rejects(f.domain.delegateBatch(core,{tasks:[{prompt:'one'},{prompt:'two'}]},{signal:new AbortController().signal},'too-many'),/fan-out/);
  assert.equal(f.specialistRuns.length,0);
  const rows=await page(f,'specialists');
  assert.equal(rows.filter(t=>t.state==='starting').length,1);
  assert.equal(rows.filter(t=>t.state==='cancelled'&&t.exit_verified).length,1);
 }finally{f.close();}
});

test('0.6.5 timeout and user Stop settle each member without losing partial output',async()=>{
 for(const manualStop of [false,true]){
  const f=await fixture();try{
   const core=await specialistCore(f);f.domain.specialistTimeoutMs=manualStop?10000:100;
   const start=f.ctx.subagents.start,controller=new AbortController();let started=0;
   f.ctx.subagents.start=async(provider,args)=>{
    const run=await start(provider,args);started++;
    run.result=new Promise(resolve=>args.signal.addEventListener('abort',()=>resolve({stopReason:'aborted',output:[{type:'reasoning',text:'hidden'.repeat(16000)},{type:'text',text:'partial'}]}),{once:true}));return run;
   };
   const work=f.domain.delegateBatch(core,{tasks:[{prompt:'one'},{prompt:'two'}]},{signal:controller.signal},`cancel-${manualStop}`);
   for(let i=0;i<100&&started<2;i++)await new Promise(r=>setTimeout(r,5));
   if(manualStop)controller.abort();
   const result=await work;
   assert.equal(result.tasks.length,2);assert(result.tasks.every(t=>t.state==='cancelled'&&t.exit_verified&&t.result.artifact));
   assert.equal(f.domain.specialistHandles.size,0);
  }finally{f.close();}
 }
});

test('0.6.5 archival failure retains verified exit and recovery never replays oversized legacy output',async()=>{
 const f=await fixture();try{
  const core=await specialistCore(f);const request=f.domain.request.bind(f.domain);
  f.domain.request=(agent,method,...rest)=>method==='specialist_result_import'?Promise.reject(Error('archive unavailable')):request(agent,method,...rest);
  const task=await f.domain.delegate(core,{prompt:'review'},{signal:new AbortController().signal},'save-error');
  assert.equal(task.state,'incomplete');assert.equal(task.exit_verified,true);assert.match(task.error,/archive unavailable/);
  f.domain.request=request;
  // Simulate the historical lost-finish defect without invoking another model.
  await request(core,'specialist_finish',{fields:{task_id:task.task_id,state:'unverified',result:{},exit_verified:false}},'lost-finish');
  const child=f.agents.get(task.child_session_id);child.status='running';
  assert.equal((await f.domain.verifySpecialist(f.main,task.task_id,'still-active')).state,'unverified');
  child.status='idle';
  const recovered=await f.domain.verifySpecialist(f.main,task.task_id,'recover');
  assert.equal(recovered.exit_verified,true);
  const before=(await page(f,'changes')).filter(e=>e.kind==='specialist.finished').length;
  await f.domain.verifySpecialist(f.main,task.task_id,'recover-again');
  assert.equal((await page(f,'changes')).filter(e=>e.kind==='specialist.finished').length,before);
  assert.equal(f.specialistRuns.length,1);
 }finally{f.close();}
});

test('0.6.5 ordinary experts cannot reopen shell access and main cannot impersonate a node',async()=>{
 const f=await fixture();try{
  const core=await specialistCore(f),node=(await f.domain.state(core)).attempt.node_id;
  await assert.rejects(f.domain.delegate(core,{prompt:'x',tool_scope:['bash']},{signal:new AbortController().signal},'shell'),/read-only/);
  await assert.rejects(f.domain.delegate(f.main,{node_id:node,prompt:'x'},{signal:new AbortController().signal},'main-experiment'),/Main coordinates/);
  assert.equal(f.specialistRuns.length,0);
  const mainGoal=f.goals.get(f.main.id).objective,coreGoal=f.goals.get(core.id).objective;
  assert.match(mainGoal,/Do not call research_finish/);assert.match(mainGoal,/clarification answers alone/);
  assert.match(coreGoal,/planning, experiments and analysis/);
 }finally{f.close();}
});

test('0.6.5 lost finish persistence reports recovery and verifies stale running or unverified terminal records',async()=>{
 const f=await fixture();try{
  const core=await specialistCore(f),request=f.domain.request.bind(f.domain);
  f.domain.request=(agent,method,...rest)=>method==='specialist_finish'?Promise.reject(Error('storage disconnected')):request(agent,method,...rest);
  const lost=await f.domain.delegate(core,{prompt:'work'},{signal:new AbortController().signal},'lost-write');
  assert.equal(lost.persisted,false);assert.equal(lost.next_action,'research_verify_specialist');assert.equal(lost.exit_verified,true);
  f.domain.request=request;
  const stale=await request(core,'specialist_get',{task_id:lost.task_id});assert.equal(stale.state,'running');
  const recovered=await f.domain.verifySpecialist(f.main,lost.task_id,'recover-stale');assert.equal(recovered.exit_verified,true);
  await request(core,'specialist_finish',{fields:{task_id:lost.task_id,state:'cancelled',result:{legacy:'keep this historical result'},exit_verified:false}},'unverified-cancel');
  const cancelled=await f.domain.verifySpecialist(f.main,lost.task_id,'verify-cancel');assert.equal(cancelled.state,'cancelled');assert.equal(cancelled.exit_verified,true);assert.deepEqual(cancelled.result,{legacy:'keep this historical result'});
  assert.equal(f.specialistRuns.length,1);
 }finally{f.close();}
});

test('0.6.5 blind tool parameters never forward source references and assembled tools exclude host subagent',async()=>{
 const {registerResearchTools}=await import('../../apps/dsh/plugin/tools.js');
 const definitions=new Map();let received;
 registerResearchTools({tools:{register(def){definitions.set(def.name,def);return()=>{};}}},{specialistTimeoutMs:100,delegate:async(_agent,args)=>{received=args;return{};}});
 await definitions.get('research_delegate').execute({label:'review',question:'Evaluate the sample',purpose:'classification',context_mode:'blind',inputs:['pub/P-SECRET#truth'],deliverable:'verdict',completion_criteria:'read',report_requirements:'uncertainty'},{agent:{id:'test'},callId:'test',signal:new AbortController().signal});
 assert(!received.prompt.includes('P-SECRET'));assert(received.prompt.includes('input-1'));
 const f=await fixture();let dispose;try{
  dispose=registerResearchEvents(f.ctx,f.domain);
  f.domain.specialistModes.set(f.main.id,'blind');f.domain.sessionRoles.set(f.main.id,'specialist');
  const original=f.domain.request.bind(f.domain);
  f.domain.request=(agent,method,...rest)=>method==='memory_context'?Promise.resolve({text:'blind fixture',context_mode:'blind',source_digest:'test'}):original(agent,method,...rest);
  const result=await f.events.get('system-prompt/assemble')({}, {agent:f.main}, async()=>({contexts:[],tools:[{name:'research_read_input'},{name:'subagent'},{name:'bash'}]}));
  assert.deepEqual(result.tools.map(t=>t.name),['research_read_input']);
  let guard;registerNativeSubagentGuard({tools:{guard(fn){guard=fn;return()=>{};}}},f.domain);
  assert.match(guard({name:'subagent',agent:f.main}),/cannot start another agent/);
 }finally{dispose?.();f.close();}
});

test('0.6.5 simultaneous retries do not create two native runs for one operation',async()=>{
 const f=await fixture();try{
  const core=await specialistCore(f),exec={signal:new AbortController().signal};
  const results=await Promise.all([f.domain.delegate(core,{prompt:'same'},exec,'same-op'),f.domain.delegate(core,{prompt:'same'},exec,'same-op')]);
  assert.equal(results[0].task_id,results[1].task_id);assert.equal(f.specialistRuns.length,1);
 }finally{f.close();}
});

test('0.6.7 consolidate prepares a proposal without resolving or re-offering it', async () => {
  const f = await fixture();
  try {
    const registered = [];
    f.ctx.tools = { register(definition) { registered.push(definition); return definition; } };
    const { registerResearchTools } = await import('../../apps/dsh/plugin/tools.js');
    registerResearchTools(f.ctx, f.domain);
    const memory = registered.find(d => d.name === 'research_memory');

    const write = (fields, op) => f.domain.request(f.main, 'memory_write', { action: 'record', fields }, op);
    const basis = await write({ kind: 'decision', statement: 'basis', visibility: 'project' }, 'k-basis');
    await write({ kind: 'decision', statement: 'uses', dependencies: [basis.ref], visibility: 'project' }, 'k-uses');
    await f.domain.request(f.main, 'memory_write', {
      action: 'revise',
      fields: {
        ref: basis.ref, expected_revision: 1, changes: { status: 'retracted' },
        reason: 'fixture retraction', affected_scope_mode: 'versions',
        affected_scope: [basis.ref], change_kind: 'retract',
      },
    }, 'k-retract');

    const before = await f.domain.request(f.main, 'impact_next', {});
    assert.ok(before, 'a pending impact is waiting');

    const exec = { agent: f.main, callId: 'c1', signal: new AbortController().signal };
    const result = await memory.execute({ action: 'consolidate' }, exec);
    assert.equal(result.impact.affected_version, before.affected_version);

    const rows = await page(f, 'impacts');
    const row = rows.find(r => r.affected_version === before.affected_version);
    assert.equal(row.review_state, 'proposal_ready', 'the specialist returning only prepares materials');
    assert.equal(row.disposition, null, 'consolidate never records a disposition');
    assert.equal(row.needs_action ?? true, true);

    const after = await f.domain.request(f.main, 'impact_next', {});
    assert.notEqual(after?.affected_version, before.affected_version, 'a prepared item is not handed out again');
  } finally { f.close(); }
});
