/** Research 0.6.10: a navigable, read-only-first view of the native ledger. */
import {researchSessionGroups,researchExpertTitle,researchExpertCounts} from './session-groups.js';
import {researchSessionStatus,researchSummaryEqual} from './session-navigation.js';
import {renderResearchMarkdown} from './markdown.js';
import {renderPublicationCard,publicationFiles,publicationReport,publicationPreview} from './publication.js';
export function createResearchWorkbench(React, rpc, openSession, projectGraph, ResearchGraph, MarkdownText, researchStyles, bindSession, sessionContext=()=>({})) {
  const h=React.createElement;
  const NAV=[['overview','成果概览'],['process','研究过程'],['knowledge','成果与知识'],['materials','材料'],['runtime','运行与维护']];
  const runNames={manual:'待开始',running:'正在研究',paused:'已暂停',stopping:'正在停止',unverified:'停止待核实',stopped:'已停止',complete:'目标已结束',cold:'等待显式恢复'};
  const text=value=>typeof value==='string'?value:value?.preview??'';
  const brief=(value,max=170)=>{const s=text(value).replace(/\s+/g,' ').trim();return s.length>max?s.slice(0,max)+'…':s;};
  const format=value=>typeof value==='number'?(Math.abs(value)<.01?value.toFixed(5):value.toFixed(4)): '—';
  const label=(textValue,tone='muted')=>h('span',{className:`ari-chip ari-${tone}`},textValue);
  const button=(title,fn,props={})=>h('button',{type:'button',onClick:fn,...props},title);
  const fmtDate=value=>value?new Date(value).toLocaleString():'未记录';
  const materialKind=item=>item.kind==='report'||item.source_path?.toLowerCase().endsWith('.md')?'reports':
    item.kind==='software'?'deliverables':
    item.source_path?.toLowerCase().endsWith('.py')?'code':'results';
  function changeBlocks(oldText,newText) {
    const before=text(oldText).split('\n'),after=text(newText).split('\n');
    let start=0,end=0;
    while(start<before.length&&start<after.length&&before[start]===after[start])start++;
    while(end<before.length-start&&end<after.length-start&&before[before.length-1-end]===after[after.length-1-end])end++;
    return {removed:before.slice(start,before.length-end).join('\n'),added:after.slice(start,after.length-end).join('\n')};
  }
  function jsonPointer(document,pointer) {
    try {return pointer.slice(1).split('/').reduce((value,key)=>value[key.replace(/~1/g,'/').replace(/~0/g,'~')],document);}catch{return undefined;}
  }
  function markdown(value) {
    return renderResearchMarkdown(React,value);
  }
  function LinkRef({value,onOpen}) {return button(value,()=>onOpen(value),{className:'ari-ref'});}

  return function Workbench({sessionId,onSessionOpened,openView}) {
    React.useEffect(()=>openView&&bindSession?.(sessionId,{setView:view=>openView(view,'')}),[sessionId,openView,bindSession]);
    const [summary,setSummary]=React.useState(null),[presentation,setPresentation]=React.useState(null);
    const [section,setSection]=React.useState('overview'),[error,setError]=React.useState(''),[notice,setNotice]=React.useState('');
    const [pages,setPages]=React.useState({}),[knowledge,setKnowledge]=React.useState(null),[knowledgeFilters,setKnowledgeFilters]=React.useState({query:'',kind:'',status:'',nodeId:''});
    const [selectedNode,setSelectedNode]=React.useState(null),[nodeDetail,setNodeDetail]=React.useState(null),[nodePubs,setNodePubs]=React.useState([]),[nodeAttempts,setNodeAttempts]=React.useState([]),[detailTab,setDetailTab]=React.useState('result');
    const [selectedKnowledge,setSelectedKnowledge]=React.useState(null),[knowledgeDetail,setKnowledgeDetail]=React.useState(null),[knowledgeHistory,setKnowledgeHistory]=React.useState(null),[knowledgeLatest,setKnowledgeLatest]=React.useState(null);
    const [publications,setPublications]=React.useState({}),[publicationUI,setPublicationUI]=React.useState({});
    const publicationRequests=React.useRef(new Map());
    const [expertDetails,setExpertDetails]=React.useState({});
    const expertRequests=React.useRef(new Set());
    const [reader,setReader]=React.useState(null),[graphMode,setGraphMode]=React.useState(false),[graphData,setGraphData]=React.useState(null);
    const [search,setSearch]=React.useState(''),[statusFilter,setStatusFilter]=React.useState('all'),[busy,setBusy]=React.useState(false),[goal,setGoal]=React.useState('');
    const [maintenanceTab,setMaintenanceTab]=React.useState('current'),[materialFilter,setMaterialFilter]=React.useState('all'),[hintKind,setHintKind]=React.useState('');
    const [restorePreview,setRestorePreview]=React.useState(null),[guidance,setGuidance]=React.useState(null),[guidancePath,setGuidancePath]=React.useState(''),[guidanceVersion,setGuidanceVersion]=React.useState('');
    const lifecycle=React.useRef(0),pollSeq=React.useRef(0),readerSeq=React.useRef(0),selectionSeq=React.useRef(0),knowledgeSeq=React.useRef(0),pageSeq=React.useRef({}),pending=React.useRef(false);
    const scrollRef=React.useRef(null),scrollMemory=React.useRef({}),focusBeforeReader=React.useRef(null),scrollBeforeReader=React.useRef(0);
    function detailOverlayStyle() {
      const main=scrollRef.current;
      if(!main||typeof window==='undefined'||main.clientWidth>=1100)return undefined;
      const rect=main.getBoundingClientRect();
      let top=Math.max(0,rect.top),bottom=Math.min(window.innerHeight,rect.bottom);
      // The native conversation can scroll the whole Research view. Clip the
      // fixed drawer to every scroll viewport, not the off-screen main rect.
      for(let parent=main.parentElement;parent;parent=parent.parentElement) {
        if(!/(auto|scroll|hidden|clip)/.test(window.getComputedStyle(parent).overflowY))continue;
        const clip=parent.getBoundingClientRect();top=Math.max(top,clip.top);bottom=Math.min(bottom,clip.bottom);
      }
      return {position:'fixed',top,right:window.innerWidth-rect.right,
        bottom:window.innerHeight-bottom,width:main.clientWidth<650?rect.width:Math.min(410,rect.width)};
    }
    const context=sessionContext(sessionId),parentSessionId=context.parentSessionId;
    const readOnly=!!parentSessionId;
    const disabled=busy||!!error||!summary||readOnly;
    const projectSessionId=context.mainSessionId??parentSessionId??sessionId;
    const readEndpoints=new Set(['workbench.summary','workbench.page','presentation.get','knowledge.page','reference.get','reference.chunk','reference.content','reference.entries','guidance.status','context.preview','history.page']);
    const call=React.useCallback(async(endpoint,payload={})=>{
      if(readOnly&&!readEndpoints.has(endpoint))throw new Error('专家历史仅供阅读，请返回研究主会话执行操作。');
      const requestSessionId=readOnly?projectSessionId:sessionId;
      const response=await rpc(endpoint,{sessionId:requestSessionId,operationId:`${requestSessionId}:ui:${crypto.randomUUID()}`,...payload});
      if(!response.ok)throw new Error(response.error?.message??'请求失败');
      return response.value;
    },[sessionId,projectSessionId,readOnly]);
    const refresh=React.useCallback(async()=>{
      const seq=++pollSeq.current,life=lifecycle.current;
      try {const value=await call('workbench.summary');if(life!==lifecycle.current||seq!==pollSeq.current)return;setSummary(old=>researchSummaryEqual(old,value)?old:value);setError('');}
      catch(e){if(life===lifecycle.current&&seq===pollSeq.current)setError(e.message);}
    },[call]);
    React.useEffect(()=>{
      lifecycle.current++;let active=true,timer;
      const poll=async()=>{if(!document.hidden)await refresh();if(active)timer=setTimeout(poll,3000);};
      poll();const visible=()=>{if(!document.hidden)refresh();};document.addEventListener('visibilitychange',visible);
      return()=>{active=false;lifecycle.current++;readerSeq.current++;selectionSeq.current++;clearTimeout(timer);document.removeEventListener('visibilitychange',visible);};
    },[refresh]);
    React.useEffect(()=>{if(!summary)return;let live=true;call('presentation.get').then(value=>{if(live)setPresentation(value);}).catch(e=>{if(live)setPresentation({status:'invalid',message:e.message});});return()=>{live=false;};},[call,summary?.project?.project_id,summary?.final_publication?.publication_id]);

    async function fullReference(ref) {
      const selected=await call('reference.get',{ref});
      const value=selected.value;
      if(!value?.summary?.truncated&&!value?.statement?.truncated)return selected;
      const chunks=[];let offset=0,part;
      do {
        try {part=await call('reference.chunk',{ref,offset});}
        catch {throw new Error('当前服务仅返回截断预览，无法读取完整原文。请在研究结束、更新服务后重试。');}
        if(!part.chunk||part.next_offset!=null&&part.next_offset<=offset)throw new Error('完整原文分块响应无效');
        chunks.push(Uint8Array.from(atob(part.chunk),c=>c.charCodeAt(0)));offset=part.next_offset;
      } while(offset!=null);
      const all=new Uint8Array(chunks.reduce((sum,v)=>sum+v.length,0));let at=0;
      for(const bytes of chunks){all.set(bytes,at);at+=bytes.length;}
      return JSON.parse(new TextDecoder().decode(all));
    }
    function pubState(id,patch){setPublicationUI(old=>({...old,[id]:{...old[id],...patch}}));}
    async function loadPublication(id) {
      if(publications[id])return publications[id];
      if(publicationRequests.current.has(id))return publicationRequests.current.get(id);
      const life=lifecycle.current;pubState(id,{loading:true,error:''});
      const request=(async()=>{
        try {
          const result=await fullReference(`pub/${id}`);
          if(result.kind!=='publication'||result.value?.publication_id!==id)throw new Error('发布记录与请求不匹配');
          if(life===lifecycle.current){setPublications(old=>({...old,[id]:result.value}));pubState(id,{loading:false,loaded:true});}
          return result.value;
        }catch(e){if(life===lifecycle.current)pubState(id,{loading:false,error:e.message});return null;}
        finally {publicationRequests.current.delete(id);}
      })();publicationRequests.current.set(id,request);return request;
    }
    async function navigate(id,options={}) {
      try {
        // Durable session rows outlive the runtime list. Never infer child identity
        // only from currently attached agents (finished specialists disappear there).
        const row=pages.sessions?.items?.find(item=>item.session_id===id)??summary?.runtime?.sessions?.find(item=>item.session_id===id);
        if(!options.parentSessionId&&id!==summary?.runtime?.main_session_id&&id!==projectSessionId) {
          const task=pages.specialists?.items?.find(item=>item.child_session_id===id)??(summary?(await loadAll('specialists')).find(item=>item.child_session_id===id):null);
          // 0.6.10 compact pages omitted the parent. Expand the durable record
          // on demand so an already-running host needs no restart to navigate.
          const parent=task?.parent_session_id??(task?.task_id?(await call('reference.get',{ref:task.task_id})).value?.parent_session_id:undefined);
          if(parent)options={...options,parentSessionId:parent,identity:{role:'specialist',node_id:task.node_id,label:task.label}};
          if(row?.role==='specialist'&&!options.parentSessionId)throw new Error('该专家缺少父会话地址，请从所属研究节点的子会话目录打开。');
        }
        const mainSessionId=summary?.runtime?.main_session_id??context.mainSessionId;
        if(mainSessionId)options={...options,mainSessionId};
        if(!options.identity&&row)options={...options,identity:row};
        await openSession(id,options);onSessionOpened?.();
      }
      catch(e){setNotice(`无法打开原生会话：${e.message}`);}
    }
    async function loadPage(collection,{more=false,nodeId=null,limit=20,kind=null}={}) {
      const key=nodeId?`${collection}:${nodeId}`:collection,previous=pages[key];
      if(more&&!previous?.cursor)return;
      const sequence=(pageSeq.current[key]??0)+1;pageSeq.current[key]=sequence;
      const next=await call('workbench.page',{collection,cursor:more?previous.cursor:undefined,limit,nodeId,kind});
      if(pageSeq.current[key]!==sequence)return;
      setPages(current=>({...current,[key]:more?{...next,items:[...(current[key]?.items??[]),...next.items]}:next}));
      return next;
    }
    async function loadKnowledge(more=false) {
      if(more&&!knowledge?.cursor)return;
      const seq=++knowledgeSeq.current;
      const next=await call('knowledge.page',{...knowledgeFilters,cursor:more?knowledge.cursor:undefined,limit:20});
      if(seq===knowledgeSeq.current)setKnowledge(current=>more?{...next,items:[...(current?.items??[]),...next.items]}:next);
    }
    React.useEffect(()=>{if(!summary)return;let live=true;const revision=summary.revision;
      const run=async()=>{try {
        if(section==='process')await loadPage('nodes');
        if(section==='materials')await loadPage('publications');
        if(section==='knowledge')await Promise.all([loadKnowledge(),loadPage('nodes')]);
        if(section==='runtime')await Promise.all(['sessions','tasks','review_todos','hints','specialists','checkpoints','snapshots','restorations'].map(collection=>loadPage(collection,{limit:collection==='hints'?8:20,kind:collection==='hints'?hintKind:null})));
        if(section==='overview'&&!presentation?.value?.report_ref)await loadPage('publications');
      } catch(e){if(live)setNotice(`无法读取${NAV.find(n=>n[0]===section)?.[1]}：${e.message}`);}};run();
      return()=>{live=false;};
    },[section,summary?.revision,knowledgeFilters.query,knowledgeFilters.kind,knowledgeFilters.status,knowledgeFilters.nodeId,hintKind]);
    React.useEffect(()=>{if(section!=='runtime'||!summary)return;let live=true;call('guidance.status').then(value=>{if(live)setGuidance(value);}).catch(()=>{});return()=>{live=false;};},[section,summary?.project?.project_id]);
    // The presentation may arrive after the overview's first render; a compact
    // publications page remains useful as a fallback and material index.
    function changeSection(next) {
      if(scrollRef.current)scrollMemory.current[section]=scrollRef.current.scrollTop;
      setSection(next);setReader(null);setSelectedKnowledge(null);setSelectedNode(null);
      requestAnimationFrame(()=>{if(scrollRef.current)scrollRef.current.scrollTop=scrollMemory.current[next]??0;});
    }
    async function loadAll(collection) {
      let cursor=null,items=[];
      do {const page=await call('workbench.page',{collection,cursor:cursor??undefined,limit:100});items.push(...page.items);cursor=page.cursor;}while(cursor);
      return items;
    }
    async function showGraph() {
      setGraphMode(true);
      if(graphData)return;
      try {const [nodes,dependencies,relations]=await Promise.all(['nodes','dependencies','relations'].map(loadAll));setGraphData({nodes,dependencies,relations});}
      catch(e){setNotice(`无法读取研究图：${e.message}`);}
    }
    async function chooseNode(id) {
      setSelectedNode(id);setDetailTab('result');setNodeDetail(null);setNodePubs([]);setNodeAttempts([]);
      const seq=++selectionSeq.current;
      try {const [detail,pubs,attempts]=await Promise.all([call('reference.get',{ref:id}),call('workbench.page',{collection:'publications',nodeId:id,limit:100}),call('workbench.page',{collection:'attempts',nodeId:id,limit:100})]);
        if(seq===selectionSeq.current){setNodeDetail(detail.value);setNodePubs(pubs.items);setNodeAttempts(attempts.items);}}
      catch(e){if(seq===selectionSeq.current)setNotice(`无法读取节点：${e.message}`);}
    }
    async function chooseKnowledge(ref) {
      setSelectedKnowledge(ref);setKnowledgeDetail(null);setKnowledgeHistory(null);setKnowledgeLatest(null);const seq=++selectionSeq.current;
      try {const detail=await fullReference(ref);const id=detail.value?.knowledge_id;
        const history=id?await call('knowledge.page',{knowledgeId:id,history:true,limit:100}):null;
        const latestRef=history?.items?.at(-1)?.ref;
        const latest=latestRef&&latestRef!==ref?await call('reference.get',{ref:latestRef}):null;
        if(seq===selectionSeq.current){setKnowledgeDetail(detail.value);setKnowledgeHistory(history);setKnowledgeLatest(latest?.value??null);}}
      catch(e){if(seq===selectionSeq.current)setNotice(`无法读取知识：${e.message}`);}
    }
    async function openReader(ref,pointer=null) {
      if(!reader){focusBeforeReader.current=document.activeElement;scrollBeforeReader.current=scrollRef.current?.scrollTop??0;}
      setReader({ref,pointer,loading:true});const seq=++readerSeq.current;
      try {
        let first=await call('reference.content',{ref,offset:0});
        if(first.kind==='directory') {
          const listing=await call('reference.entries',{ref});
          if(seq===readerSeq.current)setReader({ref,kind:'directory',resolution:listing.resolution,items:listing.items,cursor:listing.cursor});
          return;
        }
        if(first.kind==='binary') {if(seq===readerSeq.current)setReader({ref,kind:'binary',...first});return;}
        if(first.kind==='text') {
          const bytes=[];let part=first;
          while(part){bytes.push(Uint8Array.from(atob(part.chunk),c=>c.charCodeAt(0)));part=part.next_offset==null?null:await call('reference.content',{ref,offset:part.next_offset});}
          const size=bytes.reduce((a,b)=>a+b.length,0),all=new Uint8Array(size);let offset=0;
          for(const fragment of bytes){all.set(fragment,offset);offset+=fragment.length;}
          if(seq===readerSeq.current)setReader({ref,pointer,kind:'text',resolution:first.resolution,body:new TextDecoder().decode(all),truncated:first.truncated,total_bytes:first.total_bytes});
          return;
        }
        // A snapshot or publication-level ref is a container. The user sees
        // its available frozen entries, rather than a raw metadata dump.
        const meta=await call('reference.get',{ref});
        let items=[];
        if(meta.kind==='snapshot')items=(meta.value?.manifest??[]).filter(item=>item.source_path&&!item.source_path.startsWith('._')).map(item=>({name:item.source_path,kind:item.kind,ref:`${ref}#${item.source_path}`,status:item.status}));
        if(meta.kind==='publication')items=(meta.value?.items??[]).map(item=>({name:item.item_id,kind:item.kind,ref:item.ref}));
        if(seq===readerSeq.current)setReader({ref,kind:items.length?'directory':'unavailable',resolution:first.resolution,items,cursor:null,metadata:meta.value});
      } catch(e){if(seq===readerSeq.current)setReader({ref,kind:'error',message:e.message});}
    }
    function closeReader(){readerSeq.current++;setReader(null);requestAnimationFrame(()=>{if(scrollRef.current)scrollRef.current.scrollTop=scrollBeforeReader.current;focusBeforeReader.current?.focus?.();});}
    async function act(endpoint,payload={}) {
      if(pending.current||error||readOnly)return;pending.current=true;setBusy(true);setNotice('');
      try {const value=await call(endpoint,payload);if(endpoint==='restore.preview')setRestorePreview(value);else if(endpoint==='restore.create')setRestorePreview(null);
        setNotice(value?.message??'操作已完成');if(value?.sessionId)await navigate(value.sessionId,endpoint==='discussion.open'?{identity:{role:'discussion',node_id:payload.nodeId}}:{});await refresh();}
      catch(e){setNotice(`操作失败：${e.message}`);}finally{pending.current=false;setBusy(false);}
    }
    const pres=presentation?.status==='ready'?presentation.value:null;
    const run=summary?.runtime??{},project=summary?.project??{};
    const nodes=pages.nodes?.items??[];
    const visibleNodes=nodes.filter(node=>(statusFilter==='all'||node.status===statusFilter)&&(!search||`${node.node_id} ${node.question} ${pres?.nodes?.[node.node_id]?.title??''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())));
    const overviewMetric=(metric,index)=>{
      const left=metric.baseline?.value,right=metric.current?.value,comparable=typeof left==='number'&&typeof right==='number';
      const max=comparable?Math.max(Math.abs(left),Math.abs(right),metric.threshold?.value??0,.001):1;
      const display=value=>typeof value==='number'&&metric.display_decimals!=null?value.toFixed(metric.display_decimals):format(value);
      return h('article',{key:metric.id??index,className:'ari-metric'},
        h('div',{className:'ari-metric-top'},h('strong',null,metric.label),label(metric.split??'口径未注明')),
        h('div',{className:'ari-metric-values'},h('span',null,'基线 ',h('b',null,display(left))),h('span',null,'当前 ',h('b',null,display(right)),metric.unit??'')),
        comparable&&h('div',{className:'ari-compare','aria-label':`基线 ${display(left)}；当前 ${display(right)}`},
          h('span',{style:{width:`${Math.max(2,Math.abs(left)/max*100)}%`},className:'ari-base-bar'}),
          h('span',{style:{width:`${Math.max(2,Math.abs(right)/max*100)}%`},className:'ari-current-bar'})),
        metric.threshold&&h('small',null,`门槛 ${metric.threshold.operator} ${metric.threshold.value}${metric.unit??''}`,
          metric.threshold.ref&&button('门槛来源',()=>openReader(metric.threshold.ref))),
        h('small',null,`${metric.population??''} · ${metric.protocol??''}`),
        h('div',{className:'ari-source'},button('查看基线',()=>openReader(metric.baseline.ref,metric.baseline.pointer)),button('查看当前',()=>openReader(metric.current.ref,metric.current.pointer)),
          !comparable&&label(metric.comparison_warning??'冻结读值不可用','warning')));
    };
    const pubCard=(initial,category='all',featured=false)=>{
      const id=initial.publication_id,pub=publications[id]??initial,state=publicationUI[id]??{};
      return renderPublicationCard(React,{pub,state,category,featured,materialKind,
        onToggle:async()=>{if(state.expanded&&!state.error){pubState(id,{expanded:false});return;}pubState(id,{expanded:true});await loadPublication(id);},
        onRaw:()=>pubState(id,{raw:!state.raw}),
        onFiles:async(open=true)=>{pubState(id,{files:open});if(open)await loadPublication(id);},
        onRead:openReader});
    };
    React.useEffect(()=>{const id=summary?.final_publication?.publication_id;if(id)void loadPublication(id);},[summary?.final_publication?.publication_id]);
    function overview() {
      const latest=summary?.final_publication;
      return h('div',{className:'ari-view'},
        h('div',{className:'ari-view-heading'},h('div',null,h('span',{className:'ari-eyebrow'},'研究结论'),h('h2',null,pres?.title??'项目成果概览')),
          label(runNames[run.state]??run.state??'状态未知',run.state==='complete'?'success':'muted')),
        pres?h('p',{className:'ari-lead'},pres.summary):(presentation?.status==='invalid'||!latest)&&h('div',{className:'ari-callout'},
          h('strong',null,presentation?.status==='invalid'?'展示资料不可用':'本项目尚无展示摘要'),
          h('p',null,presentation?.message??'下方保留登记的阶段材料；报告与研究过程仍可阅读。')),
        pres?.stale&&h('p',{className:'ari-callout',role:'status'},'展示资料所绑定的发布版本已过期；请核对新材料后更新摘要。'),
        h('div',{className:'ari-overview-grid'},
          latest?pubCard(latest,'all',true):h('section',{className:'ari-panel'},h('h3',null,'当前成果'),h('p',null,'尚无已登记的阶段成果。'),button('查看研究过程',()=>changeSection('process'))),
          h('section',{className:'ari-panel'},h('span',{className:'ari-eyebrow'},'交付与当前状态'),
            h('p',null,`研究目标：${runNames[run.state]??run.state??'状态未知'}`),
            h('p',null,`当前执行：${run.running_count??0} 个会话 · 审批 ${run.pending_approvals??0} 项`),
            h('p',null,`知识 ${summary.counts?.knowledge??0} 个修订 · 待整理 ${summary.review_queue?.pending_total??0} 项`),
            (pres?.deliverables??[]).map((item,i)=>h('div',{key:i,className:'ari-delivery'},h('strong',null,item.label),button('打开材料',()=>openReader(item.ref)))),
            !pres?.deliverables?.length&&button('浏览已发布材料',()=>changeSection('materials')))),
        pres?.metrics?.length?h('section',{className:'ari-panel'},h('div',{className:'ari-section-head'},h('div',null,h('span',{className:'ari-eyebrow'},'可追溯的量化结果'),h('h3',null,'基线与最终方案')),h('small',null,'每项只比较相同数据划分与口径')),
          h('div',{className:'ari-metric-grid'},...pres.metrics.slice(0,4).map(overviewMetric))):
          h('section',{className:'ari-panel'},h('h3',null,'阶段材料'),...(pages.publications?.items??[]).filter(pub=>pub.publication_id!==latest?.publication_id).slice(-3).reverse().map(pub=>pubCard(pub)),
            button('查看全部材料',()=>changeSection('materials'))),
        run.pending_approvals>0&&h('section',{className:'ari-panel ari-attention'},h('h3',null,'需要处理的原生审批'),
          ...(run.approvals??[]).map(a=>h('p',{key:a.approval_id},a.reason??'审批详情',button('打开会话',()=>navigate(a.session_id))))),
        h('details',{className:'ari-panel'},h('summary',null,'完整研究目标'),h('p',{className:'ari-prose'},project.goal)));
    }
    function process() {
      const metadata=pres?.nodes??{};
      const base=graphData??{nodes,dependencies:[],relations:[]};
      const model=projectGraph(base),ids=new Set(model.nodes.map(n=>n.node_id));
      return h('div',{className:'ari-view'},h('div',{className:'ari-view-heading'},h('div',null,h('span',{className:'ari-eyebrow'},'研究演进'),h('h2',null,'研究过程')),
        h('div',{className:'ari-segmented'},button('时间线',()=>setGraphMode(false),{'aria-pressed':!graphMode}),button('执行关系图',showGraph,{'aria-pressed':graphMode}))),
        h('div',{className:'ari-filterbar'},h('input',{'aria-label':'搜索研究节点',placeholder:'搜索编号或问题',value:search,onChange:e=>setSearch(e.target.value)}),
          h('select',{'aria-label':'筛选节点状态',value:statusFilter,onChange:e=>setStatusFilter(e.target.value)},...['all',...new Set(nodes.map(n=>n.status))].map(s=>h('option',{key:s,value:s},s==='all'?'全部登记状态':s)))),
        h('div',{className:'ari-work-area'},h('div',{className:'ari-work-main'},
          graphMode?(graphData?h(ResearchGraph,{model,visibleIds:ids,selected:selectedNode,onSelect:chooseNode,onEdge:e=>setNotice(`${e.source} → ${e.target}：${e.records.map(r=>r.label??r.relation_type).join('、')}`),focused:summary.attempt?.node_id,labels:metadata}):h('p',{className:'ari-empty'},'正在读取执行关系图…')):
          h('ol',{className:'ari-timeline'},...visibleNodes.map(node=>{
            const info=metadata[node.node_id];return h('li',{key:node.node_id},h('button',{className:'ari-step','aria-pressed':selectedNode===node.node_id,onClick:()=>chooseNode(node.node_id)},
              h('span',{className:'ari-step-marker'},node.node_id),h('span',{className:'ari-step-body'},h('strong',null,info?.title??brief(node.question,70)),
                h('span',null,info?.summary??brief(node.question,180)),h('small',null,info?.outcome??`登记状态：${node.status}`)),
              h('span',{className:'ari-step-arrow','aria-hidden':'true'},'↗')),
              button('讨论进展',()=>act('discussion.open',{nodeId:node.node_id}),{className:'ari-node-discuss',disabled,'aria-label':`讨论 ${node.node_id} 的进展`})); })),
          !pages.nodes?h('p',{className:'ari-empty'},'正在读取研究节点…'):
          !visibleNodes.length&&h('p',{className:'ari-empty'},'没有匹配的研究节点。'),
          !graphMode&&pages.nodes?.cursor&&button('加载更多节点',()=>loadPage('nodes',{more:true}))),
          selectedNode&&h('aside',{className:'ari-detail','aria-label':'节点详情',style:detailOverlayStyle()},
            h('div',{className:'ari-detail-head'},h('strong',null,selectedNode),button('关闭',()=>{selectionSeq.current++;setSelectedNode(null);setNodeDetail(null);})),
            h('div',{className:'ari-discussion-actions'},
              button('围绕此节点讨论',()=>act('discussion.open',{nodeId:selectedNode}),{disabled,className:'ari-primary'}),
              h('details',null,h('summary',null,'更多'),button('新建另一场讨论',()=>act('discussion.open',{nodeId:selectedNode,fresh:true}),{disabled})),
              h('small',null,'在独立会话中讨论；已有讨论会继续打开。')),
            h('div',{className:'ari-detail-tabs'},...[['result','结果'],['evidence','证据'],['history','计划与历史']].map(([id,name])=>button(name,()=>setDetailTab(id),{'aria-pressed':detailTab===id,key:id}))),
            !nodeDetail?h('p',null,'正在读取节点…'):
            detailTab==='result'?h('div',null,h('h3',null,metadata[selectedNode]?.title??brief(nodeDetail.question,90)),
              label(metadata[selectedNode]?.outcome??`登记状态：${nodeDetail.status}`),
              h('p',null,metadata[selectedNode]?.summary??'本节点尚无独立的展示摘要；以下是登记的阶段材料。'),
              ...nodePubs.map(pub=>pubCard(pub)),!nodePubs.length&&h('p',null,'暂无归属于此节点的阶段材料；项目级材料可在“材料”中浏览。')):
            detailTab==='evidence'?h('div',null,h('h3',null,'固定输入与登记证据'),
              ...(nodeDetail.inputs??[]).map((ref,i)=>h('p',{key:i},h(LinkRef,{value:ref,onOpen:openReader}))),
              ...(metadata[selectedNode]?.refs??[]).filter(ref=>typeof ref==='string'&&(ref.startsWith('pub/')||ref.startsWith('S-'))).map(ref=>h('p',{key:`summary-${ref}`},h(LinkRef,{value:ref,onOpen:openReader}))),
              ...nodePubs.flatMap(pub=>(pub.items??[]).map(item=>h('p',{key:item.ref},h(LinkRef,{value:item.ref,onOpen:openReader})))),
              h('p',{className:'ari-muted'},'节点关联仅使用账本登记字段；未填写的知识归属不从正文猜测。')):
            h('div',null,h('h3',null,'登记问题'),h('p',{className:'ari-prose'},nodeDetail.question),
              h('h3',null,'提出理由'),h('p',{className:'ari-prose'},nodeDetail.why_now??'未记录'),
              h('h3',null,'原始研究计划'),markdown(nodeDetail.plan??'未记录'),
              h('h3',null,'工作段历史'),...nodeAttempts.map(attempt=>h('p',{key:attempt.attempt_id},`${attempt.attempt_id} · ${attempt.state} · ${fmtDate(attempt.started_at)} → ${fmtDate(attempt.ended_at)}`)),
              h('dl',{className:'ari-facts'},h('dt',null,'登记状态'),h('dd',null,nodeDetail.status),h('dt',null,'原始谱系'),h('dd',null,nodeDetail.origin_kind??'未记录'),
                h('dt',null,'问题版本'),h('dd',null,nodeDetail.question_ref??'未记录'))))));
    }
    function knowledgeView() {
      const rows=knowledge?.items??[];
      return h('div',{className:'ari-view'},h('div',{className:'ari-view-heading'},h('div',null,h('span',{className:'ari-eyebrow'},'结果与主张'),h('h2',null,'成果与知识')),
        h('small',null,`现行版本匹配 ${knowledge?.total??0} 条；版本状态不表示科学验证`)),
        pres?.metrics?.length>0&&h('section',{className:'ari-panel'},h('h3',null,'可比结果'),h('div',{className:'ari-metric-grid'},...pres.metrics.slice(0,4).map(overviewMetric))),
        h('div',{className:'ari-filterbar'},h('input',{'aria-label':'搜索项目知识',placeholder:'搜索知识编号或正文',value:knowledgeFilters.query,onChange:e=>setKnowledgeFilters({...knowledgeFilters,query:e.target.value})}),
          h('select',{'aria-label':'筛选知识类型',value:knowledgeFilters.kind,onChange:e=>setKnowledgeFilters({...knowledgeFilters,kind:e.target.value})},...['','finding','open_question','decision','method','risk'].map(s=>h('option',{key:s,value:s},s||'全部类型'))),
          h('select',{'aria-label':'筛选知识状态',value:knowledgeFilters.status,onChange:e=>setKnowledgeFilters({...knowledgeFilters,status:e.target.value})},...['','proposed','working','verified','retracted'].map(s=>h('option',{key:s,value:s},s||'全部状态'))),
          h('select',{'aria-label':'筛选知识节点',value:knowledgeFilters.nodeId,onChange:e=>setKnowledgeFilters({...knowledgeFilters,nodeId:e.target.value})},
            h('option',{value:''},'所有节点'),h('option',{value:'__unassigned__'},'未登记节点'),...(pages.nodes?.items??[]).map(n=>h('option',{key:n.node_id,value:n.node_id},n.node_id)))),
        h('div',{className:'ari-work-area'},h('div',{className:'ari-work-main'},
          h('div',{className:'ari-row-list'},...rows.map(item=>h('button',{key:item.ref,className:'ari-knowledge-row','aria-pressed':selectedKnowledge===item.ref,onClick:()=>chooseKnowledge(item.ref)},
            h('span',{className:'ari-row-head'},h('strong',null,item.ref),label(item.status)),
            h('span',null,publicationPreview(item.statement)),h('small',null,`${item.kind} · ${item.node_id??'未登记节点'} · 证据 ${item.evidence_refs?.length??0} 项`)))),
          !knowledge?h('p',{className:'ari-empty'},'正在读取现行知识…'):
          !rows.length&&h('p',{className:'ari-empty'},'没有匹配的现行知识。'),knowledge?.cursor&&button('加载更多知识',()=>loadKnowledge(true))),
          selectedKnowledge&&h('aside',{className:'ari-detail','aria-label':'知识详情',style:detailOverlayStyle()},
            h('div',{className:'ari-detail-head'},h('strong',null,selectedKnowledge),button('关闭',()=>{selectionSeq.current++;setSelectedKnowledge(null);})),
            knowledgeDetail?h('div',null,renderResearchMarkdown(React,knowledgeDetail.statement,{proseHeadings:true}),
              knowledgeLatest&&h('div',{className:'ari-revision-diff'},h('h3',null,`与现行版本 ${knowledgeHistory?.items?.at(-1)?.ref} 比较`),
                (()=>{const diff=changeBlocks(knowledgeDetail.statement,knowledgeLatest.statement);return h('div',null,
                  diff.removed&&h('pre',{className:'ari-diff-old'},`旧版独有\n${diff.removed}`),
                  diff.added&&h('pre',{className:'ari-diff-new'},`现行版本新增\n${diff.added}`),
                  !diff.added&&!diff.removed&&h('p',null,'正文相同；来源和状态仍可能不同。'));})()),
              h('h3',null,'证据'),...(knowledgeDetail.evidence_refs??[]).map(ref=>h('p',{key:ref},h(LinkRef,{value:ref,onOpen:openReader}))),
              ...(knowledgeDetail.field_checks??[]).map((check,i)=>h('p',{key:i},`字段检查：${check.result==='consistent'?'声明字段与冻结文件一致':check.result==='inconsistent'?'声明字段与冻结文件不一致':'无法检查'}`)),
              h('details',null,h('summary',null,'来源与适用条件'),h('pre',null,JSON.stringify({asserted_at:knowledgeDetail.asserted_at,execution_refs:knowledgeDetail.execution_refs,source_identity:knowledgeDetail.source_identity,scope:knowledgeDetail.scope,conditions:knowledgeDetail.conditions},null,2))),
              h('h3',null,'修订历史'),...(knowledgeHistory?.items??[]).map(item=>button(`${item.ref}${item.ref===selectedKnowledge?' · 当前查看':''}`,()=>chooseKnowledge(item.ref),{key:item.ref,className:'ari-history-link'})),
              knowledgeHistory?.items?.length>1&&h('p',{className:'ari-muted'},'选择历史版本查看其精确内容与来源；新增版本本身不代表旧版有风险。')):
              h('p',null,'正在读取知识…'))));
    }
    function materials() {
      return h('div',{className:'ari-view'},h('div',{className:'ari-view-heading'},h('div',null,h('span',{className:'ari-eyebrow'},'可复核材料'),h('h2',null,'报告与交付物')),
        h('small',null,`${pages.publications?.total??0} 次阶段发布`)),
        pres?.report_ref&&h('section',{className:'ari-feature-file'},h('div',null,h('span',{className:'ari-eyebrow'},'最终报告'),h('h3',null,'直接阅读完整报告'),h('small',null,pres.report_ref)),button('阅读报告',()=>openReader(pres.report_ref),{className:'ari-primary'})),
        pres?.deliverables?.length>0&&h('section',{className:'ari-panel'},h('h3',null,'最终交付'),...(pres.deliverables.map((item,i)=>h('div',{key:i,className:'ari-delivery'},h('strong',null,item.label),button('打开',()=>openReader(item.ref)))))),
        h('div',{className:'ari-segmented'},...[['all','全部'],['reports','报告'],['results','结果数据'],['code','代码'],['deliverables','交付']].map(([id,title])=>button(title,()=>setMaterialFilter(id),{'aria-pressed':materialFilter===id,key:id}))),
        h('div',{className:'ari-row-list'},...(pages.publications?.items??[]).map(pub=>pubCard(pub,materialFilter))),
        !pages.publications?.items?.length&&h('p',{className:'ari-empty'},'尚无已登记的阶段材料。'),
        pages.publications?.cursor&&button('加载更多发布',()=>loadPage('publications',{more:true})));
    }
    function runtime() {
      const collections={sessions:'项目会话',tasks:'探索任务',review_todos:'历史整理事项',hints:'结构候选线索',specialists:'节点内部协作',checkpoints:'节点检查点',snapshots:'历史快照',restorations:'接手记录'};
      const current=['sessions','tasks','specialists'],history=['sessions','tasks','review_todos','hints','checkpoints','snapshots','restorations'];
      const group=maintenanceTab==='current'?current:history;
      const disabled=busy||!!error||!summary||readOnly;
      const actions=run.state==='complete'?[]:run.state==='running'?[['暂停研究','pause'],['停止研究','stop']]:run.state==='paused'||run.state==='cold'?[['继续研究','resume'],['停止研究','stop']]:run.state==='unverified'?[['核实停止','verify-stop']]:[['开始研究','auto']];
      const historicTask=item=>item.state==='failed'&&(run.state==='complete'||pres?.nodes?.[item.node_id]?.outcome?.includes('替代'));
      const historicSession=item=>!!item.detached||(run.state==='complete'&&item.session_id!==run.main_session_id);
      function rowsFor(name,unfiltered=false) {
        const all=(pages[name]?.items??[]).map(item=>{
          if(name!=='sessions')return item;
          const taskRow=Object.entries(pages).filter(([key])=>key==='specialists'||key.startsWith('specialists:')).flatMap(([,page])=>page.items??[]).find(task=>task.child_session_id===item.session_id);
          const task=taskRow?{...taskRow,...expertDetails[taskRow.task_id]}:null;
          return {...item,...run.sessions?.find(live=>live.session_id===item.session_id),
            ...(task?{role:'specialist',node_id:task.node_id??item.node_id,task_id:task.task_id??item.task_id,label:task.label??item.label,purpose:task.purpose??item.purpose,specialist_state:task.state,parent_session_id:task.parent_session_id??item.parent_session_id}:{})};
        });
        if(name==='tasks')return all.filter(item=>maintenanceTab==='history'?historicTask(item):!historicTask(item));
        if(name==='sessions')return unfiltered?all:all.filter(item=>maintenanceTab==='history'?historicSession(item):!historicSession(item));
        if(name!=='hints')return all;
        const groups=new Map();
        for(const item of all){const key=`${item.target}:${item.class}`;const group=groups.get(key)??{...item,members:[],repair_evidence:[]};group.members.push(item);group.repair_evidence.push(...(item.repair_evidence??[]));groups.set(key,group);}
        return [...groups.values()];
      }
      function maintenanceRow(name,item,i,children=[],onToggle) {return h('details',{key:item.sessionKey??item.task_id??item.session_id??item.todo_id??item.snapshot_id??item.checkpoint_id??item.restoration_id??`${item.target}:${item.class}:${i}`,className:'ari-maintenance-row',onToggle},
            h('summary',null,h('strong',null,name==='sessions'?(item.groupTitle??(item.role==='specialist'?researchExpertTitle(item):`${({main:'研究主会话',node_core:'研究节点',exploration:'探索会话',specialist:'专家会话',discussion:'讨论会话',handoff:'接手会话'}[item.role]??'会话')}${item.node_id?` · ${item.node_id}`:''}`)):name==='specialists'?researchExpertTitle(item):item.session_id??item.task_id??item.todo_id??item.snapshot_id??item.checkpoint_id??item.target??item.restoration_id??`记录 ${i+1}`),
              h('span',null,` · ${name==='sessions'?(item.groupTitle?'父会话未在当前列表':researchSessionStatus(item)):item.state??item.pause_reason??item.class??item.trigger_kind??''}${item.members?.length>1?` · 同类 ${item.members.length} 条`:''}`),item.expertCounts&&h('span',{className:'ari-session-count'},item.expertCounts)),
            item.contextOnly&&h('small',{className:'ari-muted'},'所属父会话（归属上下文）'),
            h('p',null,item.label?text(item.label):(item.purpose?({domain:'领域专家',review:'审阅专家'}[text(item.purpose)]??text(item.purpose)):item.error??item.trigger_ref??item.node_id??'')),
            name==='hints'&&h('div',null,h('p',null,`修复记录：${item.repair_evidence?.length?item.repair_evidence.join('、'):'无'}`),
              ...(item.members??[]).map((member,j)=>h('p',{key:j},`${member.source??member.target}：${JSON.stringify(member.evidence??{})}`))),
            name==='review_todos'&&h('p',null,'此项为历史整理队列，不是原生审批。'),
            name==='sessions'&&h('small',null,item.session_id),
            name==='sessions'&&item.pause_reason==='wait'&&h('p',null,`正在等待：${(item.waiting??[]).map(id=>{const task=pages.tasks?.items?.find(t=>t.task_id===id);return task?.node_id??id;}).join('、')||'已派发任务'}。任务进展将由调度器通知主会话。`),
            name==='sessions'&&item.native_status==='running'&&h('p',null,`原生会话运行中${item.turn?` · 第 ${item.turn} 轮`:''}；打开对话可查看实时输出。`),
            (item.session_id??item.child_session_id)&&button('打开会话',()=>navigate(item.session_id??item.child_session_id,{parentSessionId:item.parent_session_id})),
            name==='sessions'&&['human','native_stop','finished','segment_complete','complete'].includes(item.pause_reason)&&button('继续此会话',()=>act('resume',{targetSessionId:item.session_id}),{disabled}),
            name==='sessions'&&['requested','unverified'].includes(item.close_state)&&button('核实工作段收尾',()=>act('verify-close',{targetSessionId:item.session_id}),{disabled}),
            name==='sessions'&&['fault','host_limit','unverified'].includes(item.pause_reason)&&button('核实并重试会话',()=>act('retry',{targetSessionId:item.session_id}),{disabled}),
            name==='tasks'&&item.state==='unverified'&&button('核实任务',()=>act('verify-task',{taskId:item.task_id}),{disabled}),
            name==='tasks'&&['failed','unverified'].includes(item.state)&&maintenanceTab==='current'&&button('核实并重试',()=>act('retry',{taskId:item.task_id}),{disabled}),
            name==='specialists'&&['running','unverified'].includes(item.state)&&button('核实专家退出',()=>act('verify-specialist',{taskId:item.task_id}),{disabled}),
            item.snapshot_id&&button('查看快照材料',()=>openReader(item.snapshot_id)),
            name==='snapshots'&&item.snapshot_id&&button('预览接手',()=>act('restore.preview',{snapshotId:item.snapshot_id}),{disabled}),...children);}
      const sessionGroups=researchSessionGroups(rowsFor('sessions'),rowsFor('sessions',true));
      const sessionRows=()=>sessionGroups.map(group=>{
        const nodeId=group.parent?.node_id??group.nodeId;
        const parent=group.parent??{groupTitle:nodeId?`研究节点 · ${nodeId} · 专家协作`:'未归组的专家会话'};
        const scoped=pages[`specialists:${nodeId}`];
        const hydrate=async event=>{
          if(event.target!==event.currentTarget||!event.currentTarget.open||expertRequests.current.has(group.key)||!group.children.some(row=>!row.label))return;
          expertRequests.current.add(group.key);const life=lifecycle.current;
          try {
            const page=scoped??await loadPage('specialists',{nodeId:nodeId??null,limit:100});
            const wanted=new Set(group.children.filter(row=>!row.label).map(row=>row.session_id));
            const tasks=(page?.items??[]).filter(task=>wanted.has(task.child_session_id)&&!task.label&&!expertDetails[task.task_id]);
            // Old hosts only project purpose=domain/review; fetch actual task labels on expansion.
            for(let offset=0;offset<tasks.length;offset+=4)await Promise.all(tasks.slice(offset,offset+4).map(async task=>{
              const result=await call('reference.get',{ref:task.task_id});
              if(result.value?.task_id!==task.task_id)throw new Error('专家任务与请求不匹配');
              const {label,purpose}=result.value;
              if(life===lifecycle.current)setExpertDetails(old=>({...old,[task.task_id]:{label,purpose}}));
            }));
          } catch(e){if(life===lifecycle.current)setNotice(`无法读取专家任务说明：${e.message}`);}
          finally {expertRequests.current.delete(group.key);}
        };
        return maintenanceRow('sessions',{...parent,sessionKey:group.key,expertCounts:group.children.length?researchExpertCounts(group.children):null},group.key,
          group.children.length?[h('div',{key:'experts',className:'ari-session-children'},
            h('p',{className:'ari-muted'},'专家任务 · 展开条目查看用途和打开会话'),
            ...group.children.map((child,i)=>maintenanceRow('sessions',child,i)),
            scoped?.cursor&&button('加载更多专家任务说明',async()=>{try{await loadPage('specialists',{nodeId,limit:100,more:true});}catch(e){setNotice(`无法读取专家任务说明：${e.message}`);}}))]:[],hydrate);
      });
      return h('div',{className:'ari-view'},h('div',{className:'ari-view-heading'},h('div',null,h('span',{className:'ari-eyebrow'},'执行与维护'),h('h2',null,'运行与维护')),
        label(runNames[run.state]??run.state??'状态未知')),
        h('section',{className:'ari-panel'},h('h3',null,'当前执行'),h('p',null,`运行会话 ${run.running_count??0} · 等待原生审批 ${run.pending_approvals??0} · 待整理 ${summary.review_queue?.pending_total??0}`),
          h('div',{className:'ari-action-row'},run.main_session_id&&button('打开研究主会话',()=>navigate(run.main_session_id)),
            ...actions.map(([title,endpoint])=>button(title,()=>act(endpoint),{key:endpoint,disabled}))),
          ...(run.approvals??[]).map(a=>h('p',{key:a.approval_id,role:'status'},`待审批：${a.reason??a.approval_id}`,button('打开会话',()=>navigate(a.session_id))))),
        h('div',{className:'ari-segmented'},button('当前执行',()=>setMaintenanceTab('current'),{'aria-pressed':maintenanceTab==='current'}),button('历史与整理',()=>setMaintenanceTab('history'),{'aria-pressed':maintenanceTab==='history'})),
        ...group.map(name=>h(name==='specialists'?'details':'section',{key:name,className:'ari-panel'},h(name==='specialists'?'summary':'div',{className:'ari-section-head'},h('h3',null,collections[name]),h('small',null,name==='sessions'?`已载入 ${rowsFor(name).length} 个会话 · ${sessionGroups.length} 个分组 / 共 ${pages[name]?.total??0} 个会话`:`已显示 ${rowsFor(name).length} / ${pages[name]?.total??summary.counts?.[name]??0} 项`)),
          name==='review_todos'&&h('p',{className:'ari-muted'},'整理队列是内部复核工作，不是等待用户批准。'),
          name==='hints'&&h('p',{className:'ari-muted'},'结构候选仅供复核；已有修复记录与历史版本需要分别判断。'),
          name==='hints'&&h('select',{'aria-label':'筛选结构候选类型',value:hintKind,onChange:e=>setHintKind(e.target.value)},
            ...[['','全部线索'],['prose_mention_without_relation','正文提及缺少关系'],['whole_snapshot_evidence','整份快照引用'],['lineage_mention_without_predecessor','前驱提及'],['complete_publication_cites_risk','完整发布风险']].map(([value,title])=>h('option',{key:value,value},title))),
          ...(name==='sessions'?sessionRows():rowsFor(name).map((item,i)=>maintenanceRow(name,item,i))),
          pages[name]?.cursor&&button('加载更多',()=>loadPage(name,{more:true,limit:name==='hints'?8:20,kind:name==='hints'?hintKind:null})))),
        restorePreview&&h('section',{className:'ari-panel'},h('div',{className:'ari-section-head'},h('h3',null,'接手预览'),button('关闭',()=>setRestorePreview(null))),
          h('p',null,`来源 ${restorePreview.context?.source_attempt_id??'未知'} · 缺失 ${restorePreview.missing?.length??0} 项`),
          !restorePreview.eligible&&h('p',{role:'alert'},'来源执行仍在进行或待核实，暂不能创建接手会话。'),
          button('创建接手会话',()=>act('restore.create',{snapshotId:restorePreview.snapshot_id,previewId:restorePreview.preview_id}),{disabled:disabled||!restorePreview.eligible})),
        h('details',{className:'ari-panel'},h('summary',null,'高级设置与历史上下文'),
          h('p',null,'科研指导按内容版本登记，只供相关任务使用。'),
          guidance&&h('p',null,`当前指导：${guidance.path??'无'} · ${guidance.version??'无版本'}`),
          h('input',{'aria-label':'科研指导文档路径',placeholder:'指导 Markdown 的绝对路径',value:guidancePath,onChange:e=>setGuidancePath(e.target.value)}),
          h('input',{'aria-label':'科研指导版本',placeholder:'可选版本名',value:guidanceVersion,onChange:e=>setGuidanceVersion(e.target.value)}),
          button('登记科研指导',()=>act('guidance.register',{path:guidancePath,version:guidanceVersion||undefined}),{disabled:disabled||!guidancePath.trim()}),
          button('将此会话移出项目',()=>act('detach'),{disabled})));
    }
    function readerView() {
      if(!reader)return null;
      const outcome=reader.resolution?.outcome;
      const filename=String(reader.resolution?.source_path??reader.resolution?.entry?.source_path??reader.ref).toLowerCase();
      const isMarkdown=filename.endsWith('.md')||reader.ref===pres?.report_ref;
      return h('div',{className:'ari-reader-backdrop'},h('aside',{className:'ari-reader',role:'dialog','aria-modal':'true','aria-label':'研究材料'},
        h('div',{className:'ari-reader-head'},h('div',null,h('span',{className:'ari-eyebrow'},'冻结研究材料'),h('h2',null,reader.ref)),button('关闭 · Esc',closeReader,{'aria-label':'关闭材料',autoFocus:true})),
        reader.loading?h('p',null,'正在校验并读取材料…'):
        reader.kind==='error'?h('p',{role:'alert'},reader.message):
        reader.kind==='directory'?h('div',null,h('p',{className:'ari-muted'},'选择目录中的文件继续阅读。目录级引用不指向单个结论字段。'),
          h('ul',{className:'ari-reader-list'},...(reader.items??[]).map(item=>h('li',{key:item.ref},
            button(`${item.kind==='directory'?'▣':'▤'}  ${item.name}`,()=>openReader(item.ref)),item.status&&label(item.status)))),
          reader.cursor&&button('加载更多目录项',async()=>{const next=await call('reference.entries',{ref:reader.ref,cursor:reader.cursor});setReader({...reader,items:[...reader.items,...next.items],cursor:next.cursor});})):
        reader.kind==='binary'?h('p',null,`二进制材料 · ${reader.total_bytes} 字节。冻结对象已校验，浏览器不显示文件内容。`):
        reader.kind==='text'?h('div',null,reader.truncated&&h('p',{role:'status'},`预览限制为 2 MiB；原文件 ${reader.total_bytes} 字节。`),
          reader.pointer&&h('div',{className:'ari-pointer'},h('strong',null,'指标字段'),h('code',null,reader.pointer),
            h('strong',null,(()=>{try{return String(jsonPointer(JSON.parse(reader.body),reader.pointer)??'字段缺失');}catch{return '无法解析 JSON';}})())),
          isMarkdown?markdown(reader.body):filename.endsWith('.json')?
            h('pre',{className:'ari-code'},(()=>{try{return JSON.stringify(JSON.parse(reader.body),null,2);}catch{return reader.body;}})()):
            h('pre',{className:'ari-code'},reader.body)):
        h('p',{role:'alert'},`材料不可打开：${reader.resolution?.reason??'冻结对象不可用'}`),
        h('details',{className:'ari-reader-meta'},h('summary',null,'来源与完整性'),h('pre',null,JSON.stringify(reader.resolution??reader.metadata??{},null,2)))));
    }
    React.useEffect(()=>{if(!reader)return;const key=e=>{if(e.key==='Escape'){e.preventDefault();closeReader();}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[reader]);
    return h('div',{className:'ari-app'},h('style',null,researchStyles),
      h('header',{className:'ari-app-header'},h('div',{className:'ari-brand'},h('span',{className:'ari-brand-mark'},'R'),h('div',null,h('span',{className:'ari-eyebrow'},'RESEARCH · 研究工作台'),h('strong',null,pres?.title??brief(project.goal,52)??'关联研究项目'))),
        h('div',{className:'ari-header-state'},summary&&label(runNames[run.state]??run.state??'状态未知',run.state==='complete'?'success':'muted'),
          summary&&h('small',null,`${summary.counts?.nodes??0} 个研究节点`))),
      parentSessionId&&h('div',{className:'ari-notice',role:'status'},'专家会话的研究资料（只读）',
        button('返回父会话',()=>navigate(parentSessionId)),
        (run.main_session_id??context.mainSessionId)&&button('返回研究主会话',()=>navigate(run.main_session_id??context.mainSessionId))),
      error&&h('div',{className:'ari-alert',role:'alert'},summary?'连接异常，显示最后一次成功读取的数据。':'暂时无法读取研究项目。',` ${error}`,button('重试',refresh)),
      notice&&h('div',{className:'ari-notice',role:'status'},notice,button('关闭',()=>setNotice(''))),
      !summary?(readOnly?h('p',{className:'ari-empty'},error?'项目资料暂不可用，可返回父会话继续查看。':'正在读取所属研究项目…'):h('section',{className:'ari-onboarding'},h('span',{className:'ari-eyebrow'},'项目关联'),h('h2',null,'连接研究项目'),
        h('p',null,'项目目录采用当前 DSH 会话工作目录。'),
        h('input',{'aria-label':'研究目标',placeholder:'新项目的研究目标',value:goal,onChange:e=>setGoal(e.target.value)}),
        button('新建并关联',async()=>{try{setBusy(true);await call('open',{goal});await refresh();}catch(e){setNotice(e.message);}finally{setBusy(false);}}, {disabled:busy||!goal.trim()}),
        button('关联已有项目',async()=>{try{setBusy(true);await call('open');await refresh();}catch(e){setNotice(e.message);}finally{setBusy(false);}}, {disabled:busy}))):
      h('div',{className:'ari-app-body'},h('nav',{className:'ari-nav','aria-label':'Research 页面'},...NAV.map(([id,name],i)=>button(name,()=>changeSection(id),{
        key:id,'aria-current':section===id?'page':undefined,className:'ari-nav-item',title:name,
      }))),h('main',{className:'ari-main',ref:scrollRef,tabIndex:-1},
        section==='overview'?overview():section==='process'?process():section==='knowledge'?knowledgeView():section==='materials'?materials():runtime())),
      readerView());
  };
}
