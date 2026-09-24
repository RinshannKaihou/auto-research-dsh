import {researchSessionLabel} from './session-identity.js';
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
      h('div',{className:'ari-dialog-content'},h(Workbench,{sessionId,key:sessionId,onSessionOpened:()=>dialog.current.close()})));
  }
  return function ResearchReceipt({sessionId,useReceipt,refresh,actions,navigation}) {
    // DSH's public slot renderer binds Observable faces declared under
    // `hooks.receipt` to a conventional `useReceipt` component prop.
    const snapshot=useReceipt(value=>value);
    const [opened,setOpened]=React.useState(false);
    React.useEffect(()=>{void refresh(sessionId);},[sessionId,refresh]);
    React.useEffect(()=>navigation?.bind(sessionId,actions),[sessionId,actions,navigation]);
    const failed=snapshot?.command?.kind==='error';
    const associated=snapshot?.associated;
    const [navigationError,setNavigationError]=React.useState('');
    let target;try{if(snapshot?.command?.kind==='success')target=JSON.parse(snapshot.command.text)?.sessionId;}catch{}
    const openResult=async()=>{try{setNavigationError('');if(target.startsWith('discussion-'))await navigation.prepareDiscussion(target);await navigation.open(target,{mainSessionId:snapshot?.mainSessionId});}catch(e){setNavigationError(e.message);}};
    return h(React.Fragment,null,
      h('style',null,researchStyles),
      h('aside',{className:`ari-receipt ${failed?'ari-receipt-error':''}`},
        h('div',{className:'ari-receipt-row'},h('span',{className:'ari-receipt-mark','aria-hidden':'true'},failed?'!':'R'),
          h('span',{className:'ari-receipt-copy'},
            h('strong',null,associated?`当前输入：${researchSessionLabel(snapshot.identity)}`:'Research / 研究项目'),
            h('small',null,associated&&snapshot.identity?.role==='discussion'?'独立讨论 · 消息发送到此讨论会话':associated?`${snapshot.project?.control==='auto'?'自动推进已开启':snapshot.project?.control==='manual'?'已初始化 · 规划模式':snapshot.project?.control} · ${snapshot.runtime?.current?.pending_approvals?'当前对话等待审批':snapshot.runtime?.current?.native_status==='running'?'当前对话正在工作':'当前对话空闲'} · 待审批 ${snapshot.runtime?.pending_approvals??0}`:'初始化与关联无需发送消息或调用模型')),
          snapshot?.mainSessionId&&snapshot.mainSessionId!==sessionId&&h('button',{type:'button',onClick:async()=>{try{setNavigationError('');await navigation.open(snapshot.mainSessionId);}catch(e){setNavigationError(e.message);}}},'返回主会话'),
          h('button',{type:'button',className:'ari-receipt-entry','aria-haspopup':'dialog',onClick:()=>setOpened(true)},'打开研究工作台')),
        snapshot?.command&&h('div',{key:snapshot.commandSequence,className:'ari-command-result'},
          h('p',{role:failed?'alert':'status','aria-live':'polite'},`Research 命令${failed?'失败':'已完成'} · 第 ${snapshot.commandSequence} 次 · ${snapshot.commandTime}`),
          h('details',{open:true},h('summary',null,failed?'错误详情':'命令结果'),h('pre',null,snapshot.command.text??'命令没有返回文本')),
          target&&h('button',{type:'button',onClick:openResult},'打开目标会话')),
        navigationError&&h('small',{role:'alert'},navigationError),
        snapshot?.refreshError&&h('small',{className:'ari-receipt-query-error'},`状态查询：${snapshot.refreshError}`)),
      opened&&h(ResearchDialog,{sessionId,onClose:()=>{setOpened(false);void refresh(sessionId);}}));
  };
}
