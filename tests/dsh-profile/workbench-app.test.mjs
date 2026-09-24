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
    else if(endpoint==='reference.get')value={kind:payload.ref.startsWith('knowledge/')?'knowledge':'node',value:payload.ref.startsWith('knowledge/')?
      {knowledge_id:'K-010',statement:payload.ref.endsWith('@1')?'old statement':'new statement',evidence_refs:['pub/P-001#report'],supersedes:[]}:
      {node_id:'X-001',question:'A question',plan:'Long plan',inputs:[],status:'open'}};
    else if(endpoint==='reference.content')value={kind:'text',ref:payload.ref,resolution:{outcome:'resolved',integrity:'verified',source_path:'FINAL_REPORT.md'},chunk:btoa('# Final report\n'),next_offset:null,total_bytes:15,truncated:false};
    else value={message:'ok'};
    return {ok:true,value};
  };
}
async function setup() {
  const original=globalThis.document,raf=globalThis.requestAnimationFrame,oldTimer=globalThis.setTimeout,oldClear=globalThis.clearTimeout;
  globalThis.document={hidden:false,activeElement:{focus(){}},addEventListener(){},removeEventListener(){}};
  globalThis.requestAnimationFrame=callback=>callback();
  globalThis.setTimeout=(callback,delay,...args)=>delay===3000?{poll:true}:oldTimer(callback,delay,...args);
  globalThis.clearTimeout=timer=>{if(!timer?.poll)oldClear(timer);};
  const environment=host(),calls=[],Workbench=createResearchWorkbench(environment.React,fixtureRpc(calls),()=>{},projectGraph,()=>{},null,'');
  let tree=environment.render(()=>Workbench({sessionId:'main'}));await environment.flush();
  tree=environment.render(()=>Workbench({sessionId:'main'}));await environment.flush();
  tree=environment.render(()=>Workbench({sessionId:'main'}));
  return {calls,environment,Workbench,tree,rerender:async()=>{const current=environment.render(()=>Workbench({sessionId:'main'}));await environment.flush();return environment.render(()=>Workbench({sessionId:'main'}))},
    close:()=>{environment.close();globalThis.document=original;globalThis.requestAnimationFrame=raf;globalThis.setTimeout=oldTimer;globalThis.clearTimeout=oldClear;}};
}

test('overview is the first view and does not request every history collection',async()=>{
  const app=await setup();try {
    assert.match(content(app.tree),/Readable project/);
    assert.match(content(app.tree),/Adopted result/);
    assert(button(app.tree,'阅读最终报告'));
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
    await button(app.tree,'阅读最终报告').props.onClick();
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
