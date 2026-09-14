# auto-research v5 — 图结构研究过程 brainstorm(2026-09-12,草稿 v0)

> **§18 的选题(msprobe noProb 线 → 32B 误报归因)已于 2026-09-12 被用户否决:任务过于线性,适合 v4.2 的轮循环;本机也不能完全重跑那条线。图框架要用在探索结构更复杂的研究上,选题待定(见 §18.7)。§18.0 的重心调整与 §18.5 的协议记录仍有效;`experiment2/` 保留为把真实历史写成节点的样例。§16 的实验已于 2026-09-12 跑完,结果见 §17 与 `experiment1/RESULTS.md`。§14 是协议基础。§1–§13 保留为推导过程;凡冲突之处以 §18.0、§17、§14 为准。**

> 这是 brainstorm,不是设计定稿。目的:把"用有向图编织研究过程"这个想法拆成可以分别拍板的
> 决定,并指出哪些地方 v4.2 / v5 原型的经验已经给了答案,哪些地方是真正的新问题。
> 事实源:`../auto-research-init-v4.2-dsh-plugin/` 下的 DESIGN_V42.md、ORCHESTRATOR.md.template、
> PLAN_V5_PROTOTYPE.md、auto-research-v5/{EVAL_ROUND1.md, docs/CONTRACT.md, docs/TASK_PROTOCOL_REVIEW_2026-09-07.md}、
> research-reports/2026-09-07-good-science-integrated/report.zh-CN.md(下称"报告")。

## 0. 先说诊断:v4.2 的轮间联系到底断在哪

v4.2 一轮与下一轮之间只有三根线:

| 线 | 载体 | 为什么脆弱 |
|---|---|---|
| 上一轮末尾的计划 | `memory/rounds/round-NNN.md` 的"下一步"段 + 下一轮的 `plan.md` | 只表达"接下来做什么",不表达"为什么这么做、依赖哪条结论、做完能回答什么" |
| 当前视图 | `STATE.md`(单作者、80 行上限、可压缩改写) | 压缩即丢失;每次改写都是一次无记录的裁剪,漂移正是在这里发生 |
| 批次末审计 | `memory/audit/after-round-NNN.md` | 只在批次边界出现;批次内的错误归因会污染同批后续所有轮(审阅 C2) |

结构信息——问题树、依赖关系、竞争解释、哪些结论建立在哪些前提上——全部住在 prose 里。
fresh-eyes 原则(Explorer 不读历史)进一步保证了每轮只看到压缩后的视图。
长程漂移和无法并行是同一个病根:**研究的结构没有一等公民的表示**。

v5 原型(PI / 任务契约 / claims)已经修了一半:

- claims 有 `derived_from`(证据边)与 `supersedes`(取代边);decision 有 `cites`;task/observe/run 互相以 id 引用。
- 但任务仍是线性的:`next_task = max+1`,recorder 强制串行(CONTRACT v2 §7),PI 每步只选一个任务。
- 图存在但无人维护:EVAL_ROUND1 B1 里 CL-009 推翻了 CL-004 却没登记 supersedes,两条矛盾主张同时活跃;
  审阅 C3(主张状态没有交接视图)、C10(缺口没有接手人)都是"图的一致性没有程序保证"的不同侧面。

所以 v5-graph 的问题不是"要不要图",而是:**节点是什么、边是什么、程序管哪些、agent 声明哪些、
并行时怎么不打架、循环放在哪一层**。下面逐个拆。

## 1. 本体:三种对象,三张图,一条 DAG 约束

### 1.1 节点不止一种

"节点 = 一次探索(round)"是对的,但只对了过程层。一次探索可能产出三条发现喂给三条不同的线;
如果节点只有探索一种,发现与探索被迫 1:1,结构又要退回 prose。建议三种对象:

| 对象 | 记号 | 是什么 | 对应 v5 原型里的什么 |
|---|---|---|---|
| 探索 | `X-NNN` | 一次有契约、有预算、有交付物的过程;可零到多个 run | task `T-NNN` + 其 decision + contract |
| 问题 | `Q-NNN` | 一个认识目标,带成功判据与状态(open / answered / dissolved / deferred) | RESEARCH.md 的"暂定问题 / 未决问题 / 下一缺口",但从散文变成对象 |
| 主张 | `CL-NNN` | 证据实际支持的陈述(保持 v5 原型的 propose / review / adopt 三步) | 原样保留 |

X 是"做了什么",Q 是"想知道什么",CL 是"现在相信什么"。三者分开之后,"漂移"有了精确定义:
**X 不再服务于任何可回溯到根的 Q**(§5)。

给每个 X 加一个 `contribution` 字段,取报告 §2.2 的九类(问题形成 / 描述 / 测量 / 理论 / 方法 /
因果 / 形式 / 复核 / 综合)。它决定该节点适用哪套证据要求(报告 §4.2 的选择表),
比 v5 原型的三选一(measurement / experiment / rederive)更完整,而且 Critic 按它选挑战规则。

### 1.2 边的词表(科研派生关系)

边的类型比节点类型更重要,因为"派生关系"就是要被表示的那个东西。一版候选词表:

**过程层(X → X)**

| 边 | 含义 | 谁产生 |
|---|---|---|
| `extends` | 在父节点的 artifact / 结论上继续(v4.2 的线性链) | 声明,且可由 git 祖先推导核对 |
| `branches_from` | 与兄弟节点从同一父节点分叉,探索竞争方案(Chamberlin 多重工作假设) | 声明 |
| `tests_premise_of` | 检查另一节点依赖的前提(测量节点挂在实验节点旁边) | 声明 |
| `replicates` | 独立重做(v5 的 rederive,但对象是节点而不只是一条观测) | 声明 |
| `ablates` | 对父节点做组件归因(v4.2 ablation 模块变成一种节点) | 声明 |
| `merges` | 汇聚:≥2 个父节点,产出综合或"未决冲突"对象(§4.4) | 声明,程序核对 ≥2 父 |
| `revisits` | 带新证据回到旧问题(循环的合法形态,§1.3) | 声明,程序核对"必须引用晚于旧节点的证据" |
| `consulted` | 执行中读取了另一节点的材料 | **只从工具日志推导,不接受声明** |

**问题层(X ↔ Q,Q ↔ Q)**

| 边 | 含义 |
|---|---|
| `addresses`(X→Q) | 本探索试图推进哪个问题;每个 X 至少一条 |
| `spawned_by`(Q→X) | 这个问题是哪次探索发现的 |
| `refines` / `splits`(Q→Q) | 问题被收窄或拆分;根问题 `Q-000` = 人写的意图 |
| `answers` / `dissolves`(X→Q) | 探索关闭问题:回答了,或证明问题设错 |

**知识层(CL)**

| 边 | 含义 |
|---|---|
| `derived_from`(CL→R/O) | 已有 |
| `supersedes`(CL→CL) | 已有;新增程序检查:同一对象上 finding 冲突的活跃主张必须有此边或被登记为 conflict |
| `depends_on`(CL→CL) | 主张以另一主张为前提;父被推翻时程序列出全部下游(审阅 C3 的"列出受影响结论") |

### 1.3 循环:放在哪一层就不是问题

你说"不一定非要非循环"——同意,但要分层说:

| 图 | 允许环? | 理由 | 程序怎么办 |
|---|---|---|---|
| 过程/溯源图,以 `(节点, 版本)` 为顶点 | **不允许** | 一条边只能指向创建它时已存在的东西,否则"决定先于行动、行动先于结论"的时间戳核对(v5 验收 2)失效 | 节点版本化;并行节点互看时,边指向对方的某个快照 `(X-009, v1, sha)`,而不是指向"X-009 本身" |
| 问题图 | **允许** | 回到旧问题是科研常态;`revisits` 边就是环 | 只查 G7:revisit 必须引用晚于旧节点的证据,否则是重复 |
| 证据支持图(CL 的 `derived_from` / `depends_on` 闭包) | **不允许** | 环 = 循环论证 | `graph check` 直接拒绝 |

这样"图可以有环"和"溯源可核对"不冲突:节点级图有环,`(节点, 版本)` 级图是 DAG。
git 就是这么做的——分支合并来回走,commit DAG 永远无环。

## 2. 程序性维护:强制 / 声明 / 推导三分

v4.2 与 v5 原型最硬的一条经验是"程序只对可机械检查的要求作硬判定"(报告 §4.3、审阅 C7)。
图也一样,否则图会变成一堆 agent 声明的、没人核对的断言(CL-004 事件的再现)。

| 类别 | 内容 | 机制 |
|---|---|---|
| **强制**(程序拒绝) | 节点生命周期(open 前必须有 addresses + parents + contract);边只能指向已存在的 (节点, 版本);(节点,版本) 图无环;写范围与工具面按节点;预算按节点占用 | 沿用 v5 recorder 的 strict open 与 M3 三种限制 |
| **声明**(agent 写) | 边的语义类型、问题文本、成功判据、主张 | 结构化输出 schema |
| **推导**(程序从日志算) | `consulted`(工具日志的 read 命中其它节点目录);`extends`(artifact 分支的 git 祖先);`measured`(recorder run 归属);`supports`(claims.derived_from) | `graph build` |

**Critic 的新工作 = diff 声明边与推导边**。这是 v5 原型"未被引用事件清单 / 被引用事件抽样"的推广:
声明了 `extends` 但 git 祖先不对;推导出 `consulted X-004` 但结论里没引用;声明了 `replicates` 却与父节点共享同一脚本(独立性为零)。
每一条差异都是程序生成的凭据,Critic 只复核,不重做。

## 3. 视图取代 STATE.md

STATE.md 是漂移引擎(§0)。图存在之后它没有存在的理由:所有"当前视图"由程序渲染,零作者。

| 视图 | 内容 | 谁读 |
|---|---|---|
| `map` | 全图,三级缩放:cluster(按 Q 聚合)/ line(每节点一行:id、类型、状态、一句话发现)/ full | 调度者;节点默认拿 cluster 级 |
| `frontier` | 可扩展的叶子:closed 且未被 extends 的 X、open 的 Q、待处置的 conflict | 调度者 |
| `lineage X-012` | 到根的路径,含每一跳的边类型与该节点的一句话发现 | 任务节点(它的"我在哪") |
| `conflicts` | 程序检出的矛盾活跃主张、未处置的 merge 结果 | Critic、调度者 |
| `orphans` | 发现没喂给任何东西的节点、没有 X 的 Q、没有 Q 的 X | Critic |
| `stale` | open 过久的节点、连续 k 个节点没人碰的 open Q | 调度者、人 |
| `independence CL-a CL-b` | 两条主张祖先集合的交集(节点、模型、data source_ids、脚本 sha) | Critic |

