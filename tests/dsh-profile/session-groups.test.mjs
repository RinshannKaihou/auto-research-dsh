import {test} from 'node:test';
import assert from 'node:assert/strict';
import {researchSessionGroups,researchExpertTitle,researchExpertCounts} from '../../apps/dsh/plugin/frontend/session-groups.js';

test('experts nest under exact parent across retries; node identity never overrides a known parent',()=>{
  const rows=[{session_id:'old',role:'node_core',node_id:'X-1'},{session_id:'new',role:'node_core',node_id:'X-1'},
    {session_id:'a',role:'specialist',node_id:'X-1',parent_session_id:'old'},
    {session_id:'b',role:'specialist',node_id:'X-1',parent_session_id:'new'},
    {session_id:'c',role:'specialist',node_id:'X-1',parent_session_id:'not-loaded'}];
  const groups=researchSessionGroups(rows);
  assert.equal(groups.length,3);
  assert.deepEqual(groups.map(g=>g.children.map(c=>c.session_id)),[['a'],['b'],['c']]);
  assert.equal(groups[2].parent,null);
});
test('incomplete legacy pages use unique node context but do not invent a native parent address',()=>{
  const child={session_id:'a',role:'specialist',node_id:'X-1'};
  const parent={session_id:'node',role:'node_core',node_id:'X-1'};
  const [group]=researchSessionGroups([child],[parent,child]);
  assert.equal(group.parent.session_id,'node');assert.equal(group.parent.contextOnly,true);
  assert.equal(group.children[0].parent_session_id,undefined);
  const ambiguous=researchSessionGroups([child],[parent,{...parent,session_id:'another'},child]);
  assert.equal(ambiguous[0].parent,null);
});
test('main specialists and orphaned specialists remain reachable without becoming peer rows',()=>{
  const rows=[{session_id:'main',role:'main'},{session_id:'a',role:'specialist',parent_session_id:'main'},
    {session_id:'b',role:'specialist'},{session_id:'c',role:'specialist'}];
  const groups=researchSessionGroups(rows);
  assert.equal(groups.length,2);assert.equal(groups[0].children.length,1);assert.equal(groups[1].children.length,2);
});
test('expert titles are bounded, distinguishable and literal; collapsed counts retain actionable state',()=>{
  assert.equal(researchExpertTitle({task_id:'S-1',purpose:{preview:'核对公式\n以及引用'}}),'S-1 · 核对公式 以及引用');
  assert.equal(researchExpertTitle({task_id:'S-2',label:'<script>x</script>',purpose:'Details'}),'S-2 · <script>x</script>');
  assert(researchExpertTitle({task_id:'S-3',purpose:'x'.repeat(2000)}).length<90);
  assert.notEqual(researchExpertTitle({session_id:'session-aaaa'}),researchExpertTitle({session_id:'session-bbbb'}));
  assert.equal(researchExpertCounts([{native_status:'running',pending_approvals:1},{specialist_state:'unverified'},{specialist_state:'cancelled'}]),'专家 3 · 1 运行中 · 2 待处理');
});
