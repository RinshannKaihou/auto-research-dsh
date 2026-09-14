# auto-research v5 · v0.1

把研究组织成可以分叉、汇聚和接手的工作节点。节点可以交出未完成推导、代码原型或未决问题；不必每次都运行实验或产生结论。

第一版提供 GUI 研究工作台、交互式路径图、单机协调者、默认两个并行执行者、SQLite 状态、固定版本产物、预算记录及暂停恢复。实际模型后端是独立进程运行的 DSH，当前文件与 shell 隔离实现针对 macOS。

## 直接用 GUI

在 Finder 中双击仓库里的 **[打开研究工作台.command](打开研究工作台.command)**。它会启动本机服务并打开浏览器；只打开工作台不调用模型，无需先安装 Python 包。

1. 点击“新建课题”，填写名称与研究目标。
2. 按需选择本地材料，保留或调整预算与同时探索的数量。
3. 点击“创建课题”，进入研究页面；默认保存到 `~/work/research/课题名称`。
4. 点击“开始研究”，通过本机 DSH 调用模型；路径图随研究状态更新。
5. 在页面中暂停、继续、停止，或修改后续研究要求、预算和材料。

也能从首页打开已有研究目录，并导出可离线查看的研究图。关闭浏览器页面不会停止研究。要停下正在执行的工作，使用页面里的“停止”。详细操作见 [WORKBENCH.md](docs/WORKBENCH.md)。

以下命令行入口仍保留，适合脚本和高级操作。

## 安装

查看地图与运行核心需要 Python 3.11+。调用模型还需要 Node.js，以及已配置模型和凭据的 DSH。核心和地图服务仅使用 Python 标准库，页面无需外部网络或前端构建。

```bash
cd /Users/ywang2397/work/agent-research/auto-research-init-v5
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ari --help
ari gui
```

也可以不安装，使用 `PYTHONPATH=src python3 -m auto_research` 代替 `ari`。DSH 配置与受支持环境见 [DSH_BACKEND.md](docs/DSH_BACKEND.md)。使用现有模型选择，框架不会自动购买、切换或配置模型。

## 创建并运行研究

```bash
ari -p /path/to/study init \
  --goal '研究目标，以及暂时无法确定的条件' \
  --budget 100000 --estimate 5000 --concurrency 2
ari -p /path/to/study start
```

`start` 在前台运行协调者，先提出探索，再派发执行。可以在另一个终端查看或调整：

```bash
ari -p /path/to/study status
ari -p /path/to/study show X-001
ari -p /path/to/study show 'X-001/result#draft'
ari -p /path/to/study pause
ari -p /path/to/study resume
ari -p /path/to/study stop
```

- `pause` 停止新派发，让正在运行的工作收尾；`stop` 请求终止受管进程。
- 协调进程崩溃或被 Ctrl-C 中断后，已派出的作业可能继续运行；`resume` 先检查它们，不重新派发同一工作。
- `status` 展示已持久化状态。协调者未运行时，用 `reconcile` 收集后台作业的最新结果。
- 研究暂停可能因为没有下一步提案、预算不足或执行异常；不表示科学问题已经解决。

### 明确议程与人工调整

初始化默认问题为 `Q-001`。多个问题可写为 JSON 并通过 `init --questions questions.json` 或 `steer --questions questions.json` 提交：

```json
[
  {"id": "Q-001", "text": "目前的假设在哪些条件下可能成立？"},
  {"id": "Q-002", "text": "是否存在反例或替代解释？"}
]
```

```bash
ari -p /path/to/study steer --questions questions.json --notes notes.md
ari -p /path/to/study steer --budget 150000 --concurrency 2
ari -p /path/to/study resume
```

议程与笔记保留版本，执行记录保存实际采用的版本。笔记中的明确结果引用会核对存在性；程序不判断每句话是否有充分科学依据。停止或删除某问题后，未派发的旧提案不会继续盲目启动。

也可以手动写一份提案并 `propose proposal.json --request-id operator-001`：

```json
{
  "question": "Q-001",
  "why_now": "已有草稿保留了一个尚未检查的前提",
  "plan": "检查该前提；预算内做不完就交出推导和剩余障碍",
  "modifies_artifact": false,
  "inputs": [{"ref": "X-001/result#draft", "use": "接续其中的未完成推导"}]
}
```

