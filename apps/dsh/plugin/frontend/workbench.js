export function createWorkbench(React, rpc, openSession, projectGraph, ResearchGraph, components, researchStyles) {
  const h=React.createElement, {Details,Records}=components;
  return function Workbench({sessionId}) {
    const [state,setState]=React.useState(null),[error,setError]=React.useState(null),[notice,setNotice]=React.useState(null);
    const [preview,setPreview]=React.useState(null);
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
    const runtime=state?.runtime, runState=runtime?.state??'cold';
    const roles={main:'研究主会话',exploration:'探索会话',discussion:'讨论会话',handoff:'接手会话',legacy:'历史会话'};
    const runLabels={manual:'尚未开始',running:'自主推进中',paused:'自主推进已暂停',stopping:'停止处理中',unverified:'停止待核实',stopped:'已停止',complete:'自主目标已结束',cold:'重启后等待显式恢复'};
    const reasons={project:'项目暂停',project_wait:'项目暂停（等待探索）',human:'人工介入',wait:'等待探索进展',native_stop:'原生停止',host_limit:'宿主限制',fault:'执行故障',cold:'重启未恢复',complete:'目标已完成',finished:'工作段已结束',stop:'停止处理中',unverified:'待核实',legacy_history:'历史探索（不加入自主调度）'};
    const disabled=busy||!!error||!state;
    async function act(endpoint,payload={}) {
      if(pending.current||error)return;
      pending.current=true;setBusy(true);setNotice(null);const life=lifecycle.current;
      try {const value=await call(endpoint,payload);if(life!==lifecycle.current)return;if(endpoint==='restore.preview')setPreview(value);setNotice(value?.message??'操作已完成');if(value?.sessionId)await navigate(value.sessionId);await refresh();}
      catch(e){if(life===lifecycle.current)setNotice(`操作失败：${e.message}`);}
      finally{if(life===lifecycle.current){pending.current=false;setBusy(false);}}
    }
    async function navigate(id){try{await openSession(id);}catch(e){setNotice(`无法打开原生会话：${e.message}`);}}
    const select=id=>{setSelected(id);setEdgeId(null);};
    const btn=(label,endpoint,payload={},off=disabled)=>h('button',{disabled:off,onClick:()=>act(endpoint,payload)},label);
    return h('div',{className:'ari-v5','aria-busy':busy},h('style',null,researchStyles),
      h('header',{className:'ari-header'},h('div',{className:'ari-overline'},'RESEARCH / 研究工作台'),h('h2',null,state?.project.goal??'关联研究项目'),
        state&&h(React.Fragment,null,h('div',{className:'ari-stats'},
          h('span',null,h('strong',null,model.nodes.length),'研究节点'),h('span',null,h('strong',null,runtime?.running_count??'未知'),'实际运行会话'),
          h('span',null,h('strong',null,Number(state.usage.known).toLocaleString()),'已记录 token'),h('span',null,h('strong',null,state.usage.missing??state.usage.unknown_count),'缺失请求')),
          h('p',null,`项目推进：${runLabels[runState]??runState} · 待审批：${runtime?.pending_approvals??'未知'}`),
          runtime?.main_session_id&&h('button',{onClick:()=>navigate(runtime.main_session_id)},'打开研究主会话'),
          h('p',null,`当前浏览会话：${roles[runtime?.current?.role]??'未知'} · ${runtime?.current?.native_status??'未知'} · ${reasons[runtime?.current?.pause_reason]??runtime?.current?.pause_reason??'无暂停'}`),
          h('p',null,`用量：实际 ${state.usage.actual??0} · 估算 ${state.usage.estimated??0} · 进行中 ${state.usage.in_progress??0} · 讨论 ${state.usage.discussion??0} token；只监控，无费用上限。`),
          h('div',{className:'ari-actions'},
            ['manual','stopped','complete'].includes(runState)&&btn(runState==='manual'?'开始自主研究':'再次开始研究','auto'),
            runState==='running'&&btn('暂停自主研究','pause'),
            ['paused','cold'].includes(runState)&&btn('继续自主研究','resume'),
            ['running','paused','cold','stopping','unverified'].includes(runState)&&btn(runState==='unverified'?'重新核实停止':'停止研究','stop')))),
      error&&h('div',{className:'ari-alert',role:'alert'},state?'连接异常，保留最后成功数据。':'暂时无法读取项目。',` ${error}`,h('button',{onClick:refresh},'重试')),
      notice&&h('p',{role:'status'},notice),
      preview&&h('section',{className:'ari-section','aria-label':'接手预览'},h('h3',null,'历史文件快照 · 接手预览'),
        h('p',null,`来源：${preview.context.source_attempt_id} · 目标目录：${preview.workspace}`),
        h('pre',null,JSON.stringify(preview.context,null,2)),h('p',null,`未完整保存：${preview.missing.length} 项`),
        !preview.eligible&&h('p',{role:'alert'},'来源执行仍在进行或待核实，暂不能创建接手会话'),
        btn('从此版本创建接手会话','restore.create',{snapshotId:preview.snapshot_id,previewId:preview.preview_id},disabled||!preview.eligible),
        h('button',{onClick:()=>setPreview(null)},'关闭预览')),
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
            h('strong',null,`${n.node_id} · ${n.status}${state.attempt?.node_id===n.node_id?' · 当前执行节点':''}`),h('span',null,n.question),h('small',null,`${n.strategy??'continue'} · ${n.attempts.length} 工作段 · ${n.publications.length} 发布`))))),
          view==='list'&&!visibleIds.size&&h('p',{className:'ari-empty'},'没有匹配的研究节点。')),
          h(Details,{node:chosen,edge,state,act,disabled,onSession:navigate,onClose:()=>{setSelected(null);setEdgeId(null);}})),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目规划与未归属材料 · ${model.planning.attempts.length} 工作段 · ${model.planning.publications.length} 发布`),
          h('p',null,'这些记录没有明确的节点归属，保留原始记录。工作段“未结束”不代表原生会话正在运行。'),
          h(Records,{group:model.planning,state,onSession:navigate,onRestore:snapshotId=>act('restore.preview',{snapshotId}),disabled})),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目会话 · ${runtime?.sessions.length??0}`),...(runtime?.sessions??[]).map(s=>h('article',{key:s.session_id},
          h('strong',null,`${s.detached?'历史会话':roles[s.role]} · ${s.node_id??'项目规划'}`),h('p',null,`${s.name} · ${s.native_status} · ${reasons[s.pause_reason]??s.pause_reason??'无暂停'}`),
          h('p',null,s.cwd??'目录未知'),h('button',{onClick:()=>navigate(s.session_id)},'打开会话'),
          h('details',null,h('summary',null,'关联历史与原生状态'),h('pre',null,JSON.stringify({goal:s.goal,jobs:s.jobs,intervals:state.associations.filter(a=>a.session_id===s.session_id)},null,2)))))),
        h('details',{className:'ari-section'},h('summary',null,'任务队列'),...(state.workflow?.tasks??[]).map(t=>h('p',{key:t.task_id},`${t.task_id} · ${t.node_id} · ${t.state}${t.error?' · '+t.error:''}`))),
        h('details',{className:'ari-section'},h('summary',null,'高级设置'),h('p',null,'移出后清除当前会话的项目上下文和插件续轮权限，原对话及材料保留。执行会话须先停止。'),btn('将此会话移出项目','detach')),
        h('details',{className:'ari-section'},h('summary',null,`接手记录 · ${(state.restorations??[]).length}`),...(state.restorations??[]).map(r=>h('p',{key:r.restoration_id},`${r.snapshot_id} · ${r.source_attempt_id} → ${r.target_attempt_id??'未登记工作段'}`)))));
  };
}
