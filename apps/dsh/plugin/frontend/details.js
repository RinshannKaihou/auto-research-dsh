export function createResearchDetails(React) {
  const h=React.createElement;
  const json=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
  const status=value=>value==='open'?'未结束':value;
  const section=(title,content)=>h('section',{className:'ari-detail-section'},h('h4',null,title),content);
  function Records({group,state,onSession,onRestore,disabled}) {
    const associations=new Map((state.associations??[]).map(a=>[a.association_id,a]));
    return h(React.Fragment,null,
      section(`研究执行历史 · ${group.attempts.length}`,group.attempts.length?group.attempts.map(a=>h('article',{key:a.attempt_id},
        h('strong',null,`${a.attempt_id} · ${status(a.state)} · ${a.mode}`),h('p',null,`${a.started_at} → ${a.ended_at??'尚未结束'}`),
        associations.has(a.association_id)&&h('button',{onClick:()=>onSession(associations.get(a.association_id).session_id)},'打开原生会话'),
        h('p',null,`结束原因：${a.details?.reason??'未记录'}；结束不代表实验成功。`),h('details',null,h('summary',null,'工作段记录'),h('pre',null,json(a.details))))):h('p',null,'暂无工作段')),
      section(`阶段材料 · ${group.publications.length}`,group.publications.map(p=>h('article',{key:p.publication_id},
        h('strong',null,`${p.publication_id} · ${p.status}`),h('p',null,p.summary),
        (p.gaps??[]).length>0&&h('p',null,`缺口：${p.gaps.join('；')}`),
        ...(p.items??[]).map(i=>h('details',{key:i.item_id},h('summary',null,i.ref??`pub/${p.publication_id}#${i.item_id}`),
          h('p',null,`${i.kind} · ${i.source_path??'内嵌材料'}`),i.object_version&&h('code',null,i.object_version),h('pre',null,json(i.content))))))),
      section(`历史文件快照 · ${group.snapshots.length}`,group.snapshots.map(s=>h('article',{key:s.snapshot_id},h('strong',null,`${s.snapshot_id} · ${s.complete?'完整':'部分'} · ${s.attempt_id}`),
        h('details',null,h('summary',null,'文件清单'),...(s.manifest??[]).map((m,i)=>h('p',{key:i},`${m.source_path??m.path} · ${m.status??''} · ${m.version??''}`))),
        h('button',{disabled,onClick:()=>onRestore(s.snapshot_id)},'预览接手材料')))),
      section(`笔记 · ${group.notes.length}`,group.notes.map(n=>h('details',{key:n.note_id},h('summary',null,`${n.note_id} · ${n.kind}`),h('p',{className:'ari-preserve'},n.body)))),
      (group.relations??[]).length>0&&section('登记关系',h(Relations,{records:group.relations})),
      (group.references??[]).length>0&&section('固定输入与锚点',h(Relations,{records:group.references})));
  }
  function Relations({records}) {
    return h('ul',{className:'ari-relations'},...records.map((r,i)=>h('li',{key:r.relation_id??`${r.kind}:${i}`},
      h('strong',null,r.label),h('p',null,`${r.source_ref} → ${r.target_ref}`),r.note&&h('p',{className:'ari-preserve'},r.note),
      (!r.source||!r.target)&&h('small',null,'项目级或未归属引用；未生成节点连线'),r.source&&r.source===r.target&&h('small',null,'同节点引用'))));
  }
  function Details({node,edge,state,act,disabled,onSession,onClose}) {
    if(edge) return h('aside',{className:'ari-details','aria-label':'关系详情'},h('button',{onClick:onClose},'关闭详情'),h('h3',null,`${edge.source} → ${edge.target}`),h(Relations,{records:edge.records}));
    if(!node) return h('aside',{className:'ari-details ari-detail-empty'},h('h3',null,'选择一个研究节点'),h('p',null,'查看计划、固定输入、阶段材料和全部工作段。选中节点不会启动研究。'));
    return h('aside',{className:'ari-details','aria-label':'节点详情'},h('button',{onClick:onClose},'关闭详情'),
      h('small',null,`${node.node_id} · ${node.status} · ${node.strategy??'continue'}`),h('h3',null,node.question),
      node.lineage_corrected&&h('p',{role:'status'},'谱系已补录／原始声明为根节点'),
      h('div',{className:'ari-actions'},h('button',{disabled,onClick:()=>act('discussion.open',{nodeId:node.node_id})},'围绕此节点讨论'),h('details',null,h('summary',null,'更多'),h('button',{disabled,onClick:()=>act('discussion.open',{nodeId:node.node_id,fresh:true})},'新建另一场讨论'))),
      section('议程锚',h('p',null,node.question_ref??'旧节点尚无可确认的问题版本')),
      section('提出理由',h('p',{className:'ari-preserve'},node.why_now||'未记录')),
      section('研究计划',h('p',{className:'ari-preserve'},node.plan||'未记录')),
      section('节点知识',h(React.Fragment,null,...(state.knowledge??[]).filter(item=>item.node_id===node.node_id).map(item=>h('article',{key:item.ref},h('strong',null,`${item.ref} · ${item.kind} · ${item.status}`),h('p',{className:'ari-preserve'},typeof item.statement==='string'?item.statement:item.statement?.preview),h('p',null,`条件：${json(item.conditions)}`))))),
      section('当前检查点',h('pre',null,json((state.checkpoints??[]).filter(item=>item.node_id===node.node_id).at(-1)?.state??'尚未保存'))),
      section('内部协作',h(React.Fragment,null,...(state.specialists??[]).filter(item=>item.node_id===node.node_id).map(item=>h('article',{key:item.task_id},h('strong',null,`${item.label} · ${item.state}`),h('p',null,item.purpose),item.child_session_id&&h('button',{onClick:()=>onSession(item.child_session_id)},'打开专家会话'))))),
      h(Records,{group:node,state,onSession,onRestore:snapshotId=>act('restore.preview',{snapshotId}),disabled}));
  }
  return {Details,Records,Relations};
}