**节点的默认上下文** = 父节点交付物全文 + cluster 级 map + 自己的 lineage + 按需 `graph fetch X-NNN`。
这同时满足两个看起来冲突的要求:fresh-eyes 默认成立(不灌历史),
"每个节点可以查看其他节点"也成立(想看就 fetch),而每次 fetch 变成一条 `consulted` 边——看过什么有账。
v4.2 的 memory-auditor 变成 `graph view --similar <plan>`:程序按 Q 与 contract 文本找最近节点,只告知不否决。

RESEARCH.md 保留人写的"意图与硬约束"一节,它就是 `Q-000` 的正文;其余三节(暂定问题 / 未决问题 / 下一缺口)由 `frontier` 视图取代。

## 4. 并行:leaf 同步演进与汇聚

### 4.1 批次单位换掉

v4.2 的授权是"跑 N 轮";图上的授权是**"扩展前沿 k 个节点"**(k = 并行宽度),或"扩展到预算 B"。
一个批次 = 调度者从 frontier 提出 ≤k 个节点 → 并行执行 → 全部 close → `graph check` → Critic → 人。
串行是 k=1 的特例,v5 原型的全部流程照跑。

### 4.2 不打架的最小规则

| 冲突源 | 规则 |
|---|---|
| 两个节点都改 mutable artifact | artifact 走 git 分支,分支名 = 节点 id,从父节点分支切出;measurement / replicate 节点不改 artifact,不建分支;`merges` 节点做 git merge 或明确二选一 |
| 共享文件的写入 | 没有共享写文件:STATE 已删,视图皆渲染;每节点独占 `graph/nodes/X-NNN/` |
| recorder 全局 events.jsonl 与序号 | 按节点分片:`graph/nodes/X-NNN/events.jsonl`,run id 带节点前缀 `X-012/R-003`;`graph build` 合并成只读缓存;v5 计划里"不做并行、flock"的原因随之消失 |
| 预算 | 每节点 open 时占用 est_cost(v5 的净额规则照用),frontier 扩展前核对 `sum(holds) ≤ available` |
| 最优候选 | 按 lineage 计,不再全局唯一;跨 lineage 的比较只在 merge 节点里发生,且必须同 epoch、同标签版本 |

### 4.3 汇聚协议(merge 节点)

merge 是唯一必须有 ≥2 父的节点类型。它的契约固定三项:

1. 引用双亲各自的主张;
2. 判定 `agree | conflict | unresolved`;
3. `agree` → 产出综合主张(strength 不得高于双亲中较弱者,除非有新证据);
   `conflict` → 说明差异来源(对象、条件、定义、方法、变异、未知;报告 §4.5),
   产出收窄后的主张并 supersede 双亲;
   `unresolved` → 登记 conflict 对象:未决原因、下游使用限制、重评触发条件。到期要求重新处置,不要求到期必有答案。

### 4.4 并行的额外红利:独立性可计算

报告 §8.3 说独立性不能靠"换了个模型"宣称。图上它变成可算的:两条主张的祖先闭包
(节点 → 其 consulted / extends 父 → 模型 id → data source_ids → 脚本 sha)取交集。
交集为空 = 结构独立;交集含同一脚本 = 只是同一实现跑了两次。Critic 拿到的是程序算出的"共享依赖清单",不是印象。

## 5. 反漂移:Q 节点是锚

- 根 `Q-000` = 人写的意图与硬约束,engine 不改。
- 每个 X 至少 `addresses` 一个 Q;每个 Q 沿 `refines / splits / spawned_by` 可回溯到 `Q-000`。程序核对(G2)。
- 程序标记:没有 Q 的 X(在干什么?)、连续 k 个节点无人 addresses 的 open Q(审阅 C10 的"缺口没有接手人"的结构化解)、
  离根超过 d 层且没有 `refines` 回链的 Q(问题在悄悄换)。
- **re-anchor 节点**(contribution = 综合):每 m 个节点或人要求时派一次,读全图 cluster 视图,
  重立 Q 集合:关闭已答的、dissolve 设错的、把散落的 open Q 挂回根。v5 原型的"暂定问题版本"就是这个节点的产物,
  只不过现在版本差是图上的 diff,而不是一段文字的替换。
- `revisits` 的核对(G7):新节点必须引用至少一个晚于被 revisit 节点的证据。否则程序判"重复",转 `graph view --similar`。

## 6. 调度:谁选下一批 leaf

三种做法,可以叠加:

| 做法 | 优点 | 风险 |
|---|---|---|
| PI-agent 从 frontier 一次提 ≤k 个节点(v5 现状的扩展) | 已有实现;决定有 cites | 同一 PI 提的 k 个节点倾向同质;需要多样性检查 |
| 程序排序,不决定:每个提案带报告 §8.5 的五问(影响哪个决定 / 排除哪个未知 / 打开什么机会 / 多少资源 / 有没有更便宜的替代)作结构化字段,程序按可用预算与依赖就绪排序 | 透明;人可以看排序理由 | 别让模型编精确的"信息增益分数"——报告明确警告 |
| 人在批次边界改 frontier(钉住某个 Q、砍掉某条 lineage) | 便宜的纠偏 | 只在边界 |

多样性检查(程序):两个提案父集合相同且 addresses 相同 → 合并成一个;
k 个提案全部 extends 同一 lineage → 警告(并行没有换来覆盖)。

调度启发式可以借 MCTS / beam 的形,但别真做 value function。更贴切的心智模型可能是 **issue tracker**:
Q = issue,X = PR,`answers` = "closes #",lineage = PR 链,frontier = open issues 排序。
对人和 agent 都是熟悉的语义,而且天然有"谁在做哪个 issue"的并行视图。

## 7. 存储:四种做法

| 做法 | 评价 |
|---|---|
| (a) 单个 `graph.json` | 最简单;并行写就冲突;不推荐 |
| (b) 追加事件日志 + 物化图 | 与 recorder 哲学一致;全局日志仍要锁 |
| (c) **目录即节点**:`graph/nodes/X-NNN/node.json` 只写出边(指向父的 (id, 版本, 快照 sha));入边全部由程序算;节点只写自己的目录 | git commit 模型;天然并行安全;`graph build` 从节点目录 + 工具日志 + git + claims 物化只读缓存;**推荐** |
| (d) 直接把 git DAG 当研究图 | 过度聪明:git DAG 是 artifact 状态的图,measurement / replicate 节点不改 artifact,没地方挂 |

(c) 配 (b) 的分片版本:每节点自己的 events.jsonl。全局唯一的只有 `graph/index.json`(id 分配),
用 `mkdir X-NNN` 的原子性分号即可,不需要 flock。

## 8. 一个 strawman

### 8.1 node.json

```json
{
  "id": "X-012", "version": 2,
  "kind": "exploration",
  "contribution": "measurement",
  "status": "closed:done",
  "addresses": ["Q-003"],
  "parents": [
    {"node": "X-007", "version": 1, "snapshot": "3f9a…", "edge": "extends"},
    {"node": "X-009", "version": 1, "snapshot": "b21c…", "edge": "tests_premise_of"}
  ],
  "contract": {"scope": "…", "claim": "…", "measure": "…", "compare": "…", "analysis": "…", "stop": "…",
               "baseline_ref": "X-007/R-002", "max_runs": 12, "epoch": "E-003"},
  "artifact_branch": null,
  "opened_t": "…", "closed_t": "…", "est_cost": 1.5, "cost": 1.2,
  "produced": {"runs": ["X-012/R-001"], "observations": ["X-012/O-001"], "claims": ["CL-014"]},
  "spawned": ["Q-005"],
  "closes": [{"q": "Q-003", "as": "answered"}],
  "deliverable": "graph/nodes/X-012/report.md"
}
```

契约字段沿用 v5 原型的六项,补上审阅 C9 要的 `baseline_ref / max_runs / epoch`(允许变化的因素、选择规则也可进来)。

### 8.2 不变量 G1–G8(全部程序核对,`graph check` 输出清单)

| # | 不变量 |
|---|---|
| G1 | 父引用只能指向创建时已存在的 (节点, 版本, 快照);(节点, 版本) 图无环 |
| G2 | 每个 X 至少一个 addresses;每个 Q 可回溯到 Q-000 |
| G3 | 声明边与推导边由程序 diff,差异进 Critic 凭据(不自动判错) |
| G4 | 主张支持闭包无环;同一对象上 finding 冲突的活跃主张必须有 supersedes 或登记为 conflict |
| G5 | 并行节点各自独占写目录与 artifact 分支;不存在被两个 open 节点同时可写的路径 |
| G6 | merge 节点 ≥2 父,且给出 agree / conflict / unresolved 三态之一 |
| G7 | revisits 边必须引用至少一个晚于被 revisit 节点的证据 |
| G8 | 预算按节点占用;frontier 扩展前 `sum(holds) ≤ available` |

### 8.3 CLI 动词(recorder 的兄弟,同样 stdlib-only)

```
graph open   --kind X --contribution measurement --addresses Q-003 \
             --parent X-007@1:extends --parent X-009@1:tests_premise_of --contract F --est-cost 1.5 [--strict]
graph close  X-012 --status done|failed|aborted --deliverable P
graph question new  --text S --criteria S --spawned-by X-012 [--refines Q-001]
graph question close Q-003 --as answered|dissolved|deferred --by X-012
graph view   --map [--zoom cluster|line|full] | --frontier | --lineage X-012 | --conflicts | --orphans | --stale | --similar F
graph fetch  X-004            # 返回节点交付物;同时写一条 consulted 边(调用者 = 当前节点)
graph independence CL-014 CL-009
graph check                   # G1–G8
graph build                   # 物化只读缓存 graph/_build/graph.json + 推导边
```

### 8.4 与 v5 原型的映射(哪些能搬,哪些要重做)

