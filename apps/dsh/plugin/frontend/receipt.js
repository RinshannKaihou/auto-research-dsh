export function createResearchReceipt(React) {
  const h=React.createElement;
  return function ResearchReceipt({sessionId,useReceipt,refresh}) {
    // DSH's public slot renderer binds Observable faces declared under
    // `hooks.receipt` to a conventional `useReceipt` component prop.
    const snapshot=useReceipt(value=>value);
    React.useEffect(()=>{void refresh(sessionId);},[sessionId,refresh]);
    if(!snapshot?.associated&&!snapshot?.command)return null;
    const failed=snapshot.command?.kind==='error';
    return h('aside',{className:`ari-receipt ${failed?'ari-receipt-error':''}`,role:failed?'alert':'status','aria-live':'polite'},
      h('style',null,researchStyles),h('span',{className:'ari-receipt-mark','aria-hidden':'true'},failed?'!':'R'),
      h('span',{className:'ari-receipt-copy'},
        h('strong',null,failed?'Research 命令未完成':snapshot.project?.goal??'Research 已关联'),
        h('small',null,failed?(snapshot.command?.text??'请查看命令结果'):`${snapshot.project?.control??'manual'} · schema ${snapshot.schema_version??'?'}`)),
      h('span',{className:'ari-receipt-entry'},'打开上方 Research 标签 →'));
  };
}
