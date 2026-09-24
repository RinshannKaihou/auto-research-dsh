// Session-scoped command receipts. This layer only queries status; it never
// sends a prompt, starts a goal, or changes the host's blank-session state.
export function createReceiptState(rpc, now=()=>new Date().toLocaleTimeString(),sessionContext=()=>({})) {
  const values=new Map(),listeners=new Map(),requests=new Map();
  const publish=(id,value)=>{values.set(id,value);for(const listener of listeners.get(id)??[])listener();};
  const store=id=>({
    getSnapshot:()=>values.get(id)??null,
    subscribe:listener=>{const set=listeners.get(id)??new Set();set.add(listener);listeners.set(id,set);return()=>{set.delete(listener);if(!set.size)listeners.delete(id);};},
  });
  async function refresh(id) {
    const seq=(requests.get(id)??0)+1;requests.set(id,seq);
    try {
      const context=sessionContext(id),sourceId=context.parentSessionId?(context.mainSessionId??context.parentSessionId):id;
      const response=await rpc('status',{sessionId:sourceId});
      if(!response.ok)throw new Error(response.error?.message??'无法读取研究状态');
      if(requests.get(id)!==seq)return;
      const {command,commandSequence,commandTime}=values.get(id)??{};
      const value=response.value;
      const identity=value.runtime?.sessions?.find(row=>row.session_id===id)
        ??(value.workflow?.session?.session_id===id?value.workflow.session:null)
        ??context.identity;
      const current=identity??(context.parentSessionId?{session_id:id,role:'specialist',node_id:value.runtime?.sessions?.find(row=>row.session_id===context.parentSessionId)?.node_id}:null);
      publish(id,{...value,runtime:{...value.runtime,current:value.runtime?.sessions?.find(row=>row.session_id===id)},identity:current,mainSessionId:value.runtime?.main_session_id??context.mainSessionId,
        parentSessionId:context.parentSessionId,associated:true,command,commandSequence,commandTime});
    } catch(error) {
      if(requests.get(id)!==seq)return;
      const {command,commandSequence,commandTime}=values.get(id)??{};
      // Do not leave an old project's success badge after detach or RPC errors.
      publish(id,{associated:false,refreshError:error.message,command,commandSequence,commandTime});
    }
  }
  function executed(id,name,result) {
    if(name!=='research')return;
    const previous=values.get(id)??{};
    publish(id,{...previous,command:result,commandSequence:(previous.commandSequence??0)+1,commandTime:now()});
    void refresh(id);
  }
  return {store,refresh,executed};
}
