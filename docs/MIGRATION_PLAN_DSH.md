# auto-research-init v5：迁回完整 DSH 的迁移计划

审查日期：2026-09-14（Asia/Shanghai）。本阶段仅审查和规划；本文中的新接口、表、文件与验收步骤是后续实施任务，除明确标为“事实”的内容外，均不代表已经实现或验证。

## 1. 结论与范围

**设计决策：以正式 DSH Web profile 为宿主，新增研究 Harness 的服务端插件与客户端工作台；保留 Python + SQLite 图存储、事务、归档和账本。淘汰自行装配精简 DSH Agent 的执行路径，以及独立 App 的调度、控制和模型配置入口。** 研究图组织探索，原生会话承载实际执行。完整 DSH 不等于无条件启用所有第三方插件；研究执行仍须具备按尝试生效的权限边界。

当前版本是图运行原型。“完整 v0.1 已交付”的旧声明已撤回，不能以文档声明、测试数量或出现了图形界面证明能力。完成标准是：用户从 DSH 工作台即可理解、追查并控制研究，包括没有节点、没有最终提交、工具失败、超时和重启后的场景。

本次未运行模型、未恢复研究、未改 provider/凭据/正式 profile，未调用会产生 reconcile 副作用的旧运行入口。只交付本文件，不向 brainstorm 追加版本，不实施迁移，不修改现有运行项目。后续实施中的真实模型验证须单独授权。

### 1.1 实际审查对象

| 代号 | 位置与用途 |
|---|---|
| V5 | `/Users/ywang2397/work/agent-research/auto-research-init-v5`，当前 Python + SQLite 实现；本文的交付目录 |
| V42 | `/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v42`，原生插件接入方式参考 |
| OLD | `/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5`，本次初始 cwd；旧原型，不在此交付 |
| DSH | `/Users/ywang2397/.hermes/node/lib/node_modules/@deepseek-ai/dsh`；`package.json` 实际版本为 **0.1.2-alpha.2** |
| PKG | `DSH/node_modules/@deepseek-ai/`；下文 DSH 组件文件路径均相对于此处 |
| CASE | `/Users/ywang2397/work/research/Gaussian-based Debiasing Derivations`，只读故障核查对象 |

已读取 V5 的 `V0_1_PLAN.md`、`BRAINSTORM_REVIEW.md`、`docs/PROTOCOL.md`、`docs/DSH_BACKEND.md`、`docs/WORKBENCH.md`、`docs/VALIDATION.md`、`docs/START_DEBUG_2026-09-14.md`，并核对实现与测试源。`BRAINSTORM_V5_GRAPH.md`、experiment1/2 仍为历史材料，不承担当前规格。旧 `DSH_PLUGIN_PRIMER.md` 只作背景，接口以当前安装代码为准。

当前 V5 目录执行 `git status` 返回“不是 Git 仓库”，因此不能给它编造 commit 基线。实施前先做源码快照及清单，再建立可回滚的代码版本；不要把旧原型的 Git 状态当成新 V5 的状态。

### 1.2 阅读本文的标记

- **事实 F/H**：已从本机代码或现存故障文件核实；代码路径后的数字是本次审查行号。
- **决策**：本文推荐的唯一目标方案，实施时按此推进。
- **待验证 G**：接口组合、运行时行为或产品体验尚未通过实际验证，不能在发布材料里写成已实现。

## 2. 代码审查：哪些结论有证据

### 2.1 当前 V5

| 编号 | 已证实事实 | 证据（相对 V5）与影响 |
|---|---|---|
| F1 | worker 自建 Cordis Context，使用 `agent-spine-demo`，关闭 skills、workspaceContext、bash、jobs、goals 等，再注册 read/write/run | `apps/dsh/worker.mjs:177–230`。未装配正式会话持久化/查询链路；此路径必须退役 |
| F2 | 工具事件只写工具名及少量退出信息；shell 输出保存在变量且截断 | `apps/dsh/worker.mjs:164–171,216–217`。丢失的参数、正文、输出不能从这类日志恢复 |
| F3 | session 建立时就有 ID；usage 在 assistant/message 累加，但日志到 turn/end 才带 tokens；结果先留内存 | `apps/dsh/worker.mjs:230–259,285–287`。`submit_result` 回 accepted 并不等于研究数据库已经接受；异常最终包可为 result:null |
| F4 | session ID 到 collect 才写库，且只有执行 completed 才导入产物或 proposals | `src/auto_research/runtime.py:300–347`。过程保存与结果导入、会话终止不应继续绑在一起 |
| F5 | 工作区接手复制只对有 node 的尝试执行；coordinator 被排除 | `runtime.py:214–223`。所有角色都要有接手路径，不能仅缩减 coordinator 职责来回避 |
| F6 | 图状态、幂等、预占、固定引用已有独立基础 | `store.py:86–91,172–207,567–629,700–737`；`artifacts.py` 的 freeze/materialize/prepare_workspace。保留事务与归档语义，不重写成 JavaScript 数据库 |
| F7 | 目前跨节点引用要求来源 closed，publish 一次即关闭；同节点 local_id 只能发布一次 | `store.py:130–136,447–455,739–838`。不能直接表达同一开放节点发布多个固定阶段；不是完整的一般研究关系图 |
| F8 | Store 构造器建表并无条件设置 user_version=1；set_attempt 可写任意扩展字段 | `store.py:94–149,658–698`。先引入显式版本迁移及受限生命周期 API，防止新库被旧构造器降标、session 关联被覆盖 |
| F9 | GUI 只选择活动尝试或最后一次尝试，过滤原始日志、命令、参数；图的 attempt 投影不含 session_id 和错误详情 | `workbench.py:20–83`；`visualization.py:26–46`。已有页面不能替代执行历史与原生会话入口 |
| F10 | UI 用最后事件推断“模型正在处理”，0 节点在部分状态下显示“尚未开始” | `assets/research-workbench.html:1274,1291–1316`。有过失败规划的空图不能显示成从未执行 |
| F11 | 调度前沿仅观察 closed 节点/议程/纠偏；备份函数会 reconcile | `runtime.py:388–395,542–565`。阶段发布要单独触发后续；旧备份入口不适合作为只读迁移盘点工具 |
| F12 | 故障与事务测试有价值，但不覆盖完整 DSH 用户路径 | `tests/test_runtime_edges.py:142–223,285–389`、`tests/test_job.py:189–242`、`tests/test_store.py:194–250,343–427`；`tests/test_start_feedback.py:35–88` 还把不暴露参数当作预期。保留保护凭据的断言，改掉屏蔽正常研究过程的断言 |

### 2.2 v4.2 应继承的连接方式

| 编号 | 已证实事实 | 证据（相对 V42）与处置 |
|---|---|---|
| F13 | server/client 插件进入宿主，注册 tools/commands/workflow/RPC | `package.json:5–10,22–37`，`cordis.patch.yml:4–7`，`lib/index.js:29,92–110`。继承装配方式 |
| F14 | 启动前 wfOpen，start 后立即把真实 run.id 绑定到条目 | `lib/engine.js:627–647`。继承顺序，但将内存操作改成持久事务 |
| F15 | 订阅原生 session/event；slots 注入 Campaign；能跳子会话及宿主导出 | `lib/workflow.js:75–126`，`lib/client.js:481–495,613,1431–1443`。继承“研究摘要 → 原生轨迹”的路径 |
| F16 | RPC 按会话 cwd 定位项目；命令接 jobs | `lib/rpc.js:40–61`，`lib/commands.js:49–58,77–84,99–119`。改为显式 session→attempt→project 映射；保留同一宿主控制入口 |
| F17 | shared.batch 单例、逐 round await、单一 inflight、批末 best-effort timeline | `lib/tools.js:30–38`；`engine.js:274–296,509–510,538–592`；`workflow.js:23–24,90–96`。不能只换成 Promise.all 或直接复制工作流投影 |
| F18 | 无 inflight 推断“思考中”，缺失用量显示 0；读取宿主内部缓存文件 | `client.js:955–964,1279`；`lib/statusdata.js:361–378`。改用当前正式查询接口与显式观测状态 |

