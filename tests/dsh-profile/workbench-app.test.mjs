import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createResearchWorkbench} from '../../apps/dsh/plugin/frontend/workbench-app.js';
import {projectGraph} from '../../apps/dsh/plugin/frontend/model.js';
import {renderResearchMarkdown} from '../../apps/dsh/plugin/frontend/markdown.js';

function host() {
  const slots=[],pending=[];let cursor=0;
  const same=(a,b)=>a?.length===b?.length&&a.every((value,index)=>Object.is(value,b[index]));
  const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)}),
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},
    useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},
    useCallback(fn){cursor++;return fn;},
    useEffect(fn,deps){const i=cursor++,prior=slots[i];if(!prior||!same(prior.deps,deps))pending.push(()=>{prior?.cleanup?.();slots[i]={deps,cleanup:fn()};});},
  };
  return {React,render:component=>{cursor=0;return component();},flush:async()=>{for(const effect of pending.splice(0))effect();await new Promise(resolve=>setImmediate(resolve));},
    close:()=>{for(const slot of slots)slot?.cleanup?.();}};
}
function find(tree,predicate){if(!tree||typeof tree!=='object')return null;if(predicate(tree))return tree;for(const child of tree.children??[]){const hit=find(child,predicate);if(hit)return hit;}return null;}
function content(tree){if(typeof tree==='string'||typeof tree==='number')return String(tree);if(!tree||typeof tree!=='object')return '';return(tree.children??[]).map(content).join('');}
function button(tree,title){return find(tree,node=>node.type==='button'&&content(node)===title);}
const summary={schema_version:9,revision:1,project:{project_id:'fixture',goal:'Long scientific goal',control:'manual'},
  counts:{nodes:1,knowledge:2,publications:1},review_queue:{pending_total:3},final_publication:{publication_id:'P-001',summary:'Final report',status:'complete'},
  runtime:{state:'complete',running_count:0,pending_approvals:0,approvals:[],sessions:[]}};
const presentation={status:'ready',value:{title:'Readable project',summary:'Adopted result.',featured_node_id:'X-001',
  nodes:{'X-001':{title:'Winner',summary:'Measured outcome',outcome:'Adopted'}},
  report_ref:'pub/P-001#report',deliverables:[{label:'Package',ref:'pub/P-001#report'}],
  metrics:[{id:'f1',label:'Audit F1',split:'audit',baseline:{ref:'pub/P-001#metrics',pointer:'/before',value:0.4},
    current:{ref:'pub/P-001#metrics',pointer:'/after',value:0.8}}]}};

