/** Publication presentation is optional; immutable prose remains the source. */
import {renderResearchMarkdown} from './markdown.js';
export const publicationDisplayKind='research-display';
export function publicationFiles(pub) {
  return (pub.items??[]).filter(item=>item.kind!==publicationDisplayKind&&item.item_id!=='__research_display');
}
export function publicationDisplay(pub) {
  const d=pub.display??pub.items?.find(item=>item.kind===publicationDisplayKind)?.content;
  if(!d||d.schema!=='publication-display/v1'||typeof d.title!=='string'||!d.title.trim()||d.title.length>80||typeof d.overview!=='string'||!d.overview.trim()||d.overview.length>400)return null;
  if(d.sections!=null&&(!Array.isArray(d.sections)||d.sections.length>4||d.sections.some(s=>typeof s?.heading!=='string'||!s.heading.trim()||s.heading.length>80||!Array.isArray(s.items)||!s.items.length||s.items.length>4||s.items.some(v=>typeof v!=='string'||!v.trim()||v.length>240))))return null;
  if(d.primary_item_id!=null&&!publicationFiles(pub).some(item=>item.item_id===d.primary_item_id&&item.source_path&&item.object_kind!=='directory'))return null;
  return d;
}
export function publicationReport(pub) {
  const files=publicationFiles(pub),d=publicationDisplay(pub);
  if(d?.primary_item_id)return files.find(item=>item.item_id===d.primary_item_id&&item.source_path&&item.object_kind!=='directory')??null;
  const reports=files.filter(item=>/\.md$/i.test(item.source_path??'')&&item.object_kind!=='directory');
  return reports.length===1?reports[0]:null;
}
export function publicationPreview(value) {
  return (typeof value==='string'?value:value?.preview??'').replace(/^\s*#{1,6}\s+/gm,'').replace(/\*\*([^*]+)\*\*/g,'$1').replace(/`([^`]+)`/g,'$1').replace(/\[([^\]]+)\]\([^)]+\)/g,'$1').trim();
}
export function renderPublicationCard(React,{pub,state={},featured=false,category='all',materialKind,onToggle,onRaw,onFiles,onRead}) {
  const h=React.createElement,d=publicationDisplay(pub),files=publicationFiles(pub),report=publicationReport(pub);
  const visible=category==='all'?files:files.filter(item=>materialKind(item)===category);
  if(category!=='all'&&!visible.length)return null;
  const title=d?.title??`${pub.node_id??'项目'}${pub.node_id?' 阶段成果':'阶段成果'} · ${pub.publication_id}`;
  const bodyId=`publication-${pub.publication_id}-${featured?'featured':'card'}-body`;
  const btn=(label,onClick,props={})=>h('button',{type:'button',onClick,...props},label);
  return h('article',{key:pub.publication_id,className:`ari-publication${featured?' ari-publication-featured':''}`},
    h('div',{className:'ari-publication-meta'},h('span',null,pub.node_id??'项目级成果',' / ',pub.publication_id),h('span',{className:'ari-chip'},pub.status==='complete'?'交付完成':'阶段交付')),
    h('h3',{className:'ari-publication-title'},title),
    !state.expanded&&h('p',{className:'ari-publication-preview'},publicationPreview(d?.overview??pub.summary)||'暂无发布说明'),
    d&&h('div',{className:'ari-publication-sections'},...(d.sections??[]).map((s,i)=>h('section',{key:i},h('h4',null,s.heading),h('ul',null,...s.items.map((v,j)=>h('li',{key:j},v)))))),
    h('div',{className:'ari-action-row'},
      report?btn('阅读报告',()=>onRead(report.ref),{className:'ari-primary'}):btn('查看材料',()=>onFiles()),
      btn(state.expanded?'收起发布说明':'展开完整发布说明',onToggle,{'aria-expanded':!!state.expanded,'aria-controls':bodyId})),
    state.expanded&&h('div',{id:bodyId,className:'ari-publication-body'},
      state.loading?h('p',{role:'status'},'正在读取完整发布记录…'):state.error?h('div',{role:'alert'},state.error,btn('重试读取',onToggle)):
      h(React.Fragment,null,renderResearchMarkdown(React,pub.summary??'',{proseHeadings:true}),btn(state.raw?'收起原文':'查看原文',onRaw),state.raw&&h('pre',{className:'ari-publication-raw'},pub.summary??''))),
    h('details',{className:'ari-publication-sources',open:!!state.files,onToggle:event=>{if(event.currentTarget.open!==!!state.files)onFiles(event.currentTarget.open);}},
      h('summary',null,`材料与来源${visible.length?` · ${visible.length} 项`:''}`),
      pub.created_at&&h('small',null,new Date(pub.created_at).toLocaleString()),
      state.error&&h('p',{role:'alert'},state.error),
      !visible.length&&h('p',null,state.loading?'正在读取材料…':'暂无已登记附件'),
      h('ul',{className:'ari-file-list'},...visible.map(item=>h('li',{key:item.ref},h('span',null,item.source_path??item.item_id),btn('阅读',()=>onRead(item.ref))))),
      pub.gaps?.length>0&&h('div',null,h('h4',null,'限制与缺口'),h('ul',null,...pub.gaps.map((gap,i)=>h('li',{key:i},gap))))),
    h('small',{className:'ari-publication-footnote'},'交付状态不代表科学验证；结论与适用范围请以报告及证据为准。'));
}
