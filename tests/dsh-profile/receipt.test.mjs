import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createReceiptState} from '../../apps/dsh/plugin/frontend/receipt-state.js';
import {createResearchReceipt} from '../../apps/dsh/plugin/frontend/receipt.js';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const ok=goal=>({ok:true,value:{project:{goal,control:'manual'},schema_version:5}});
test('repeated successful status has visible sequence; commands and errors stay session-scoped',async()=>{
  const calls=[],state=createReceiptState(async(e,p)=>{calls.push([e,p]);return ok(p.sessionId);},()=> '10:40:27');
  let notifications=0;const off=state.store('A').subscribe(()=>notifications++);
  state.executed('A','research',{kind:'success',text:'same result'});await tick();
  state.executed('A','research',{kind:'success',text:'same result'});await tick();
  assert.equal(state.store('A').getSnapshot().commandSequence,2);assert.equal(notifications,4);
  assert.equal(state.store('B').getSnapshot(),null);
  state.executed('B','research',{kind:'error',text:'not associated'});await tick();
  assert.equal(state.store('B').getSnapshot().command.text,'not associated');
  assert.equal(state.store('A').getSnapshot().command.text,'same result');
  state.executed('A','unrelated',{kind:'success'});assert.equal(calls.length,3);
  assert(calls.every(([e])=>e==='status'));off();
});
test('late status response cannot resurrect association after a newer detach failure',async()=>{
  const replies=[],state=createReceiptState(()=>new Promise(resolve=>replies.push(resolve)));
  const first=state.refresh('A'),second=state.refresh('A');
  replies[1]({ok:false,error:{message:'not associated'}});await second;
  replies[0](ok('stale project'));await first;
  assert.equal(state.store('A').getSnapshot().associated,false);
  assert.equal(state.store('A').getSnapshot().project,undefined);
});
test('RPC failure removes stale badge and a subsequent successful refresh recovers',async()=>{
  let fail=false;const state=createReceiptState(async()=>{if(fail)throw Error('offline');return ok('fixture');});
  await state.refresh('A');fail=true;await state.refresh('A');
  assert.equal(state.store('A').getSnapshot().associated,false);assert.equal(state.store('A').getSnapshot().refreshError,'offline');
  fail=false;await state.refresh('A');assert.equal(state.store('A').getSnapshot().associated,true);
  assert.equal(state.store('A').getSnapshot().refreshError,undefined);
});
function hooks(){
  const values=[],effects=[];let cursor=0;
  const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)}),
    useState(initial){const i=cursor++;if(!(i in values))values[i]=initial;return[values[i],v=>values[i]=v];},
    useRef(initial){const i=cursor++;return values[i]??(values[i]={current:initial});},useEffect:fn=>effects.push(fn)};
  return{React,render:fn=>{cursor=0;return fn();},effects};
}
function find(tree,predicate){if(!tree||typeof tree!=='object')return null;if(predicate(tree))return tree;for(const child of tree.children??[]){const found=find(child,predicate);if(found)return found;}return null;}
test('blank unassociated receipt opens real workbench dialog with no prompt or tab dependency',()=>{
  const host=hooks(),Workbench=()=>{},refreshes=[];
  const Receipt=createResearchReceipt(host.React,Workbench,'fixture styles');
  const props={sessionId:'blank',useReceipt:select=>select(null),refresh:id=>refreshes.push(id)};
  let tree=host.render(()=>Receipt(props));
  const button=find(tree,n=>n.type==='button');assert.equal(button.children[0],'打开研究工作台');
  assert.equal(button.props['aria-haspopup'],'dialog');button.props.onClick();
  tree=host.render(()=>Receipt(props));const component=find(tree,n=>typeof n.type==='function');
  let modalTree=component.type(component.props),shown=0,closed=0;
  const dialog=find(modalTree,n=>n.type==='dialog');
  dialog.props.ref.current={showModal:()=>shown++,close:()=>closed++};
  host.effects.at(-1)();assert.equal(shown,1);
  assert.equal(find(modalTree,n=>n.type===Workbench).props.sessionId,'blank');
  find(modalTree,n=>n.type===Workbench).props.onSessionOpened();assert.equal(closed,1);
  find(modalTree,n=>n.type==='button').props.onClick();assert.equal(closed,2);
  dialog.props.onClose();tree=host.render(()=>Receipt(props));
  assert.equal(find(tree,n=>typeof n.type==='function'),null);assert.deepEqual(refreshes,['blank']);
});
test('receipt displays successful raw result and visible error, including repeat counter',()=>{
  const host=hooks(),Receipt=createResearchReceipt(host.React,()=>{},'');
  for(const kind of ['success','error']){
    const tree=host.render(()=>Receipt({sessionId:'A',refresh:()=>{},useReceipt:select=>select({command:{kind,text:'FULL RESULT'},commandSequence:2,commandTime:'10:40:27'})}));
    assert.equal(find(tree,n=>n.type==='pre').children[0],'FULL RESULT');
    assert.equal(find(tree,n=>n.type==='details').props.open,true);
    assert(find(tree,n=>n.props.role===(kind==='error'?'alert':'status')&&n.children[0].includes('第 2 次')));
  }
});
test('generated client wires per-session hooks and remount keys through public dock API',async()=>{
  const registrations=[],events={},React={createElement:(type,props)=>({type,props})};let entry;
  const source=readFileSync(new URL('../../apps/dsh/plugin/client.js',import.meta.url),'utf8');
  vm.runInNewContext(source,{window:{__ModuleLoader__:{load:value=>entry=value}}});
  entry.factory(()=>React).apply({slots:{entries:()=>[{store:'native-conversation-store'}],subscribe:()=>()=>{},inject:(_name,fn)=>fn(),register:(options,component)=>registrations.push({options,component})},
    connection:{rpc:{call:async()=>ok('fixture')}},sessions:{open:()=>assert.fail('must not open a native session')},on:(name,fn)=>events[name]=fn});
  const dock=registrations.find(r=>r.options.name==='conversation.input.dock');
  assert.equal(dock.options.store,'native-conversation-store');
  const actions={setView:()=>{}};
  const a=dock.options.inject('A',actions),b=dock.options.inject('B',actions);
  assert.equal(a.actions,actions);assert.equal(typeof a.navigation.bind,'function');
  events['command/executed']('A','research',{kind:'success',text:'visible'});await tick();
  assert.equal(a.hooks.receipt.getSnapshot().command.text,'visible');assert.equal(b.hooks.receipt.getSnapshot(),null);
  assert.equal(dock.component(a).props.key,'A');assert.equal(dock.component(b).props.key,'B');
});
test('server RPC failures satisfy DSH failure envelope instead of hiding the domain error',async()=>{
  let handler;const noop=()=>{};
  const ctx={agents:{get:()=>undefined},systemPrompt:{section:()=>noop},tools:{guard:()=>noop},inject:(_deps,fn)=>fn({connection:{rpc:{handle:(_channel,fn)=>handler=fn}}})};
  const source=readFileSync(new URL('../../apps/dsh/plugin/index.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'').replace(/^export /gm,'');
  vm.runInNewContext(source+'\napply(ctx);',{ctx,StorageClient:class {},SessionAdapter:class {},ResearchDomain:class {},registerResearchCommand:()=>noop,registerResearchTools:()=>[],registerResearchEvents:()=>[]});
  const result=await handler('status',{sessionId:'unattached'});
  assert.equal(result.ok,false);assert.equal(result.error.message,'The native session is not currently attached');
  assert.equal(typeof result.error.code,'string');assert.equal(typeof result.error.details,'object');
  assert.notEqual(result.error.details,null);
});

test('navigation dock waits for the native conversation store to register',()=>{
  let entry,store;const changes=[],registrations=[];
  const source=readFileSync(new URL('../../apps/dsh/plugin/client.js',import.meta.url),'utf8');
  vm.runInNewContext(source,{window:{__ModuleLoader__:{load:value=>entry=value}}});
  entry.factory(()=>({})).apply({slots:{entries:()=>store?[{store}]:[],subscribe:(_name,fn)=>{changes.push(fn);return()=>{};},inject:(_name,fn)=>fn(),register:options=>{registrations.push(options);return()=>{};}},
    connection:{rpc:{call:async()=>ok('fixture')}},sessions:{},on:()=>{}});
  assert(!registrations.some(options=>options.name==='conversation.input.dock'));
  store={create:()=>{}};changes.forEach(fn=>fn());
  const docks=registrations.filter(options=>options.name==='conversation.input.dock');
  assert.equal(docks.length,1);assert.equal(docks[0].store,store);
  changes.forEach(fn=>fn());assert.equal(registrations.filter(options=>options.name==='conversation.input.dock').length,1);
});

test('current identity is read from durable session when runtime is paginated and is isolated across sessions',async()=>{
  const state=createReceiptState(async(_e,p)=>({ok:true,value:{runtime:{main_session_id:'main',sessions:[]},workflow:{session:{session_id:p.sessionId,role:p.sessionId==='main'?'main':'discussion',node_id:p.sessionId==='main'?null:'X-004'}}}}));
  await state.refresh('discussion-a');await state.refresh('main');
  assert.equal(state.store('discussion-a').getSnapshot().identity.role,'discussion');
  assert.equal(state.store('discussion-a').getSnapshot().identity.node_id,'X-004');
  assert.equal(state.store('main').getSnapshot().identity.role,'main');
});

test('completed expert reads through parent while retaining expert identity, never parent current state',async()=>{
  const calls=[],state=createReceiptState(async(e,p)=>{calls.push(p.sessionId);return {ok:true,value:{runtime:{main_session_id:'main',current:{session_id:'main',native_status:'running'},sessions:[{session_id:'parent',role:'node_core',node_id:'X-004'}]},workflow:{session:{session_id:'main',role:'main'}}}};},undefined,()=>({parentSessionId:'parent',mainSessionId:'main'}));
  await state.refresh('expert');const value=state.store('expert').getSnapshot();
  assert.deepEqual(calls,['main']);assert.equal(value.identity.role,'specialist');assert.equal(value.identity.node_id,'X-004');assert.equal(value.runtime.current,undefined);
});

test('successful command result has direct target navigation and composer names the recipient',async()=>{
  const host=hooks(),opened=[],Receipt=createResearchReceipt(host.React,()=>{},'');
  const tree=host.render(()=>Receipt({sessionId:'discussion-a',refresh:()=>{},navigation:{prepareDiscussion:async()=>{},open:async(...args)=>opened.push(args)},useReceipt:select=>select({associated:true,identity:{role:'discussion',node_id:'X-004'},mainSessionId:'main',command:{kind:'success',text:JSON.stringify({sessionId:'discussion-b'})}})}));
  assert.equal(find(tree,n=>n.type==='strong').children[0],'当前输入：X-004 · 讨论');
  await find(tree,n=>n.type==='button'&&n.children[0]==='打开目标会话').props.onClick();
  assert.deepEqual(opened,[['discussion-b',{mainSessionId:'main'}]]);
});