function fixtureRpc(calls) {
  return async (endpoint,payload)=>{calls.push({endpoint,payload});let value;
    if(endpoint==='workbench.summary')value=summary;
    else if(endpoint==='presentation.get')value=presentation;
    else if(endpoint==='workbench.page')value={items:payload.collection==='nodes'?[{node_id:'X-001',question:'A question',status:'open',strategy:'continue'}]:[],total:payload.collection==='nodes'?1:0,cursor:null};
    else if(endpoint==='knowledge.page')value=payload.history?
      {items:[{ref:'knowledge/K-010@1'},{ref:'knowledge/K-010@2'}],total:2,cursor:null}:
      {items:[{ref:'knowledge/K-010@2',knowledge_id:'K-010',revision:2,kind:'lesson',status:'working',statement:'new statement',evidence_refs:['pub/P-001#report']}],total:1,cursor:null};
    else if(endpoint==='reference.get'&&payload.ref==='pub/P-001')value={kind:'publication',value:{...summary.final_publication,items:[{item_id:'report',ref:'pub/P-001#report',kind:'report',source_path:'FINAL_REPORT.md',object_kind:'file'}]}};
    else if(endpoint==='reference.get')value={kind:payload.ref.startsWith('knowledge/')?'knowledge':'node',value:payload.ref.startsWith('knowledge/')?
      {knowledge_id:'K-010',statement:payload.ref.endsWith('@1')?'old statement':'new statement',evidence_refs:['pub/P-001#report'],supersedes:[]}:
      {node_id:'X-001',question:'A question',plan:'Long plan',inputs:[],status:'open'}};
    else if(endpoint==='reference.content')value={kind:'text',ref:payload.ref,resolution:{outcome:'resolved',integrity:'verified',source_path:'FINAL_REPORT.md'},chunk:btoa('# Final report\n'),next_offset:null,total_bytes:15,truncated:false};
    else value={message:'ok'};
    return {ok:true,value};
  };
}
async function setup({rpc:overrideRpc,openSession=()=>{},onSessionOpened,sessionContext}={}) {
  const original=globalThis.document,raf=globalThis.requestAnimationFrame,oldTimer=globalThis.setTimeout,oldClear=globalThis.clearTimeout;
  globalThis.document={hidden:false,activeElement:{focus(){}},addEventListener(){},removeEventListener(){}};
  globalThis.requestAnimationFrame=callback=>callback();
  globalThis.setTimeout=(callback,delay,...args)=>delay===3000?{poll:true}:oldTimer(callback,delay,...args);
  globalThis.clearTimeout=timer=>{if(!timer?.poll)oldClear(timer);};
  const environment=host(),calls=[],Workbench=createResearchWorkbench(environment.React,overrideRpc??fixtureRpc(calls),openSession,projectGraph,()=>{},null,'',undefined,sessionContext);
  let tree=environment.render(()=>Workbench({sessionId:'main',onSessionOpened}));await environment.flush();
  tree=environment.render(()=>Workbench({sessionId:'main',onSessionOpened}));await environment.flush();
  tree=environment.render(()=>Workbench({sessionId:'main',onSessionOpened}));
  return {calls,environment,Workbench,tree,rerender:async()=>{const current=environment.render(()=>Workbench({sessionId:'main',onSessionOpened}));await environment.flush();return environment.render(()=>Workbench({sessionId:'main',onSessionOpened}))},
    close:()=>{environment.close();globalThis.document=original;globalThis.requestAnimationFrame=raf;globalThis.setTimeout=oldTimer;globalThis.clearTimeout=oldClear;}};
}

test('overview is the first view and does not request every history collection',async()=>{
  const app=await setup();try {
    assert.match(content(app.tree),/Readable project/);
    assert.match(content(app.tree),/Adopted result/);
    assert(button(app.tree,'阅读报告'));
    assert(!app.calls.some(call=>call.endpoint==='context.preview'||call.endpoint==='history.page'));
    assert(app.calls.filter(call=>call.endpoint==='workbench.page').length<=1);
    await button(app.tree,'研究过程').props.onClick();
    const process=await app.rerender();
    assert.match(content(process),/Winner/);
    assert(app.calls.some(call=>call.endpoint==='workbench.page'&&call.payload.collection==='nodes'));
  }finally{app.close();}
});

test('report opens readable frozen text without a model or ledger write',async()=>{
  const app=await setup();try {
    await button(app.tree,'阅读报告').props.onClick();
    const reader=await app.rerender();
    assert(find(reader,node=>node.props.role==='dialog'&&node.props['aria-label']==='研究材料'));
    assert.match(content(reader),/Final report/);
    assert(find(reader,node=>node.type==='h1'&&content(node)==='Final report'));
    assert(app.calls.some(call=>call.endpoint==='reference.content'&&call.payload.ref==='pub/P-001#report'));
    assert(!app.calls.some(call=>['auto','resume','publish','model'].includes(call.endpoint)));
  }finally{app.close();}
});

test('knowledge view opens exact revision and its older version',async()=>{
  const app=await setup();try {
    button(app.tree,'成果与知识').props.onClick();
    let view=await app.rerender();
    assert.match(content(view),/knowledge\/K-010@2/);
    const row=find(view,node=>node.type==='button'&&node.props.className==='ari-knowledge-row');
    await row.props.onClick();view=await app.rerender();
    assert(app.calls.some(call=>call.endpoint==='reference.get'&&call.payload.ref==='knowledge/K-010@2'));
    const older=button(view,'knowledge/K-010@1');assert(older);
    await older.props.onClick();view=await app.rerender();
    assert.match(content(view),/与现行版本/);
    assert(app.calls.some(call=>call.endpoint==='reference.get'&&call.payload.ref==='knowledge/K-010@1'));
  }finally{app.close();}
});

