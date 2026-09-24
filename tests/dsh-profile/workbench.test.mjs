import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorkbench} from '../../apps/dsh/plugin/frontend/workbench.js';
import {projectGraph} from '../../apps/dsh/plugin/frontend/model.js';
// Exercise async state transitions with a small deterministic hook host; actual DOM is tested in DSH.
function hooks(){
 const slots=[],effects=[];let cursor=0;
 const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)}),
 useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},
 useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useCallback:f=>f,useMemo:f=>f(),useEffect:f=>effects.push(f)};
 return {React,render:f=>{cursor=0;return f();},start:()=>effects.splice(0).map(f=>f()),slots};
}
const sample=()=>({project:{goal:'fixture',control:'manual'},nodes:[{node_id:'X',status:'open',question:'Question'}],relations:[],legacy_refs:[],attempts:[],publications:[],notes:[],snapshots:[],restorations:[],associations:[],knowledge:[],checkpoints:[],review_todos:[],specialists:[],sessions:[],usage:{known:1,actual:1,estimated:0,in_progress:0,missing:0,discussion:0,unknown_count:0},workflow:{run:{state:'manual'},tasks:[]},runtime:{state:'manual',sessions:[],running_count:0,specialist_count:0,pending_approvals:0},counts:{nodes:1,relations:0,legacy_refs:0,attempts:0,publications:0,notes:0,snapshots:0,restorations:0,associations:0,knowledge:0,checkpoints:0,review_todos:0,specialists:0,sessions:0}});
const response=(endpoint,payload)=>endpoint==='query'?sample():endpoint==='context.preview'?{text:'fixture',status:'fresh'}:endpoint==='guidance.status'?null:endpoint==='history.page'?{items:sample()[payload.collection]??[],cursor:null}:{message:'ok'};
function find(tree,predicate){if(!tree||typeof tree!=='object')return null;if(predicate(tree))return tree;for(const child of tree.children??[]){const x=find(child,predicate);if(x)return x;}return null;}
function textOf(tree){if(typeof tree==='string'||typeof tree==='number')return String(tree);if(!tree||typeof tree!=='object')return'';return(tree.children??[]).map(textOf).join('');}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('unverified creation visibly reserves capacity and opens the recovery section',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),state=sample();state.tasks=[{task_id:'T-blocked',session_id:'not-created',node_id:'X',state:'unverified'}];
 state.counts.tasks=1;
 const rpc=async(endpoint,payload)=>({ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='tasks'?{items:state.tasks,cursor:null}:response(endpoint,payload)});
 const W=createWorkbench(host.React,rpc,()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();await settle(host);
 const tree=host.render(()=>W({sessionId:'main'}));
 assert(find(tree,n=>n.props.role==='alert'&&n.children[0].includes('保留 1 个研究槽位')));
 const recovery=find(tree,n=>n.type==='details'&&n.children.some(c=>c?.type==='summary'&&c.children[0]==='探索任务与恢复'));
 assert.equal(recovery.props.open,true);
 assert(find(recovery,n=>n.type==='button'&&n.children[0]==='核实并重试创建'));
 assert(find(recovery,n=>n.type==='button'&&n.children[0]==='核实是否未启动'));
 clean.forEach(f=>f());globalThis.window=old;
});
const settle=async host=>{for(let index=0;index<30&&!host.slots[0];index++)await new Promise(resolve=>setTimeout(resolve,2));};
test('selection is read-only, polling failure keeps graph, stale actions disabled, retry recovers',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),calls=[];let fail=false;
 const rpc=async(endpoint,payload)=>{calls.push({endpoint,payload});if(fail)return {ok:false,error:{message:'offline'}};return {ok:true,value:response(endpoint,payload)};};
 const Graph=()=>{},Details=()=>{};
 const Workbench=createWorkbench(host.React,rpc,()=>{},projectGraph,Graph,{Details,Records:()=>{}},'');
 let tree=host.render(()=>Workbench({sessionId:'a'}));const cleanup=host.start();await settle(host);tree=host.render(()=>Workbench({sessionId:'a'}));
 const graph=find(tree,n=>n.type===Graph);assert(graph,JSON.stringify({state:host.slots[0],error:host.slots[1],calls}));graph.props.onSelect('X');tree=host.render(()=>Workbench({sessionId:'a'}));
 assert.equal(find(tree,n=>n.type===Details).props.node.node_id,'X');assert(calls.some(c=>c.endpoint==='query'));assert(calls.some(c=>c.endpoint==='history.page'));
 // Use a real poll after forcing its async query failure.
 fail=true;cleanup.forEach(f=>f());host.start().forEach(f=>f());
 // Re-mount the effect after cleanup, just as host attachment changes.
 host.render(()=>Workbench({sessionId:'a'}));const end=host.start();await tick();tree=host.render(()=>Workbench({sessionId:'a'}));
 assert(find(tree,n=>n.props.role==='alert'));assert(find(tree,n=>n.type===Graph));assert.equal(find(tree,n=>n.type===Details).props.disabled,true);
 fail=false;await find(tree,n=>n.type==='button'&&n.children[0]==='重试').props.onClick();tree=host.render(()=>Workbench({sessionId:'a'}));assert.equal(find(tree,n=>n.props.role==='alert'),null);
 assert.equal(find(tree,n=>n.type===Details).props.node.node_id,'X');end.forEach(f=>f());globalThis.window=old;
});
test('late reply from a detached component is discarded',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};const host=hooks();let resolve;
 const Workbench=createWorkbench(host.React,(endpoint,payload)=>endpoint==='query'?new Promise(r=>resolve=r):Promise.resolve({ok:true,value:response(endpoint,payload)}),()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>Workbench({sessionId:'old'}));const cleanup=host.start();cleanup.forEach(f=>f());resolve({ok:true,value:sample()});await tick();assert.equal(host.slots[0],null);globalThis.window=old;
});
test('pending action guard prevents duplicate discussion calls',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};const host=hooks();const calls=[];let done;
 const rpc=async(endpoint,payload)=>{calls.push(endpoint);if(endpoint==='discussion.open')await new Promise(r=>done=r);return {ok:true,value:response(endpoint,payload)};};
 const Graph=()=>{},Details=()=>{},W=createWorkbench(host.React,rpc,()=>{},projectGraph,Graph,{Details,Records:()=>{}},'');
 host.render(()=>W({sessionId:'a'}));const clean=host.start();await settle(host);let tree=host.render(()=>W({sessionId:'a'}));const detail=find(tree,n=>n.type===Details);assert(detail,JSON.stringify({state:host.slots[0],calls}));const act=detail.props.act;
 const first=act('discussion.open',{nodeId:'X'});await act('discussion.open',{nodeId:'X'});assert.equal(calls.filter(c=>c==='discussion.open').length,1);done();await first;clean.forEach(f=>f());globalThis.window=old;
});
test('approval and knowledge provenance are shown as separate actionable fields',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};const host=hooks(),state=sample(),opened=[];
 state.runtime={...state.runtime,pending_approvals:1,waiting_approval_count:1,approvals:[{approval_id:'A1',session_id:'core',node_id:'X',reason:'访问只读目录'}]};
 state.knowledge=[{ref:'knowledge/K@1',kind:'claim',status:'working',statement:'claim',scope:{subset:'audit'},conditions:{hardware:'cpu'},evidence_refs:['S-1'],source_identity:{session_id:'core'},supersedes:[]}];
 const rpc=async(endpoint,payload)=>({ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='knowledge'?{items:state.knowledge,cursor:null}:response(endpoint,payload)});
 const W=createWorkbench(host.React,rpc,id=>opened.push(id),projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();await settle(host);const tree=host.render(()=>W({sessionId:'main'})),all=textOf(tree);
 assert.match(all,/等待原生审批/);assert.match(all,/访问只读目录/);assert.match(all,/证据引用：S-1/);assert.match(all,/自述来源：/);assert.match(all,/适用范围：/);assert.match(all,/适用条件：/);
 await find(tree,n=>n.type==='button'&&n.children[0]==='打开审批会话').props.onClick();assert.deepEqual(opened,['core']);
 clean.forEach(f=>f());globalThis.window=old;
});

