export function researchSessionLabel(session) {
  if(!session)return '研究会话 · 身份待确认';
  const roles={main:'研究主会话',node_core:'节点执行',exploration:'历史探索',discussion:'讨论',handoff:'接手',specialist:'专家',legacy:'历史会话'};
  return [session.node_id,roles[session.role]??'研究会话',session.role==='specialist'?session.label:null].filter(Boolean).join(' · ');
}
export function createResearchSessionIdentity(React,researchStyles) {
  const h=React.createElement;
  return function SessionIdentity({sessionId,useReceipt,refresh,navigation,actions}) {
    const snapshot=useReceipt(value=>value),[error,setError]=React.useState('');
    React.useEffect(()=>{void refresh(sessionId);},[sessionId,refresh]);
    React.useEffect(()=>navigation?.bind(sessionId,actions),[sessionId,actions,navigation]);
    if(!snapshot?.associated)return null;
    const label=researchSessionLabel(snapshot.identity);
    const project=snapshot.project_root?.split('/').filter(Boolean).at(-1);
    const go=async id=>{try{setError('');await navigation.open(id,{mainSessionId:snapshot.mainSessionId});}catch(e){setError(e.message);}};
    return h('div',{className:'ari-session-identity','aria-label':'当前研究会话','data-role':snapshot.identity?.role},
      h('style',null,researchStyles),
      h('div',{className:'ari-session-caption'},h('strong',null,label),
        h('small',{title:sessionId},[project,sessionId.slice(-8)].filter(Boolean).join(' · '))),
      snapshot.parentSessionId&&h('button',{type:'button',onClick:()=>go(snapshot.parentSessionId)},'返回父会话'),
      snapshot.mainSessionId&&snapshot.mainSessionId!==sessionId&&h('button',{type:'button',onClick:()=>go(snapshot.mainSessionId)},'返回主会话'),
      error&&h('span',{role:'alert'},error));
  };
}