| v5 原型 | v5-graph | 动作 |
|---|---|---|
| task T-NNN + decision D-NNN + contract C-NNN@V | X 节点(decision 的 chose / rejected / cites 成为 open 时的 rationale;契约版本化保留) | 合并成一个对象,recorder 逻辑可搬 |
| RESEARCH.md 三节 | Q 节点 + frontier 视图 | 重做 |
| STATE.md | 无 | 删除 |
| claims.jsonl + propose / review / adopt | 原样 + G4 检查 + depends_on | 搬 + 补 |
| memory/tasks/T-NNN.md | graph/nodes/X-NNN/report.md | 搬 |
| 全局 events.jsonl、串行 open 检查 | 按节点分片、并行 open | 重做 |
| negative_index + memory-auditor | closed:failed 节点 + 其主张 = 负结果索引;`graph view --similar` | 删除模块,视图替代 |
| ablation 模块 | `ablates` 边的节点 | 变成节点类型 |
| Critic 程序核对(replay、预注册时间戳、val-probe) | 保留 + `graph check` 凭据 | 搬 + 补 |
| M3 三种强制(工具名 / 嵌套 / 路径) | 保留;路径按节点目录与分支 | 搬 |
| PI 一次一个任务 | 调度者一次 ≤k 个节点 + 多样性检查 | 扩展 |

## 9. 风险与张力(先摆出来)

1. **图膨胀 vs 上下文**:100 个节点后 line 级 map 也放不进上下文。三级缩放 + 默认只给 cluster 级是必需品,不是优化。
   要不要一个"图书馆员"子代理回答"关于图的问题"?可以晚点决定,`graph view --similar` 先顶着。
2. **过度工程 vs "两页 markdown"哲学**:v4-lite 的卖点是任何 CLI 都能跑。图 CLI 是第二个程序件(recorder 之后)。
   最小版本应该是:node.json + 六个动词 + G1/G2/G4 三条不变量;其余全部先留 prose。别一上来做全部八条。
3. **声明会撒谎**:G3 是整套设计里最重要的一条。没有推导边,图就是 CL-004 的放大版。
4. **并行乘预算**:k 个并行节点 = k 倍支出。批次授权必须同时给 k 与 B。
5. **人的注意力**:并行后一次批次末要看 k 份交付物。`conflicts` 与 `frontier` 视图得足够好,否则人就只看 digest 一行。
6. **"节点 = round"的诱惑**:v4.2 用户已经习惯 round 编号。X 节点可以继续叫 round,但 R-NNN(run)必须与 X 解耦(v5 原型已经做了)。
7. **DSH 侧的并行**:子代理并行 spawn 在 DSH 里可行(`ctx.subagents.start` 多次),但工具的调用者门控要按节点 id 而不是按"当前唯一任务"。

## 10. 需要你拍板的(按对后续影响排序)

1. **节点粒度**:X = v5 的 task(一个契约、零到多 run)?还是 v4.2 的 round(一个候选一个 run)?我的建议是前者。
2. **底座**:保留 recorder / claims 的 event-sourcing 模型作为底座,图是其上的一层;还是真的从零?
   我的建议是保留——它已经通过 M0–M4 验收,而且 §8.4 的映射表显示大半能搬。"推翻重做"的是调度与状态表示,不是记录。
3. **循环三分法**(§1.3):过程图按版本无环、问题图有环、证据图无环。接受吗?
4. **artifact 是否按节点分支**(§4.2):这是并行实验节点的前提;代价是 merge 节点要会 git merge。
5. **谁调度**(§6):PI-agent 提 k 个 + 程序排序 + 人在边界,三者的默认组合。
6. **并行的目标形态**:同一 DSH 进程内多个子代理?还是多个 harness / 多台机器(那 graph 目录要经 git 同步)?
7. **M0 的范围**(§11)。

## 11. 建议的 M0:不接模型,先证明"图能抓到线性抓不到的东西"

沿用 v5 原型"评估案例先行"的做法:

1. 写 graph CLI 的最小版本(node.json、open / close / question / view / check、G1 / G2 / G4)。
2. 把 EVAL_ROUND1 的四个 run(A1 / A2 / B1 / B2)的 events + claims + toollog **回放**成图:
   task → X,decision.cites → parents,RESEARCH.md 版本 → Q,claims 原样。
3. 验收:`graph check` 必须程序检出 B1 的 CL-004 / CL-009 矛盾(G4);
   `graph view --orphans` 必须列出 A1 里 Critic 手工指出的"应引未引的 O-002";
   `graph view --stale` 必须列出 A2 停机时留下的三条未决疑点(它们在 stop note 里,没有接手人)。
4. 负例:一份把 supersedes 补上的 fixture,`graph check` 必须干净。

这一步不通过——图上程序核对不出线性版本靠 Critic 才发现的问题——后面的并行与调度都不值得做。
通过了,再做 M1(并行 open + 分片 recorder)、M2(调度者提 k 个 + 视图)、M3(merge / revisit / re-anchor 节点)、M4(真实模型跑 A/B)。

---

## 12. 与 gpt-astra 方案的对照与合并(2026-09-12,草稿 v0.1)

Astra 只看了 v4.2 的 ORCHESTRATOR 模板,没有看 v5 原型(PI / 契约 / claims / EVAL_ROUND1)。
这解释了它的操作清单为什么与 v5 recorder 几乎同构却没有引用它(§12.4)。

### 12.1 收敛点(两边独立得出,可以视为定案候选)

| 设计点 | Astra 的说法 | 本文的说法 |
|---|---|---|
| 三种对象 | 探索节点 + `question_id` + `claim_id`,同一存储 | X / Q / CL,同一存储 |
| 环 | 整体图可有环,按关系类型决定;"必须等待完成"的依赖不许环;E1 原始记录保留,E5 是新节点 | 过程图按 (节点, 版本) 无环,问题图有环,证据图无环;revisit = 新节点 |
| 视图 | 摘要是可重建的视图,不再依赖压缩改写的 STATE.md | STATE.md 删除,`graph view` 渲染 |
| 分工 | 程序管引用存在、版本明确、状态转换、预算;科学判断归 agent 且可质疑 | 强制 / 声明 / 推导三分 |
| 汇聚 | 汇聚是正式探索;多父节点产生单分支提不出的问题 | merge 节点 ≥2 父,三态 |
| 并行基础设施 | 独立 worktree、领取与预占原子、统一写入口留事件 | 节点独占目录与分支、分片 recorder、预算按节点占用 |
| 上下文 | 派发时程序构造局部上下文包;随时可查全图 | 父节点全文 + cluster 级 map + 按需 fetch |
| 调度 v1 | 不做数值评分,记录选择理由 | 报告 §8.5 五问作结构化字段,程序只排序 |

### 12.2 从 Astra 采纳的(每条写明改本文哪里)

1. **节点边界由研究问题决定,不由会话或 run 决定**(改 §1.1、拍板项 1)。
   解除"一个节点 = 一次实验 = 一个 agent 会话"的绑定;"尝试新损失函数"不是节点,"检验长尾错误是否主要来自梯度失衡"才是。
   同一 Q 可以有多个 X;X 结束后重访就开新 X,节点不会膨胀成做不完的方向。

2. **根可达不够,漂移要在提案时抓**(改 §5,新增 G9)。
   Astra 的反例:推理慢 → 缓存 → 压缩 → 并发,每条边局部合理,整条链已离开"跨域泛化"。
   本文的 G2(每个 Q 回溯到 Q-000)抓不住它,因为每一步都能合法地 refine 出子问题。
   采纳:**每个提案必须写明 `(question_id, 目标 claim 或 new, 预期改变)`**,即"它将改变与哪个研究问题有关的哪项判断"。
   程序核对三元组存在(G9);"优化并发"能不能诚实地填出对"跨域泛化"哪条判断的改变,由调度者与 Critic 读。
   frontier 视图附带该 Q 到根的 refine 深度,人一眼看到"这个提案服务的是第 4 层子问题"。
   G2 保留为必要条件,G9 是充分条件的机械部分,re-anchor 节点(§5)是周期性兜底。

3. **调度对象是提案,leaf 只是提案的一种来源**(改 §6)。
   叶子可能已答完或不值得续;内部节点可能因新证据值得复现;两个远节点之间可能需要新建对照;一个负结果可能同时改多个分支的下一步。
   采纳:frontier = 提案集合,来源三种——节点 agent 在 close 时提的后续(node close 交付物新增 `proposals[]`)、
   调度者按议程提的、**程序按结构义务生成的**(stale Q → "需要有人 address";conflict → "需要 merge / resolve";
   被取代的主张有 depends_on 下游 → "需要 recheck")。第三种是本文 orphans / stale / conflicts 视图的自然延伸。

4. **暂定发现与已发布结果分开**(改 §4.2)。
   fetch 一个 running 节点返回的是带 `tentative: true` 的快照;consulted 边记录该标记;
   主张的 derived_from 不能引用 tentative 快照,只能引用对方 close 后发布的 run / observation。
   新信息改变计划 → 记 plan 修订事件(v4.2 的 plan.md 追加行 + commit 已有此形)。

5. **复现类节点限制读取对方的解释**(改 §3)。
   contribution = replication 的节点,上下文包只含父节点的协议、输入、脚本,不含 report.md 的解释段;
   程序构造上下文包时执行,推导出的 consulted 边事后检出违规(与 G3、`independence` 视图闭环:防与查各一半)。

6. **worker 失联的恢复顺序**(改 §4.2):先核对外部实验是否仍在跑(recorder run 的状态与进程),再决定恢复或重试;
   不能只按 stale hold 释放预算了事。

7. **评估设计加对照**(改 §11):三个行为场景——防漂移、跨分支学习、结论修订——各与同预算的"顺序摘要"、
   "普通历史检索"(有 fetch 无结构)、"随机连边"对照。随机连边对照回答的问题是:收益来自更多历史信息,还是来自关系结构。
   **v5 原型本身就是"顺序摘要"对照组**:同 recorder、同 judge、同 mock,只差图这一层。这是保留底座的又一个理由(§12.4)。

8. 边的词表补两项:`uses`(声明的复用:代码、数据、方法;与推导的 `consulted` 对比)、
   `requires`(执行依赖:输入就绪才能开始;纯调度关系,必须无环,不进科学图的溯源)。
   `tests` 与 `supersedes / contradicts` 的目标改为具体 claim id,不是节点:一个节点三条结论,新证据可能只动其中一条。

