export function createResearchGraph(React, layoutGraph, inViewport, clampZoom) {
  const h=React.createElement;
  return function ResearchGraph({model,visibleIds,selected,onSelect,onEdge,focused,labels={}}) {
    const root=React.useRef(null), drag=React.useRef(null), moved=React.useRef(false), fitted=React.useRef(false);
    const [size,setSize]=React.useState({width:0,height:560});
    const [camera,setCamera]=React.useState({x:24,y:24,k:1});
    const layout=React.useMemo(()=>layoutGraph(model.nodes,model.edges),[model.structuralKey]);
    const marker=React.useId().replace(/:/g,'');
    React.useEffect(()=>{
      const observer=new ResizeObserver(entries=>{ const r=entries[0].contentRect; if(r.width) setSize({width:r.width,height:r.height}); });
      if(root.current) observer.observe(root.current);
      return ()=>observer.disconnect();
    },[]);
    function fit() {
      const points=model.nodes.filter(n=>visibleIds.has(n.node_id)).map(n=>layout.positions.get(n.node_id));
      if(!points.length) return;
      const x=Math.min(...points.map(p=>p.x))-115,y=Math.min(...points.map(p=>p.y))-40;
      const w=Math.max(...points.map(p=>p.x+260))-x+115,hh=Math.max(...points.map(p=>p.y+128))-y+28;
      const k=clampZoom(Math.min(1.2,size.width/w,size.height/hh));
      setCamera({x:(size.width-w*k)/2-x*k,y:(size.height-hh*k)/2-y*k,k});
    }
    React.useEffect(()=>{
      if(!fitted.current && model.nodes.length && size.width) {
        const first=layout.positions.get(focused)??layout.positions.get(model.nodes[0].node_id);
        setCamera({x:24-first.x,y:28-first.y,k:1});fitted.current=true;
      }
    },[model.structuralKey,size.width]);
    React.useEffect(()=>{if(selected&&size.width)reveal(selected);},[selected,size.width]);
    function zoom(factor) { setCamera(c=>{const k=clampZoom(c.k*factor),f=k/c.k;return {k,x:size.width/2-(size.width/2-c.x)*f,y:size.height/2-(size.height/2-c.y)*f};}); }
    function reveal(id) {
      const p=layout.positions.get(id);
      if(!inViewport(p,camera,size,0)) setCamera(c=>({...c,x:size.width/2-(p.x+130)*c.k,y:size.height/2-(p.y+64)*c.k}));
    }
    function select(id) { if(!moved.current) onSelect(id); }
    const nodes=model.nodes.filter(n=>visibleIds.has(n.node_id));
    const visible=nodes.filter(n=>inViewport(layout.positions.get(n.node_id),camera,size) || n.node_id===selected);
    const rendered=new Set(visible.map(n=>n.node_id));
    function key(event,id) {
      if(event.key==='Enter' || event.key===' ') {event.preventDefault();onSelect(id);return;}
      if(!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
      event.preventDefault(); const i=nodes.findIndex(n=>n.node_id===id),delta=['ArrowLeft','ArrowUp'].includes(event.key)?-1:1;
      const next=nodes[event.key==='Home'?0:event.key==='End'?nodes.length-1:(i+delta+nodes.length)%nodes.length];
      if(next) {onSelect(next.node_id);reveal(next.node_id);requestAnimationFrame(()=>root.current?.querySelector(`[data-node-id="${CSS.escape(next.node_id)}"]`)?.focus());}
    }
    const curves=model.edges.filter(e=>visibleIds.has(e.source)&&visibleIds.has(e.target)).map(e=>{
      const a=layout.positions.get(e.source),b=layout.positions.get(e.target);
      // Keep an edge crossing the viewport even if both endpoints were culled.
      const minX=Math.min(a.x,b.x)-100,maxX=Math.max(a.x,b.x)+360,minY=Math.min(a.y,b.y)-180,maxY=Math.max(a.y,b.y)+180;
      if(maxX*camera.k+camera.x<0||minX*camera.k+camera.x>size.width||maxY*camera.k+camera.y<0||minY*camera.k+camera.y>size.height)return null;
      const forward=b.x>a.x, x1=a.x+(forward?260:0),x2=b.x+(forward?0:260),y1=a.y+64,y2=b.y+64;
      const sameColumn=a.x===b.x, side=b.y>a.y?1:-1, sx=a.x+(side>0?260:0), bend=sx+side*100;
      const d=sameColumn?`M${sx},${y1} C${bend},${y1} ${bend},${y2} ${sx},${y2}`:forward?`M${x1},${y1} C${x1+65},${y1} ${x2-65},${y2} ${x2},${y2}`:`M${x1},${y1} C${x1-80},${y1-130} ${x2+80},${y2-130} ${x2},${y2}`;
      const kind=e.records.some(r=>r.label==='lineage_correction')?'lineage':e.records.some(r=>r.kind==='relation')?'relation':e.records.some(r=>r.kind==='anchor')?'anchor':'input';
      const label=e.records.length>1?`${e.records[0].label} +${e.records.length-1}`:e.records[0].label;
      return h('g',{key:e.id,className:`ari-edge ari-${kind}`,role:'button',tabIndex:0,'aria-label':`${e.source} → ${e.target}: ${label}`,onClick:()=>onEdge(e),onKeyDown:event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();onEdge(e);}}},
        h('title',null,e.records.map(r=>`${r.source_ref} → ${r.target_ref}: ${r.label}`).join('\n')),
        h('path',{d,className:'ari-edge-hit'}),h('path',{d,markerEnd:`url(#${marker}-${kind})`}),
        h('text',{x:sameColumn?sx+side*78:(x1+x2)/2,y:(y1+y2)/2-(sameColumn?8:forward?10:96),textAnchor:sameColumn?(side>0?'start':'end'):'middle'},label.length>24?label.slice(0,23)+'…':label));
    });
    return h('div',{className:'ari-graph-shell'},
      h('div',{className:'ari-graph-tools'},h('span',null,'实线：原始关系 · 补录：lineage_correction · 虚线：输入 · 点线：锚点'),
        h('button',{onClick:()=>zoom(1/1.25),'aria-label':'缩小研究图'},'−'),h('span',{'aria-live':'polite'},`${Math.round(camera.k*100)}%`),
        h('button',{onClick:()=>zoom(1.25),'aria-label':'放大研究图'},'+'),h('button',{onClick:fit},'显示全图')),
      h('div',{ref:root,className:'ari-canvas',onPointerDown:e=>{moved.current=false;if(e.button!==0 || e.target.closest('[role="button"]'))return;moved.current=false;drag.current={x:e.clientX,y:e.clientY,camera};e.currentTarget.setPointerCapture(e.pointerId);},
        onPointerMove:e=>{if(!drag.current)return;const dx=e.clientX-drag.current.x,dy=e.clientY-drag.current.y;moved.current=Math.abs(dx)+Math.abs(dy)>3;setCamera({...drag.current.camera,x:drag.current.camera.x+dx,y:drag.current.camera.y+dy});},
        onPointerUp:()=>{drag.current=null;},onPointerCancel:()=>{drag.current=null;},
        onWheel:e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();zoom(e.deltaY>0?1/1.1:1.1);}}},
        h('svg',{width:'100%',height:'100%',role:'group','aria-label':'研究节点图。使用方向键浏览节点，回车查看详情。'},
          h('defs',null,...['relation','lineage','input','anchor'].map(kind=>h('marker',{key:kind,id:`${marker}-${kind}`,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto'},h('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:kind==='anchor'?'#b77a33':kind==='lineage'?'#8b5cf6':'#7c8c97'})))),
          h('g',{transform:`translate(${camera.x} ${camera.y}) scale(${camera.k})`},...curves,
            ...visible.map(n=>{const p=layout.positions.get(n.node_id);return h('g',{key:n.node_id,transform:`translate(${p.x} ${p.y})`,className:`ari-node ${n.status==='closed'?'ari-closed':''} ${selected===n.node_id?'ari-selected':''} ${focused===n.node_id?'ari-focused':''}`,
              role:'button',tabIndex:0,'data-status':n.status,'data-node-id':n.node_id,'aria-label':`${n.node_id} ${n.status} ${n.question}`,'aria-pressed':selected===n.node_id,
              onClick:()=>select(n.node_id),onKeyDown:e=>key(e,n.node_id)},
              h('title',null,n.question),h('rect',{width:260,height:128,rx:10}),
              h('text',{x:16,y:25,className:'ari-node-id'},n.node_id),h('text',{x:244,y:25,textAnchor:'end',className:'ari-node-status'},`${n.status}${focused===n.node_id?' · 当前':''}`),
              h('foreignObject',{x:16,y:37,width:228,height:49},h('div',{className:'ari-node-question'},labels[n.node_id]?.title??n.question)),
              h('text',{x:16,y:110,className:'ari-node-meta'},`${n.lineage_corrected?'谱系已补录 · ':''}${labels[n.node_id]?.outcome??n.strategy??'研究节点'}`));}))),
        !nodes.length&&h('p',{className:'ari-empty'},model.nodes.length?'没有匹配的节点；清除筛选后查看。':'尚无研究节点。规划工作和材料可在下方查看。')),
      h('span',{className:'ari-sr-only'},`共 ${nodes.length} 个节点，可视区域 ${rendered.size} 个。也可切换列表访问所有节点。`));
  };
}