旧插件 `/stop` 文案承诺本轮收尾，实际 `commands.js:81` 调 jobs.kill，`engine.js:160,299` 立即走 abort。新实现必须统一按钮、命令和实际控制语义。PRIMER 中 jobs.kill/userQuestions 的旧签名也不能照搬。

### 2.3 当前完整 DSH 已有能力及限制

| 编号 | 已证实事实 | 本机代码位置（相对 PKG） |
|---|---|---|
| H1 | base 挂载 JSONL 持久化、精确 session 查询、projection/cache、原生 jobs/subprocess/permissions；Web 加统计、会话控制、工具/轨迹/设置 UI | `dsh-base/cordis.patch.yml:110–133`、`dsh-web-app/cordis.patch.yml:57–87,303–445`；`dsh-agent-presets/presets/standard/agent.cordis.yml:1–9`。全文搜索默认关闭，不影响精确 session 读取，不能承诺默认全局全文检索 |
| H2 | Web 使用每会话 preset；create 接受预先指定的 sessionId、cwd、agentPreset，创建本身不 prompt | `dsh-api-session-controller/lib/index.js:349–360,440–461,565–592`；服务 `ctx.sessionController` 在 `:2594`。正式 create 包含 preset 与模型选择，优先复用 |
| H3 | `AgentRegistry.create` 的 meta.cwd 是正式接口；默认 subagent spawn 继承 parent cwd | `dsh-agent/lib/types/index.d.ts:58–110`；`dsh-subagent/lib/types/types.d.ts:92–147`、`lib/types/child-agent.js:111–124`；`dsh-subagent-in-process-driver/lib/index.js:160–186`。签名存在不能证明并行工具目录与访问边界实际正确 |
| H4 | 原生直接 prompt 会 steer/followup，cancel 使用 keepInbox；subagent origin 有单独控制所有权 | `dsh-api-session-controller/lib/index.js:719–760,825–830,120–133`。原生 UI 操作必须进入研究门控，否则会绕开预算或停后继续 |
| H5 | 有 `agent/pre-step` 拒绝能力与 per-agent `tools.guard` | `dsh-agent/lib/types/runtime-types.d.ts:228–245`、`dsh-agent-loop/lib/index.js:497–518`、`dsh-tools/lib/types/index.d.ts:612–621`。这是步骤/工具门控扩展点，不覆盖所有直接 LLM 调用 |
| H6 | 默认 workspace-write 不限制所有读取；jobs-local 状态在进程内 | `dsh-tool-bash/lib/index.js:171–182`、`dsh-tool-fs/lib/index.js:240–257`、`dsh-sandbox-policy/lib/index.js:132–146`、`dsh-fs-sandbox/lib/index.js:87–101,125–165`、`dsh-sandbox-local/lib/index.js:45–74`；`dsh-jobs-local/lib/index.js:96–106,165–208,394–451`。cwd、只读位、jobs 空列表不能独立证明隔离或进程结束 |
| H7 | 原生 bash 中止有丢掉已取得结果而抛 TOOL_ABORTED 的路径；后台 bash 不直接使用当前工具的 signal | `dsh-tool-bash/lib/index.js:403–436`。完整 profile 并不自动补齐所有取消输出，必须前置验证并修补宿主扩展 |
| H8 | 原生会话保留正文/工具参数/结果，但写入存在 write-behind；关键交接需要 flush | `dsh-session/lib/types/types.d.ts:218–303`、`dsh-session-persistence/lib/index.js:842,1284–1293,1435–1456`。`dsh-session-checkpoint-policy/lib/index.js:17–29,49–75` 已在模型/工具派发前提供 flush；业务 binding/发布/停止边界仍要显式核实，`whenIdle` 不是持久确认 |
| H9 | 标题和压缩直接调用 LLM，不能只累计 assistant/message | `dsh-session-title-llm/lib/index.js:209–244` 直接 stream 且返回不带 usage；`dsh-compaction-basic/lib/index.js:291–302,591–605` 直接 stream，成功 summary 才保存 usage。原生 token 字段口径见 `dsh-llm/lib/types/types.d.ts:115–137` |
| H10 | 原生子进程有进程组、TERM→KILL、dispose/exit 清理，但不能据此保证宿主 SIGKILL 后全部结束 | `dsh-subprocess-local/lib/index.js:803–856,909–930,1245–1306`。不可观察的硬退出与逃逸进程仍需持久身份核实 |

**限制说明：以上是静态代码证据，不是本次运行验证。** 正式 profile 下实时打开、重启回看、真实双会话隔离、取消外部进程、费用去重及入账门控仍须通过第 10 节的验证门。

## 3. 目标架构与唯一责任人

```text
正式 DSH Web profile
  ├─ 原生模型设置 / 凭据 / preset / permissions / tools
  ├─ 原生 sessions / JSONL / 查询 / 轨迹 / 导出 / 控制
  └─ auto-research-v5 插件
       ├─ 原生 slots：研究图 + 全部执行列表 + 文件/账本/控制状态
       ├─ 唯一研究 scheduler + session adapter + 事件投影 + 访问门控
       └─ 私有本地 IPC → Python 存储服务（不调用模型，不自主派发）
                              ├─ SQLite：图、尝试、命令、预占、结算、游标
                              └─ artifacts：私有 workspace、快照、固定对象
```

### 3.1 决策：正式 Web 会话控制器是首选执行接入点

采用正式 base + web profile，加增量插件 bundle；会话优先经 `ctx.sessionController.create({sessionId,cwd,agentPreset})` 创建，使用它装配的原生模型选择、preset、工具和会话能力，再经原生 prompt/control 路径执行。按宿主实际选择解析完整 preset（当前正式默认是 standard），不擅改用户保存的模型/preset。研究角色指令、材料上下文和权限通过插件扩展进入会话，不覆盖成一套简化 persona/executor。当前正式扩展点是 `agentPresets.mount`（`dsh-agent-presets/lib/types/index.d.ts:192–228`）；不要假设存在未经核实的 YAML `extends: standard`，也不复制整套 standard 配置造成漂移。

研究节点默认对应**普通原生会话**，不伪造 `origin:subagent` 或 parent 以求侧栏树形展示。这样可直接使用原生 Web 会话查看和控制。若后续真正使用 subagents，则必须完整遵守其 owner 与专用控制路由；它是受账本管理的执行能力，不是研究图的结构约束。

`ctx.agents.create({meta:{cwd},setup})` 仅作正式控制器无法提供必要能力时的适配退路，且必须复用宿主 preset/model composition 并补齐 Web 生命周期验证。不能退回 agent-spine-demo、重挂 read/write/run、另做模型和日志页面。若完整宿主缺少必要门控或进程观测接口，先提交范围明确的 DSH 扩展/修复；门槛未通过时停留在只读工作台。