### 12.3 本文有而 Astra 没有的(保留)

1. **推导边与声明边的 diff(G3)**。Astra 的程序检查全部作用于声明的数据(引用存在、版本明确、状态合法)。
   EVAL_ROUND1 的教训是声明不完整(CL-009 没写 supersedes);工具日志、git 祖先、claims 三个来源推导出的边才能抓漏。
   这是本文最重要的一条,Astra 方案里没有对应物。
2. **独立性可计算**(祖先集合交集)。Astra 有"限制读取对方解释"的防,本文有"共享依赖清单"的查;合起来才完整。
3. **(节点, 版本, 快照) 三元组**作为"节点级有环、溯源级无环"的具体实现,也顺便处理了 running 节点被 fetch 的情形。
4. **contribution 九类**决定证据要求。Astra 列了"错误分析、文献调查、复现、理论推导"但没接到证据标准上。
5. **Q 的生命周期**(open / answered / dissolved / deferred)与 stale-Q 标记,对应审阅 C10。
6. **不接模型的 M0**(回放 EVAL_ROUND1),在花模型钱之前先证明程序核对能抓到线性版本靠 Critic 才发现的问题。
7. 与 v5 原型的映射表(§8.4、§12.4)。

### 12.4 一处真正的分歧:存储

Astra:单机原型用事务数据库,节点、边、事件几张表。本文 §7:目录即节点,入边程序算,不要全局锁。

Astra 的原子性顾虑是对的:本文的 `mkdir` 原子性只覆盖 id 分配,"领取 + 预占预算"两步之间在并行提案下有竞争窗口。
本文的顾虑也是对的:v4.2 的可移植性(任何 CLI 读 markdown)、人可直接审阅、预注册时间戳靠 git commit(不变量 I9)都要求 payload 在 git 里。

合并:**SQLite(Python stdlib)只做索引与账本**——nodes / edges / events / holds 四张小表,open 是一个事务;
**payload 留在 git 目录**——report.md、plan.md、artifact 分支、observations 存档。
`graph build` 仍从目录 + 工具日志 + git 推导边,写回索引;索引丢了可以从目录重建(索引是缓存,目录是事实源)。

顺带一提 Astra 的操作清单与 v5 recorder 的对应:

| Astra | v5 recorder(CONTRACT v2) | 差别 |
|---|---|---|
| propose_exploration | (无;PI decision 内嵌) | 新增:提案成为对象 |
| claim_exploration | `task open --strict` | 加原子预占 |
| publish_observation | `observe` | 同 |
| publish_claim | `claim propose / review / adopt` | 同 |
| link_evidence | `decision.cites`、`claim.derived_from` | 加边类型 |
| finish_exploration | `task close` | 交付物加 Δ(Q, CL) 与 proposals |
| query_frontier | `preflight.next_task` | 从"下一个序号"变成"提案集合" |

七个操作里五个已经有实现和测试。要重做的是 propose 与 frontier,以及并行化 open。

### 12.5 回答 Astra 的问题:节点完成时最核心的交付物是什么

同意"对某个研究问题的一次有证据的更新",并把它写成结构:

```
node close 交付物 = Δ(Q, CL) + 证据 + 提案
  claims:            提出 / 收窄 / 取代了哪些主张(各带 derived_from)
  question_updates:  每个 addressed 的 Q → answered | dissolved | open(必须写"仍无法区分的解释")
  proposals:         后续探索,各带 (Q, 目标 claim 或 new, 预期改变, 为什么现在, 成本)
  evidence:          runs / observations(已有)
```

**空更新是合法交付**:`claims: []`,Q 仍 open,但"仍无法区分的解释"非空。这就是报告 §4.3 与审阅 C7 说的
"成功执行且结果无法判定,仍可是一项合格研究任务"。

这个选择决定的事:

- 节点边界 = 一个 Q,直到更新有证据或 stop 规则触发;
- 调度目标 = 单位成本下对 Q 状态的预期改变(v1 定性,写理由不打分);
- merge = 引用 ≥2 个分支主张的一次更新;
- 每条边都在说"用了哪项判断"(uses / tests)或"改了哪项判断"(supersedes / contradicts);
- v5 原型的任务交付物已经是"候选主张节 + notes",差的只是把 Q 状态的变化从 RESEARCH.md 的散文变成显式字段。

### 12.6 合并后的里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M0 | graph CLI 最小版(SQLite 索引 + 目录 payload;open / close / question / propose / view / check;G1 / G2 / G4 / G9);回放 EVAL_ROUND1 四个 run 成图 | 程序检出 B1 的 CL-004 / CL-009 矛盾、A1 的应引未引 O-002、A2 的三条无人接手疑点;补齐 supersedes 的 fixture 干净 |
| M1 | 并行 open(原子预占)、分片 recorder、artifact 分支、tentative 快照 | 两个节点并行跑 mock,无共享写,预算不超支,fetch running 节点得到 tentative 标记 |
| M2 | 提案对象与 frontier 三来源、调度者一次 ≤k、多样性检查、上下文包按 contribution 构造 | 复现节点的工具日志里没有父节点 report.md;程序生成的结构义务提案出现在 frontier |
| M3 | merge / revisit / re-anchor 节点,depends_on 下游标记,推导边 diff 进 Critic | 推翻一条早期主张后,`graph view --conflicts` 列出全部受影响下游,且原始历史不变 |
| M4 | Astra 的三场景 × 三对照(顺序摘要 = v5 原型、无结构检索、随机连边),同预算,各 ≥2 次 | 三场景各有通过判据;报告收益来自结构还是来自信息量 |

相关工作:Graph of Thoughts(聚合与反馈操作)、AI Scientist-v2(实验管理与树搜索)只作参照;
长期问题承接、跨分支证据复用、结论修订三件事没有现成设计,M4 就是它们的第一次验证。

---

## 13. 范围约束:以工业界研究为主(2026-09-12,草稿 v0.2)

### 13.1 约束本身,以及它排除了什么

工业界研究的目标是**能力或决策**:在约束 C 下把指标 M 提到 T;决定一个方向值不值得继续投;弄清生产系统为什么坏。
由此:

- 证明与理论只作支撑。不存在以证明为目标的节点;推导是一种证据对象(§13.3),机制是一种主张类型(§13.4)。
- 新颖性不是目标。有效、可迁移、可部署才是;不做 N11 式的新颖性核查。
- 外部复现不是目标。复核只为防实现错误、种子偶然与条件依赖。
- 终点是一个决策:ship / 不 ship / 继续投 / 转向。图的终点交付物是决策备忘录(§13.6),不是论文。

v5 原型把主张限定为 performance 与 measurement 两类,回头看正是工业界研究的核心两类;
本节是在此基础上补一类(mechanism)、补一种证据(derivation)、把 contribution 收窄。

### 13.2 节点的 contribution:九类收成六类

| contribution | 回答什么 | 典型交付 | 证据要求 | 对应已有的 |
|---|---|---|---|---|
| `measure` | 分数量到目标本义了吗;标签对吗;audit split 上还成立吗 | measurement 主张;探针 run;relabel;val-probe | 探针能区分竞争解释;参考答案本身核实过 | v5 measurement、rf18 重标协议、audit-split 模块 |
| `improve` | 在契约范围内改 artifact,是否公平地更好 | performance 主张;run;artifact 分支 | 预注册;表面因素受控;效应量对噪声;成本与延迟入账 | v5 experiment、v4.2 round |
| `diagnose` | 哪里坏、坏在什么分布上、为什么 | mechanism 主张(可为 exploratory);错误分析;分层统计;新 Q | 分层可复算;解释有区分性预测 | 无显式对应(v4.2 里散落在 round 叙述中);工业界最常见却最没结构的一类 |
| `replicate` | 独立重做后还成立吗;换种子 / 换 cell 还成立吗 | 对既有主张的支持或质疑;观测 | 上下文包不含父节点解释;声明全部输入 | v5 rederive、replay |
| `scout` | 谁试过;已知失败模式;最近的已知方法 | 提案;负知识条目;候选清单 | 来源可追溯;不产生主张 | negative_index、memory-auditor 的查重 |
| `synthesize` | 多分支放在一起说明了什么;问题集要不要重立;现在能不能做决定 | merge 三态;re-anchor 的 Q 集合 diff;决策备忘录 | 引用双亲主张;同 oracle 版本才比 | v4.2 digest + Critic 报告的叙事段 |

删掉的四类去哪了:形式构造与证明 → `derivation` 证据对象;理论与解释 → `mechanism` 主张;
问题形成 → 首个 `diagnose` / `scout` 节点,或 re-anchor;综合与连接 → `synthesize`。

### 13.3 证据对象加一种:derivation

推导仍然可能是**最便宜的证据**:评分公式里有没有长度项,看公式比跑 60 个探针快;阈值的 margin 几何;复杂度或延迟上界。
所以它要能被 derived_from 引用,但要带上自己的检查状态:

```json
{"kind": "derivation", "id": "X-012/D-001",
 "statement": "score.py 的 length_bonus 项在 n_tokens > 120 时单调增,与内容无关",
 "premises": [{"text": "length_bonus = 0.015 * max(0, n - 120)", "verified_against": "judge/score.py@sha", "by": "program"}],
 "supports": ["CL-014"],
 "checked_by": "second-model | human | program | none",
 "check_note": "…"}
```

规则:

- premises 逐条标注"对照 artifact 核实过吗"。q20 的教训就是错误前提的推导:agent 把 0°F 当规范答案,推出"死题"。
- premises 未核实的 derivation 只算 exploratory 证据;不能单独把任何主张提到 supported。
- derivation 不能单独把 performance 主张提到 confirmed:上界成立不等于实现符合前提。
- Critic 对 derivation 的挑战规则固定一条:前提与当前 artifact / judge 版本是否一致(epoch 变了前提可能失效,标 needs_recheck)。

### 13.4 主张类型从两种到三种,以及迁移规则

| 类型 | 说什么 | 证据要求 |
|---|---|---|
| `performance` | 候选在条件 S 下优于基线 | 已有 |
| `measurement` | 分数在用途 S 内量到 / 没量到目标本义 | 已有 |
| `mechanism`(新) | 现象或改善的原因是 M | 区分性预测 + 干预(ablation 节点)+ 跨条件(cell)一致;三者缺一只能 exploratory |