// CE-17: the same six server resolutions drive display; no local availability inference.
for (const [ref,outcome,reason] of [
 ['pub/P-001#report','unavailable','object_missing'],
 ['S-002#REPORT.md','ambiguous','duplicate_path'],
 ['S-001#missing.txt','not_found','entry_missing'],
 ['S-001#out/../x','unsupported','path_escape'],
 ['S-001#out/sub/b.txt','resolved',null],
 ['pub/P-001#note','resolved',null],
]) test(`CE-17 UI ${ref}`,async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),state=sample(),calls=[];
 state.knowledge=[{ref:'knowledge/K@1',kind:'claim',status:'working',statement:'claim',evidence_refs:[ref]}];
 const rpc=async(endpoint,payload)=>{
  calls.push({endpoint,payload});
  if(endpoint==='reference.get') return {ok:true,value:{kind:ref==='pub/P-001#note'?'publication-item':'snapshot-entry',value:{},resolution:{ref,outcome,reason,object:null}}};
  return {ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='knowledge'?{items:state.knowledge,cursor:null}:response(endpoint,payload)};
 };
 const W=createWorkbench(host.React,rpc,()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();
 try {
  await settle(host);
  let tree=host.render(()=>W({sessionId:'main'}));
  await find(tree,n=>n.type==='button'&&n.children[0]===ref).props.onClick();
  tree=host.render(()=>W({sessionId:'main'}));
  assert.equal(calls.filter(c=>c.endpoint==='reference.get').at(-1).payload.ref,ref);
  const panel=find(tree,n=>n.props['aria-label']==='研究材料');
  assert.equal(panel.props['data-resolution-outcome'],outcome);
  assert.equal(panel.props['data-resolution-reason'],reason);
  if(outcome==='resolved') assert.equal(panel.props.role,undefined);
  else {
   assert.equal(panel.props.role,'alert');
   assert.equal(find(panel,n=>n.type==='button'&&n.children[0]==='打开材料'),null);
   assert(textOf(panel).includes(reason));
  }
 } finally {clean.forEach(f=>f());globalThis.window=old;}
});

