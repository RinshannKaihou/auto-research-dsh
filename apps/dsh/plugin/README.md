# auto-research-v5 0.6.0

这是安装在正式 DSH 内的研究插件。DSH 继续负责模型请求、原生工具、会话轨迹、goal、subagent、权限与取消；插件增加研究项目、知识、材料、并行探索、节点专家和恢复工作流。Python 3.11+ 私有进程只处理 SQLite 与文件事务，不调用模型，也不运行独立研究循环。

## 安装与开始

```bash
dsh plugin --profile web add ./auto-research-v5-0.6.0.tgz --offline
dsh web
```

在需要作为研究主会话的普通 DSH 会话中运行：

```text
/research init <研究目标>
/research auto
```

`init` 只建立项目并关联当前原生会话，不调用模型。`auto` 创建插件拥有的原生 goal，在同一会话里由 DSH 正常续轮。项目用量只监控，没有预算或消费上限。

## 命令

| 命令 | 行为 |
|---|---|
| `/research status` | 查看有界项目摘要与实际原生运行状态 |
| `/research auto` | 首次开始；停止或完成后显式再次开始 |
| `/research pause` | 暂停项目管理的自动续轮与新派发，当前轮可收尾 |
| `/research resume` | 恢复项目暂停；保留人工、Stop、故障等局部暂停 |
| `/research resume --session <id>` | 显式继续一个已核实安静的研究执行会话 |
| `/research retry --session <id>` | 核实故障退出，在原 cwd 创建带 `retry_of` 的恢复工作段 |
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
- `research_propose`：创建带固定 `open_question` 议程锚的节点；`dispatch=true` 使用同一派发处理器。
- `research_dispatch` / `research_wait`：在独立 cwd 的普通 DSH 会话中并行探索，并通过持久通知回传。
- `research_note`：记录进度、条件、缺口和纠偏现场。
- `research_memory`：`record`、`revise`、`checkpoint` 或调用整理复核者 `consolidate`。知识引用固定为 `knowledge/<id>@<revision>`。
- `research_publish`：发布不可变 partial/complete 阶段材料，可零实验、空 findings，并引用固定知识版本。
- `research_snapshot`、`research_relate`、`research_finish`、`research_close_node`：分别保存文件快照、关系、结束工作段和显式关闭节点。
- `research_delegate`：启动一个有界原生节点专家。任务固定问题、目的、输入、工具范围、产物与完成条件。

## 会话、专家与权限

会话角色包括 `main`、`exploration`、`discussion`、`handoff`、`legacy`；临时专家登记为独立 `specialist` 身份。项目最多一个有效主会话。默认研究会话槽位为 2，每节点专家扇出为 2。专家共享父 cwd，适用于只读复核或明确的单写者任务；需要独立 cwd 时使用 `research_dispatch`。

讨论、接手和专家能查询研究记录，但服务端拒绝研究写入、发布、关闭节点和派发。专家不能递归委派。专家完成、取消、供应商错误和退出未知均返回结构化状态；退出未知继续占用扇出，重试不会启动第二个执行，也不会把父 goal 标为 fault。

## 长期记忆

schema 4 将完整过程与本次上下文分开：

- 不可变知识修订记录正文、范围、条件、证据、来源身份、依赖和 supersedes；并发修改使用 `expected_revision`。
- 节点问题必须引用固定 `open_question`；节点检查点保存具体工作状态、文件用途、下一步和阻塞。
- 中文检索使用 bigram 影子索引，英文、数字、版本号和固定引用保留精确项；FTS5 不可用时有界扫描。
- `system-prompt/assemble` 按角色和任务组装稳定内容包。用量、时间戳、请求 ID 和流水号不进入正文，因此纯用量变化不会产生重复上下文消息。
- 内容包与逐请求来源清单分开存储；读取超时只使用同一会话最近缓存并标记 stale。
- `.research/memory/PROJECT.md` 是只读可重建投影。外部修改会先保存到 `drafts/`，不能覆盖 SQLite 权威状态。

## 迁移、恢复与限制

schema 2 先备份并迁移到 3，schema 3 再生成 `schema-3-backup.sqlite3` 并事务迁移到 4。旧节点建立带 legacy 来源的议程锚；旧引用、材料、费用和历史工作段保留。冷恢复后插件 goal 不自动武装。复制迁移可用：

```bash
PYTHONPATH=python python3 -m auto_research.maintenance_cli \
  -p /path/to/source migration migrate-copy --destination /path/to/copy
```

包内没有旧 worker、独立 GUI 或自主 Python Runtime 入口。回滚前应导出 schema 4 状态并保留完整升级项目，再切换到兼容旧副本；不提供有损反向降库。

文件系统仍沿用 DSH 权限。同一用户进程可能读取其他目录，独立 cwd 不构成强隔离。科研指导和专家输出是可追溯输入，不被程序自动判定为科学事实。
