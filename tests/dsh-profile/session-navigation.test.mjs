import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createResearchSessionNavigation,researchSessionStatus,researchSummaryEqual} from '../../apps/dsh/plugin/frontend/session-navigation.js';

test('opening the current session switches its native view to chat',async()=>{
  const calls=[],nav=createResearchSessionNavigation({open:id=>calls.push(['open',id])});
  nav.bind('main',{setView:view=>calls.push(['view',view])});
  await nav.open('main');assert.deepEqual(calls,[['open','main'],['view','chat']]);
});
test('opening another session switches its view when its dock mounts',async()=>{
  const views=[],nav=createResearchSessionNavigation({open:()=>{}});
  const leave=nav.bind('main',{setView:view=>views.push(['main',view])});
  await nav.open('child');assert.deepEqual(views,[]);leave();
  nav.bind('child',{setView:view=>views.push(['child',view])});
  assert.deepEqual(views,[['child','chat']]);
});
test('persisted children refresh the parent catalog before selection; errors propagate',async()=>{
  const calls=[],nav=createResearchSessionNavigation({refreshSubagents:async id=>calls.push(['catalog',id]),list:{getSnapshot:()=>({subagentsByParent:{parent:{entries:[{kind:'child',id:'child',mode:'oneshot'}]}}})},open:()=>assert.fail('child must use addressed route'),openSubagent:address=>{calls.push(['open',address.childSessionId]);assert.deepEqual(address,{parentSessionId:'parent',childSessionId:'child',mode:'oneshot'});throw Error('unknown session');}});
  await assert.rejects(nav.open('child',{parentSessionId:'parent'}),/unknown session/);
  nav.bind('child',{setView:()=>assert.fail('failed navigation must not remain pending')});
  assert.deepEqual(calls,[['catalog','parent'],['open','child']]);
});
test('live runtime changes invalidate equal ledger revisions',()=>{
  const old={revision:12,runtime:{state:'running',pending_approvals:0,sessions:[{session_id:'node',native_status:'idle'}]}};
  const next=structuredClone(old);next.runtime.sessions[0].native_status='running';
  assert.equal(researchSummaryEqual(old,next),false);
  assert.equal(researchSummaryEqual(old,structuredClone(old)),true);
  assert.equal(researchSessionStatus({pause_reason:'wait',native_status:'idle'}),'等待研究任务');
  assert.equal(researchSessionStatus({native_status:'running'}),'运行中');
  assert.equal(researchSessionStatus({pause_reason:'wait',pending_approvals:1}),'等待人工审批');
});

test('read-only view binding navigates without a composer and leaves other bindings intact',async()=>{
  const views=[],nav=createResearchSessionNavigation({open:()=>{}});
  const leaveDock=nav.bind('child',{setView:view=>views.push(['dock',view])});
  const leaveView=nav.bind('child',{setView:view=>views.push(['view',view])});
  await nav.open('child');leaveView();await nav.open('child');leaveDock();
  assert.deepEqual(views,[['view','chat'],['dock','chat']]);
  await nav.open('readonly');
  nav.bind('readonly',{setView:view=>views.push(['readonly',view])});
  assert.deepEqual(views.at(-1),['readonly','chat']);
});

test('reload context recovers a completed child from the native durable address',async()=>{
  const nav=createResearchSessionNavigation({subagentAddress:id=>id==='child'?{parentSessionId:'parent',childSessionId:'child',mode:'oneshot'}:undefined});
  assert.equal(nav.context('child').parentSessionId,'parent');
});
test('known children never fall back to a root route when their catalog is unavailable',async()=>{
  const nav=createResearchSessionNavigation({subagentAddress:()=>({parentSessionId:'parent'}),refreshSubagents:async()=>{},list:{getSnapshot:()=>({subagentsByParent:{parent:{entries:[],error:{message:'catalog unavailable'}}}})},open:()=>assert.fail('unsafe root route')});
  await assert.rejects(nav.open('child'),/catalog unavailable/);
});

test('discussion preparation registers its own cwd and adopts the same native session before opening',async()=>{
  const calls=[],workspace={workspaceId:'ws',path:'/project/workspaces/discussion-a',sessionIds:[]};
  const sessions={create:async args=>calls.push(['adopt',args]),refresh:async()=>calls.push(['refresh']),list:{getSnapshot:()=>({byId:{'discussion-a':{}}})},binding:()=>({session:{rename:async title=>(calls.push(['title',title]),{ok:true})}})};
  const workspaces={list:{getSnapshot:()=>({items:[]})},create:async args=>(calls.push(['workspace',args]),workspace),rename:async(...args)=>calls.push(['workspace-title',...args])};
  const nav=createResearchSessionNavigation(sessions,workspaces,async()=>({ok:true,value:{workflow:{session:{session_id:'discussion-a',role:'discussion',node_id:'X-004',cwd:workspace.path}},runtime:{main_session_id:'main'}}}));
  await nav.prepareDiscussion('discussion-a');
  assert.deepEqual(calls[0],['workspace',{path:workspace.path}]);
  assert.deepEqual(calls[2],['adopt',{sessionId:'discussion-a',workspaceId:'ws'}]);
  assert.equal(nav.context('discussion-a').identity.node_id,'X-004');assert.equal(nav.context('discussion-a').mainSessionId,'main');
});
test('opening an existing discussion preserves its workspace and user title',async()=>{
  const nav=createResearchSessionNavigation({refresh:async()=>{},list:{getSnapshot:()=>({byId:{d:{title:'My discussion'}}})},create:()=>assert.fail('already attached'),binding:()=>assert.fail('preserve user title')},{list:{getSnapshot:()=>({items:[{path:'/discussion',sessionIds:['d']}]})},create:()=>assert.fail('reuse workspace')},async()=>({ok:true,value:{workflow:{session:{session_id:'d',role:'discussion',node_id:'X-001',cwd:'/discussion'}}}}));
  await nav.prepareDiscussion('d');
});
test('discussion preparation rejects a mismatched session before workspace mutation',async()=>{
  const nav=createResearchSessionNavigation({}, {create:()=>assert.fail('wrong session')},async()=>({ok:true,value:{workflow:{session:{session_id:'main',role:'main',cwd:'/project'}}}}));
  await assert.rejects(nav.prepareDiscussion('d'),/身份或工作目录/);
});
