# auto-research-v5 0.6.5

这是安装在正式 DSH 内的研究插件。DSH 继续负责模型请求、原生工具、会话轨迹、goal、subagent、权限与取消；插件增加研究项目、知识、材料、并行探索、节点专家和恢复工作流。Python 3.11+ 私有进程只处理 SQLite 与文件事务，不调用模型，也不运行独立研究循环。

## 安装与开始

```bash
dsh plugin --profile web add ./auto-research-v5-0.6.5.tgz --offline
dsh web
```

在需要作为研究主会话的普通 DSH 会话中运行：

```text
/research init <研究目标>
/research auto
```

`init` 只建立项目并关联当前原生会话，不调用模型。空白会话没有顶部标签栏，请点击输入区上方的“打开研究工作台”，直接在弹窗查看项目或关联已有项目；不需要先发普通消息。`init/status` 每次显示编号、时间和命令结果。已有对话的顶部 Research 标签仍可用。`auto` 或工作台“开始自主研究”才启动研究 goal 并可能产生模型费用。项目用量只监控，没有预算或消费上限。

## 命令

| 命令 | 行为 |
|---|---|
| `/research status` | 查看有界项目摘要与实际原生运行状态 |
| `/research auto` | 首次开始；停止或完成后显式再次开始 |
| `/research pause` | 暂停项目管理的自动续轮与新派发，当前轮可收尾 |
| `/research resume` | 恢复项目暂停；保留人工、Stop、故障等局部暂停 |
| `/research resume --session <id>` | 显式继续一个已核实安静的研究执行会话 |
| `/research retry --session <id>` | 核实故障退出，在原 cwd 创建带 `retry_of` 的恢复工作段 |
| `/research verify-close --session <id>` | 只核实节点工作段的完成意图、原生执行与所属作业，不重放命令 |
| `/research stop` | 请求停止受管理研究执行与可归属作业 |
| `/research verify-stop` | 只重新核实停止状态，不重放模型或命令 |
| `/research discuss <node-id>` | 创建或复用带冻结背景的独立人工讨论会话 |
| `/research restore <snapshot-id>` | 无副作用地预览历史文件接手 |
| `/research restore <snapshot-id> --preview-id <id>` | 从预览固定的版本创建手动接手会话 |
| `/research guidance [<绝对路径> --version <版本>]` | 查看或登记科研指导 Markdown |
| `/research detach` | 将当前会话移出项目，保留对话与材料 |

旧 `/research focus` 和 `/research branch` 会返回退役说明。点击研究图节点只选择详情。自主研究方向由研究 Agent 使用 `research_propose`、`research_dispatch` 与 `research_wait` 决定。

## 模型工具

- `research_query`：读取摘要、固定引用、知识检索或 keyset 分页。大记录返回预览；以相同 `ref` 和 `offset` 分块展开。
- `research_propose`：原子创建带固定 `open_question` 议程锚的节点、输入和有类型前驱；必须声明独立根理由，或声明 `depends_on`、`branches_from`、`revises` 前驱。
- `research_consume`：追加记录执行期间采用的固定材料及其 `adopts`、`supports`、`contradicts` 或 `context` 语义；重试保持同一消费记录和原冻结工作段归属。
- `research_dispatch` / `research_wait`：在独立 cwd 的普通 DSH 会话中并行探索，并通过持久通知回传。
- `research_note`：记录进度、条件、缺口和纠偏现场。
- `research_memory`：`record`、`revise`、`checkpoint` 或调用整理复核者 `consolidate`。知识引用固定为 `knowledge/<id>@<revision>`。
- `research_publish`：发布不可变 partial/complete 阶段材料，可零实验、空 findings，并引用固定知识版本。
- `research_snapshot`、`research_relate`、`research_finish`、`research_close_node`：分别保存文件快照、关系、结束工作段和显式关闭节点。
- `research_delegate`：启动一个有界原生节点专家。任务固定问题、目的、输入、工具范围、产物与完成条件。

## 会话、专家与权限

会话角色包括 `main`、`node_core`、`discussion`、`handoff`、`legacy`；旧 `exploration` 作为兼容角色继续识别，临时专家登记为独立 `specialist` 身份。项目最多一个有效主会话。节点进入执行时创建或恢复唯一核心会话与 cwd，阶段发布后继续复用；默认研究会话槽位为 2，每节点专家扇出为 2。专家共享父 cwd，适用于只读复核或明确的单写者任务；需要独立 cwd 时使用 `research_dispatch`。