### 3.2 权责分配

| 对象 | 唯一责任与约束 |
|---|---|
| DSH | 模型请求、原生工具执行、会话事件与轨迹、原生控制事实、provider 报告的 usage；不判断研究节点完成或科学正确性 |
| Harness scheduler | 每项目一个调度持有者；决定何时请求规划、领取、派发、暂停、停止及接手。coordinator 模型只提建议，不能另起调度循环 |
| Python + SQLite | 图和执行业务状态的权威账本；领取/预占/幂等命令/结算在事务中完成，Node 不直接另写 SQLite |
| 事件适配器 | 接收 DSH 的真实执行事实，持久化带源标识的投影；不能凭超时/无事件/空结果自己宣称工具已退出 |
| 执行终态 | DSH 提供 turn/工具/jobs 的事实；Harness 确认“该 attempt 不会再发模型、相关外部进程已终止”后将终态写库。turn/end 不自动关闭 node |
| 费用结算 | Python 账本唯一落账；依据去重后的宿主/provider 观测，分别记单位、来源、完整性。缺失不能变成 0，tokens 不等于金额 |
| 工作区与对象库 | 保留 ArtifactStore，负责尝试私有目录、版本摘要、引用物化；运行隔离由 DSH 的每会话 guard 与内核执行边界落实 |
| 研究看板 | 显示持久研究状态、当前观测、命令是否生效及原生链接；不自行维护另一套运行状态机 |

Python 服务采用子进程 stdio 的有界 JSON RPC，继承通道只供宿主插件访问，不开第二个 HTTP 控制服务。模型不能访问 IPC、数据库、jobs 控制文件或账本写入口。请求带 request_id、project_id、attempt_id、租约代次；回应只有事务提交后才称 accepted。服务失联时保留 UI 的最后状态并显示失联，停止新派发，DSH 原生停止仍可操作；恢复后从记录补投影，禁止另起 scheduler。

## 4. 现有模块处置

下列新文件名是建议实施边界，可按项目构建习惯调整，职责不能合并回一个独立执行器。

| 当前模块 | 处置 | 后续代码范围与理由 |
|---|---|---|
| `store.py` | 保留并改造 | 显式 schema migration；保留事务/请求去重/预占；加入不可覆盖的 session 绑定、阶段发布、源事件去重与账本结算 API |
| `artifacts.py` | 保留并扩展 | 保留 SHA-256、原子归档、竞争/符号链接校验；多版本输入目录按 publication/ref 区分；增加所有角色的恢复清单与快照 |
| `io.py` | 保留可重建视图与原子写 | 导出包含版本引用、尝试和观测缺口；agent 查历史用查询/只读视图，不依赖上一轮散文 |
| `prompts.py` | 改造 | 保留零实验、空 findings、条件和证据语义；改为原生工具与增量研究工具契约，coordinator 同样可保存/发布材料；资源路径明确进入上下文 |
| `runtime.py` | 拆分后退役旧执行循环 | 保留配置校验、上下文构建、候选策略与归档帮助函数；移入无自主循环的 domain/service 模块。`Popen`、output.json collect、tick/start 与旧 frontier 路径退出正式运行 |
| `job.py` | 退出 DSH 执行路径 | 不再监督 worker.mjs；保留 legacy 数据读取器及“停止回执不等于进程终止”等测试语义。必要的进程跟踪应接宿主 subprocess/jobs，不产生另一套模型 supervisor |
| `apps/dsh/worker.mjs` | 淘汰 | 不继续补工具、模型、日志或 resume；以 `apps/dsh/plugin/` 的 index/session-adapter/scheduler/observability/permissions/rpc/client 等模块替代 |
| `workbench.py` | 拆分 | 保留项目注册、参数校验和读模型；线程队列/控制器退出。创建与控制经 DSH 插件同一命令入口 |
| `gui.py`、独立启动脚本 | 退役交互入口 | GUI 迁到原生 slots；宿主提供模型、权限、会话与轨迹页面。原独立服务仅作迁移前旧版本回滚环境，不与新库同时运行 |
| `research-workbench.html` | 复用内容与交互设计，改为客户端组件 | 保留图/执行选择、材料与预算表单；去掉独立 fetch 服务和 dsh_config 选择；增加原生链接与全部尝试历史 |
| `visualization.py`、`research-map.html` | 保留图语义和离线导出 | 在线图经插件数据源，支持阶段版本、一般关系及循环；离线文件明确标导出时间、缺失轨迹，不拥有控制权 |
| `cli.py` | 保留管理/导出/校验；控制改路由 | 新库的 start/resume/stop 发给宿主同一 handler；宿主不可用时不得偷起旧 runtime。纯离线读取不实例化带写副作用的 Store |
| `tests/` | 保留有价值的业务与故障断言 | 旧后端测试仅证明旧实现；新增正式 DSH profile 的集成和 UI 验收，尤其是未提交/取消/重启/接手 |
| 文档、打包 | 实施后同步 | 更新 README、协议、DSH_BACKEND、WORKBENCH、VALIDATION 和 package data，去掉旧 worker 的正式安装入口；历史验收保留日期与适用范围，不继续称完整交付 |

## 5. 身份、图与最小数据变更

### 5.1 身份

项目增加稳定 `project_id`（UUID）；现有 project 表的常量 id=1 与基于路径的 GUI hash 只作 legacy 标识。项目搬目录不换身份。DSH 的定位使用 `(host_instance_id, session_id)`，host ID 是稳定安装身份，不是易变端口或 PID。

`project → node（可空）→ attempt → DSH session`：每次尝试最多绑定一个原生 session，绑定后不可覆盖；重试/接手创建新 attempt 和新 session，保留 `resumes_attempt_id`、采用的材料版本与接手清单。无节点规划用 `role=coordinator,node_id=null`，从预占完成开始出现在“全部执行”，空图页直接展示它。不能为了让规划可见而编造研究节点。

可以把项目挂在既有 DSH workspace/入口会话导航下，但入口不是未经记账的额外规划器。任何与研究关联的模型运行，包括入口发言、人工 followup、重试及实际启用的辅助 LLM，都要有可归属的用量记录。已终结尝试的原会话可回看；若用户从原生 composer 接续，必须重定向为“新尝试 + 新原生 session”，按需带入历史并导航到新 session，不能重新 prompt 旧 session 或让旧 A-id 重新 running。实现不了这条门控就不能开放该会话的自由继续功能并声称账本完整。

DSH parent/child 表示实际执行所有权；研究 inputs、修订、相关/接续理由存在图中。一个 session 不需要多个 DSH parent，综合节点通过多个固定 input 表达。原生 fork 是创建对话的能力；研究分叉还需新 node/attempt、权限及预算，不等于点 fork 后自动拥有新的研究状态。

### 5.2 材料版本依赖与研究关系分开

保留 `proposed/open/closed`：closed 只表示该工作段由人或 agent 明确结束，不是正确、成功或问题解决。允许 open 节点多次发布固定阶段材料；发布和关闭是两个独立操作，关闭时可以同时发布，但两者均不要求实验或 findings。

正式新引用采用 `pub/P-001#draft`，publication ID 项目内唯一，节点关联由发布记录提供；这样无节点 coordinator 也能发布材料并被后续引用。原 `X-001/result#draft` 永久解析到迁移时那个固定版本，绝不悄悄改为 latest。草稿“当前文件”链接带 attempt 和路径，只供查看；一旦用作正式 input/evidence，必须固定成 publication 引用。可以明确发布半成品，其 status/gaps/limitations 如实保留。

