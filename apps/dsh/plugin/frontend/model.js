/** Project graph projection. No inferred scientific or execution relationships. */
export function projectGraph(state) {
  const ordered = [...(state.nodes ?? [])].sort((a,b) => `${a.created_at ?? ''}:${a.node_id}`.localeCompare(`${b.created_at ?? ''}:${b.node_id}`));
  const nodes = new Map(ordered.map(n => [n.node_id, {...n, attempts:[], publications:[], snapshots:[], notes:[], references:[], relations:[]} ]));
  const attempts = new Map((state.attempts ?? []).map(a => [a.attempt_id,a]));
  const pubs = new Map((state.publications ?? []).map(p => [p.publication_id,p]));
  const snapshots = new Map((state.snapshots ?? []).map(s => [s.snapshot_id,s]));
  const legacy = new Map((state.legacy_refs ?? []).map(r => [r.ref,r]));
  const knowledge = new Map((state.knowledge ?? []).map(r => [r.ref,r]));
  const owner = record => {
    // A declared but missing node must not be silently assigned elsewhere.
    const id = record?.node_id ?? attempts.get(record?.attempt_id)?.node_id;
    return nodes.has(id) ? id : null;
  };
  function resolve(ref) {
    if (typeof ref !== 'string') return null;
    if (nodes.has(ref)) return ref;
    if (legacy.has(ref)) return owner(legacy.get(ref));
    if (attempts.has(ref)) return owner(attempts.get(ref));
    if (snapshots.has(ref)) return owner(snapshots.get(ref));
    if (knowledge.has(ref)) return owner(knowledge.get(ref));
    if (ref.startsWith('pub/')) {
      const [id,itemId] = ref.slice(4).split('#');
      const p = pubs.get(id);
      if (!p || (itemId !== undefined && !(p.items ?? []).some(i => i.item_id === itemId))) return null;
      return owner(p);
    }
    return null;
  }
  const planning = {attempts:[],publications:[],snapshots:[],notes:[],references:[],relations:[]};
  for (const key of ['attempts','publications','snapshots','notes']) {
    for (const record of state[key] ?? []) (nodes.get(owner(record)) ?? planning)[key].push(record);
  }
  const edges = new Map();
  function link(source,target,record) {
    if (!source || !target || source === target) return;
    const id = `${source}→${target}`;
    if (!edges.has(id)) edges.set(id,{id,source,target,records:[]});
    edges.get(id).records.push(record);
  }
  const incomingDependencies = new Map();
  for (const dependency of state.dependencies ?? []) {
    const source = nodes.has(dependency.predecessor_node_id) ? dependency.predecessor_node_id : null;
    const target = nodes.has(dependency.successor_node_id) ? dependency.successor_node_id : null;
    const record = {...dependency,kind:'dependency',source,target,label:dependency.relation_type};
    if (target) {
      const list=incomingDependencies.get(target)??[];list.push(record);incomingDependencies.set(target,list);
      nodes.get(target).relations.push(record);
    }
    if (source) nodes.get(source).relations.push(record);
    if (!source || !target) planning.relations.push(record);
    link(source,target,record);
  }
  for (const n of nodes.values()) {
    for (const [kind,refs] of [['input',n.inputs ?? []],['anchor',n.anchor_ref ? [n.anchor_ref] : []],['question',n.question_ref ? [n.question_ref] : []]]) {
      for (const ref of refs) {
        const source = resolve(ref);
        const record = {kind,source_ref:ref,target_ref:n.node_id,label:kind === 'anchor' ? '历史锚点' : kind === 'question' ? '议程问题' : '固定输入',source,target:n.node_id};
        n.references.push(record);
        link(source,n.node_id,record);
      }
    }
  }
  for (const r of state.relations ?? []) {
    const source = resolve(r.source_ref), target = resolve(r.target_ref);
    const record = {...r,kind:'relation',source,target};
    for (const id of new Set([source,target])) if (id) nodes.get(id).relations.push(record);
    if (!source || !target) planning.relations.push(record);
    link(source,target,record);
  }
  const edgeList = [...edges.values()].sort((a,b) => a.id.localeCompare(b.id));
  for (const node of nodes.values()) {
    node.origin_class = node.origin_kind === 'root' && node.root_reason
      ? 'explicit_root'
      : node.origin_kind === 'legacy_unresolved'
        ? 'legacy_unresolved'
        : node.origin_kind === 'derived' && (incomingDependencies.get(node.node_id)?.length ?? 0) > 0
          ? 'derived'
          : 'protocol_violation';
  }
  return {nodes:[...nodes.values()],edges:edgeList,planning,resolve,
    structuralKey:JSON.stringify([ordered.map(n => [n.node_id,n.created_at]),edgeList.map(e => [e.source,e.target])])};
}