for(const scenario of ['CE-19','CE-20','CE-23']) test(`${scenario} provenance display`,async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),state=sample();
 state.knowledge=[{ref:'knowledge/K@1',statement:'test',kind:'decision',status:'working',source_identity:{session_id:'fake'},asserted_at:scenario==='CE-23'?null:{host_id:'host',session_id:'main',turn:3,operation_id:'op'},execution_refs:[{ref:'session:ghost',status:'unlinked',reason:'not_found'}]}];
 const rpc=async(endpoint,payload)=>({ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='knowledge'?{items:state.knowledge,cursor:null}:response(endpoint,payload)});
 const W=createWorkbench(host.React,rpc,()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();
 try {await settle(host);const tree=host.render(()=>W({sessionId:'main'}));
  const paragraphs=[];function visit(n){if(!n||typeof n!=='object')return;if(n.type==='p')paragraphs.push(textOf(n));for(const c of n.children??[])visit(c);}visit(tree);
  if(scenario==='CE-19') {const source=paragraphs.find(t=>t.startsWith('记账来源：'));assert(source?.includes('main'));assert(!source.includes('fake'));assert(paragraphs.some(t=>t.startsWith('自述来源：')&&t.includes('fake')&&!t.includes('main')));}
  if(scenario==='CE-20') assert(paragraphs.some(t=>t.includes('session:ghost')&&t.includes('未关联')&&t.includes('not_found')));
  if(scenario==='CE-23') assert(paragraphs.some(t=>t.startsWith('记账来源：')&&t.includes('历史记录，无来源')&&!t.includes('main')));
 }finally{clean.forEach(f=>f());globalThis.window=old;}
});