新增普通 `relations(source_node,target_node,label,note,created_by,created_at)`，只记录人的/agent 的明确关系及理由，允许成环；不推导科学真假，不默认触发等待。修订仍指向固定 finding，旧 finding 保留且不自动判废。只有材料引用本身必须存在且固定；**不对 node 总图施加 DAG 校验**。同节点不同阶段可使用此前自己的版本；跨节点 A 阶段 → B 阶段 → A 新阶段合法，时间顺序约束的是材料版本而不是研究方向。

### 5.3 建议的最小增量 schema

| 变更 | 必需字段/约束及理由 |
|---|---|
| 版本迁移入口 | 显式 schema 版本与 migration history；旧于当前才迁移，新于支持范围则拒绝写；拆出只读 reader，不再构造即建表/降版本 |
| 项目标识和控制 | project_id、legacy_alias、control、controller_owner/epoch；本机排他锁防双控制器，持久代次使旧命令失效。不能仅因 lease 超时就重派未知执行 |
| session_bindings | attempt_id 唯一；host_id/session_id 唯一；预分配 ID、bind 状态、preset/权限摘要、created_at；legacy ID 单独记来源，不能伪装成可恢复原生会话 |
| commands | request_id 唯一，target、desired_action、requested/acknowledged/effective、reason、epoch；原生 UI 与研究按钮进入相同处理器 |
| execution_observations | attempt/session、DSH 事件 seq/原生事件键、observed_at、durable_cursor、最后活动、控制/进程核实证据；唯一源键避免重放重复折叠 |
| process_observations | attempt/session/callId、宿主启动代次、PID+出生标识、PGID、已知子进程及退出证据；在原生启动/退出处持续记录，支持重启后核实，不能只保存临时 jobs ID |
| usage_observations | 来源事件或 provider request ID、单位、累计/增量语义、provider/model、reported value、完整性；原始观测只追加，去重后汇总入账 |
| publications + publication_items | publication_id、node_id（可空，允许规划材料）、attempt_id（新执行强制绑定，旧材料来源无证据时可空）、manifest、created_at；item 在 publication 内唯一，ref 永久稳定，文件摘要沿用现有算法 |
| snapshots / recovery manifest | attempt、时间、文件及摘要、完整/变化中/不可读说明、原会话链接、前次尝试；不是 finding，也不是正式发布 |
| relations | 仅结构关系及注释；不增设 merge/revisit 状态机 |

现有 attempts、events、requests、预算字段和原 published_items 原始内容必须保留。新版本可复制旧发布项到新表并建立兼容 ref 映射，旧表转为只读；不要立即毁掉旧表的约束再让旧程序继续写。旧发布项没有直接 attempt_id，只有原请求回执等证据明确关联时才绑定，否则标“旧材料来源尝试未知”，不能猜最后一次或成功那次。`nodes.result` 仅保留旧兼容终段视图，新实现不以它冒充全部阶段历史。

生命周期从任意 `set_attempt(fields)` 收窄为 `reserve / bind_session / start / request_control / observe_terminal / settle` 等域操作。每个操作校验身份、权限、状态与幂等键。节点同时至多一个不确定或活动 attempt 的现有约束继续保留；真实并行用不同节点，不让两个 agent 无约束共写一个目录。

## 6. 执行、持久化与账本

### 6.1 第一次规划也走完整流程

1. scheduler 获取项目排他权；事务领取 attempt、预占预算、记录配置/议程/笔记版本、选定的 session ID 和创建命令。此刻 UI 已能显示“准备启动 A-xxx”，即使还没有 node。
2. 准备私有 workspace、明确材料清单与权限清单。输入版本固定，提示包含实际路径、文件格式和读取入口；不能只在权限表里列材料。
3. 全局门控先按已登记研究 session ID 生效，再调用正式 create。验证返回 ID/cwd/preset，等待必要 session flush，持久化 binding 后才允许 prompt。创建失败与“创建了但回复丢失”分开：后者按预定 ID 查询，不能直接换 ID 重发。
4. agent/pre-step 检查步骤准入，LLM 调用边界再对每个实际请求检查额度并登记调用身份（含标题、压缩、重试），工具 guard 配合原生 fs/sandbox backend 强制节点边界。不能靠解析 shell 命令实现读隔离。真实事件持续进入 DSH 原生记录，研究投影实时更新并带持久游标。
5. `research.propose`、`research.note`、`research.snapshot`、`research.publish`、`research.close_node` 通过受绑定身份约束的宿主工具分别提交。工具只有在对应事务/归档完成后回执，不在内存缓存等最终 JSON。结构不合法只拒绝本次操作，既有文件、轨迹及此前已发布阶段保留。
6. turn 结束后先阻止该尝试再起新请求，核实受管工具/后台进程，再记录终态和结算进度。没有最终成果不等于执行失败；有工具错误不等于科学负结果；执行完成也不自动关闭 node。

### 6.2 连续记录的四层内容

| 层 | 保存时机 | 可看/可接手/可引用 |
|---|---|---|
| 原生会话轨迹 | 模型消息、工具参数/返回、错误随宿主事件记录；关键边界 flush | 运行中即能看，重启后查原会话；接手按需查阅。提供者未返回的内容不能补造 |
| 草稿与工作文件 | 工具写入即存在；宿主文件索引发现并展示，不依赖 progress.json | 允许随时查看，标“变化中”和最近观测时间；不能作为漂移的正式引用 |
| 阶段快照 | 用户/agent 请求、停止收尾及受控周期触发；用现有 freeze 检查稳定性 | 可供接手；变化中文件单独标失败/跳过，不能宣称整目录是同一时点快照 |
| 正式发布 | 显式 publish，文件先固定，引用和发布记录后事务提交 | 可被后续节点正式引用；允许 partial/空 findings/零实验；不会自动关闭节点或认证结果 |

文件列表至少有路径、大小、mtime、快照/版本、所属 attempt，以及已知的创建/写入工具链接；外部程序写文件未必能准确归因，显示“发现于扫描”，不伪造工具事件。大文件与输出允许有界预览，但提供持久全文/附件入口并明确截断。不是每次工具调用都创建研究节点。

coordinator 与 worker 共用这四层。progress.json 可作为 agent 自述，但缺失时 UI 仍有原生事件、文件、用量、错误与恢复清单；它不再承担可观测性的唯一来源。

### 6.3 用量与停止不是一次操作

研究预算默认单位是 tokens。保留 `cost_kind=actual/estimated/unknown` 的历史信息，新增明确 unit/source/completeness；actual 在历史案例中解释为“后端报告值”，不冒充供应商发票或人民币/美元。只有有价格来源、币种与计价时点时才另算金额。

**决策：计费准入与计量落在宿主 LLM stream/request 边界，按 sessionId、purpose、唯一调用尝试登记；具体 hook 可用性由 G4/G5 验证。** pre-step 只管 agent 步骤，不能覆盖 H9 的标题/压缩。用量在拿到 provider 报告时立即持久记录；assistant/message、compaction/summary、原生 stats 用作关联和对账，不再重复加入同一消费。失败未报告也留下调用存在但费用未知的记录。