test('frozen Markdown report renders headings and comparison tables as elements',()=>{
  const React={createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)})};
  const tree=renderResearchMarkdown(React,'# Final report\n\n| Baseline | Winner |\n|---|---|\n| 0.40115 | **0.78669** |');
  assert(find(tree,node=>node.type==='h1'&&content(node)==='Final report'));
  assert(find(tree,node=>node.type==='table'));
  assert(find(tree,node=>node.type==='strong'&&content(node)==='0.78669'));
});

test('runtime rows use live roles/status and explain task waits; navigation failures are visible',async()=>{
  const opened=[];let closed=0,fail=false;
  const rpc=async(endpoint,payload)=>{
    if(endpoint==='workbench.summary')return {ok:true,value:{...summary,runtime:{state:'running',running_count:1,pending_approvals:0,sessions:[{session_id:'main',role:'main',pause_reason:'wait',waiting:['T-1'],native_status:'idle'},{session_id:'node',role:'node_core',node_id:'X-001',native_status:'running',turn:2}]}}};
    if(endpoint==='workbench.page')return {ok:true,value:{items:payload.collection==='sessions'?[{session_id:'main'},{session_id:'node'}]:payload.collection==='tasks'?[{task_id:'T-1',node_id:'X-001',state:'running'}]:[],total:2}};
    return fixtureRpc([])(endpoint,payload);
  };
  const app=await setup({rpc,openSession:async id=>{if(fail)throw Error('catalog unavailable');opened.push(id);},onSessionOpened:()=>closed++});
  try {
    button(app.tree,'运行与维护').props.onClick();let tree=await app.rerender();
    assert.match(content(tree),/研究主会话 · 等待研究任务/);
    assert.match(content(tree),/正在等待：X-001/);
    assert.match(content(tree),/研究节点 · X-001 · 运行中/);
    await button(tree,'打开会话').props.onClick();assert.deepEqual(opened,['main']);assert.equal(closed,1);
    fail=true;await button(tree,'打开会话').props.onClick();tree=await app.rerender();
    assert.match(content(tree),/无法打开原生会话：catalog unavailable/);assert.equal(closed,1);
  }finally{app.close();}
});


