/** Presentation-only grouping; never invent a native parent address. */
export function researchSessionGroups(visible, all=visible) {
  const parents=all.filter(row=>row.role!=='specialist');
  const groups=new Map(visible.filter(row=>row.role!=='specialist').map(row=>[row.session_id,{key:row.session_id,parent:row,children:[]}]));
  for(const child of visible.filter(row=>row.role==='specialist')) {
    const candidates=child.node_id?parents.filter(row=>row.node_id===child.node_id&&row.role==='node_core'):[];
    const parent=child.parent_session_id?parents.find(row=>row.session_id===child.parent_session_id):(candidates.length===1?candidates[0]:null);
    const key=parent?.session_id??(child.parent_session_id?`parent:${child.parent_session_id}`:`unassigned:${child.node_id??'project'}`);
    if(!groups.has(key))groups.set(key,{key,parent:parent?{...parent,contextOnly:true}:null,nodeId:child.node_id,children:[]});
    groups.get(key).children.push(child);
  }
  return [...groups.values()];
}
export function researchExpertTitle(item) {
  const text=value=>typeof value==='string'?value:value?.preview??'';
  const purpose=text(item.label||item.purpose).replace(/\s+/g,' ').trim();
  const id=item.task_id??`专家 ${String(item.session_id??item.child_session_id??'未知').replace(/^session-/,'').slice(0,12)}`;
  return purpose?`${id} · ${purpose.length>76?purpose.slice(0,76)+'…':purpose}`:id;
}
export function researchExpertCounts(children) {
  const active=children.filter(row=>row.native_status==='running'||(!row.native_status&&['starting','running'].includes(row.specialist_state))).length;
  const attention=children.filter(row=>row.pending_approvals>0||['fault','unverified','human','host_limit'].includes(row.pause_reason)||row.specialist_state==='unverified').length;
  return `专家 ${children.length}${active?` · ${active} 运行中`:''}${attention?` · ${attention} 待处理`:''}`;
}
