export const CARD = {width:260,height:128,gapX:100,gapY:36};
/** SCC condensation gives stable ranks without requiring a DAG of research. */
export function layoutGraph(nodes, edges) {
  const ids = nodes.map(n => n.node_id), order = new Map(ids.map((id,i) => [id,i]));
  const adjacency = new Map(ids.map(id => [id,[]]));
  for (const e of edges) if (adjacency.has(e.source) && adjacency.has(e.target)) adjacency.get(e.source).push(e.target);
  const index = new Map(), low = new Map(), stack = [], onStack = new Set(), groups = [];
  let sequence = 0;
  function visit(id) {
    index.set(id,sequence); low.set(id,sequence++); stack.push(id); onStack.add(id);
    for (const next of adjacency.get(id)) {
      if (!index.has(next)) { visit(next); low.set(id,Math.min(low.get(id),low.get(next))); }
      else if (onStack.has(next)) low.set(id,Math.min(low.get(id),index.get(next)));
    }
    if (low.get(id) === index.get(id)) {
      const group = []; let next;
      do { next=stack.pop(); onStack.delete(next); group.push(next); } while (next !== id);
      group.sort((a,b) => order.get(a)-order.get(b)); groups.push(group);
    }
  }
  ids.forEach(id => { if (!index.has(id)) visit(id); });
  groups.sort((a,b) => order.get(a[0])-order.get(b[0]));
  const groupOf = new Map(groups.flatMap((g,i) => g.map(id => [id,i])));
  const nexts = groups.map(() => new Set()), indegree=groups.map(() => 0), rank=groups.map(() => 0), connected=new Set();
  for (const e of edges) {
    const a=groupOf.get(e.source),b=groupOf.get(e.target);
    if (a === undefined || b === undefined) continue;
    connected.add(e.source); connected.add(e.target);
    if (a !== b && !nexts[a].has(b)) { nexts[a].add(b); indegree[b]++; }
  }
  const queue=groups.map((_,i)=>i).filter(i=>!indegree[i]);
  while(queue.length) {
    const i=queue.shift();
    for(const j of nexts[i]) { rank[j]=Math.max(rank[j],rank[i]+1); if(--indegree[j]===0) queue.push(j); }
  }
  const positions = new Map(), rows=new Map();
  groups.forEach((g,i) => {
    if (g.every(id=>!connected.has(id))) return;
    for(const id of g) { const row=rows.get(rank[i]) ?? 0; positions.set(id,{x:32+rank[i]*360,y:48+row*164}); rows.set(rank[i],row+1); }
  });
  const isolated=ids.filter(id=>!connected.has(id));
  const isolatedY=rows.size ? 110+Math.max(...rows.values())*164 : 48;
  isolated.forEach((id,i)=>positions.set(id,{x:32+(i%3)*360,y:isolatedY+Math.floor(i/3)*164}));
  const all=[...positions.values()];
  return {positions,isolatedY:isolated.length ? isolatedY : null,width:Math.max(600,...all.map(p=>p.x+292)),height:Math.max(320,...all.map(p=>p.y+170))};
}
export function inViewport(p, camera, size, margin=100) {
  return (p.x+CARD.width)*camera.k+camera.x>=-margin && p.x*camera.k+camera.x<=size.width+margin &&
    (p.y+CARD.height)*camera.k+camera.y>=-margin && p.y*camera.k+camera.y<=size.height+margin;
}
export function clampZoom(k) { return Math.max(0.12,Math.min(2.5,k)); }