先核对 provider 的 input/output/cache/total 包含关系，再选一个口径，禁止 total 与其子项重复相加，也禁止对累计值反复求和。本机 DSH 类型将 inputTokens 定义为未命中缓存输入，cacheRead/cacheWrite 为不同项，reasoningTokens 不能机械再加到 output；适配器若提供 total 优先保留原值并检测差异。晚到或修正的用量追加调整记录，保留旧来源，不静默覆盖已结算实际值。

进行中用量消耗已有预占，不能“已知消费 + 原始整笔 hold”双算。每次尝试设当前总准入额 R、去重已知用量 K：已知消费计入 spent，未消耗预占为 `max(R-K,0)`；项目可用额为 budget 减去唯一的 spent 汇总和剩余预占。下一请求预计超出剩余额时，先事务补预占再发出；超出预估的实际消费照记。若存在未知尾部，另保留 unresolved 标志及未用预占，即使剩余预占为0也阻止自动继续。

迁移后预算只从新的去重账本汇总：每个旧 attempt 导入一条以 `legacy:<project_id>:<attempt_id>` 为稳定键的汇总基线，保留原数值/单位/来源/未知；旧 attempts.cost 原样留下作审计兼容字段，不再第二次加总。新 native attempt 只累计其真实请求观测。重复导入不增加基线，不把历史汇总拆造成虚构的逐请求 usage；13070、524659、537729 的恒等验收必须走新汇总路径。

运行中 UI 区分“已报告的用量下界”“仍预占”“缺失/未报告”“已完整结算”。有一个调用缺 usage 时，即使其他调用有数，也不能把总量标完整。无模型的准备失败只有在证明确未发出请求时才记 0。实际超额如实入账并阻止下一步；预算是准入与请求边界控制，不承诺精确 token 处硬停。

执行终态与费用完整性分开：已确认停止、费用未知，可以保留终态但仍保留未知费用占用；工具/会话存活未知则 attempt 维持 unknown，节点不能再次领取。`cancel acknowledged`、`session idle`、`jobs list 为空` 都不能各自成为释放占用和接手的充分证据。

历史 usage 永远无法补齐时，不把项目永久锁死。看板提供“补充账单证据”“登记人工估计”“增加后续可用额度”的明确操作，记录人、时间、理由、来源及调整值，只追加不抹除原 unknown。只有进程已确认终止后，用户才能采用明确的估计/风险预留方案授权后续新尝试；估计值不改标 actual，缺失尾部仍在历史视图中，不能靠填0消除未知。尚不能确认旧进程时，增加预算也不能授权重复运行。

## 7. DSH 内用户工作台

工作台以原生 slots 注入，首屏同时提供“研究图”和“全部执行”。图上选节点看到所有尝试与阶段发布；执行列表始终包含无节点规划、失败尝试和已中断尝试，不能只剩最后一次。节点状态、执行状态、费用完整性分别显示。

| 用户需要知道 | 原生 DSH 负责 | 研究插件补充 |
|---|---|---|
| 谁在做什么 | session/模型/工具轨迹 | project/node/attempt、角色、目的、采用版本、workspace、实际模型标识 |
| 读了什么、运行什么 | 模型可见正文、工具参数、返回和错误 | 从事件/文件/材料直接跳所属 session；如原生尚无精确 seq 跳转，带定位标识并提供明确可用的查找路径，不能是假链接 |
| 产生哪些文件 | 原生文件/附件能力可用部分 | 草稿清单、阶段快照、固定发布、大小/时间/摘要、打开/复制到接手工作区 |
| 花费多少 | provider 事件和原生统计 | attempt/project 账本、预占、未知、超额及来源差异 |
| 哪里错了 | 工具调用、结果、错误轨迹 | 失败时间、错误发生阶段、调用定位、相关草稿；错误摘要指回实际记录 |
| 怎样介入 | 原生 prompt/steer/cancel、权限提示 | 项目暂停/停止/纠偏、命令请求/确认/生效、范围和仍在运行的尝试 |
| 历史能否复查 | 原生冷会话查询/轨迹/导出 | 查询项目关联会话清单、版本和关系；缺失轨迹清楚显示原因，不制造可点击但无内容的入口 |

状态显示规则：有未结束 callId 才显示“工具运行中/等待工具”；有宿主审批/提问事件才显示“等待用户”；只有实际调度/模型事件才显示对应活动。另显示 last_event_at、观察连接状态与距最后事件时间。长时间没新事件显示“无新事件，最近已知活动为…”，不能猜“正在思考”。超时显示实际 deadline、取消是否已发出、是否已确认停止；不能只变红或写“失败”。

研究投影用 `(session_id,call_id)` 活跃工具集合，不能用单一 inflight。UI 的离线/落后、宿主活性和执行活性分别标注；断线时保留旧内容并显示时间，重连按游标补齐，浏览器刷新不负责驱动研究。

凭据脱敏保留：API key、Authorization、cookie、明确密钥值不应进入研究视图/导出。正常命令、文件路径、工具参数和结果不应整体删除。优先避免把凭据放入模型工具环境，并复用宿主脱敏边界；原生 transcript 与导出也要用合成密钥标记测试。若宿主无法安全展示某段，明确标明脱敏字段，不把整次执行只剩工具名当成可观测性。

## 8. 并行、人工干预与恢复

### 8.1 只有一个协调循环

插件 scheduler 按持久发布事件/人工纠偏版本推进，处理每条分支完成或阶段发布后即可调度的工作，不等一批全部结束。Python 可以保留纯候选策略函数，但不再自行 timer/tick/Popen。coordinator 输出是建议，经域校验和预算准入后才成为提案与执行。一般关系边不自动变成依赖锁。

并发数定义为活跃探索数，另有最多一个规划尝试；两类都计入模型调用额度和同一预算。若产品采用总并发限制，必须同时在 UI 和 scheduler 中明确，不能隐含多出 coordinator。超额、未知费用或不能确认的旧执行阻止相关新派发。

默认不允许受管 session 私自启动未登记的子 agent；原生委派工具经 guard 禁止或改走 Harness 准入。需要时仍复用真实 subagent 创建/轨迹，但必须分配受管 attempt、工作区和预算。此处限制的是研究权限，不是卸载完整 DSH。原生后台 bash/jobs 可按项目策略启用，必须登记 owner/进程/退出观测；无法恢复核实的常驻 daemon 不在 v0.1 支持范围。

### 8.2 控制语义

| 操作 | 持久命令及效果 |
|---|---|
| 暂停项目 | 先提交 paused，禁止新 attempt 和新规划；已在运行的 attempt 可按原边界收尾，UI 显示仍有几项运行。不能把“暂停”显示成“所有进程已停” |
| 停止项目/尝试 | 先冻结准入、记录 stop_requested，再经原生 cancel/jobs 控制清空或抑制待执行 inbox/goal/followup；待工具/进程确认退出后写终态。保留草稿与已发布阶段 |
| 原生会话 Stop | 与研究 Stop 折叠为同一取消意图，防止 keepInbox 造成停后自启；不能由看板另发第二套终态 |
| 纠偏 | 先保存带版本的研究笔记；默认后续派发采用，当前尝试保持原输入。需要立即影响当前尝试时，明确选择原生 steer 或停止后新尝试，并记录送达时点 |
| 分叉/综合 | 创建普通节点；分叉引用固定阶段，综合引用多个版本。不同 workspace 实际并行，不自动合并 Git 或判定意见一致 |
| 接手 | 先核实旧执行停止；生成恢复清单与稳定副本，创建新 attempt/session。不能覆盖旧目录、旧 ID、用量或失败记录 |