**迁移规则(G11)**:采纳一条 performance 主张不需要 mechanism;但**声称改善迁移到未测条件**
("换数据也行"、"上线后也行")必须有直接证据,或有一条自身达到 supported 的 mechanism 主张作 depends_on。
这是工业界最常见的越界处,报告 §4.5 说的"外推需要桥梁"在这里变成一条可核对的边。

### 13.5 根问题是决策,终态对应决策

`Q-000` 模板:

```
目标指标 M(objective)、约束 / gates、阈值 T、预算 B、oracle 版本策略
终态三种:
  answered:达到 T 且 measurement 主张无 open conflict
  answered-negative:在 C 下不可达,且 diagnose 给出了原因(mechanism ≥ supported)
  deferred:平台期 + 预算判断"不值得继续",记录重启条件
```

v4.2 contract 块的 objective / gates / budget_cap 已经是 Q-000 的机器可读部分;缺的只是阈值 T 与终态定义。
Q 的终态直接映射人的决策(ship / 不 ship / 继续 / 转向),但决策本身仍是人的 adopt 行为,不是主张强度。

### 13.6 决策备忘录是可渲染视图

`graph view --memo`,零作者,随时渲染:

- 最优候选及其 lineage(每一跳的边类型与一句话发现);
- 关于指标 M 的全部 measurement 主张及当前状态(active / narrowed / superseded);
- audit-split 的 gap 与 oracle 版本;
- 未决 conflicts 与 stale Q;
- 迁移条件:哪些条件有直接证据,哪些靠 mechanism 主张,哪些没有;
- 成本:按角色与节点分列。

v4.2 的 claim 时刻审计清单(seal tag 后 judge/ 无改动、replay --best 一致、val_probe 同版本)是这份备忘录的前置核对,原样保留。

### 13.7 scout 节点的位置

便宜、不产生主张、交付物是提案与负知识。来源顺序:本 campaign 的 closed:failed 节点 → 旧 campaign 的 negative index → 其它内部 repo → 外部文献。
典型触发:frontier 扩展前(避免重复)、方法族停滞时(报告 §8.2 "方法族停滞但问题仍有价值")。
不做新颖性判断;"已经有人做过"只影响提案优先级,不否决。

### 13.8 merge 就是 bake-off

- 只在同 epoch、同标签版本、同 split 下比(G10);不满足先 rerun 再比。
- conflict 的典型形态是"A 赢 cell 1、B 赢 cell 2":产出组合候选(新 improve 节点),或按条件 scope 两条主张。
- 组合候选的 ablation 是下一个自然节点(v4.2 的 ablation 模块在这里复活)。

### 13.9 对前文的修改清单

| 位置 | 改动 |
|---|---|
| §1.1 | contribution 九类 → §13.2 六类 |
| §1.2 | `ablates` 保留;`tests` 目标可以是 derivation |
| §8.1 | node.json 的 contribution 枚举改六类;`produced` 加 `derivations` |
| §8.2 | 新增 G10(merge 同 oracle 版本)、G11(迁移主张需直接证据或 supported 的 mechanism) |
| §8.4 | v5 rederive → replicate;measurement / experiment 改名 measure / improve 或保留旧名(拍板) |
| §12.5 | 交付物 Δ(Q, CL) 的 claims 可含 mechanism 类型;evidence 可含 derivation |
| §12.6 M4 | 三场景改工业口径:防漂移 = "有吸引力的支线 vs KPI";跨分支学习 = "两个方法分支的 bake-off";结论修订 = "早期 measurement 结论被推翻"(不变) |

### 13.10 新增拍板项

8. 主张三类(performance / measurement / mechanism)与证据三种(run / observation / derivation)是否就此冻结为 v5 的全部词表。
9. Q-000 的终态三种是否够;"转向"算 dissolved 还是新 Q-000 版本。
10. 节点类型名沿用 v5 原型(measurement / experiment / rederive)还是改成 §13.2 的六个动词。

---

## 14. 剃刀之后:v0.3(2026-09-12,回应 Astra 的批判性整合)

待验证的假设只有一个:**显式记录探索之间的关系,能否让后续探索做出更好的决定。**
§1–§13 同时在设计研究图、知识管理、科学审计和并行运行时,超出了验证这一个假设所需的范围。本节收回多余部分。

### 14.1 接受的诊断:把"能记录某种判断"推进成了"程序能判断它成立"

| 原规则 | 处置 | 理由 |
|---|---|---|
| G3:推导边与声明边 diff 查漏(§2) | 降为视图线索 | 读过不等于使用,代码继承不等于观点依赖;§2 原文写了"不自动判错",但"查漏"措辞越界 |
| 祖先交集判独立(§4.4) | 降为"共享依赖清单"的展示 | 交集为空不等于独立(可共享错误前提);同脚本换数据仍有新信息 |
| G7:revisit 需更新证据(§5) | 删除;`why_now` 非空即可 | 更强的模型、修正的分析方法都是重做的理由 |
| 同父同问的提案自动合并(§6) | 删除;只提示相似,重复提交才去重 | 那恰好可能是两个竞争假设 |
| merge 三态 + 最小强度(§4.3) | 删除;汇聚是普通探索,只是输入多于一个 | 不同条件下的结果可以并存;没有通用的最小强度规则 |
| G11:迁移主张需 mechanism(§13.4) | 从不变量降为写给 agent 的 prose | 有 mechanism 也不自动外推;新条件是否满足前提是判断,不是核对 |
| answered-negative 需 supported mechanism(§13.5) | 删除;终态 = answered / stopped(原因自由填) | 停止投入是成本与证据的决策,不需先证明不可达 |
| "节点 = 探索则发现与探索 1:1"(§1.1) | 撤回 | 一个探索可以输出多条有稳定编号的发现 |
| SQLite 既是账本又是可丢缓存(§12.4) | 撤回;SQLite 是元数据的权威源 | 领取与预占只在库里,从目录重建不出来 |
| artifact 走 git 分支(§4.2) | 改为 worktree | 同一 checkout 上的分支不隔离 |
| M0 验收"程序检出 CL-004 / CL-009 矛盾"(§11) | 撤回,**事实错误** | 两条都是 finding=flaw,结构上不冲突;识别推翻关系需要理解证据 |

### 14.2 不变量:11 条剩 5 条,全部是结构核对

| 保留 | 内容 |
|---|---|
| S1 | `inputs[].ref` 只能指向已 close 节点的固定结果;不能引用 running 节点 |
| S2 | `question` 必须是议程里存在的编号 |
| S3 | 每个执行独占 worktree;元数据经单一入口更新;领取 + 预占是一个事务 |
| S4 | 预算:open 时预占,close 时结算;可用额不足不派发 |
| S5 | 时间序:open_t < 首次 run t < close_t(沿用 v5 的"决定先于行动") |

删除:G3、G4 后半、G6、G7、G9 作为检查、G10 降为视图里的一行警告、G11。

### 14.3 冻结的协议:Astra 的 YAML 加两个字段

```yaml
id: X-012
question: Q-02                    # 议程编号(S2)
why_now: …                        # 自由文本;取代 G7 与 G9
modifies_artifact: false          # 唯一的类型标记(权限,不是分类;见下)
inputs:
  - ref: X-007/result#finding-2
    use: 作为本次要检验的假设       # 唯一的边类型:用了什么、怎么用
  - ref: X-009/result#build-samples
    use: 继续实现其 trim();接口不变  # ref 也可指向产物;重写须写明理由
plan: …                           # 怎么做、投入边界;replicate / ablate / scout 等写在这里或 tag
status: proposed | open | closed  # closed = 工作停止且产物已记录,不表示问题已解决
result:
  findings:                       # 可为空;每条必须有 evidence,不得为关闭节点而生成
    - id: finding-1
      text: …
      conditions: …
      evidence: [X-012/R-001]
      revises: X-007/result#finding-2   # 可选;唯一的修订机制
  products:                       # 可选;中间产物:代码、样本、工具、推导片段
    - id: build-samples
      path: scratch/x12/build_samples.py
      version: <sha 或 commit>
      interface: …
      status: partial | usable
      gaps: [trim() 未实现, token 计数与评价器是否一致未验证]
  limitations: …                  # 未验证的条件、已知缺口、障碍;零 run 或空 findings 时首行写关闭原因
  next: […]                       # 建议后续,可指向具体 product 与 gap
```

两个新增字段各自的理由:

- **`findings[].revises`(可选)**。Astra 的验收第三步要求接手的 agent"判断哪些旧结果仍可用、哪些需要重评";
  没有这个字段,每次接手都要把前人已做过的修订重新推一遍。它是一个可选反向指针,不是状态机;
  程序只做一件事:视图里把被 revises 的 finding 标灰并链到新发现。CL-004 事件缺的正是这一个字段,
  而它能否被填上取决于 agent,不取决于程序——这也正是 §14.5 要观察的行为之一。
- **`modifies_artifact`(布尔)**。六类收成一个布尔。它是目前唯一导致程序行为不同的区分:
  true 则 worktree 含 mutable artifact,false 则禁写(沿用 v5 的 SCOPE_MENU 与路径钩子,M3 已验证 enforced)。
  按 Astra 自己的判据"只有确实需要不同程序行为的类别才加类型",这一个应保留,其余类别不加。

**v0.5 补充:产物与未完成工作(为 §16 主实验所需,不加节点类型,不加状态机)。**
`result.products` 是一个可选列表,给中间产物稳定编号、路径、版本、接口、状态与缺口,使后续节点能以 `inputs[].ref` 指向它。
它不并入 findings:findings 是有证据的判断,products 是尚未构成判断的东西;混在一起就抹掉了"节点关闭 ≠ 问题解决"这条区分。
`limitations` 的内容明确为未验证的条件、已知缺口与障碍;零 run、空 findings 的关闭合法,关闭原因写在 limitations 首行。
`evidence` 移到每条 finding 之下:一条没有 evidence 的 finding 是违规,不是省略。

协议之外的三个文件:

- `agenda.md`:目标、约束、Q-01…。人与 PI 可编辑的 prose;程序只查编号存在。这是 Q 节点的 v0.3 形态。
- `notes.md`:短、带引用的研究笔记。接受 Astra 的修正:科学总结需要解释与取舍,不能全靠渲染。
  它与 STATE.md 的区别是两点:每句话带 ref,程序查 ref 存在;orphans 视图列出 notes 未提及的 findings。
