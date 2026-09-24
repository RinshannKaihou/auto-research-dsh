/** Native session selection and Chat navigation, including same-session opens. */
export function createResearchSessionNavigation(sessions,workspaces,rpc) {
  const bindings=new Map(),projects=new Map(),identities=new Map();let pending=null;
  function arrive(id) {
    const binding=[...(bindings.get(id)??[])].at(-1);
    if(pending!==id||!binding)return;
    binding.setView('chat');pending=null;
  }
  function context(id) {
    let address=sessions.subagentAddress?.(id);
    if(!address)for(const [parentSessionId,catalog] of Object.entries(sessions.list?.getSnapshot()?.subagentsByParent??{})) {
      const child=catalog.entries?.find(item=>item.kind==='child'&&item.id===id);
      if(child){address={parentSessionId,childSessionId:id,mode:child.mode};break;}
    }
    return {...address,identity:identities.get(id),mainSessionId:projects.get(id)??projects.get(address?.parentSessionId)};
  }
  return {
    context,
    bind(id,actions) {
      if(typeof actions?.setView!=='function')throw new Error('原生对话导航不可用');
      const active=bindings.get(id)??new Set();active.add(actions);bindings.set(id,active);arrive(id);
      return()=>{active.delete(actions);if(!active.size)bindings.delete(id);};
    },
    async prepareDiscussion(id,identity) {
      if(!workspaces||!rpc)throw new Error('讨论工作区导航不可用');
      const response=await rpc('status',{sessionId:id});
      if(!response.ok)throw new Error(response.error?.message??'无法读取讨论会话');
      const value=response.value;
      const row=value.workflow?.session??value.runtime?.current;
      if(row?.session_id!==id||row.role!=='discussion'||!row.cwd)throw new Error('讨论会话身份或工作目录不完整');
      const label=`${row.node_id} · 讨论 · ${id.slice(-8)}`;
      const snapshot=workspaces.list.getSnapshot();
      let workspace=snapshot.items.find(w=>w.path===row.cwd);
      if(!workspace) {
        workspace=await workspaces.create({path:row.cwd});
        await workspaces.rename(workspace.workspaceId,label);
      }
      if(!workspace.sessionIds.includes(id))await sessions.create({sessionId:id,workspaceId:workspace.workspaceId});
      await sessions.refresh();
      const entry=sessions.list.getSnapshot().byId[id];
      if(!entry?.title||entry.title===row.cwd.split('/').at(-1)) {
        const renamed=await sessions.binding(id)?.session.rename(label);
        if(renamed&&!renamed.ok)throw new Error(renamed.error?.message??'无法设置讨论标题');
      }
      identities.set(id,{...identity,...row});
      if(value.runtime?.main_session_id)projects.set(id,value.runtime.main_session_id);
    },
    async open(id,{parentSessionId,mainSessionId,identity}={}) {
      if(!id)throw new Error('缺少会话标识');
      // Persisted native children need their direct-parent catalog before open().
      let address;
      parentSessionId??=context(id).parentSessionId;
      if(parentSessionId) {
        await sessions.refreshSubagents(parentSessionId);
        const catalog=sessions.list.getSnapshot().subagentsByParent?.[parentSessionId];
        const child=catalog?.entries?.find(item=>item.kind==='child'&&item.id===id);
        if(!child)throw new Error(catalog?.error?.message??'父会话目录中找不到该子会话');
        address={parentSessionId,childSessionId:id,mode:child.mode};
      }
      if(mainSessionId)projects.set(id,mainSessionId);
      if(identity)identities.set(id,{...identity,session_id:id});
      pending=id;
      try {if(address)sessions.openSubagent(address);else sessions.open(id);arrive(id);}
      catch(error){if(pending===id)pending=null;throw error;}
    },
  };
}
export function researchSessionStatus(session) {
  if(session.pending_approvals>0)return '等待人工审批';
  if(session.pause_reason==='wait')return '等待研究任务';
  if(session.native_status==='running')return '运行中';
  const terminal={cancelled:'已取消',completed:'已完成',succeeded:'已完成',failed:'已失败'};
  if(terminal[session.specialist_state])return terminal[session.specialist_state];
  const reasons={human:'等待用户继续',native_stop:'已暂停',fault:'运行异常',host_limit:'达到运行限制',unverified:'状态待核实',finished:'已结束',segment_complete:'工作段已结束',complete:'已完成'};
  return reasons[session.pause_reason]??session.pause_reason??({idle:'空闲',unloaded:'未加载'}[session.native_status])??'状态未知';
}
export function researchSummaryEqual(a,b) {
  // Native activity changes without a ledger revision (start/stop/approval).
  return a?.revision===b.revision&&JSON.stringify(a?.runtime)===JSON.stringify(b.runtime);
}
