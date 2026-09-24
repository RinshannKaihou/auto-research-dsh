import {test} from 'node:test';
import assert from 'node:assert/strict';
import {projectGraph} from '../../apps/dsh/plugin/frontend/model.js';
import {layoutGraph,inViewport,clampZoom} from '../../apps/dsh/plugin/frontend/layout.js';
const node=(node_id,extra={})=>({node_id,question:node_id,status:'open',created_at:node_id,inputs:[],...extra});
function fixture(){return {nodes:[node('A'),node('B',{inputs:['pub/P1#x','pub/P2#x','old/x','pub/P0#x','pub/MISSING#x'],anchor_ref:'S1'}),node('C')],
 attempts:[{attempt_id:'T1',node_id:'A'},{attempt_id:'T0',node_id:null}],
 publications:[{publication_id:'P1',attempt_id:'T1',items:[{item_id:'x'}]},{publication_id:'P2',node_id:'A',items:[{item_id:'x'}]},{publication_id:'P0',attempt_id:'T0',items:[{item_id:'x'}]}],
 snapshots:[{snapshot_id:'S1',attempt_id:'T1'}],legacy_refs:[{ref:'old/x',node_id:'A'}],relations:[{relation_id:'R1',source_ref:'A',target_ref:'B',label:'supports'},{relation_id:'R2',source_ref:'B',target_ref:'A',label:'revisits'},{relation_id:'R3',source_ref:'A',target_ref:'A',label:'same'}]};}
test('versions, anchors, legacy refs, cycles and planning retain exact attribution',()=>{
 const m=projectGraph(fixture());assert.equal(m.edges.length,2);assert.equal(m.edges.find(e=>e.source==='A').records.length,5);
 assert.equal(m.nodes[1].references.length,6);assert.equal(m.nodes[0].publications.length,2);assert.equal(m.nodes[0].snapshots.length,1);
 assert.equal(m.planning.publications[0].publication_id,'P0');assert.equal(m.planning.attempts[0].attempt_id,'T0');
 assert.equal(m.resolve('pub/P1#nonexistent'),null);assert.equal(m.resolve('pub/P0#x'),null);assert.equal(m.nodes[0].relations.length,3);
 assert.equal(m.edges.some(e=>e.source==='C'||e.target==='C'),false);
});
test('direct ownership wins, dangling ownership never falls back to an unrelated node',()=>{
 const s=fixture();s.publications.push({publication_id:'P3',node_id:'B',attempt_id:'T1',items:[]},{publication_id:'P4',node_id:'missing',attempt_id:'T1',items:[]});
 const m=projectGraph(s);assert.equal(m.resolve('pub/P3'),'B');assert.equal(m.resolve('pub/P4'),null);
});
test('missing endpoints and self references remain readable without invented edges',()=>{
 const s=fixture();s.nodes[0].inputs=['pub/P1#x'];s.relations.push({source_ref:'pub/P0#x',target_ref:'B',label:'context'});
 const m=projectGraph(s);assert.equal(m.edges.length,2);assert.equal(m.planning.relations.length,1);assert.equal(m.nodes[1].relations.length,3);
});
test('typed predecessors render directly and zero-edge nodes retain origin semantics',()=>{
 const s={nodes:[node('R',{origin_kind:'root',root_reason:'independent baseline'}),node('D',{origin_kind:'derived'}),node('L',{origin_kind:'legacy_unresolved'}),node('V',{origin_kind:'derived'})],dependencies:[{dependency_id:'D-1',predecessor_node_id:'R',successor_node_id:'D',relation_type:'depends_on',scheduling:true,input_refs:['knowledge/K@1']}],relations:[]};
 const m=projectGraph(s);
 assert.equal(m.edges.length,1);assert.equal(m.edges[0].records[0].kind,'dependency');
 assert.deepEqual(Object.fromEntries(m.nodes.map(n=>[n.node_id,n.origin_class])),{R:'explicit_root',D:'derived',L:'legacy_unresolved',V:'protocol_violation'});
});
test('lineage corrections remain distinct from original root declarations',()=>{
 const s={nodes:[node('A',{origin_kind:'root',root_reason:'original'}),node('B',{origin_kind:'root',root_reason:'original'})],relations:[{relation_id:'R-fix',source_ref:'A',target_ref:'B',label:'lineage_correction',note:'post-hoc correction'}]};
 const m=projectGraph(s),b=m.nodes.find(n=>n.node_id==='B');
 assert.equal(b.lineage_corrected,true);assert.equal(b.origin_class,'lineage_corrected_root');
 assert.equal(m.edges[0].records[0].label,'lineage_correction');assert.equal(b.origin_kind,'root');
});
test('SCC layout is stable, non-overlapping, and preserves disconnected nodes',()=>{
 const m=projectGraph(fixture()),l=layoutGraph(m.nodes,m.edges),repeat=layoutGraph(m.nodes,[...m.edges].reverse());
 assert.deepEqual(l,repeat);assert.equal(l.positions.size,3);assert.notDeepEqual(l.positions.get('A'),l.positions.get('B'));assert(l.positions.get('C').y>l.positions.get('B').y);
 assert.equal(projectGraph({...fixture(),notes:[{body:'new note'}]}).structuralKey,m.structuralKey);
});
test('200 nodes / 500 edges scale fixture, zoom bounds and viewport clipping',()=>{
 const s={nodes:Array.from({length:200},(_,i)=>node(`X${String(i).padStart(3,'0')}`)),relations:Array.from({length:500},(_,i)=>({source_ref:`X${String(i%200).padStart(3,'0')}`,target_ref:`X${String((i%200+1+Math.floor(i/200))%200).padStart(3,'0')}`,label:'test'}))};
 const m=projectGraph(s),l=layoutGraph(m.nodes,m.edges);assert.equal(m.edges.length,500);assert.equal(l.positions.size,200);
 const positions=[...l.positions.values()];assert.equal(new Set(positions.map(p=>`${p.x},${p.y}`)).size,200);
 assert.equal(clampZoom(100),2.5);assert.equal(clampZoom(0),.12);
 assert.equal(inViewport({x:0,y:0},{x:0,y:0,k:1},{width:600,height:400}),true);
 assert.equal(inViewport({x:5000,y:5000},{x:0,y:0,k:1},{width:600,height:400}),false);
 assert.equal(layoutGraph([],[]).positions.size,0);
});