准入矩阵必须区分暂停和停止：`running` 允许符合额度的已登记执行；`paused` 禁止新领取/新规划，但允许暂停前已登记且仍运行的尝试在原投入边界内继续模型步骤和工具；`stop_requested/stopped` 拒绝所有新模型请求和新工具启动，并取消已有执行。未知身份、账本失联或费用准入不满足时均不得新发付费请求。恢复 paused 项目先核实，不将停止队列重新灌入旧尝试。

### 8.3 崩溃窗口与退路

| 断点 | 恢复动作 |
|---|---|
| 预占完成、未创建 session | 用预定 ID 查宿主；证实没有执行后可结束该准备尝试并释放费用占用；原记录保留 |
| session 已创建、binding/回执未完成 | 以预定 ID 查询/补绑定；未核实前不 prompt，不另造第二个 session |
| 已派发、投影服务掉线 | 原生记录继续；研究 UI 标“同步中断”；新准入被阻止。重连重放源事件，不按当前 UI 空状态重派 |
| 发布文件已固定、DB 未提交 | 保留未引用对象，重试同 request_id；不得出现 DB 引用半写文件 |
| 发布已提交、工具回执丢失 | 同 request_id 返回已存在发布；后来 session 失败不会撤销该阶段 |
| 宿主正常退出 | 阻止新派发，取消/等待受管工具，flush 会话/投影，保存控制器退出记录；未确认项保持 unknown |
| 宿主被杀、jobs Map 丢失 | 先查询原生持久轨迹并核对已登记进程身份与退出证据；无 jobs 不等于无进程。必要时用户经看板执行核实/终止；确认前禁止恢复模型或重发实验 |
| 确认终止但 usage 不完整 | 终态可见，账本仍显示未知与保留预占；新材料可被查阅，不能为方便接手重置账本 |

恢复清单包括：研究目的、旧 A-id/session、输入/笔记版本、已发布阶段、当前草稿及快照摘要、最后可见活动、错误/停止原因、未核实事项和已知用量。coordinator 与 worker 同样生成。摘要是导航，不替代完整原生轨迹；用户和新 agent 都能按权限查询相关历史，而非只能看到上一轮总结。

## 9. 现有数据迁移与 A-001/A-002

### 9.1 本次只读核查结果

2026-09-14 01:29 左右，CASE 数据库没有 WAL 文件；使用 SQLite `mode=ro&immutable=1` 做读取，未实例化 Store/Runtime。数据库为 paused，0 nodes，2 个 coordinator 尝试，二者均 interrupted；账本合计 **537729 tokens**，hold 合计 0。此处 immutable 只用于已停且没有 WAL 的此次读取，不是以后备份活库的方法。

| 尝试 | 现存证据 | 迁移后的呈现 |
|---|---|---|
| A-001 | cost=13070、cost_kind=actual；output result:null；`AGENT_ABORTED`；日志有材料读取 ENOENT/READ_DENIED/EISDIR；原 session ID `ari-8799bbd0-9e27-4c9e-a37f-86cbe44f564b` | 保留失败规划、后端报告用量、材料读取错误与 legacy session ID；无完整原生轨迹时明确“历史未持久化” |
| A-002 | cost=524659、cost_kind=actual；status reason=timeout、约900秒；output result:null；provider/model=`volce / qwen3.8-max`；原 session ID `ari-19905450-015c-4838-885d-689a33c6d8b5` | 保留超时、全部原始文件与用量；显示“未提交 proposals 的规划尝试，有未完成材料”，不能显示“没有做过研究” |

原工作台端口 54673 当时仍由 Python PID 85071 监听；只读 ps 查询中 A-001 的历史 PID 76186/76189、A-002 的 85706/85709 已不存在。**这只证明这些登记 PID 已不在，不证明所有可能脱离父树的进程均已排除**；本次没有点击继续，也没有把端口监听当成 GUI 产品验收。

A-002 `scratch` 现存文件及 SHA-256：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `probe_gaussian.py` | 7311 | `fad89acbb161d6ac594cf6fd96f263c46886667e85844ebfdeca2e68934c6768` |
| `scan_gain.py` | 3066 | `1f7718415ebfc28f3e27bf748c0eea5c8c67b2d24d1625255582a79083794d53` |
| `grid_scan_m1m2_2.json` | 154677 | `e805d8b54705c1bf0614a55a46fb622d057daa2e4d1d0b3e2d633866b59fd749` |
| `paper.txt` | 128300 | `8db979ad41ff5c9c71c5b7900fa3d7fce762d80e3099b20947eeec39b9c133ef` |
| `__pycache__/probe_gaussian.cpython-311.pyc` | 13234 | `3ab9bfa8095ed3b1d5ae2475bc3a9ce5d81e30d2c1894d50229ba41104b0d2fc` |

字节清单用于迁移完整性核对，不认证脚本或推导正确。源 PDF 位于 `/Users/ywang2397/work/interpretability/debiasing/_NeurIPS25__Gradient_Path.pdf`；迁移登记外部依赖及可用性，不能修改或假装已把原文完整纳入归档。

已经丢失或未被证实留存的内容包括：旧内存会话的模型正文、完整工具参数/返回、逐请求 usage、取消时未写出的内容，以及每次文件写入与工具的精确对应。只导入真实存在的 DB/events、request/output/status、简短 stdout/stderr 和 workspace；legacy 导入事件标明原文件来源、导入时间与原始时间，不能转换成仿造的 DSH 原生轨迹。即使存在 ari-* 字符串，也不表示宿主 sessionQuery 能找到它。

### 9.2 后续迁移操作顺序

1. **只读盘点**：新建迁移工具 preflight，读取 schema、项目控制、活跃/unknown 尝试、账本、对象引用、外部依赖与路径。不得调用旧 `backup_project()` 或 reconcile。记录源码版本与文件清单。
2. **确定静止点**：实际迁移前经既有控制入口暂停并核实受管工作退出；若仍活跃，仅做一致性备份，不执行可写切换。只有已获授权的实施阶段才做暂停/停止。本次不做。
3. **备份**：使用 SQLite backup API 产生一致 DB 副本，复制 `.research/objects`、所有 workspaces、jobs 原始记录、控制记录及必要视图；在冻结窗口完成交叉清单校验。活库不得仅拷一个 sqlite 文件或用 immutable 忽略 WAL。原生 DSH 数据通过官方会话导出/一致备份另存；不备份凭据进研究包。
4. **两份副本**：原备份只读封存，另建迁移演练副本，例如新建临时目录下的 `case-copy`。模拟活跃/失败数据另用合成夹具，绝不在唯一 CASE 目录上跑迁移测试。
5. **显式 dry-run 与迁移**：输出将新增的表、ID/ref/path 映射及缺口；迁移在副本进行。A-001/A-002 保留 ID、state、cost、cost_kind、时间、error 和原 files；新表追加 `unit=tokens, source=legacy_backend_reported`，控制仍 paused，不自动造 nodes/findings/publications。
6. **校验**：DB integrity/foreign_key、记录数及稳定字段逐项比对、原请求/事件保留、537729/13070/524659/hold=0 恒等、所有文件摘要、旧 ref 解析、外部依赖缺失提示、迁移幂等。移走原目录的副本测试必须证明新路径解析不偷偷读取原目录。Git worktree 另记录 base commit、脏文件和未跟踪文件；原始备份按字节封存。演练/恢复目录在任何 Git 写入前隔离复制来的 `.git` 指针，以只读普通文件查看，或在独立仓库重建后恢复全部变更；禁止直接对副本执行 `git worktree repair` 或改动源仓库 worktree 注册。验收要让旧根不可访问，确认新 Git 元数据不再指回原项目。
7. **DSH 只读导入验收**：看板显示空研究图但两次失败规划及其文件；legacy 轨迹入口明确缺失，不显示可靠结论。用户可预览“从 A-002 接手”恢复清单，确认真正启动后才建新 attempt；预览/导入本身不调用模型。
8. **切换**：验证通过后将新宿主注册指向迁移副本，原目录和封存备份保留。先只读，再经实施授权开启新执行；确保旧 CLI/GUI 不再作为这个项目的写入者。
9. **回滚**：停止新派发、核实新进程，保存切换后增量及新费用；切回封存旧版本/旧副本只能作为历史快照查看。若已经产生新用量，必须先带入追加账本或保持整体暂停，不能让回滚把新消费归零。禁止让旧 Store 打开 v2 库、禁止倒放 DDL 或覆盖唯一源库。

