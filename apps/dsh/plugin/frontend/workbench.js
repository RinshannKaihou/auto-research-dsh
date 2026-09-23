export function createWorkbench(React, rpc, openSession, projectGraph, ResearchGraph, components, researchStyles) {
  const h=React.createElement, {Details,Records}=components;
  return function Workbench({sessionId}) {
    const [state,setState]=React.useState(null),[error,setError]=React.useState(null),[notice,setNotice]=React.useState(null);
    const [preview,setPreview]=React.useState(null),[referencePreview,setReferencePreview]=React.useState(null);
    const [goal,setGoal]=React.useState(''),[busy,setBusy]=React.useState(false),[updated,setUpdated]=React.useState(null);
    const [knowledgeSearch,setKnowledgeSearch]=React.useState(''),[guidancePath,setGuidancePath]=React.useState(''),[guidanceVersion,setGuidanceVersion]=React.useState('');
    const [selected,setSelected]=React.useState(null),[edgeId,setEdgeId]=React.useState(null),[search,setSearch]=React.useState(''),[filter,setFilter]=React.useState('all');
    const [view,setView]=React.useState(()=>window.matchMedia('(max-width:600px)').matches?'list':'graph');
    const hintOffset=React.useRef(0);
    const lifecycle=React.useRef(0),requestSeq=React.useRef(0),pending=React.useRef(false),lastState=React.useRef(null);
    const call=React.useCallback(async(endpoint,payload={})=>{
      const response=await rpc(endpoint,{sessionId,operationId:`${sessionId}:ui:${crypto.randomUUID()}`,...payload});
      if(!response.ok)throw new Error(response.error?.message??'请求失败');
      return response.value;
    },[sessionId]);
    const loadCollection=React.useCallback(async collection=>{
      const items=[];let cursor={};
      do{const page=await call('history.page',{collection,cursor,limit:200});items.push(...page.items);cursor=page.cursor??null;}while(cursor);
      return items;
    },[call]);
    const refresh=React.useCallback(async()=>{
      const life=lifecycle.current,seq=++requestSeq.current;
      try {
        const [summary,contextPreview,guidance]=await Promise.all([call('query'),call('context.preview'),call('guidance.status')]),previous=lastState.current;
        const collections=['nodes','relations','dependencies','consumptions','usage_gaps','legacy_refs','attempts','publications','notes','snapshots','restorations','associations','knowledge','impacts','checkpoints','review_todos','specialists','sessions','tasks'];
        const loaded=await Promise.all(collections.map(async name=>{
          if(!['nodes','attempts','impacts','review_todos','specialists','sessions','tasks'].includes(name)&&previous&&previous.counts?.[name]===summary.counts?.[name]&&previous[name])return[name,previous[name]];
          return[name,await loadCollection(name)];
        }));
        const initialHints=summary.structure_hints??{items:[],total:0,shown_count:0,offset:0,next_offset:null};
        const offset=Math.min(hintOffset.current,Math.floor(Math.max(0,initialHints.total-1)/8)*8);
        const hints=offset?await call('history.page',{collection:'hints',cursor:{offset},limit:8}):initialHints;
        const value={...summary,...Object.fromEntries(loaded),structure_hints:hints,context_preview:contextPreview,guidance};
        if(life!==lifecycle.current||seq!==requestSeq.current)return;
        hintOffset.current=offset;lastState.current=value;setState(value);setError(null);setUpdated(new Date());
      }
      catch(e){if(life===lifecycle.current&&seq===requestSeq.current)setError(e.message);}
    },[call,loadCollection]);
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
    const roles={main:'研究主会话',node_core:'节点核心 Agent',exploration:'历史探索会话',discussion:'讨论会话',handoff:'接手会话',specialist:'节点专家',legacy:'历史会话'};
    const runLabels={manual:'尚未开始',running:'自主推进中',paused:'自主推进已暂停',stopping:'停止处理中',unverified:'停止待核实',stopped:'已停止',complete:'自主目标已结束',cold:'重启后等待显式恢复'};
    const reasons={project:'项目暂停',project_wait:'项目暂停（等待探索）',human:'人工介入',wait:'等待探索进展',native_stop:'原生停止',host_limit:'宿主限制',fault:'执行故障',cold:'重启未恢复',complete:'目标已完成',finished:'历史工作段已结束',segment_complete:'工作段已核实结束',stop:'停止处理中',unverified:'待核实',legacy_history:'历史探索（不加入自主调度）'};
    const sessionCards=(state?.sessions??[]).map(saved=>({...saved,...(runtime?.sessions??[]).find(live=>live.session_id===saved.session_id)}));
    const blockedCreations=(state?.tasks??state?.workflow?.tasks??[]).filter(t=>t.state==='unverified'&&!(state?.sessions??[]).some(s=>s.session_id===t.session_id));
    const knowledgeTerm=knowledgeSearch.trim().toLocaleLowerCase();
    const knowledgeRows=(state?.knowledge??[]).filter(item=>!knowledgeTerm||`${item.ref} ${item.kind} ${typeof item.statement==='string'?item.statement:item.statement?.preview??''} ${JSON.stringify(item.conditions)}`.toLocaleLowerCase().includes(knowledgeTerm));
    const disabled=busy||!!error||!state;
    async function act(endpoint,payload={}) {
      if(pending.current||error)return;
      pending.current=true;setBusy(true);setNotice(null);const life=lifecycle.current;
      try {const value=await call(endpoint,payload);if(life!==lifecycle.current)return;if(endpoint==='restore.preview')setPreview(value);setNotice(value?.message??'操作已完成');if(value?.sessionId)await navigate(value.sessionId);await refresh();}
      catch(e){if(life===lifecycle.current)setNotice(`操作失败：${e.message}`);}
      finally{if(life===lifecycle.current){pending.current=false;setBusy(false);}}
    }
    async function navigate(id){try{await openSession(id);}catch(e){setNotice(`无法打开原生会话：${e.message}`);}}
    async function inspectReference(ref){setReferencePreview(null);try{setReferencePreview(await call('reference.get',{ref}));}catch(e){setNotice(`无法读取研究材料：${e.message}`);}}
    const select=id=>{setSelected(id);setEdgeId(null);};
    const btn=(label,endpoint,payload={},off=disabled)=>h('button',{disabled:off,onClick:()=>act(endpoint,payload)},label);
    return h('div',{className:'ari-v5','aria-busy':busy},h('style',null,researchStyles),
      h('header',{className:'ari-header'},h('div',{className:'ari-overline'},'RESEARCH / 研究工作台'),h('h2',null,state?.project.goal??'关联研究项目'),
        state&&h(React.Fragment,null,h('div',{className:'ari-stats'},
          h('span',null,h('strong',null,model.nodes.length),'研究节点'),h('span',null,h('strong',null,runtime?.running_count??'未知'),'实际运行会话'),
          h('span',null,h('strong',null,runtime?.waiting_approval_count??0),'等待审批'),
          h('span',null,h('strong',null,runtime?.specialist_count??0),'运行中专家'),
          h('span',null,h('strong',null,state.review_queue?.pending_total??'未知'),'待整理'),
          h('span',null,h('strong',null,Number(state.usage.known).toLocaleString()),'已记录 token'),h('span',null,h('strong',null,state.usage.missing??state.usage.unknown_count),'缺失请求')),
          h('p',null,`项目推进：${runLabels[runState]??runState} · 待审批：${runtime?.pending_approvals??'未知'}`),
          blockedCreations.length>0&&h('p',{role:'alert'},`需要处理：${blockedCreations.length} 个探索任务的创建结果尚未核实，保留 ${blockedCreations.length} 个研究槽位，其他任务可能排队。请在“探索任务与恢复”核实并重试；停止项目后也可使用“核实停止”。`),
          runtime?.main_session_id&&h('button',{onClick:()=>navigate(runtime.main_session_id)},'打开研究主会话'),
          h('p',null,`当前浏览会话：${roles[runtime?.current?.role]??'未知'} · ${runtime?.current?.native_status??'未知'} · ${reasons[runtime?.current?.pause_reason]??runtime?.current?.pause_reason??'无暂停'}`),
          h('p',null,`用量：实际 ${state.usage.actual??0} · 估算 ${state.usage.estimated??0} · 进行中 ${state.usage.in_progress??0} · 覆盖缺口 ${state.usage.coverage_incomplete??0} · 讨论 ${state.usage.discussion??0} token；只监控，无费用上限。`),
          h('div',{className:'ari-actions'},
            ['manual','stopped','complete'].includes(runState)&&btn(runState==='manual'?'开始自主研究':'再次开始研究','auto'),
            runState==='running'&&btn('暂停自主研究','pause'),
            ['paused','cold'].includes(runState)&&btn('继续自主研究','resume'),
            ['running','paused','cold','stopping'].includes(runState)&&btn('停止研究','stop'),
            runState==='unverified'&&btn('重新核实停止','verify-stop')))),
      (runtime?.approvals??[]).length>0&&h('section',{className:'ari-section','aria-label':'待审批'},h('h3',null,'等待原生审批'),
        ...(runtime.approvals??[]).map(a=>h('article',{key:`${a.session_id}:${a.approval_id}`},
          h('strong',null,`${a.node_id??'项目规划'} · 等待审批`),h('p',null,a.reason??'未提供原因'),
          h('small',null,`${a.approval_id}${a.asked_at?` · 自 ${new Date(a.asked_at).toLocaleString()} 起`:''}`),
          h('button',{onClick:()=>navigate(a.session_id)},'打开审批会话')))),
      error&&h('div',{className:'ari-alert',role:'alert'},state?'连接异常，保留最后成功数据。':'暂时无法读取项目。',` ${error}`,h('button',{onClick:refresh},'重试')),
      notice&&h('p',{role:'status'},notice),
      referencePreview&&h('section',{className:'ari-section','aria-label':'研究材料','data-resolution-outcome':referencePreview.resolution?.outcome,'data-resolution-reason':referencePreview.resolution?.reason,role:referencePreview.resolution&&referencePreview.resolution.outcome!=='resolved'?'alert':undefined},h('h3',null,referencePreview.resolution&&referencePreview.resolution.outcome!=='resolved'?`材料不可打开：${referencePreview.resolution.reason}`:'研究材料'),h('pre',null,JSON.stringify(referencePreview,null,2)),h('button',{onClick:()=>setReferencePreview(null)},'关闭材料')),
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
            h('strong',null,`${n.node_id} · ${n.status}${state.attempt?.node_id===n.node_id?' · 当前执行节点':''}`),h('span',null,n.question),h('small',null,`${n.origin_class} · ${n.strategy??'continue'} · ${n.attempts.length} 工作段 · ${n.publications.length} 发布`))))),
          view==='list'&&!visibleIds.size&&h('p',{className:'ari-empty'},'没有匹配的研究节点。')),
          h(Details,{node:chosen,edge,state,act,disabled,onSession:navigate,onClose:()=>{setSelected(null);setEdgeId(null);}})),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目规划与未归属材料 · ${model.planning.attempts.length} 工作段 · ${model.planning.publications.length} 发布`),
          h('p',null,'这些记录没有明确的节点归属，保留原始记录。工作段“未结束”不代表原生会话正在运行。'),
          h(Records,{group:model.planning,state,onSession:navigate,onRestore:snapshotId=>act('restore.preview',{snapshotId}),disabled})),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目知识与未决问题 · ${state.knowledge.length}`),
          h('input',{'aria-label':'搜索项目知识',placeholder:'搜索主张、条件、引用…',value:knowledgeSearch,onChange:e=>setKnowledgeSearch(e.target.value)}),
          ...knowledgeRows.map(item=>h('article',{key:item.ref},h('strong',null,`${item.ref} · ${item.kind} · ${item.status}`),
            h('p',{className:'ari-preserve'},typeof item.statement==='string'?item.statement:item.statement?.preview),
            h('p',null,h('strong',null,'证据引用：'),item.evidence_refs?.length?item.evidence_refs.map(ref=>h('button',{key:ref,onClick:()=>inspectReference(ref)},ref)):'未关联证据'),
            h('p',null,h('strong',null,'记账来源：'),item.asserted_at?JSON.stringify(item.asserted_at):'历史记录，无来源'),
            ...(item.execution_refs??[]).map((ref,index)=>h('p',{key:`execution-${index}`},`执行来源：${ref.ref} · ${ref.status==='linked'?'已关联':`未关联 · ${ref.reason}`}`)),
            h('p',null,h('strong',null,'自述来源：'),item.source_identity&&Object.keys(item.source_identity).length?JSON.stringify(item.source_identity):'未填写',item.source_identity?.session_id&&h('button',{onClick:()=>navigate(item.source_identity.session_id)},'打开来源会话')),
            h('p',null,h('strong',null,'适用范围：'),item.scope&&Object.keys(item.scope).length?JSON.stringify(item.scope):'未填写'),
            h('p',null,h('strong',null,'适用条件：'),item.conditions&&Object.keys(item.conditions).length?JSON.stringify(item.conditions):'未填写'),
            item.supersedes?.length>0&&h('p',null,h('strong',null,'修订历史：'),...item.supersedes.map(ref=>h('button',{key:ref,onClick:()=>inspectReference(ref)},ref))))),
          !knowledgeRows.length&&h('p',{className:'ari-empty'},'没有匹配的知识条目。')),
        h('details',{className:'ari-section',open:true,'aria-label':'结构候选提示'},h('summary',null,`结构候选提示 · 显示 ${state.structure_hints.shown_count} / ${state.structure_hints.total}`),
          h('p',null,'这些线索仅供复核，不会自动补边或阻止研究。'),
          ...state.structure_hints.items.map((item,index)=>h('article',{key:index},
            h('strong',null,`候选 · ${item.target}`),
            h('p',null,({prose_mention_without_relation:'正文提及尚无结构关联',whole_snapshot_evidence:'证据指向整份快照',lineage_mention_without_predecessor:'提及其他节点但未声明前驱',complete_publication_cites_risk:'完整发布仍引用有风险的依据'})[item.class]),
            h('p',null,JSON.stringify(item.evidence)),
            h('p',null,`修复记录：${item.repair_evidence.length?item.repair_evidence.join('、'):'无'}`))),
          h('button',{disabled:disabled||hintOffset.current===0,onClick:()=>{hintOffset.current=Math.max(0,hintOffset.current-8);return refresh();}},'上一页'),
          h('button',{disabled:disabled||state.structure_hints.next_offset==null,onClick:()=>{hintOffset.current=state.structure_hints.next_offset;return refresh();}},'下一页')),
        h('details',{className:'ari-section'},h('summary',null,`节点检查点 · ${state.checkpoints.length}`),...state.checkpoints.map(item=>h('article',{key:item.checkpoint_id},h('strong',null,`${item.node_id??'项目规划'} · revision ${item.revision}`),h('pre',null,JSON.stringify(item.state,null,2))))),
        h('details',{className:'ari-section'},h('summary',null,`整理与复核待办 · ${(state.review_todos??[]).filter(item=>item.state!=='completed').length}`),...(state.review_todos??[]).filter(item=>item.state!=='completed').map(item=>h('p',{key:item.todo_id},`${item.todo_id} · ${item.node_id??'项目规划'} · ${item.trigger_kind} · ${item.trigger_ref} · ${item.state}`))),
        h('details',{className:'ari-section'},h('summary',null,`当前上下文来源 · ${state.context_preview.status}`),h('p',null,`摘要：${state.context_preview.source_digest??'无'} · ${state.context_preview.truncated?'已按内容块裁剪':'完整'}`),state.context_preview.error&&h('p',{role:'alert'},state.context_preview.error),h('pre',null,state.context_preview.text)),
        h('details',{className:'ari-section',open:true},h('summary',null,`项目会话 · ${sessionCards.length}`),...sessionCards.map(s=>h('article',{key:s.session_id},
          h('strong',null,`${s.detached?'历史会话':roles[s.role]} · ${s.node_id??'项目规划'}`),h('p',null,`${s.name??s.session_id} · ${s.native_status??'未加载'} · ${reasons[s.pause_reason]??s.pause_reason??'无暂停'}`),
          h('p',null,s.cwd??'目录未知'),h('button',{onClick:()=>navigate(s.session_id)},'打开会话'),
          s.pending_approvals>0&&h('p',{role:'status'},`等待审批 · ${s.approvals?.[0]?.reason??'未提供原因'}`),
          ['human','native_stop','finished','segment_complete','complete'].includes(s.pause_reason)&&h('button',{disabled,onClick:()=>act('resume',{targetSessionId:s.session_id})},'继续此会话'),
          ['requested','unverified'].includes(s.close_state)&&h('button',{disabled,onClick:()=>act('verify-close',{targetSessionId:s.session_id})},'核实工作段收尾'),
          ['fault','host_limit','unverified'].includes(s.pause_reason)&&h('button',{disabled,onClick:()=>act('retry',{targetSessionId:s.session_id})},'核实并重试'),
          h('details',null,h('summary',null,'关联历史与原生状态'),h('pre',null,JSON.stringify({goal:s.goal,jobs:s.jobs,intervals:state.associations.filter(a=>a.session_id===s.session_id)},null,2)))))),
        h('details',{className:'ari-section'},h('summary',null,`节点内部协作 · ${(state.specialists??[]).length}`),...(state.specialists??[]).map(s=>h('article',{key:s.task_id},h('strong',null,`${s.task_id} · ${s.purpose} · ${s.state}`),h('p',null,`${s.label} · ${s.node_id??'项目规划'} · ${s.context_mode==='blind'?'盲评：仅分配材料':'研究背景'}`),s.child_session_id&&h('button',{onClick:()=>navigate(s.child_session_id)},'打开专家会话'),(['running','unverified'].includes(s.state)||!s.exit_verified)&&h('button',{disabled,onClick:()=>act('verify-specialist',{taskId:s.task_id})},'核实专家退出'),s.error&&h('p',{role:'alert'},s.error)))),
        h('details',{className:'ari-section',open:blockedCreations.length>0},h('summary',null,'探索任务与恢复'),...(state.tasks??state.workflow?.tasks??[]).map(t=>h('article',{key:t.task_id},h('p',null,`${t.task_id} · ${t.node_id} · ${t.state}${t.error?' · '+t.error:''}`),blockedCreations.some(b=>b.task_id===t.task_id)&&h('p',null,'需要处理：创建结果待核实，保留 1 个研究槽位；未确认前不会自动重新创建。'),t.state==='unverified'&&h('button',{disabled,onClick:()=>act('verify-task',{taskId:t.task_id})},'核实是否未启动'),['failed','unverified'].includes(t.state)&&h('button',{disabled,onClick:()=>act('retry',{taskId:t.task_id})},'核实并重试创建')))),
        h('details',{className:'ari-section'},h('summary',null,'高级设置'),
          h('p',null,'科研指导按内容版本登记，只在相关任务上下文中取用方法卡。'),
          state.guidance&&h('p',null,`当前指导：${state.guidance.path} · ${state.guidance.version} · ${state.guidance.content_hash}`),
          h('input',{'aria-label':'科研指导文档路径',placeholder:'科研指导 Markdown 的绝对路径',value:guidancePath,onChange:e=>setGuidancePath(e.target.value)}),
          h('input',{'aria-label':'科研指导版本',placeholder:'可选版本名',value:guidanceVersion,onChange:e=>setGuidanceVersion(e.target.value)}),
          btn('登记科研指导','guidance.register',{path:guidancePath,version:guidanceVersion||undefined},disabled||!guidancePath.trim()),
          h('p',null,'移出后清除当前会话的项目上下文和插件续轮权限，原对话及材料保留。执行会话须先停止。'),btn('将此会话移出项目','detach')),
        h('details',{className:'ari-section'},h('summary',null,`接手记录 · ${(state.restorations??[]).length}`),...(state.restorations??[]).map(r=>h('p',{key:r.restoration_id},`${r.snapshot_id} · ${r.source_attempt_id} → ${r.target_attempt_id??'未登记工作段'}`)))));
  };
}