讨论、接手和专家能查询研究记录，但服务端拒绝研究写入、发布、关闭节点和派发。专家不能递归委派。受管理的主协调和节点核心会话不能直接绕过 `research_delegate` 调用原生 `subagent` 工具；登记、冻结归属和扇出检查先于专家首次模型请求。普通非研究会话不受此门控影响。专家完成、取消、供应商错误和退出未知均返回结构化状态；退出未知继续占用扇出，重试不会启动第二个执行，也不会把父 goal 标为 fault。

## 长期记忆

schema 5 将完整过程与本次上下文分开：

- 不可变知识修订记录正文、范围、条件、证据、来源身份、依赖和 supersedes；并发修改使用 `expected_revision`。
- 节点问题必须引用固定 `open_question`；节点检查点保存具体工作状态、文件用途、下一步和阻塞。
- 中文检索使用 bigram 和单字影子列。目前查询在这些规范化字段上执行 SQLite `LIKE` 扫描，结果按单调键分页；FTS5 表会维护，但当前查询未使用它。分页限制响应大小，不保证扫描成本与历史规模无关。
- `system-prompt/assemble` 按角色和任务组装稳定内容包。用量、时间戳、请求 ID 和流水号不进入正文，因此纯用量变化不会产生重复上下文消息。
- 内容包与逐请求来源清单分开存储；读取超时只使用同一会话最近缓存并标记 stale。
- `.research/memory/PROJECT.md` 是只读可重建投影。外部修改会先保存到 `drafts/`，不能覆盖 SQLite 权威状态。
- 结构化 `node_dependencies` 是图边权威来源；旧节点保留为 `legacy_unresolved`，前端不会按创建顺序、文字或目录读取猜边。追加消费写入 `material_consumptions`。
- goal 投影按 revision 单调更新；同 revision 冲突会拒绝。工作段完成先写 close intent，仅在原生轮次、工具和所属作业核实安静后结束；未知状态可用 `verify-close` 复核。
- 待整理上下文同时报告总数、实际展示数、是否还有后页与 cursor；它是建议性整理队列，不是项目完成门禁。用量页分别显示数值记录与执行者覆盖缺口。

## 迁移、恢复与限制

schema 2 先迁移到 3，schema 3 再迁移到 4；schema 4 生成一致的 `schema-4-backup.sqlite3` 后事务迁移到 5。旧节点建立带 legacy 来源的议程锚并标记 `legacy_unresolved`；旧引用、材料、费用和历史工作段保留。冷恢复后插件 goal 不自动武装。复制迁移可用：

```bash
PYTHONPATH=python python3 -m auto_research.maintenance_cli \
  -p /path/to/source migration migrate-copy --destination /path/to/copy
```

包内没有旧 worker、独立 GUI 或自主 Python Runtime 入口。旧 0.6.2 包会拒绝写 schema 5。回滚前应保留 `schema-4-backup.sqlite3`、完整 schema 5 项目及升级后增量，再切换到兼容旧副本；不提供有损反向降库。

文件系统仍沿用 DSH 权限。同一用户进程可能读取其他目录，独立 cwd 不构成强隔离。科研指导和专家输出是可追溯输入，不被程序自动判定为科学事实。

## 0.6.1 恢复与记忆修正

- `/research retry --task <T-id>`：核实未登记执行的会话是否不存在，或是否仅创建了空闲、无 goal 和工具执行的原生会话；随后使用原任务、原会话 ID、原工作目录重试创建。宿主读取错误不当作“会话不存在”。已有研究执行登记时，改用 `retry --session`。
- `/research retry --session <id>`：核实退出后恢复已有执行，同时重新占有节点任务并记录新工作段的 `retry_of`，阻止同节点双执行。未知会话返回明确错误。
- `/research verify-specialist <S-id>`：核实专家原生会话、未完成工具与已登记作业，并重试退出操作；核实成功后转为 `incomplete`、释放扇出占用。时间流逝、空 jobs 列表、会话未加载均不构成退出证据。
- 默认专家工具超时为 600000 ms（10 分钟）；通过原生 `exec.signal` 取消，无法确认退出时仍显示 `unverified`。
- 节点执行视图的问题条目单独限额，只选择当前及输入节点的问题；知识优先保留固定输入对应的版本、后来修订、争议和输入节点经验。正文按条目裁剪，并提供省略计数和原始引用。超长任务保留问题与计划预览。
- 控制读取按页返回必要的执行事实，不返回历史会话上下文；项目控制和调度遍历所有控制页。完整历史仍可分页展开。
- 发布、结束和修订每个唯一事件仍生成一条整理待办；只有明确处理该待办的 consolidate 成功才标为完成。不会自动合并不同事件，也不会因为普通回答而清空待办。

## 0.6.2 主会话记忆与归属