test('CE-21 candidate hints display repair evidence and page before rendering',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),state=sample(),calls=[];
 const all=Array.from({length:11},(_,i)=>({class:'whole_snapshot_evidence',target:`knowledge/K-${String(i+1).padStart(3,'0')}@1`,candidate:true,confidence:'candidate',evidence:{ref:'S-001'},repair_evidence:i===0?['knowledge/K-001@2']:[]}));
 const page=offset=>({items:all.slice(offset,offset+8),total:11,shown_count:all.slice(offset,offset+8).length,offset,next_offset:offset===0?8:null,sequence_bound:20});
 state.structure_hints=page(0);
 const rpc=async(endpoint,payload)=>{calls.push({endpoint,payload});return {ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='hints'?page(payload.cursor?.offset??0):response(endpoint,payload)};};
 const W=createWorkbench(host.React,rpc,()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();
 try {
  await settle(host);let tree=host.render(()=>W({sessionId:'main'}));
  let section=find(tree,n=>n.props['aria-label']==='结构候选提示');assert(section);
  assert(textOf(section).includes('候选'));assert(!textOf(section).includes('诊断'));
  assert(textOf(section).includes('11'));assert(textOf(section).includes('knowledge/K-001@2'));
  assert(!textOf(section).includes('knowledge/K-009@1'));
  await find(section,n=>n.type==='button'&&n.children[0]==='下一页').props.onClick();
  tree=host.render(()=>W({sessionId:'main'}));section=find(tree,n=>n.props['aria-label']==='结构候选提示');
  assert(textOf(section).includes('knowledge/K-009@1'));assert(!textOf(section).includes('knowledge/K-001@1'));
  assert(calls.some(c=>c.endpoint==='history.page'&&c.payload.collection==='hints'&&c.payload.cursor.offset===8));
  assert.equal(find(section,n=>n.type==='button'&&n.children[0]==='下一页').props.disabled,true);
 } finally {clean.forEach(f=>f());globalThis.window=old;}
});

test('CE-27 field consistency wording and declared values',async()=>{
 const old=globalThis.window;globalThis.window={matchMedia:()=>({matches:false})};
 const host=hooks(),state=sample();
 const spec={ref:'S-001#results/m.json',path:'/audit/fpr',op:'approx',value:0.0104,tolerance:0.00005};
 state.knowledge=[{ref:'knowledge/K-002@1',statement:'claim',kind:'claim',status:'proposed',checks:[spec],
   field_checks:[{spec,result:'consistent',observed_text:'0.010401'},
    {spec,result:'inconsistent',observed_text:'0.25'},
    {spec,result:'not_checkable',reason:'missing_value',observed_text:null}]}];
 const rpc=async(endpoint,payload)=>({ok:true,value:endpoint==='query'?state:endpoint==='history.page'&&payload.collection==='knowledge'?{items:state.knowledge,cursor:null}:response(endpoint,payload)});
 const W=createWorkbench(host.React,rpc,()=>{},projectGraph,()=>{}, {Details:()=>{},Records:()=>{}},'');
 host.render(()=>W({sessionId:'main'}));const clean=host.start();
 try {await settle(host);const text=textOf(host.render(()=>W({sessionId:'main'})));
  for(const expected of ['字段检查','声明字段与冻结文件一致','声明字段与冻结文件不一致','无法检查：missing_value','approx','/audit/fpr','0.0104','0.00005','0.25']) assert(text.includes(expected),expected);
  assert(!/verified|已验证|验证通过/i.test(text));
 }finally{clean.forEach(f=>f());globalThis.window=old;}
});

test('CE-27 research_memory declares checks and their limits',async()=>{
 const {registerResearchTools}=await import('../../apps/dsh/plugin/tools.js');
 const tools=registerResearchTools({tools:{register:value=>value}},{});
 const memory=tools.find(t=>t.name==='research_memory');
 const checks=memory.parameters.properties.checks;
 assert.equal(checks.type,'array');assert.equal(checks.items.type,'object');
 assert.deepEqual(Object.keys(checks.items.properties).sort(),['op','path','ref','tolerance','value']);
 assert.match(memory.description,/declared fields.*frozen file/);
 assert.match(memory.description,/does not prove/);
 assert.match(memory.description,/changes\.checks/);
 assert.match(memory.description,/omitting.*inherits/);
});