- `runs/toollog.jsonl`:已存在,零成本,保留。不作核对依据,作 §14.5 的评估仪表(判断 agent 是否真的读了某条负结果)。

Critic 不进图协议。批次末照 v4.2 跑,读视图而不是 STATE;它的 narrow 就是一个 `modifies_artifact: false` 的探索,
findings 带 revises。人的采纳仍是 adopt 行为,在图之外。

### 14.4 存储

SQLite(stdlib)是元数据的权威源:nodes、inputs、findings、holds 四张表加事件表。
文件保存报告(`graph/nodes/X-NNN/report.md`)、plan、worktree 产物、observations 存档。视图由二者生成。
v0.3 不做:节点日志分片、多来源推导边、库与目录互相重建。

### 14.5 第一个实验(v0.5 改向)

主实验改为 **§16:未完成工作的交接与中间产物的组合验证**。原因:此前的设计(现 §15)检验"新 agent 能否利用已形成的结论及其修订",
默认每个探索都能独立产出明确结论;复杂研究常需多阶段积累,单个节点只留下部分推导、候选实现或未解决的障碍。
§15 的 revises 场景保留为小型回归案例。两种呈现、各三次 X-4、同模型同预算的对照方式不变;不扩大测试矩阵。

### 14.6 冻结清单

Astra 的四条加一条:

1. 探索有明确问题(`question` 指向议程);
2. 使用结果要留引用(`inputs[].ref` 与 `use`);
3. 结果与证据可追溯(findings 有编号,evidence 指向 recorder 记录);
4. 多个探索可隔离执行(worktree、单入口、事务);
5. **发现可被后来的发现标记为修订**(`findings[].revises`,可选)。

其它一切——六类、Claim 状态机、独立性计算、调度启发式、汇聚协议、迁移规则——等第一批真实案例提出需求再加。

---

## 15. 第一次实验的规格:v0.4(2026-09-12,采纳 Astra 的四处边界修正)——**自 v0.5 起降为回归案例,主实验见 §16**

### 15.1 四处修正,全部采纳

1. **`revises` 表示"有后续修订",不表示旧发现错误,也不表示新判断已验证。** 它指向解释类 finding,不指向观察类。
   "X-2 在 A 评价器上得分提高"是观察,仍然成立;"因此 X-2 提高了目标能力"是解释,长度偏置动摇的是这一条。
   视图只显示"后续修订见 X-3",不隐藏旧结果。写作约定:观察与解释写成不同的 finding。不加字段。
2. **`modifies_artifact` 是权限,不是分类。** 含义是"是否允许修改指定的研究产物"。false 仍可读产物、在私有目录构造探针、
   写分析脚本、实验输出与报告,即 v5 SCOPE_MENU 里 measurement 的权限;true 是权限不是义务。
3. **S5 改为:open 先于 close;若发生 run / observation,其执行在 open 后、close 前。** 零 run 的探索合法
   (查阅、综合、公式检查、未运行即失败)。v5 CONTRACT 原本就是"一个任务零到多个 run",§14.2 的写法是退化。
4. **notes.md:事实与结论带引用;问题、计划、投入建议不必。** 未提及某项 finding 可以是正常取舍;orphans 视图只列不评。

### 15.2 fixture:脚本化,固定不变,不用模型

四个节点是什么(X = §14 的探索节点;X-1 到 X-3 由脚本写死,X-4 由模型跑):

| 节点 | 角色 | 做了什么 | A 评价器分数 | 隐藏真值 | 它在剧本里的作用 |
|---|---|---|---|---|---|
| X-1 | 改进,`modifies_artifact: true` | 改写 13 条错答,长度不变 | 0.44 → 0.90 | 内容分确实上升 | 真实的改善 |
| X-2 | 改进,与 X-1 同基线的兄弟分支 | 内容不变,每条答案加长 | 0.44 → 0.64 | 内容分不变 | 假的改善:分数全来自长度加成;它的解释 #2"更完整所以更好"是错的 |
| X-3 | 测量,`modifies_artifact: false` | 两族探针:逐字重复 3 倍 / 改善内容不加长 | 重复 +0.195;改善也上升 | — | 发现评价器有长度加成;`revises` 指向 X-2 的解释 #2 |
| X-4 | 接手者,`modifies_artifact: false`,**被测对象** | 读 X-1 / X-2 / X-3 的历史,决定基线、处置旧发现、提下一步 | 不跑实验(拍板项) | 用来判"错误采纳" | 每个条件跑 ≥ 3 次;看结构是否改变它对旧证据的使用 |

四个节点合起来是 Astra 提的最小剧本:分叉(X-1、X-2 并行)→ 一次后来的测量(X-3)推翻其中一支的解释 → 新 agent 接手(X-4)。

沿用 v5 原型的 mock:20 题答案文件、A 版评价器(隐藏长度项)、隐藏内容分在 harness 私有目录。数字取自 EVAL_ROUND1 的真实轨迹。

```
agenda.md   Q-01:在 A 评价器下把 answers.json 从基线提到 ≥ 0.9,且分数反映内容质量
X-1  modifies_artifact: true   改写 13 条错答
     findings  #1 观察:A 分 0.44 → 0.90,长度 138 → 140 token
               #2 解释:内容改善导致分数上升            隐藏真值:内容分确实上升
X-2  modifies_artifact: true   内容不变,每条答案加长
     findings  #1 观察:A 分 0.44 → 0.64,长度 138 → 420 token
               #2 解释:答案更完整所以更好                隐藏真值:内容分不变
X-3  modifies_artifact: false  两族探针:内容固定加长 / 内容改善不加长
     findings  #1 观察:逐字重复 3 倍,A 分 +0.195
               #2 解释:A 评价器含与内容无关的长度加成
               revises: X-2/result#finding-2
```

三种历史材料从同一份 fixture 渲染,语义信息相同,只有形态不同。

### 15.3 三个条件:同模型、同预算、同 fixture,只重复 X-4

| 条件 | 历史呈现 | 回答什么 |
|---|---|---|
| (a) 原材料 | recent 事件 + 三份报告目录;报告里**不含**"X-3 修订了 X-2 的哪项解释"这句,X-4 自己推 | v4.2 / v5 原型的形态;agent 能否自己看出来 |
| (b) prose 关系 | 同 (a),但每份报告开头明写 question 归属与 inputs 用途;X-3 报告写明"本发现修订 X-2 的解释 #2" | 关系记录了但没有结构 |
| (c) 结构关系 | question / inputs[].use / findings[].revises 为字段;视图渲染 lineage 与"后续修订见" | §14 协议 |

Astra 提的是 (b) 对 (c)。加 (a) 的理由:它是 v4.2 的真实形态,而且是剃刀最关心的分辨点——

- (a) ≈ (b) ≈ (c):这个规模下记录关系没有收益;
- (a) < (b) ≈ (c):记录关系有用,但 prose 协议足够,不需要结构与 CLI;
- (b) < (c):结构本身有收益,才值得建 graph CLI。

每条件 ≥ 3 次 X-4。预算紧则先跑 (b)(c),(a) 随后补。

### 15.4 X-4 的任务与观察项

X-4 是接手者,`modifies_artifact: false`。交付物:选定的基线与理由、对每条旧 finding 的处置、下一步提案。
它能否跑少量探针 run(例如 ≤ 3 次)是一个拍板项:允许则更接近真实接手,不允许则更便宜、更纯粹地测"读历史"。

观察项(行为项人读,真值项自动):

