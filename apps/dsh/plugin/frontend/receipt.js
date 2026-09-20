export function createResearchReceipt(React, Workbench, researchStyles) {
  const h=React.createElement;
  function ResearchDialog({sessionId,onClose}) {
    const dialog=React.useRef(null);
    React.useEffect(()=>{
      const element=dialog.current;element.showModal();
      return()=>element.close();
    },[]);
    return h('dialog',{ref:dialog,className:'ari-workbench-dialog','aria-label':'Research 研究工作台',onClose},
      h('div',{className:'ari-dialog-bar'},h('strong',null,'Research / 研究工作台'),
        h('button',{type:'button',onClick:()=>dialog.current.close(),'aria-label':'关闭研究工作台'},'关闭 · Esc')),
      h('div',{className:'ari-dialog-content'},h(Workbench,{sessionId,key:sessionId})));
  }
  return function ResearchReceipt({sessionId,useReceipt,refresh}) {
    // DSH's public slot renderer binds Observable faces declared under
    // `hooks.receipt` to a conventional `useReceipt` component prop.
    const snapshot=useReceipt(value=>value);
    const [opened,setOpened]=React.useState(false);
    React.useEffect(()=>{void refresh(sessionId);},[sessionId,refresh]);
    const failed=snapshot?.command?.kind==='error';
    const associated=snapshot?.associated;
    return h(React.Fragment,null,
      h('style',null,researchStyles),
      h('aside',{className:`ari-receipt ${failed?'ari-receipt-error':''}`},
        h('div',{className:'ari-receipt-row'},h('span',{className:'ari-receipt-mark','aria-hidden':'true'},failed?'!':'R'),
          h('span',{className:'ari-receipt-copy'},
            h('strong',null,associated?snapshot.project?.goal??'Research 已关联':'Research / 研究项目'),
            h('small',null,associated?`${snapshot.project?.control==='auto'?'自动推进已开启':snapshot.project?.control==='manual'?'已初始化 · 规划模式':snapshot.project?.control} · ${snapshot.runtime?.current?.pending_approvals?'当前对话等待审批':snapshot.runtime?.current?.native_status==='running'?'当前对话正在工作':'当前对话空闲'} · 待审批 ${snapshot.runtime?.pending_approvals??0} · schema ${snapshot.schema_version??'?'}`:'初始化与关联无需发送消息或调用模型')),
          h('button',{type:'button',className:'ari-receipt-entry','aria-haspopup':'dialog',onClick:()=>setOpened(true)},'打开研究工作台')),
        snapshot?.command&&h('div',{key:snapshot.commandSequence,className:'ari-command-result'},
          h('p',{role:failed?'alert':'status','aria-live':'polite'},`Research 命令${failed?'失败':'已完成'} · 第 ${snapshot.commandSequence} 次 · ${snapshot.commandTime}`),
          h('details',{open:true},h('summary',null,failed?'错误详情':'命令结果'),h('pre',null,snapshot.command.text??'命令没有返回文本'))),
        snapshot?.refreshError&&h('small',{className:'ari-receipt-query-error'},`状态查询：${snapshot.refreshError}`)),
      opened&&h(ResearchDialog,{sessionId,onClose:()=>{setOpened(false);void refresh(sessionId);}}));
  };
}