- 主会话处于无节点的协调工作时，有独立项目视图：最近 8 个未关闭节点的问题候选，以及分歧（3）、近期修订（3）、跨节点 lesson（4）、项目级条目（3）的独立候选配额。各组分别生成有界摘要，保留来源、条件及展开引用；实际注入数量按正文额度缩减，不能视为完整知识库。用量变化不改变正文。
- `research_memory(action="record")` 省略 `node_id` 时默认取当前工作段节点；项目级条目必须显式使用 `visibility="project"`，此时不能同时填写 `node_id`。无当前节点时，指定目标节点或声明项目级。科学适用条件仍放在 `conditions`/`scope`，不是 `visibility`。
- 归属用于上下文选取，不构成访问隔离。原有全局记录保留；旧操作重试返回原回执。默认节点在首次写入事务中确定，重试不随随后切换的节点改变。
- 会话创建结果待核实的任务仍保留研究槽位；工作台顶部显示“需要处理”及占用数量，并展开恢复区。它不能因超时自动释放，需按已有 `retry --task` 或停止核实流程处理。

## 0.6.3 执行协议与可核实收尾

- 主会话只做协调；每个进入执行的节点使用唯一、可恢复的 `node_core` 会话、cwd 和冻结 attempt。重复派发不会创建第二个核心执行者。
- 根、前驱和固定输入在提案事务中一并校验；调度依赖方向为 predecessor → successor 并防环，修订/分支关系保留其非调度语义。
- `research_finish` 在节点核心上表示完成意图；阶段材料继续用 `research_publish`，等待依赖用 `research_wait`。主协调会话不能用节点 finish 意外暂停整个项目。
- 空白会话命令回执使用 DSH 公开 dock 与 `command/executed` 事件，不伪造用户/助手消息，也不触发模型。


## 0.6.5 修正：角色、专家与盲评

每个执行节点保留一个核心 Agent，完成规划、实验和分析。主协调负责盘点、计划、派发、综合和接续，不直接承接完整实验；节点专家的身份来自调用者，传入 node_id 只能检查是否一致。主协调的项目级专家通过 research_memory(action="consolidate") 启动。

- `research_delegate` 是同步单项委派；`research_delegate_batch({tasks})` 是有界同步并行，默认最多两项。超额批次不启动任何专家，不产生后台队列。每项分别报告结果和退出状态。
- `specialistTimeoutMs` 默认为 600000，可在插件配置调整；宿主工具另留 60000 ms 收尾时间。Stop 传递给整批子会话，单项失败不取消兄弟任务。
- 普通专家默认只能使用 read、research_query、research_read_input；tool_scope 只能缩小这个集合。
- `context_mode="blind"` 强制使用原生 spawn 新会话，不复制父对话。inputs 必须是已冻结文件的 `pub/P-…#item` 引用。专家只能看到 input-1 等句柄，使用 `research_read_input({input_id,offset,limit})` 读取；offset/limit 按 Unicode 字符计，单页最多 8192 字符。不能访问研究记忆、guidance、通用文件读取或另开 Agent。材料提供者负责匿名化、选择正确协议及不泄露答案；框架不猜测或清洗自然语言里的标签线索。
- 完整结果保存为 ArtifactStore 文件，task.result.artifact 提供引用；预览最多 1600 字符。只归档最终文本及结构化交付内容，不将 reasoning/tool-call 当作答案。主执行者可读取引用中的完整 JSON；历史 inline result 保持可读。
- `research_verify_specialist({task_id})` 与 `/research verify-specialist <id>` 使用同一核实逻辑，可处理遗留 running、unverified 和退出未核实的终态。活跃任务不被误结算，不重跑模型。持久化失败时回执明确 `persisted=false`；存储恢复后再核实。
- 只通过研究工具修改账本，不用 SQL 或 shell 修改 .research 来解锁。这里是操作约束，不是同 Unix 用户的文件权限隔离。

init/manual 用于项目建立和规划，auto 开启自动推进。用户要求计划确认时先提交整份计划，澄清问题的回答不自动批准整份计划；已经授权启动时不重复确认。计划/checkpoint 记录接受标准；最终报告逐项列出证据、缺口与终止原因。阶段材料 complete 不代表科学验收通过，pending review 不构成额外审批门。

schema 6 只新增 specialist_tasks.context_mode，旧任务默认 research，旧 result 不改写。首次打开 schema 5 库时创建 schema-5-backup.sqlite3，再事务升级；旧插件拒绝写 schema 6。回滚使用旧版兼容副本并保留升级后材料，不能直接降库。本次安装包不替用户安装、重启或修复真实研究项目。
