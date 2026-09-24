import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {renderPublicationCard,publicationReport,publicationDisplay} from '../../apps/dsh/plugin/frontend/publication.js';
import {renderResearchMarkdown} from '../../apps/dsh/plugin/frontend/markdown.js';
const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props??{},children:children.flat(Infinity)})};
const all=(tree,fn)=>!tree||typeof tree!=='object'?[]:[...(fn(tree)?[tree]:[]),...(tree.children??[]).flatMap(v=>all(v,fn))];
const text=tree=>typeof tree==='string'?tree:(tree?.children??[]).map(text).join('');
const long=readFileSync(new URL('../fixtures/long-publication-summary.txt',import.meta.url),'utf8').trimEnd();
const pub={publication_id:'P-001',node_id:'X-001',status:'complete',summary:long,items:[]};
const card=(changes={})=>renderPublicationCard(React,{pub,...changes,onRead:()=>{},onFiles:()=>{},onToggle:()=>{},onRaw:()=>{}});
test('user long summary is a single normal preview, never a heading; raw stays exact',()=>{
  const view=card();assert.equal(all(view,n=>n.type==='h3').map(text).join(''),'X-001 阶段成果 · P-001');
  assert.equal(all(view,n=>n.props.className==='ari-publication-preview').length,1);
  assert(!all(view,n=>/^h\d$/.test(n.type)).some(n=>text(n).includes('Part A')));
  const expanded=card({state:{expanded:true,loaded:true,raw:true}});
  assert.equal(all(expanded,n=>n.type==='pre').map(text).join(''),long);
  assert.equal(all(expanded,n=>n.props.className==='ari-publication-preview').length,0);
  assert(!all(expanded,n=>/^h\d$/.test(n.type)).some(n=>text(n).includes('Part A')));
});
test('Markdown renders prose-level headings, lists and tables without unsafe HTML/links',()=>{
  const tree=renderResearchMarkdown(React,'# Heading\n\n- Point\n\n| A | B |\n|---|---|\n| x | y |\n\n[bad](javascript:alert) [ok](https://example.com) <script>bad</script>',{proseHeadings:true});
  assert.equal(all(tree,n=>n.type==='h4').length,1);assert.equal(all(tree,n=>n.type==='table').length,1);
  assert.equal(all(tree,n=>n.type==='a').length,1);assert(!all(tree,n=>n.type==='script').length);
});
test('report selection uses registered attachments, never prose paths',()=>{
  const md={item_id:'report',source_path:'a/long/report.md',ref:'pub/P-001#report',kind:'report'};
  assert.equal(publicationReport(pub),null);
  assert.equal(publicationReport({...pub,items:[md]}),md);
  assert.equal(publicationReport({...pub,items:[md,{...md,item_id:'second'}]}),null);
  assert.equal(publicationReport({...pub,display:{schema:'publication-display/v1',title:'Title',overview:'Brief',primary_item_id:'report'},items:[md,{...md,item_id:'second'}]}),md);
});
test('bad optional display falls back; empty publications remain readable',()=>{
  assert.equal(publicationDisplay({...pub,display:{title:['bad']}}),null);
  assert.match(text(card({pub:{...pub,summary:'',display:{title:['bad']}}})),/暂无发布说明/);
  assert(!text(card({pub:{...pub,items:[{item_id:'__research_display',kind:'research-display'}]}})).includes('__research_display'));
});

test('display sections render as lists and remain local to each publication',()=>{
  const d={schema:'publication-display/v1',title:'调研完成',overview:'短摘要',sections:[{heading:'待验证假设',items:['尚未实验验证']}]};
  assert.match(text(card({pub:{...pub,display:d}})),/调研完成/);
  assert.equal(all(card({pub:{...pub,display:d}}),n=>n.type==='li'&&text(n)==='尚未实验验证').length,1);
  assert(!text(card({pub:{...pub,publication_id:'P-002'}})).includes('调研完成'));
  const invalid={...d,primary_item_id:'missing'};assert.equal(publicationDisplay({...pub,display:invalid}),null);
});

test('research_publish keeps display optional and forwards it without another model call',async()=>{
  const {registerResearchTools}=await import('../../apps/dsh/plugin/tools.js');
  const defs=[],requests=[];
  registerResearchTools({tools:{register:def=>{defs.push(def);return()=>{};}}},{request:async(agent,method,args,id)=>{requests.push({method,args,id});return {publication_id:'P-1'};},progress:async()=>{}});
  const publish=defs.find(def=>def.name==='research_publish');
  assert(!publish.parameters.required.includes('display'));
  assert.deepEqual(publish.parameters.properties.display.required,['title','overview']);
  const fields={title:'Title',overview:'Brief',sections:[{heading:'Limits',items:['Not yet tested']}]};
  await publish.execute({summary:'original',status:'partial',display:fields},{agent:{id:'main'},callId:'one'});
  await publish.execute({summary:'legacy',status:'partial'},{agent:{id:'main'},callId:'two'});
  assert.deepEqual(requests[0].args.display,fields);assert.equal(requests[1].args.display,undefined);
  assert.deepEqual(requests.map(r=>r.method),['publish','publish']);
});
