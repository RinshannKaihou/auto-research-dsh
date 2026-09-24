# DSH 原生宿主边界

正式 DSH 是唯一执行后端。插件使用当前会话已经选择的 provider、model、reasoning effort、agent preset、原生工具和权限，不读取或复制用户凭据，也不装配自己的 Agent。

## 使用的宿主能力

| DSH API | 插件用途 |
|---|---|
| `commands.register` | `/research` 命令组 |
| `tools.register` | 八个研究元数据工具；身份来自 `exec.agent` |
| `systemPrompt.context` | 注入有界研究记忆 |
| `session/event`、`agent/pre-step` | 轨迹投影和 turn 归属冻结 |
| `llm/stream` | 观察普通、标题和压缩用量 |
| `goals` | 创建、暂停和恢复插件拥有的原生 goal |
| `sessionController` | 创建/恢复普通会话、继承模型、取消运行 |

自主研究不调用 `sessionController.prompt()`，因为该接口会生成用户来源消息。续轮由原生 goal driver 产生。人工消息保留宿主 user source，并暂停该会话的插件 goal。

## 私有存储进程

插件加载时检查 Python 3.11+，然后执行打包源码中的 `python -m auto_research.service --registry ...`。传输是父子进程 stdio JSONL，没有 HTTP 端口。请求上限 64 KiB，响应上限 4 MiB，最多 16 个 pending 请求，默认超时 15 秒。

业务操作 ID 与 IPC 传输 ID 分开。研究工具使用原生 call ID 构造业务 ID，重试不会重新发布或重复计费。浏览器只能携带当前 session ID；项目路径由可信服务端从原生 session cwd 和持久 registry 解析。

## 权限说明

基础版本采用 DSH 的单用户可信工作区权限。分支会话使用独立 cwd 和独立工作副本，但标准 DSH 读取策略可能仍允许读取其他可见路径。能力接口明确返回 `strict_cross_session_read_isolation: false`。这一限制不影响研究归属、写入目录、publication 固定、用量观察和手动工作。

宿主不可用或 Python 不满足版本时，插件报告具体错误；不会联网安装依赖，也不会启动旧 worker 或备用模型执行器。
