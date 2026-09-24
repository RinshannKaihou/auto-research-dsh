# DSH 原生研究协议（schema 2）

## 生命周期

一个项目包含多个 DSH 会话关联区间。关联区间包含研究工作段；工作段可聚焦节点，也可执行无节点的规划。一个工作段跨多个原生 turn。工作段结束、节点关闭和原生 goal 完成互不推断。

运行中请求 focus 只登记下一工作段。每轮开始时，插件把关联区间、attempt 和 node 固定到该 turn；模型请求开始时再次固定用量归属。当前 focus 后续变化不会移动迟到回执或费用。

默认 control 是 `manual`。`auto` 只表示允许插件拥有的原生 goals 续轮。项目 pause 阻止所有插件 goals 后续续轮；人工消息只暂停当前会话。用量和未知用量只记录和展示，不改变 control。存储失联会暂停插件拥有的自主 goal，手动消息仍由 DSH 正常处理。

## 节点、publication 和关系

节点状态为 `proposed|open|closed`，并保存问题、当前理由、计划、固定 inputs、用途和 `continue|redirect|anchor` 策略。`anchor` 必须引用已存在的历史节点、publication item 或 legacy ref。

publication 状态为 `partial|complete`。它可以没有实验、没有 findings 或没有文件；summary 和 gaps 清楚说明阶段状态。发布不会自动关闭节点。同一节点能产生多个 publication；引用 `pub/P-001#draft` 永久指向该次发布的 item。

有 `source_path` 的 item 先固定到 `.research/objects/<sha256>`，再写 intent，最后提交 SQLite。回执丢失后相同 operation ID 复用已经固定的 bytes。`research_relate` 只记录结构和理由，不判定科学真假。

## 工作副本和接手

分支节点的固定输入物化到 `workspaces/branch-*/inputs/`；可写工作放在 `scratch/` 和 `output/`。同一 item 名在不同 publication 中有不同 ref 和不同副本。content-only item 进入 `research-inputs.json` 索引。

快照只接收项目内明确路径，排除 `.research`、`.git` 和疑似凭据。部分固定失败会保存 `complete=false` 和每项错误。接手要求来源 attempt 已明确 finished/stopped；active 或 unknown 时拒绝创建冲突副本。执行接手创建新 cwd 和新 DSH 会话，旧记录保持不变。

## 用量和恢复

每个原生 LLM 请求只有一个 observation；结束或晚到修正写追加 adjustment。`actual`、`estimated` 和 `unknown` 不互相伪装。统计结果不作为创建、续轮、恢复或派发的准入条件。

冷恢复不自动武装 goal，不重放没有工具结果的命令。schema-1 项目只能迁移到新副本，迁移保留原库一致备份、旧 ref 和旧 aggregate usage 来源键。