示例引用必须先存在。程序允许同父同问的不同探索，不按文本相似自动合并。

### 材料、预算与恢复

使用 `init --resource /absolute/read-only-data` 明确加入只读外部材料。使用 `--source-git /path/to/repo` 可给需要修改代码的节点建立独立 worktree；源仓库必须干净。研究产物按文件/目录归档，Git 元数据不进入归档；不自动选冠军、提交源仓库或合并分支。

`--budget` 与 `--estimate` 的单位是后端报告的 token，不是美元。协调者也计费；预估是派发门槛，单次真实调用可能超出预估。硬控制的是并发与受管执行超时，不宣称拥有后端不存在的硬 token 上限。

缺失用量保留 `unknown` 和预算占用。确认费用后可以补账：

```bash
ari -p /path/to/study settle A-001 --cost 6000 --cost-kind estimated
```

如果程序无法确认旧执行是否结束，会暂停并保留占用。先检查/停止旧进程，再显式确认：

```bash
ari -p /path/to/study reconcile
ari -p /path/to/study reconcile --attempt A-001 --confirm-stopped
```

这一步不会凭空清零费用。仍存活的受管执行不能被确认结束；进程查询不可用也不能当作结束证据。

所有作业结束或核实后可以备份、恢复到新目录：

```bash
ari -p /path/to/study pause
ari -p /path/to/study backup /path/to/new-backup
ari -p /path/to/restored-study restore /path/to/new-backup
ari -p /path/to/restored-study check
```

备份包含一致的数据库与项目材料；未复制的外部数据、源 Git 仓库和后端配置仍需可用。恢复保留未完成文件，并调整内部工作区位置。

## 查看与验证

打开研究地图：

```bash
ari -p /path/to/study view
```

命令启动仅监听本机的只读页面，并打开浏览器；研究运行时，页面每 3 秒读取最新持久化状态。点节点查看计划、输入、产物版本、发现及未决事项；沿引用跳转，用上下游追踪查看一条研究路径，也可缩放、拖动画布和筛选节点。实线表示使用了已有材料，虚线表示某条发现被后来的发现修订。已归档表示这一段工作结束，不代表科学问题解决。

`view --port 8173 --no-open` 可指定端口并只打印地址。Ctrl-C 关闭地图服务，不会停止研究。页面只读取状态，不负责派发、收集作业或修改节点；若协调者已停，用 `reconcile` 收集后再查看。操作与状态含义见 [RESEARCH_MAP.md](docs/RESEARCH_MAP.md)。

```bash
ari -p /path/to/study export
```

`export` 生成可直接用浏览器打开的 `.research/views/research-map.html`，保留同样的交互，显示导出时的快照；也保留 `INDEX.md` 和 JSON 节点卡。HTML 自带数据，无需启动服务或访问 CDN。导出包含研究正文，分享文件即分享其中的研究内容。

没有研究项目时，可直接用浏览器打开仓库附带的 [离线演示](examples/research-map-demo.html)，或生成一个明确标记为演示的中文项目：

```bash
PYTHONPATH=src python3 scripts/create_visual_demo.py --project /tmp/ari-map-demo
ari -p /tmp/ari-map-demo view
```

演示包含理论推导分支、反例、修订、半成品接续和多输入汇聚，不调用模型，也不代表真实研究成果。`check` 核对数据库、引用和归档版本，不验证科学结论。

```bash
PYTHONPATH=src python3 scripts/run_demo.py
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
node tests/dsh/smoke.mjs
```

第一个命令用确定性后端跑完整 A → B/C → D → E 流程，不调用模型；第二个运行本地测试；DSH 烟测的模型开关和参数见后端文档。进程测试需要宿主允许查询子进程，macOS shell 隔离测试需要允许调用 Seatbelt。

开发测试依赖可通过 `python -m pip install -e '.[dev]'` 安装。

协议见 [PROTOCOL.md](docs/PROTOCOL.md)，实施结果与限制见 [VALIDATION.md](docs/VALIDATION.md)。旧 brainstorm 和两个实验目录是设计历史，不是运行时指令。