test('unattached specialist rows resolve the durable parent without a live runtime entry',async()=>{
  const opened=[];
  const base=fixtureRpc([]);
  const app=await setup({openSession:(id,options)=>opened.push({id,options}),rpc:async(endpoint,payload)=>{
    if(endpoint==='workbench.summary')return {ok:true,value:{...summary,runtime:{...summary.runtime,state:'running',main_session_id:'main',sessions:[]}}};
    if(endpoint==='workbench.page'&&payload.collection==='sessions')return {ok:true,value:{items:[{session_id:'ended',role:'specialist'}],cursor:null,total:1}};
    if(endpoint==='workbench.page'&&payload.collection==='specialists')return {ok:true,value:{items:[{child_session_id:'ended',parent_session_id:'node',state:'cancelled'}],cursor:null,total:1}};
    return base(endpoint,payload);
  }});
  try {
    button(app.tree,'运行与维护').props.onClick();const view=await app.rerender();
    assert.match(content(view),/已取消/);
    await button(view,'打开会话').props.onClick();
    assert.equal(opened[0].options.identity.role,'specialist');
    assert.deepEqual(opened.map(({id,options:{identity,...options}})=>({id,options})),[{id:'ended',options:{parentSessionId:'node',mainSessionId:'main'}}]);
  }finally{app.close();}
});
test('completed child Research reads through its parent and disables project mutations',async()=>{
  const opened=[],calls=[],base=fixtureRpc(calls);
  const app=await setup({sessionContext:()=>({parentSessionId:'parent'}),openSession:(id)=>opened.push(id),rpc:async(endpoint,payload)=>endpoint==='workbench.summary'?(calls.push({endpoint,payload}),{ok:true,value:{...summary,runtime:{...summary.runtime,state:'running'}}}):base(endpoint,payload)});
  try {
    assert.match(content(app.tree),/只读/);
    assert(calls.length>0&&calls.every(call=>call.payload.sessionId==='parent'));
    assert(!button(app.tree,'关联已有项目'));
    button(app.tree,'运行与维护').props.onClick();const runtime=await app.rerender();
    assert.equal(button(runtime,'暂停研究').props.disabled,true);
    await button(runtime,'暂停研究').props.onClick();assert(!calls.some(call=>call.endpoint==='pause'));
    await button(app.tree,'返回父会话').props.onClick();assert.deepEqual(opened,['parent']);
  }finally{app.close();}
});
test('child Research retains an escape when both child and project are unavailable',async()=>{
  const opened=[];
  const app=await setup({sessionContext:()=>({parentSessionId:'parent'}),openSession:id=>opened.push(id),rpc:async()=>({ok:false,error:{message:'The native session is not currently attached'}})});
  try {
    assert.match(content(app.tree),/暂时无法读取/);
    assert(!button(app.tree,'新建并关联'));assert(!button(app.tree,'关联已有项目'));
    await button(app.tree,'返回父会话').props.onClick();assert.deepEqual(opened,['parent']);
  }finally{app.close();}
});


test('legacy compact specialist pages expand the durable detail on demand',async()=>{
  const opened=[],calls=[],base=fixtureRpc(calls);
  const app=await setup({openSession:(id,options)=>opened.push({id,options}),rpc:async(endpoint,payload)=>{
    if(endpoint==='workbench.summary')return {ok:true,value:{...summary,runtime:{...summary.runtime,state:'running',main_session_id:'main',sessions:[]}}};
    if(endpoint==='workbench.page'&&payload.collection==='sessions')return {ok:true,value:{items:[{session_id:'ended',role:'specialist'}],cursor:null,total:1}};
    if(endpoint==='workbench.page'&&payload.collection==='specialists')return {ok:true,value:{items:[{task_id:'S-legacy',child_session_id:'ended',state:'cancelled'}],cursor:null,total:1}};
    if(endpoint==='reference.get'&&payload.ref==='S-legacy'){calls.push({endpoint,payload});return {ok:true,value:{kind:'specialist-task',value:{parent_session_id:'node'}}};}
    return base(endpoint,payload);
  }});
  try {
    button(app.tree,'运行与维护').props.onClick();const view=await app.rerender();
    await button(view,'打开会话').props.onClick();
    assert.equal(opened[0].options.identity.role,'specialist');
    assert.deepEqual(opened.map(({id,options:{identity,...options}})=>({id,options})),[{id:'ended',options:{parentSessionId:'node',mainSessionId:'main'}}]);
    assert.equal(calls.filter(call=>call.endpoint==='reference.get'&&call.payload.ref==='S-legacy').length,1);
  }finally{app.close();}
});

test('publication expansion reads full records rather than compact page previews and preserves raw text',async()=>{
  const original='### '+('Part A — scientific content; '.repeat(80)),calls=[],base=fixtureRpc(calls);
  const app=await setup({rpc:async(endpoint,payload)=>{
    if(endpoint==='reference.get'&&payload.ref==='pub/P-001'){calls.push({endpoint,payload});return {ok:true,value:{kind:'publication',value:{...summary.final_publication,summary:original,items:[]}}};}
    return base(endpoint,payload);
  }});
  try {
    assert(!find(app.tree,n=>n.type==='h3'&&content(n).includes('scientific content')));
    await button(app.tree,'展开完整发布说明').props.onClick();let view=await app.rerender();
    assert.match(content(view),/Part A — scientific content/);
    await button(view,'查看原文').props.onClick();view=await app.rerender();
    assert.equal(content(find(view,n=>n.type==='pre'&&n.props.className==='ari-publication-raw')),original);
    assert.equal(calls.filter(c=>c.endpoint==='reference.get'&&c.payload.ref==='pub/P-001').length,1);
    await button(view,'收起发布说明').props.onClick();view=await app.rerender();
    assert(!button(view,'查看原文'));assert(button(view,'展开完整发布说明'));
  }finally{app.close();}
});

