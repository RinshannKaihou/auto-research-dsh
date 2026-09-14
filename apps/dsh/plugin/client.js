/* Native DSH research workbench. Model selection and chat remain on DSH surfaces. */
window.__ModuleLoader__.load({
  id: 'auto-research-v5',
  factory: require => {
    const React = require('react');
    const h = React.createElement;
    let rpc;
    let openSession;
    const box = { border: '1px solid var(--border-color, #ccc)', borderRadius: 8, padding: 12, marginBottom: 12 };
    const button = (label, action, disabled = false) => h('button', {
      onClick: action,
      disabled,
      style: { padding: '5px 10px', marginRight: 6, marginBottom: 6 },
    }, label);

    function Workbench({ sessionId }) {
      const [state, setState] = React.useState(null);
      const [error, setError] = React.useState(null);
      const [notice, setNotice] = React.useState(null);
      const [goal, setGoal] = React.useState('');
      const [budget, setBudget] = React.useState('');
      const [branchNode, setBranchNode] = React.useState('');

      const call = React.useCallback(async (endpoint, payload = {}) => {
        const response = await rpc(endpoint, {
          sessionId,
          operationId: `${sessionId}:ui:${crypto.randomUUID()}`,
          ...payload,
        });
        if (!response.ok) throw new Error(response.error.message);
        return response.value;
      }, [sessionId]);

      const refresh = React.useCallback(async () => {
        try {
          setState(await call('query'));
          setError(null);
        } catch (failure) {
          setState(null);
          setError(failure.message);
        }
      }, [call]);

      React.useEffect(() => {
        let active = true;
        let timer;
        const poll = async () => {
          if (active) await refresh();
          if (active) timer = setTimeout(poll, 3000);
        };
        poll();
        return () => { active = false; clearTimeout(timer); };
      }, [refresh]);

      async function act(endpoint, payload) {
        try {
          const value = await call(endpoint, payload);
          setError(null);
          setNotice(null);
          await refresh();
          if (value?.sessionId) setNotice(`已创建原生会话 ${value.sessionId}`);
        } catch (failure) {
          setNotice(null);
          setError(failure.message);
        }
      }

      return h('div', {
        className: 'ari-v5',
        style: { padding: '16px 16px 200px', height: '100%', overflow: 'auto', color: 'inherit', fontSize: 14 },
      },
      h('style', null, '.ari-v5 h2{margin:0 0 8px;font-size:22px}.ari-v5 h3{margin:0 0 8px;font-size:16px}.ari-v5 p{margin:6px 0;line-height:1.5}.ari-v5 td,.ari-v5 th{padding:4px 6px}.ari-v5 input{padding:6px;margin:4px 6px 8px 0}'),
      h('h2', null, '研究工作台'),
      h('p', null, '当前 DSH 会话继续负责模型、原生工具、权限、轨迹、取消与 goal 续轮。'),
      error && h('p', { role: 'alert', style: { color: '#b74b35' } }, error),
      notice && h('p', { role: 'status', style: { color: '#2f7d4a' } }, notice),
      !state ? h('section', { style: box },
        h('h3', null, '关联当前会话'),
        h('input', { value: goal, onChange: event => setGoal(event.target.value), placeholder: '研究目标' }),
        h('input', { value: budget, onChange: event => setBudget(event.target.value), placeholder: '自主预算（可稍后设置）', type: 'number' }),
        button('新建并关联', () => act('open', { goal, budget: Number(budget || 0) }), !goal.trim()),
        button('关联已有项目', () => act('open', {}))) : h(React.Fragment, null,
        h('section', { style: box }, h('h3', null, state.project.goal),
          h('p', null, `控制：${state.project.control} · 当前工作段：${state.attempt?.attempt_id ?? '无'}`),
          h('p', null, `用量 ${state.usage.known} / ${state.usage.budget}；未知请求 ${state.usage.unknown_count}`),
          h('input', { value: budget, onChange: event => setBudget(event.target.value), placeholder: '更新自主预算', type: 'number' }),
          button('开启自主研究', () => act('auto', budget ? { budget: Number(budget) } : {})),
          button('暂停', () => act('pause')),
          button('继续', () => act('resume')),
          button('停止项目', () => act('stop')),
          button('解除当前会话关联', () => act('detach'))),
        h('section', { style: box }, h('h3', null, `研究图 · ${state.nodes.length}`),
          state.nodes.length ? h('ul', null, ...state.nodes.map(node => h('li', { key: node.node_id },
            h('span', null, `${node.node_id} · ${node.status} · ${node.strategy ?? 'continue'} · ${node.question} `),
            button('聚焦', () => act('focus', { nodeId: node.node_id }), node.status === 'closed'),
            button('分支', () => act('branch', { nodeId: node.node_id }), node.status === 'closed')))) : h('p', null, '模型可用 research_propose 提出节点。'),
          h('input', { value: branchNode, onChange: event => setBranchNode(event.target.value), placeholder: '节点 ID' }),
          button('聚焦规划工作', () => act('focus', { nodeId: null })),
          button('创建独立工作区分支', () => act('branch', { nodeId: branchNode }), !branchNode)),
        h('section', { style: box }, h('h3', null, `阶段材料 · ${state.publications.length}`),
          ...state.publications.map(publication => h('div', { key: publication.publication_id },
            h('strong', null, `${publication.publication_id} · ${publication.status}`),
            h('p', null, publication.summary),
            h('p', null, publication.items.map(item => item.ref).join(' · '))))),
        h('section', { style: box }, h('h3', null, `接手快照 · ${state.snapshots.length}`),
          state.snapshots.length ? state.snapshots.map(snapshot => h('div', { key: snapshot.snapshot_id },
            h('span', null, `${snapshot.snapshot_id} · ${snapshot.complete ? '完整' : '部分'} · ${snapshot.attempt_id} `),
            button('创建接手副本', () => act('restore', { snapshotId: snapshot.snapshot_id })))) : h('p', null, '模型可用 research_snapshot 保存可接手文件。')),
        h('section', { style: box }, h('h3', null, '关联的原生会话'),
          ...state.associations.filter(item => item.ended_at === null).map(item => h('div', { key: item.association_id },
            h('code', null, item.session_id), ' ',
            button('在 DSH 打开', () => openSession(item.session_id))))),
        h('section', { style: box }, h('h3', null, `全部工作段 · ${state.attempts.length}`),
          h('table', { style: { width: '100%', textAlign: 'left' } },
            h('thead', null, h('tr', null, ...['工作段', '节点', '模式', '状态'].map(label => h('th', { key: label }, label)))),
            h('tbody', null, ...state.attempts.map(attempt => h('tr', { key: attempt.attempt_id },
              h('td', null, attempt.attempt_id), h('td', null, attempt.node_id ?? '规划'),
              h('td', null, attempt.mode), h('td', null, attempt.state))))))));
    }

    return {
      inject: ['slots', 'connection', 'sessions'],
      apply(ctx) {
        rpc = (endpoint, payload) => ctx.connection.rpc.call('/research-v5', endpoint, payload);
        openSession = id => ctx.sessions.open(id);
        ctx.slots.inject('conversation.view', () => ctx.slots.register({
          name: 'conversation.view', id: 'research-v5', order: 51, label: 'Research',
        }, Workbench));
      },
    };
  },
});