## 10. 实施顺序、依赖与阻断门

### 10.1 大规模改造前先验证完整宿主

所有 G 项用**正式 base + web + preset 装配**的隔离测试实例和临时研究目录验证。工程故障注入可装测试用确定性 provider；创建的是实际原生 session，工具是真实工具和真实并发进程。不得用两个旧 worker.mjs 进程或 mock 的成功返回代替。测试实例不修改用户 provider/凭据/正式 profile；真正模型验证见 11.2。

| 门 | 验证目标与必须留下的证据 | 未通过时的退路 |
|---|---|---|
| G1 创建/查询/原生 UI | 预分配 sessionId，create 两个不同 cwd 会话，prompt 前均可从工作台打开；原生模型/工具/preset 正常；运行中消息/工具可见，flush 后宿主重启仍可读，session export 可核对 | 暂停自动执行迁移，修正式 controller/preset/持久化适配，不自组精简 Agent |
| G2 并行隔离 | 同一宿主两个真正重叠会话：bash pwd、原生 fs 相对路径、读写各自哨兵；覆盖实际启用的 fs/search/bash/terminal/文件引用等所有读取通道；跨工作区、DB/jobs、共享输入拒写，未授权材料拒读；symlink/绝对路径/临时目录/环境变量边界均验证 | 先完成每 session guard + 内核限制扩展；只改 cwd 或工具名单不算通过。无法强制隔离就不开放并行执行 |
| G3 取消/退出/输出 | 长命令先输出再 sleep、前台/后台工具、忽略 TERM 的后代、人工原生 Stop、超时、宿主正常退出及被杀；检查尾输出与错误可见、进程真正结束、inbox/goal 不复活、预算不提前释放 | 在宿主 subprocess/tool/jobs 扩展中按 callId 持久保存已观测 stdout/stderr（大输出归档为附件），取消前固定已有内容，再记录 abort；补持久进程证据与退出清理。无法核实的显示 unknown，禁止自动接手 |
| G4 事件与费用 | native event seq、callId、重复重放、缺失/部分 usage、cache 语义、晚到事件、工具已回但 turn 未结束、flush 前后中断；检查原生统计与研究账本无重复计算 | 先补事件/usage 适配及持久游标；不能退回只在 turn/end 写账 |
| G5 原生操作准入 | 原生 prompt/steer/followup/fork/retry/goal/子代理、权限切换和直接 tools 路径不能绕过研究预算、身份或目录边界；已终态 session 不会复用旧 attempt。pre-step 与 tools.guard 是否足够逐路验证 | 接宿主正式 lifecycle/LLM hook；无法门控的受管功能暂不开放，并明确受影响范围，不接受“反正用户不会点” |
| G6 组件与保留策略 | slots、RPC/session scope、冷会话链接、导出、轨迹保留与删除行为；确认默认全文搜索关闭下仍可按项目/会话/节点查相关历史 | 使用正式 query/control/导航 API，禁止读取内部 projection-cache 文件充当长期协议 |

辅助模型调用（标题、压缩、自动重试等）已有 H9 代码证据证明不能全部靠 agent/pre-step 管理。必须盘点实际启用路径及原生 usage/请求标识，在 LLM 调用边界统一准入与记录：可归属的单独入账，不得漏计或双计；无法归属时明确缺口并阻止声称“完整预算”。不能静默关掉完整 DSH 的原生能力来绕过验收。

### 10.2 里程碑

| 阶段 | 依赖与代码范围 | 完成标准 | 失败退路 |
|---|---|---|---|
| M0 基线与只读盘点 | 无；源码/数据清单、只读 preflight；定义测试夹具 | 新旧目录分清、无运行副作用、迁移源与失败记录可复核 | 保留现状，不触碰研究目录 |
| M1 完整 DSH 最小插件验证 | M0；`apps/dsh/plugin` 最小 adapter/preset/guard/slots，`tests/dsh-profile` | G1–G6 的高风险路径获得实际证据；允许发现宿主缺口，但缺口关闭前不进入大规模迁移 | 修小范围 DSH 集成；保留原型但不继续堆功能 |
| M2 先交付过程可见性 | M1；schema dispatcher + session_bindings/observations/commands；Python 私有 IPC；单 attempt 预占/源用量/原生准入/取消和进程核实；执行列表、原生链接与文件视图 | 单个手动发起的规划/worker 在零 node、长期无结果、工具失败、中止和重启时均可追查且正确记账控制；发布不是展示前提 | 只读面仍可用；取消新执行，保留原生轨迹和持久映射 |
| M3 发布与恢复解耦 | M2；store/artifacts/prompts/io 增量协议，publications/快照/relations、所有角色接手 | open 节点发布固定半成品；另一分支可引用；旧 ref 不漂移；A-002 类型材料能经清单接手；发布后再崩溃不丢阶段 | 使用只读旧引用映射与草稿查看；不切换真数据写入 |
| M4 并行调度与一致控制 | M3；唯一插件 scheduler，将 M2 已有准入/用量/进程核实扩展至多 attempt 并发；同一命令 handler，退出旧 runtime/job 路径 | 并行与分支即时接续、综合、暂停、停止、纠偏、重启不重复派发；所有原生入口受门控，未知不当作零或结束 | 暂停项目，保留原会话；问题修复前不自动恢复 |
| M5 副本迁移与回滚演练 | M3+M4；migration CLI、manifest、legacy reader、项目注册切换 | 合成夹具及 CASE 副本全部通过第9节；只读导入可在 GUI 独立理解；回滚不遗失旧/新费用 | 弃用演练副本，原备份不变；切换后有新费用则保留增量并暂停 |
| M6 用户路径与受限真实验证 | M2–M5；浏览器流程、受控 live、打包和文档 | 第11节 UI 场景全部可复现，真实模型验证目标达成且证据留存；不依赖另一个 AI 解释日志 | 保持“原型/迁移中”，列明未通过路径，不发布完整 v0.1 |

允许并行准备纯 schema 设计、夹具及只读 UI，但 G 门未通过前不投入大规模执行器替换。发布顺序始终是**正式会话与完整过程可见 → 版本/恢复 → 自动化与并行 → 数据切换**。

## 11. 验收：从界面完成，不只检查 JSON

### 11.1 工程验收矩阵

