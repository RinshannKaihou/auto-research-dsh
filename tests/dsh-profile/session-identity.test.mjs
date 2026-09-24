import {test} from 'node:test';
import assert from 'node:assert/strict';
import {researchSessionLabel,createResearchSessionIdentity} from '../../apps/dsh/plugin/frontend/session-identity.js';
import {createResearchSessionNavigation} from '../../apps/dsh/plugin/frontend/session-navigation.js';
const React={createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)}),useState:v=>[v,()=>{}],useEffect:()=>{}};
const text=tree=>typeof tree==='string'?tree:(tree?.children??[]).map(text).join('');
test('header distinguishes main, execution, discussion and expert, with a direct return',async()=>{
  assert.equal(researchSessionLabel({role:'main'}),'研究主会话');
  assert.equal(researchSessionLabel({role:'node_core',node_id:'X-004'}),'X-004 · 节点执行');
  assert.equal(researchSessionLabel({role:'specialist',node_id:'X-004',label:'review'}),'X-004 · 专家 · review');
  const opened=[],Identity=createResearchSessionIdentity(React,'');
  const tree=Identity({sessionId:'discussion-abcd1234',useReceipt:select=>select({associated:true,identity:{role:'discussion',node_id:'X-004'},mainSessionId:'main',project_root:'/project/demo'}),navigation:{open:async(...a)=>opened.push(a)}});
  assert.match(text(tree),/X-004 · 讨论/);assert.match(text(tree),/demo · abcd1234/);
  await tree.children.find(n=>n?.type==='button').props.onClick();assert.deepEqual(opened,[['main',{mainSessionId:'main'}]]);
});
test('navigation retains role/node identity across the Chat transition, with no cross-session leakage',async()=>{
  const nav=createResearchSessionNavigation({open:()=>{}});
  await nav.open('discussion-a',{identity:{role:'discussion',node_id:'X-004'},mainSessionId:'main'});
  assert.equal(nav.context('discussion-a').identity.node_id,'X-004');assert.equal(nav.context('main').identity,undefined);
});