test('truncated summaries use chunk transport; old hosts show an explicit error instead of fake full text',async()=>{
  const calls=[],base=fixtureRpc(calls),full={kind:'publication',value:{...summary.final_publication,summary:'尾部完整文本🙂',items:[]}};
  const encoded=Buffer.from(JSON.stringify(full)),chunks=[encoded.subarray(0,encoded.length-3),encoded.subarray(encoded.length-3)];let chunkCalls=0;
  const app=await setup({rpc:async(endpoint,payload)=>{
    if(endpoint==='reference.get'&&payload.ref==='pub/P-001')return {ok:true,value:{...full,value:{...full.value,summary:{truncated:true,preview:'前缀'}}}};
    if(endpoint==='reference.chunk'){const index=chunkCalls++;return {ok:true,value:{chunk:chunks[index].toString('base64'),next_offset:index===0?encoded.length-3:null}};}
    return base(endpoint,payload);
  }});
  try {await button(app.tree,'展开完整发布说明').props.onClick();const view=await app.rerender();assert.match(content(view),/尾部完整文本🙂/);assert.equal(chunkCalls,2);}finally{app.close();}
  const old=await setup({rpc:async(endpoint,payload)=>{
    if(endpoint==='reference.get'&&payload.ref==='pub/P-001')return {ok:true,value:{...full,value:{...full.value,summary:{truncated:true,preview:'前缀'}}}};
    if(endpoint==='reference.chunk')return {ok:false,error:{message:'Unknown RPC'}};
    return base(endpoint,payload);
  }});
  try {await button(old.tree,'展开完整发布说明').props.onClick();const view=await old.rerender();assert.match(content(view),/仅返回截断预览/);assert(!button(view,'查看原文'));}finally{old.close();}
});

test('node discussion opens its own session from timeline and detail, with reuse/fresh and duplicate-click guard',async()=>{
  const calls=[],opened=[];let release,closed=0;
  const rpc=async(endpoint,payload)=>{
    calls.push({endpoint,payload});
    if(endpoint==='discussion.open'){await new Promise(resolve=>release=resolve);return {ok:true,value:{sessionId:'discussion-1'}};}
    if(endpoint==='workbench.summary')return {ok:true,value:{...summary,runtime:{...summary.runtime,main_session_id:'main'}}};
    return fixtureRpc([])(endpoint,payload);
  };
  const app=await setup({rpc,openSession:async(id,options)=>opened.push({id,options}),onSessionOpened:()=>closed++});
  try {
    button(app.tree,'研究过程').props.onClick();let tree=await app.rerender();
    const start=button(tree,'讨论进展');assert(start);assert.equal(start.props['aria-label'],'讨论 X-001 的进展');
    const first=start.props.onClick();await start.props.onClick();
    assert.equal(calls.filter(c=>c.endpoint==='discussion.open').length,1);
    tree=await app.rerender();assert.equal(button(tree,'讨论进展').props.disabled,true);
    release();await first;
    assert.equal(opened[0].id,'discussion-1');assert.equal(opened[0].options.identity.node_id,'X-001');
    assert.equal(opened[0].options.identity.role,'discussion');assert.equal(opened[0].options.mainSessionId,'main');assert.equal(closed,1);
    tree=await app.rerender();await find(tree,n=>n.props.className==='ari-step').props.onClick();tree=await app.rerender();
    assert(button(tree,'围绕此节点讨论'));const fresh=button(tree,'新建另一场讨论').props.onClick();
    assert.equal(calls.filter(c=>c.endpoint==='discussion.open').at(-1).payload.fresh,true);
    release();await fresh;
  }finally{app.close();}
});

