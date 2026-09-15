export function createWorkbench(React, rpc, openSession, projectGraph, ResearchGraph, components, researchStyles) {
  const h=React.createElement, {Details,Records}=components;
  return function Workbench({sessionId}) {
    const [state,setState]=React.useState(null),[error,setError]=React.useState(null),[notice,setNotice]=React.useState(null);
    const [goal,setGoal]=React.useState(''),[busy,setBusy]=React.useState(false),[updated,setUpdated]=React.useState(null);
    const [selected,setSelected]=React.useState(null),[edgeId,setEdgeId]=React.useState(null),[search,setSearch]=React.useState(''),[filter,setFilter]=React.useState('all');
    const [view,setView]=React.useState(()=>window.matchMedia('(max-width:600px)').matches?'list':'graph');
    const lifecycle=React.useRef(0),requestSeq=React.useRef(0),pending=React.useRef(false);
    const call=React.useCallback(async(endpoint,payload={})=>{
      const response=await rpc(endpoint,{sessionId,operationId:`${sessionId}:ui:${crypto.randomUUID()}`,...payload});
      if(!response.ok)throw new Error(response.error?.message??'请求失败');
      return response.value;
    },[sessionId]);
    const refresh=React.useCallback(async()=>{
      const life=lifecycle.current,seq=++requestSeq.current;
      try {const value=await call('query');if(life!==lifecycle.current||seq!==requestSeq.current)return;setState(value);setError(null);setUpdated(new Date());}
      catch(e){if(life===lifecycle.current&&seq===requestSeq.current)setError(e.message);}
    },[call]);
    React.useEffect(()=>{
      lifecycle.current++;let active=true,timer;
      const poll=async()=>{await refresh();if(active)timer=setTimeout(poll,3000);};poll();
      return()=>{active=false;lifecycle.current++;clearTimeout(timer);};
    },[refresh]);
    const model=React.useMemo(()=>projectGraph(state??{}),[state]);
    const term=search.trim().toLocaleLowerCase();
    const visibleIds=new Set(model.nodes.filter(n=>(filter==='all'||n.status===filter)&&(!term||`${n.node_id} ${n.question} ${n.plan} ${n.why_now}`.toLocaleLowerCase().includes(term))).map(n=>n.node_id));
    const chosen=model.nodes.find(n=>n.node_id===selected), edge=model.edges.find(e=>e.id===edgeId);
    const disabled=busy||!!error||!state;
    async function act(endpoint,payload={}) {
      if(pending.current||error)return;
      pending.current=true;setBusy(true);setNotice(null);const life=lifecycle.current;
      try {const value=await call(endpoint,payload);if(life!==lifecycle.current)return;setNotice(value?.sessionId?`已创建原生会话 ${value.sessionId}`:'操作已提交');await refresh();}
      catch(e){if(life===lifecycle.current)setNotice(`操作失败：${e.message}`);}
      finally{if(life===lifecycle.current){pending.current=false;setBusy(false);}}
    }
    async function navigate(id){try{await openSession(id);}catch(e){setNotice(`无法打开原生会话：${e.message}`);}}
    const select=id=>{setSelected(id);setEdgeId(null);};
    const btn=(label,endpoint,payload={},off=disabled)=>h('button',{disabled:off,onClick:()=>act(endpoint,payload)},label);
    return h('div',{className:'ari-v5','aria-busy':busy},h('style',null,researchStyles),
      h('header',{className:'ari-header'},h('div',{className:'ari-overline'},'RESEARCH / 研究工作台'),h('h2',null,state?.project.goal??'关联研究项目'),
        state&&h(React.Fragment,null,h('div',{className:'ari-stats'},
          h('span',null,h('strong',null,model.nodes.length),'研究节点'),h('span',null,h('strong',null,state.attempts.filter(a=>!a.ended_at).length),'未结束工作段'),
          h('span',null,h('strong',null,Number(state.usage.known).toLocaleString()),'已记录 token'),h('span',null,h('strong',null,state.usage.unknown_count),'缺失请求')),
          h('p',null,`项目控制：${state.project.control} · 当前工作段：${state.attempt?.attempt_id??'无'}`),
          h('div',{className:'ari-actions'},btn('开启自主研究','auto'),btn('暂停','pause'),btn('继续','resume'),btn('停止项目','stop'),btn('解除关联','detach')))),
      error&&h('div',{className:'ari-alert',role:'alert'},state?'连接异常，保留最后成功数据。':'暂时无法读取项目。',` ${error}`,h('button',{onClick:refresh},'重试')),
      notice&&h('p',{role:'status'},notice),
      !state?h('section',{className:'ari-section'},h('p',null,'初始化与关联不调用模型。项目目录采用当前 DSH 会话工作目录。'),
        h('input',{'aria-label':'研究目标',placeholder:'研究目标',value:goal,onChange:e=>setGoal(e.target.value)}),
        h('button',{disabled:busy||!goal.trim(),onClick:async()=>{setError(null);pending.current=false;try{setBusy(true);await call('open',{goal});await refresh();}catch(e){setNotice(e.message);}finally{setBusy(false);}}},'新建并关联'),
        h('button',{disabled:busy,onClick:async()=>{try{setBusy(true);await call('open');await refresh();}catch(e){setNotice(e.message);}finally{setBusy(false);}}},'关联已有项目')):
      h(React.Fragment,null,
        h('div',{className:'ari-toolbar'},h('h3',null,'研究图'),h('input',{'aria-label':'搜索研究节点',placeholder:'搜索节点、问题或计划…',value:search,onChange:e=>setSearch(e.target.value)}),
          h('select',{'aria-label':'筛选节点状态',value:filter,onChange:e=>setFilter(e.target.value)},...['all',...new Set(model.nodes.map(n=>n.status))].map(s=>h('option',{key:s,value:s},s==='all'?'全部状态':s))),
          h('button',{'aria-pressed':view==='graph',onClick:()=>setView('graph')},'图'),h('button',{'aria-pressed':view==='list',onClick:()=>setView('list')},'列表'),
          h('small',{className:'ari-updated'},updated?`更新于 ${updated.toLocaleTimeString()}`:'正在读取')),
        h('div',{className:'ari-layout'},h('div',null,
          h('div',{style:{display:view==='graph'?'block':'none'}},h(ResearchGraph,{model,visibleIds,selected,onSelect:select,onEdge:e=>setEdgeId(e.id),focused:state.attempt?.node_id})),
          view==='list'&&h('ul',{className:'ari-list','aria-label':'研究节点列表'},...model.nodes.filter(n=>visibleIds.has(n.node_id)).map(n=>h('li',{key:n.node_id},h('button',{'aria-pressed':selected===n.node_id,onClick:()=>select(n.node_id)},
            h('strong',null,`${n.node_id} · ${n.status}${state.attempt?.node_id===n.node_id?' · 当前聚焦':''}`),h('span',null,n.question),h('small',null,`${n.strategy??'continue'} · ${n.attempts.length} 工作段 · ${n.publications.length} 发布`))))),
          view==='list'&&!visibleIds.size&&h('p',{className:'ari-empty'},'没有匹配的研究节点。')),
          h(Details,{node:chosen,edge,state,act,disabled,onSession:navigate,onClose:()=>{setSelected(null);setEdgeId(null);}})),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目规划与未归属材料 · ${model.planning.attempts.length} 工作段 · ${model.planning.publications.length} 发布`),
          h('p',null,'这些记录没有明确的节点归属，保留原始记录。工作段“未结束”不代表原生会话正在运行。'),btn('聚焦规划工作','focus',{nodeId:null}),
          h(Records,{group:model.planning,state,onSession:navigate,onRestore:snapshotId=>act('restore',{snapshotId}),disabled})),
        h('details',{className:'ari-section'},h('summary',null,`全部原生会话 · ${state.associations.length}`),...state.associations.map(a=>h('p',{key:a.association_id},
          h('code',null,a.session_id),` · ${a.ended_at?'已解除关联':'已关联'} `,h('button',{onClick:()=>navigate(a.session_id)},'打开原生会话')))),
        h('details',{className:'ari-section'},h('summary',null,`接手记录 · ${(state.restorations??[]).length}`),...(state.restorations??[]).map(r=>h('p',{key:r.restoration_id},`${r.snapshot_id} · ${r.source_attempt_id} → ${r.target_attempt_id??'未登记工作段'}`)))));
  };
}