所有场景从正式 DSH 工作台开始，由浏览器操作留下屏幕证据/交互记录及所关联 session、attempt、文件清单。测试可另外检查 DB/进程，但用户路径本身不得要求人工翻磁盘日志。通过/失败说明写实际行为，不用“通过 N 项”替代。

| 场景 | 用户必须能完成/看见 |
|---|---|
| 首次规划、零节点 | 点击开始立即看到 A-id、准备/连接/活动、模型、材料清单；打开原生会话看到真实读取与响应，无 proposals 也有完整执行历史 |
| 材料读取失败 | 选择缺失/不可读/不支持材料，定位实际路径、错误码与工具调用；修正材料作为新版本，旧失败保留 |
| 长期没有最终提交 | 持续工具/文件/消息可见；缺 progress.json 也能查；刷新/重连后仍看得到，能手动停并保留现场 |
| 两个并行工作区 | 同屏看到重叠执行、各自 cwd/工具/文件；真实越界尝试被拒且原因可定位；不能由进程级 chdir 偷换共享 cwd |
| 阶段半成品与无实验 | 无节点 coordinator 先发布材料，后续节点直接引用而不造假节点；open 节点发布 partial、空 findings、零实验；分支 B 在 C 未结束时引用固定阶段并继续；图不要求整批完成 |
| 多输入/一般关系/修订 | 综合读取两个固定版本，登记后续修订仍能打开旧 finding；一般关系可成环，查看/布局/调度不会挂起或误判真假 |
| 工具失败与部分输出 | 点开失败调用看到参数、错误、退出码与已观测输出；长输出截断有标记和持久附件；不会只显示工具名 |
| 超时 | deadline 后显示停止请求、原生取消和进程核实；已保存草稿仍可打开，消耗已知/未知分明，不能靠 final JSON 才出现历史 |
| 人工停止 | 从研究按钮和原生 Stop 分别操作，得到同一终态；运行进程实际退出，无 inbox/goal 自启，无额外调用，无提前释放预占 |
| 暂停与纠偏 | 暂停时已有长尝试仍可按边界继续、尚未领取的另一分支不再启动；纠偏版本与生效范围可见；继续前核实旧执行，不复制出第二个控制器 |
| 宿主正常/异常重启 | 原生历史、草稿、用量和命令仍在；不确定进程显示待核实；jobs 空列表不会触发自动重跑，旧 session 可以直接打开 |
| 新会话接手 | 看恢复清单，确认旧尝试停止后创建新 A/session；从旧材料副本续写，旧失败/用量/目录不变；coordinator 和 worker 都覆盖 |
| 用量缺失/重放/超额 | 缺失不显示 0；重复源事件不重复计费；实际超额照记并阻止下一步；已停止但待费用核实可区分；无法取得原账单时，用户可追加有来源的估计和额度决策后新建尝试，原未知仍可查 |
| 身份/权限/导出 | 通过原生 composer、fork、retry、模型/权限控件不能绕过登记；合成凭据标记不泄漏，正常路径/命令/结果保留；导出和冷会话链接可打开 |
| 迁移故障案例 | 打开 CASE 副本即看到 2 次中断、0 节点、537729 tokens、A-002 文件和历史轨迹缺失说明；回滚后旧记录与新增费用都可追溯 |

### 11.2 真实模型验证另行执行

本计划不启动真实模型，也不重新挑选复杂研究课题。工程验证先用确定性 provider 和固定小材料完成。随后经实施阶段明确授权，使用用户当前选择的 provider/model，在临时研究项目做一次受限验证；不修改用户配置，不恢复 CASE。

- **目标**：确认真实模型经过完整 profile 的上下文、原生工具、阶段工具、真实 usage、停止和新会话接手；不验证科学成果。
- **规模**：最多 4 个 session：两个实际重叠的短探索，一个受控中止尝试，一个新会话接手。使用不超过约 1500 tokens 的小文本材料，单响应上限建议 512 tokens，模型请求总数最多 10 次；若当前 provider 不支持该上限，先说明并重新设限。
- **预算**：预估总量不超过 20000 tokens；观察到总用量达到 20000、未知 usage、单尝试超过120秒、工具越界、不能确认取消或任一关键门失败，立即阻止后续请求并取消受管工作。总验证窗口最多10分钟。provider 延迟上报可能超额，照实记录，不能宣称此数是供应商硬封顶。
- **额外调用**：标题/压缩/重试等也计入上述请求与用量范围；无法归属时停止 live 验证并解决适配问题，不继续消耗额度摸索。
- **证据**：保留原生 session 导出、事件游标/usage 对账、工作区清单、原生 UI 录屏或截图、取消与进程退出观测、接手前后文件差异。失败也留存，不为了获得成功截图自动重跑。

## 12. 风险与“完整 v0.1”的重新认定

最大风险是**完整 DSH 的多会话权限和控制边界尚未被证明能与持久研究账本一致**：cwd 不代表读取隔离、原生 cancel 不代表外部进程全停、辅助 LLM 不一定走同一门控、原生事件写入也有缓冲。第二风险是把原生工具/取消输出缺口误认为插件接入后自然消失。第三风险是数据/费用迁移中的兼容错误，尤其旧 Store 的版本覆盖、路径迁移和回滚丢失新消费。

只有同时满足下列条件，才能重新称为完整 v0.1：

1. 正式完整 DSH profile 上安装即可使用研究工作台，复用原生模型配置、工具、轨迹、持久化与控制；不依赖旧 worker 或独立 GUI。
2. 用户从零规划到半成品、并行、失败、停止、重启和新会话接手都能在界面独立追查，无需另一个 AI 翻磁盘解释；缺失信息如实标注。
3. 固定引用与变化中工作分开，节点无需实验/发现即可开展，阶段发布无需关闭，图不强制 DAG，不自动判断科学真假。
4. 所有受管调用、原生控制入口、工具进程与账本经过同一约束；未知费用/状态不伪装为零或完成，失败和历史不被覆盖。
5. G1–G6、UI 矩阵、副本迁移/回滚及受限真实模型验证有可复查证据；A-001/A-002 的537729 tokens及半成品完整保留。

这一定义不要求证明框架提升科学发现质量，也不要求启用所有第三方插件；它要求研究组织、执行过程、人工控制和历史接手在完整 DSH 内实际闭合。

## 附：本次源代码基线

本次审查的关键文件 SHA-256（相对 V5），供实施前发现源码已变更时重新定位证据：

```text
src/auto_research/store.py                  191c349a7974bc99b403dbe745da0889e49c66c6ec05fa0a180365acd6b8f374
src/auto_research/artifacts.py              af07ffb0d14b4ec9de8ceb48b5b79c94b27f145cf2dafb707d3db68347a562fc
src/auto_research/runtime.py                962cb29150a668ceb7de380bae0c759d2b932057f488fd4c9765b2adf2305431
src/auto_research/job.py                    1fa0caf463aa82ec34c475b0310efbbbd314065711cabb4537f483a7a00a46f3
apps/dsh/worker.mjs                        517ab6ad330eff41bafd8a0eb7d004ad9145063a00122be43300b3f6a2460746
src/auto_research/workbench.py              a9776758cb9e73d278c5e1ad1319b76ae286b3ebfc721ea33a7b75cde0d15dea
src/auto_research/gui.py                    3c90c763c1282e2c819d724e883c8871d34bae43b15114156ae5b1d8a6655872
src/auto_research/assets/research-workbench.html adc62ca685e571a8ae96f60d3e385ebc6f6a97d9349b024a3c4af31bdfea744b
```