test('discussion creation failure stays on the node and displays the error',async()=>{
  const rpc=async(e,p)=>e==='discussion.open'?{ok:false,error:{message:'cannot prepare node'}}:fixtureRpc([])(e,p);
  const app=await setup({rpc,openSession:()=>assert.fail('must not leave on error')});
  try{button(app.tree,'研究过程').props.onClick();let tree=await app.rerender();await button(tree,'讨论进展').props.onClick();tree=await app.rerender();assert.match(content(tree),/cannot prepare node/);assert.equal(button(tree,'讨论进展').props.disabled,false);}finally{app.close();}
});

test('project sessions collapse experts under their node and hydrate legacy task titles on expansion',async()=>{
  const calls=[],opened=[],base=fixtureRpc(calls);
  const app=await setup({openSession:(id,options)=>opened.push({id,options}),rpc:async(endpoint,payload)=>{
    calls.push({endpoint,payload});
    if(endpoint==='workbench.summary')return {ok:true,value:{...summary,runtime:{state:'running',main_session_id:'main',sessions:[]}}};
    if(endpoint==='workbench.page'&&payload.collection==='sessions')return {ok:true,value:{items:[
      {session_id:'node',role:'node_core',node_id:'X-001'},
      {session_id:'expert-one',role:'specialist',node_id:'X-001'},
      {session_id:'expert-two',role:'specialist',node_id:'X-001'}],total:3,cursor:null}};
    if(endpoint==='workbench.page'&&payload.collection==='specialists')return {ok:true,value:{items:payload.nodeId?[
      {task_id:'S-021',child_session_id:'expert-one',parent_session_id:'node',node_id:'X-001',purpose:'domain',state:'completed'},
      {task_id:'S-022',child_session_id:'expert-two',parent_session_id:'node',node_id:'X-001',purpose:'domain',state:'cancelled'}]:[],total:2,cursor:null}};
    if(endpoint==='reference.get'&&['S-021','S-022'].includes(payload.ref))return {ok:true,value:{kind:'specialist',value:{task_id:payload.ref,purpose:'domain',label:payload.ref==='S-021'?'核对文献公式':'检查实验假设'}}};
    return base(endpoint,payload);
  }});
  try {
    button(app.tree,'运行与维护').props.onClick();let tree=await app.rerender();
    let panel=find(tree,n=>n.type==='section'&&n.children.some(c=>c?.props?.className==='ari-section-head'&&content(c).startsWith('项目会话')));
    let roots=panel.children.filter(c=>c?.type==='details');assert.equal(roots.length,1);assert(!roots[0].props.open);
    assert.match(content(roots[0].children[0]),/专家 2/);
    assert(!calls.some(c=>c.endpoint==='workbench.page'&&c.payload.nodeId==='X-001'));
    const element={open:true};await roots[0].props.onToggle({target:element,currentTarget:element});tree=await app.rerender();
    panel=find(tree,n=>n.type==='section'&&n.children.some(c=>c?.props?.className==='ari-section-head'&&content(c).startsWith('项目会话')));
    const children=find(panel,n=>n.props.className==='ari-session-children');assert(children);
    assert.match(content(children),/S-021 · 核对文献公式/);assert.match(content(children),/S-022 · 检查实验假设/);
    await button(children,'打开会话').props.onClick();assert.equal(opened[0].id,'expert-one');assert.equal(opened[0].options.parentSessionId,'node');assert.equal(opened[0].options.mainSessionId,'main');
    assert(!calls.some(c=>['publish','resume','auto'].includes(c.endpoint)));
    const duplicate=find(tree,n=>n.type==='details'&&n.props.className==='ari-panel'&&content(n).startsWith('节点内部协作'));assert(duplicate);assert(!duplicate.props.open);
  }finally{app.close();}
});