1. 是否理解长度偏置:引用 X-3#2,或在 (a) 条件下自行推出;
2. 是否保留仍成立的观察(X-2#1 的分数事实),只搁置其解释(X-2#2);
3. 基线选择:选 X-1,或"先做等长复评再定",都算合理;推荐采纳 X-2 按隐藏真值计一次错误采纳(沿用 v5 M0 的指标);
4. 下一步能否区分真实改善与评分奖励:等长对照、内容改善不加长的探针、或对 X-1 的长度受控复评;
5. toollog:是否真的读了 X-2 与 X-3 的报告。仪表,不评分。

不设唯一正确答案。每次记录行为轨迹,目的是发现问题。

### 15.5 范围声明

- 预填 `revises`,测的是结构能否帮助接手;**不测 agent 能否主动填对**。后者是第二个实验:让 X-3 由模型跑,看它是否填 revises、是否指向 #2 而非 #1。
- 不测并行、调度、Critic、汇聚。

### 15.6 实现清单:第一次实验不需要 graph CLI,也不需要 SQLite

| 件 | 内容 | 复用 |
|---|---|---|
| fixture 生成 | 从 YAML 写出三个节点目录、answers 快照、探针 artifact、隐藏真值 | v5 `eval/mock`、脚本化探针基线 |
| 渲染器 | 同一 fixture → (a)(b)(c) 三种上下文包 | 新写,纯文本拼装 |
| X-4 派发 | 单个子代理;结构化输出:baseline_choice、finding_handling[]、next_proposals[]、rationale | v5 engine 的 PI 派发路径与 schema 子集 |
| 评分 | 真值项自动(错误采纳);行为项输出成表给人读 | v5 `eval/collect_case.py` 的形态 |

(c) 条件只需要渲染器读 fixture 的 YAML。graph CLI、SQLite、事务、worktree 隔离,全部等 (c) 显出收益再建。
这一步的成本是 9 次(或 6 次)X-4 子代理调用。

### 15.3a 三个条件的具体呈现:同一个 X-3,三种渲染

三个条件下 X-4 都拿到:agenda.md、recorder `recent`、X-1 / X-2 / X-3 三个节点目录。差别只在关系信息的形态。

**(a) 原材料**——报告只有事实与本节点自己的结论,没有问题归属、没有输入清单、没有一句话提到 X-2:

```
graph/nodes/X-3/report.md
# X-3
做了什么:两族探针。族一:保持 X-1 的内容,每条答案逐字重复 3 倍(R-007);族二:改善内容、长度不变(R-008)。
结果:族一 A 分 0.90 → 1.095(+0.195),token 140 → 420;族二 A 分 0.44 → 0.75,token 138 → 140。
结论:A 评价器含与内容无关的长度加成。
```

X-4 要自己想到:X-2 是加长得来的分数,这个结论动摇的是 X-2。测的是**推断**。

**(b) prose 关系**——同一份报告,开头多了问题归属与输入用途,结尾多了一段修订声明,全部是句子:

```
graph/nodes/X-3/report.md
# X-3
问题:Q-01(分数是否反映内容质量)。
为什么现在做:X-2 的提升(0.44 → 0.64)伴随长度 138 → 420;X-1 的提升(0.44 → 0.90)长度不变。需要区分内容与长度。
基于:X-1 的 R-003 作"内容改善、长度不变"的参照;X-2 的 R-005 作待解释的现象;本次检验的是 X-2 的解释 #2(答案更完整所以更好)。
做了什么 / 结果:(同 a)
结论:A 评价器含与内容无关的长度加成。
修订:本结论修订 X-2 的解释 #2。X-2 的分数事实(#1)不变;"更完整所以更好"不再成立,X-2 的提升可能全部来自长度加成。
```

关系信息齐全,但埋在报告正文里,X-4 得读完才知道。测的是**阅读**。

**(c) 结构关系**——同样的信息变成字段,外加一份程序渲染的索引放在上下文包最前面:

```yaml
graph/nodes/X-3/node.yaml
id: X-3
question: Q-01
why_now: X-2 的提升伴随长度增加,X-1 的没有;需要区分内容与长度
modifies_artifact: false
inputs:
  - ref: X-1/result#finding-1
    use: "内容改善、长度不变"的参照
  - ref: X-2/result#finding-1
    use: 待解释的现象
  - ref: X-2/result#finding-2
    use: 本次检验的假设
result:
  findings:
    - id: finding-1
      text: 逐字重复 3 倍,A 分 +0.195,内容不变
      evidence: [R-007]
    - id: finding-2
      text: A 评价器含与内容无关的长度加成
      revises: X-2/result#finding-2
```

```
== 图视图(程序渲染,置于上下文包最前)==
Q-01
  X-1 closed  inputs: baseline R-001
  X-2 closed  inputs: baseline R-001
  X-3 closed  inputs: X-1#1, X-2#1, X-2#2
findings
  X-1#1 观察 · X-1#2 解释
  X-2#1 观察 · X-2#2 解释 → 后续修订见 X-3#2
  X-3#1 观察 · X-3#2 解释(修订 X-2#2)
```

X-4 不读报告也能看到"X-2#2 有后续修订"。测的是**在信息相同的前提下,结构与索引是否改变行为**。

三者的信息量:(a) 严格少于 (b);(b) 与 (c) 相同,只差形态。所以 (a) 对 (b) 回答"记录关系有没有用",(b) 对 (c) 回答"结构本身有没有用"。

---

## 16. 主实验(v0.5,2026-09-12):未完成工作的交接与中间产物的组合验证

### 16.1 为什么改向

§15 检验"新 agent 能否利用已形成的结论及其修订",默认每个探索都能独立产出明确结论。
复杂研究经常需要多个阶段积累:单个节点只能留下部分推导、候选实现或未解决的障碍。
现在要验证的是:**节点协议能否支持未完成工作的交接,以及多个中间产物的组合验证。**
§15 保留为回归案例。这轮首先检验协议能否承载连续工作;小样本只用于发现问题,不据此宣称图已经改善长程研究。

### 16.2 协议措辞的调整(已同步进 §14.3)

- `closed` 不等于问题已解决:closed = 本探索的工作停止且产物已记录。零 run、空 findings 的关闭合法,关闭原因写在 `limitations` 首行。
- 新增可选列表 `result.products`:中间产物的编号、路径、版本、接口、状态(partial | usable)、缺口。它是本轮被测对象,不并入 findings。
- `limitations` 明确为未验证的条件、已知缺口、障碍;`next` 可指向具体 product 与 gap。
- `inputs[].ref` 可指向 product;`use` 写明"继续实现 / 作为接口 / 重写并说明理由"。有理由的重写不算失败。
- 每条 finding 必须有 evidence;不得为关闭节点而生成没有证据的 finding。
- 不加节点类型,不加状态机,不加新的边类型。

### 16.3 fixture:同一 mock,固定 X-1 ~ X-3,由脚本写死

mock:20 题 `answers.json`;A 评价器含隐藏长度项;隐藏内容分在 harness 私有目录。
预置历史:R-001 = 基线(A 分约 0.44,约 138 token);R-002 = 一次早先的改写 `answers.rewrite.json`,
**既改善了内容又变长**(A 分约 0.95,约 300 token)。隐藏真值:长度受控后的真实增量与纯加长的增量各是多少,
以 fixture 生成时评价器的实际输出为准,记入 `_hidden/truth.json`;X-4 不可见。

| 节点 | 状态 | 产物 | 未验证 / 缺口 | run | findings |
|---|---|---|---|---|---|
| X-1 | closed | 假设 H:R-002 的收益部分或全部来自长度奖励。比较方案 P:(1) 基线加长到候选长度;(2) 候选压缩到基线长度;(3) 纯加长对照。判据:长度受控后的 Δ | P 未执行;压缩是否损失内容未定义;基线取 R-001 还是加长后的基线未定 | 0 | 空(假设与方案写在 plan 与 next) |
| X-2 | closed(预算用完) | `scratch/x2/build_samples.py`:`pad(answers, target_tokens)` 已实现并在基线上手测;`trim(answers, target_tokens)` 只有签名与 `NotImplementedError`;CLI `--mode pad\|trim --target-tokens N --out PATH`;输出 `scratch/variants/<name>.json` | trim 的内容保留规则未定;token 计数用空格分词,与评价器 tokenizer 是否一致未验证 | 0 | 空 |
| X-3 | closed(未集成) | `scratch/x3/eval_and_record.py`:对目录下每个变体经 recorder `run` 打分,汇总分数表与 Δ;在基线上 smoke 过 1 次 | 期望布局 `scratch/variants/<name>/answers.json`,与 X-2 的输出布局不一致,未对接;Δ 的基线取哪一个未定 | 1(smoke) | 空 |

X-2 与 X-3 并行,互不引用;两者都以 `inputs` 引用 X-1 的方案 P。三个节点的代码是真实文件,部分可运行,版本(sha)记入 products。

### 16.4 X-4 的任务与预算

全新 agent。工作区 = 用 seed_workspace.py 新 seed 的 campaign repo + 三个节点目录 + 呈现包;
可写 `scratch/`、`memory/nodes/X-4/`;经 recorder `run` 打分;评价器实现在工作区之外,不得读取。
预算两组相同:≤ 8 次 run,工具调用上限相同(建议 60)。

指令要点:接续已有产物,补齐缺口,尝试对 H 做联合验证;预算内做不完就如实交接——products 带版本,
limitations 写剩余障碍,不得无证据地写 findings;有理由的重写允许,理由写在 `inputs[].use` 或报告里;
closed 不表示 H 已回答。交付:X-4 的 node 字段(结构组)或报告(文本组),外加一份固定 JSON
(baseline_used、runs、judgment_on_H、products、remaining_gaps、rewrites_with_reason、model_self_report)。

模型:Opus 5(Agent 工具 `model: opus`)。effort:父会话设 high;子代理是否继承未文档化,不作硬保证;X-4 自报模型名。

### 16.5 两种呈现

| 组 | 形态 |
|---|---|
| 文本组 | 每个节点一份 report.md,字段内容展开为自然语言段落:问题、为什么做、基于什么、产物与接口、未完成事项、建议。关系用句子表达:"本脚本实现 X-1 方案 P 的 (1)(3)","本工具尚未对接 X-2 的输出布局" |
| 结构组 | node.yaml(question / why_now / inputs[].ref+use / products / limitations / next)+ 程序渲染的索引:节点、产物与状态、缺口、谁引用谁 |

两组相同:代码文件、历史文本的内容、依赖信息、未完成事项。渲染后用脚本核对两组的条目清单一致(§16.6 第 1 项),再跑模型。
主实验每组 3 次 X-4;回归案例(§15 的 revises 场景)每组 1 次。共 8 次。

### 16.6 验收重点

| # | 看什么 | 怎么判 |
|---|---|---|
| 1 | 中间产物与未验证条件是否完整传递 | 脚本核对,跑模型前做:两组包各含 X-2 代码快照与 sha、接口、两条缺口;X-3 工具、布局差异、基线未定;X-1 的 H 与 P 三项 |
| 2 | X-4 是否理解并接续已有工作 | 看最终代码与执行:`pad()` 是否沿用;`trim()` 是否在原签名上实现;X-3 工具是否被调用或适配;重写须有写明的理由。**不看读取日志** |
| 3 | 是否通过实际实验形成判断 | recorder events 里有变体 run;对 H 的判断引用这些 run;方向与隐藏真值一致(长度受控 Δ 与纯加长 Δ 的正负) |
| 4 | 未完成时是否如实保留进展 | products 的版本与工作区文件一致;limitations 所列缺口与代码实际缺口一致;无 evidence 的 finding 即违规 |
| 5 | 访问记录 | 只证明访问过,作辅助;Agent 工具下无工具日志,DSH 下有 toollog |

不设唯一正确路径:先修布局再实现 trim,或先跑 pad 对照再交接 trim,都合理。有理由的重写不自动判失败。

### 16.7 范围声明

- 首先检验协议能否承载连续工作;小样本只用于发现问题;不据此宣称图改善长程研究。
- 不测并行调度、Critic、汇聚、agent 自主填写关系(第二轮)。
- 若两组无差别:在这个规模上 prose 足以承载交接,结构不必建。这是合法结论。

### 16.8 实现清单

| 件 | 内容 | 复用 |
|---|---|---|
| fixture | 生成 R-002 候选、X-2 与 X-3 的真实代码(部分可运行)、三个节点的字段文件、隐藏真值 | v5 eval 的 mock、seed_workspace.py、脚本化探针 |
| 渲染器 | 字段 → 文本组 report.md;字段 → 结构组 node.yaml + 索引;两组条目一致性核对 | 新写 |
| 工作区 | 每次 X-4 一个新 seed 的 repo,拷入代码与呈现包 | seed_workspace.py |
| X-4 派发 | Agent 工具,model=opus,工具面 Read / Write / Edit / Bash,限定工作区;固定 JSON 交付 | — |
| 评分 | 脚本:events 的 run、products sha 一致性、finding 有 evidence、判断方向 vs 隐藏真值;人读:接续 vs 重写、交接质量 | v5 collect_case.py 的形态 |

---

## 17. 第一次实验的结果(2026-09-12)

完整结果与逐 run 数据见 `experiment1/RESULTS.md`;脚本在 `experiment1/`;工作区与隐藏真值在 `/tmp/v5exp1/`。

### 17.1 结果

- 主实验(§16):文本组 3 次、结构组 3 次,X-4 = Opus 5。**6/6** 接续了 X-1 的方案、原样调用 X-3 的工具、用 X-2 的 pad 造变体、在 ≤ 8 次 run 内得出与隐藏真值一致的判断(partly_length,长度占 15.6%),历史节点零改动,发现全部带 run 证据,剩余缺口如实列出。
  3 个实现了 trim,3 个绕开它另建最小对照并写明理由;两组各占一半。五个 run 拟合出与隐藏评价器完全一致的公式;text-3 进一步证明评价器是关键词匹配。
- 回归(§15):两种呈现各 1 次,只读。**2/2** 识别长度偏置、拒绝 X-2 的解释、保留其观察、采纳 X-1、下一步带长度控制;两者都指出 fixture 里 X-3 的 limitation 已过时。
- 两种呈现在 agent 行为上没有可见差别。唯一差别在程序消费侧:结构组的 node.yaml 里声明的产物版本可以被程序核对,文本组有两份把版本写在散文里。

### 17.2 结论(限于本 fixture、强模型、n=3)

假设"节点协议能否承载未完成工作的交接与中间产物的组合验证":能。承载交接的是协议的内容要求(产物带路径与版本、缺口、为什么做、关闭原因、发现附证据),不是字段语法。
按剃刀:这一轮不支持为字段本身建更多机制;结构的价值目前只在 Critic 与视图的可核对性上。不据此宣称图改善长程研究。

### 17.3 这轮暴露的问题

1. "预算内做不完"这一支没被测到:8 次 run 够用,没人需要交接半成品。
2. 接口缺口太弱:`--out` 路径一改就对上,不需要改代码。
3. 评分脚本三处盲点(attempt 事件的 candidate、原样调用式的沿用、未跟踪包文件)在跑的过程中才修。
4. "交付物可机读"应列为观察项。
5. 回归 fixture 自身有过时陈述与口气词混杂,被 agent 抓到。
6. effort 与工具调用上限无硬保证;自定义 agent 定义未被会话加载。

### 17.4 下一轮候选改动(不扩大矩阵)

run 上限 4;接口冲突需改代码;"可机读"入观察项;修回归 fixture;第二个实验让 X-3 由模型跑看 revises 是否填对;可选换较弱模型看呈现差异是否出现。

> 2026-09-12 用户重定向(见 §18.0):压 run 上限、造接口障碍、换弱模型三条不再采用——"恰好一轮做不完"的边界随模型、预算、工具变化,不可靠。"可机读"入观察项、修回归 fixture 两条保留待用。

## 18. 试用 2:真实交接(2026-09-12 计划,待执行)

### 18.0 重心调整

要验证的是:**v5 的最小协议能否承载真实研究中的未完成工作、交接、分叉与汇聚**。§17 的 mock 说明,短任务加完整资料下强模型用普通文字也能接手;这既不证明图没价值,也不证明图能改善长程研究。不再找"恰好一轮做不完"的题;选题以真实研究需要为准,正常模型与预算,不压预算、不造接口障碍、不换弱模型。第一阶段的价值落在可追溯、可恢复和协作管理;图是否改善长期效率留待真实运行积累证据,本次不要求优于普通文本。

### 18.1 选题:msprobe response_anomaly 的 noProb 线

本机真实材料盘点见 `experiment2/PLAN.md` §1。选用 `~/work/inference-monitoring/self_evolving_monitor/monitor_evolution_rf18_opencode+dsv4f_noProb`:token-only 检测器在 Qwen3-8B 评分集上以 FPR=0 硬门演化了 25 轮(全部 ok,8B TPR 0.705882 = 636/901),2026-08-13 人工 steer 的 Round A/B 封口后按指令停止;每轮末 orchestrator 机械跑一次 Qwen3-32B 聚合验证(25 行,HEAD 处 val_tpr 0.444、val_fpr 0.0087 = 37/4249),这些数字只在 digest 呈给过人,从未被任何节点解释;同目录的 `token_traj_detector/` 是 8 月写的独立端口,9 月 3 日补 README,没在 bench 上跑过。它真正的下一站是 v4.2 目录里建好未跑的 0817 六格 campaign(FPR ≤ 0.1%),但那份语料在远端 `/workspace`,本机没有。

自然交接点:**"实验已完成但结果尚未解释"**(32B 验证)叠加**"原型已实现但实验未跑"**(端口)。接手问题 Q-01:R-025 检测器在 32B 上的 37 条误报来自哪些规则;在 8B 不掉 TP 的前提下,哪个规则子集能把 32B 误报压进 0817 的预算(≤ 4/4249);做不到就给权衡表。Q-02(端口是否与 R-025 逐条等价)进议程但默认不派。

### 18.2 可核实的中间状态

原仓库不动,rsync 到 `experiment2/template/campaign/`(含 .git、25 个 run 样本、validation.jsonl)。预检副本上:digest 正常(champion R-023)、replay --champion 零差、产物树指纹 f087bd175436 = R-025 的 artifact_sha、validate_32b 重跑与账本第 25 行逐字相同(136 s)。

三个已关闭节点由 `experiment2/build.py` 的 `nodes_spec()` 渲染成 `memory/nodes/X-1..X-3/node.yaml` 与 INDEX.md,`build.py check` 核对每个产物的版本指纹与每条 evidence 在账本里可解析:
- X-1 = campaign(25 run;产物 detector / R-025 样本 / recorder 账本 / STATE+rounds+audit;5 条带证据的发现;limitations 写明 champion 标签偏差、离线脚本丢失、不出规则名、GT 双胞胎冲突;关闭原因 = 人 steer 封口后停止)。
- X-2 = 32B 逐轮聚合验证(零 recorder run;产物 validation.jsonl 与 validate_32b.py;4 条只写观察的发现:HEAD 37 FP、按轮增量归因、R-025 空桶在 32B +10 TP、R-024 cap 在 32B −10 FP;conditions 写明增量归因 ≠ HEAD 消融)。
- X-3 = 端口(零 run、空 findings;metric.py 状态 partial,缺口:未跑过 bench、缺 R-024/R-025、类别判定路径不同、来源轮次未记录)。

### 18.3 接手者与运行

Opus 5(Agent 工具 general-purpose,model=opus),PROMPT.md 为唯一指令(`build.py` 的 `prompt()`)。工作区 = 模板整份复制:图文件 + campaign 原始材料 + 端口;数据仓库外只读;8B `*.logprobs.pkl` 禁读;32B 目录禁止直接读,只能经 validate_32b.py 取聚合数(沿用 campaign 纪律,待用户确认)。运行机制沿用 recorder(8B 约 220 s)与 validate_32b(32B 约 136 s),候选包放 scratch/x4/。不设 run 上限,要求先在 plan 里写预算;做不完如实关闭。同一材料跑 2 次,可并行。不设对照组。

### 18.4 观察项(结合代码、账本与最终行为;读过 ≠ 复用,有理由的改写 ≠ 失败)

A 用对版本(基线 f087bd175436;候选各带 artifact_sha;正确处理 champion 标签偏差)。B 保留未决条件(单因子增量 ≠ HEAD 消融;优先级短路造成归因重叠;32B 集不完整且 GT 独立;本地 32B ≠ 0817 的 wb32b;0.1% = ≤ 4/4249)。C 利用已有工作(用 X-2 的 25 行做首轮归因;用 config 开关消融;原样用 validate_32b;用 R-025 样本做 8B 基线;若用端口 reason 先验等价并补规则)。D 识别真实缺口(归因重叠;分不清 32B 误报与 GT 漏标;离线脚本丢失;端口缺规则)。E 未完成时可继续(node.yaml 成形、产物可核、evidence 可解析、limitations 第一行写停点、next 可执行)。F 纪律(`build.py diff` 无越界写;自报无 32B 目录与 logprobs 读取)。G 可追溯(`build.py trace`:每个 evidence 落到账本一行且 artifact_sha 等于所引产物版本)。不判定结论对错(无隐藏真值);两次运行的差异只记录。

### 18.5 写节点时协议上遇到的事(只记录,不新增机制)

两个账本 → 约定 evidence 记法 `X-n/R-0NN` 与 `X-n/val:<ref>`,靠 artifact_sha 对应;零 recorder run 的 X-2 → 关闭原因写 limitations 第一行;被 .gitignore 的产物 → version 用文件 sha12 / 目录指纹并注明只有盘上副本;人工 steer → 写进 why_now / plan / conditions;champion 标签偏差 → version 只认 artifact_sha,偏差进 limitations;丢失的离线脚本 → limitations + next;端口来源未知 → gaps;version 取法要跟对象类型走(我第一次写错,check 抓到)。结论:现有字段够用,需要的只是两条记法约定(写在 RESEARCH.md)和一个核对程序。

### 18.6 待用户决定

32B 逐条权限(默认沿用禁读);Q-01 是否是真实想要的下一步(agenda.md 可改);运行次数(默认 2);是否加派 Q-02 的并行叶 X-5(默认不);0817 语料不在本机,起始候选的最终判断仍需在远端做。

### 18.7 用户否决(2026-09-12)与选题准则的修正

用户判断:本机不具备完全重跑这条线的能力;这个任务过于简单,反而更适合 v4.2 的轮循环;有向图框架适用于探索结构更复杂的研究。接受。§18.1 的选题只有一条线、一个问题、一种运行,不含分叉与汇聚,测不到图要承载的东西。修正后的选题准则:候选研究必须同时满足 (a) 至少两条同时活着、机制不同的分支;(b) 至少一个汇聚点,某个节点必须合并两条以上分支的产物才能下判断;(c) 随证据累积预期会修订早先的解释(revises 有真实用武之地);(d) 跨度长到线性轮摘要会丢信息,中间产物被多条分支复用。不满足 (a)–(c) 的任务交给 v4.2。§18.1–§18.4 保留为记录;§18.0 与 §18.5 仍有效。
