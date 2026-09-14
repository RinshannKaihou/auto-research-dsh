# DSH backend for graph v0.1

`apps/dsh/worker.mjs` 是独立 headless SDK 进程。worker 与 coordinator 使用相同后端；每个进程实际 `chdir` 到自己的 workspace，既不借用全局 DSH 会话，也不改用户 profile。Python 核心拥有图、版本、调度和恢复；这个入口只执行一个请求。

## 启动与协议

```sh
node /absolute/path/to/apps/dsh/worker.mjs /absolute/request.json /absolute/output.json
```

需要本机已安装的 `@deepseek-ai/dsh`、Node.js 和 macOS `/usr/bin/sandbox-exec`。开发验证版本为 DSH `0.1.2-alpha.2`、Node `22.22.3`。脚本从 PATH 中的 dsh 解析已安装依赖；也可设置 `ARI_DSH_PACKAGE=/absolute/path/to/@deepseek-ai/dsh/package.json`。不会下载依赖。Linux/Windows 暂不支持；缺少内核沙箱时不会降级为无限制 shell。

请求字段：

```json
{
  "role": "worker",
  "prompt": "Perform one bounded work stage.",
  "output_schema": {"type": "object"},
  "workspace": "/project/workspaces/X-001",
  "read_roots": ["/project/workspaces/X-001", "/project/.research/views", "/project/.research/objects"],
  "write_roots": ["/project/workspaces/X-001"],
  "readonly_roots": ["/project/workspaces/X-001/inputs", "/project/workspaces/X-001/history"],
  "history_dir": "/project/.research/views",
  "timeout_s": 600
}
```

`write_roots` 必须恰好等于 workspace。`history_dir` 自动加入禁止写入范围。核心需要在 `read_roots` 显式加入允许查阅的历史、对象和用户资源。`readonly_roots` 中的输入应已由核心准备好；工具不能原地改它们。coordinator 也有独立 workspace，仅通过结果提交提案。

模型可调用 `read`、`write`、`run`、`submit_result`。`read` 可列出已授权目录、读取 UTF-8 文本，并通过本机 `pdftotext` 在沙箱内提取 PDF 正文；返回 `next_offset` 时可继续分页读取。PDF 无文本层时报告不可提取，不把二进制当正文。最后一个工具使用请求中的 JSON Schema 校验结构化答复；这避免把自然语言结尾误当成 JSON。兼容直接返回严格 JSON 的答复。模型仍需明确写入 workspace/progress.json；后端不会凭空编造研究进度。

成功时后端宿主原子写入指定 output.json（此路径可在核心的 jobs 目录）：

```json
{"result": {}, "usage": {"tokens": 1234}, "session_id": "ari-..."}
```

失败、取消或最终 schema 无效时退出非零；如果给出了有效的 output 路径，宿主仍原子写入失败包 `{"result":null,"usage":{"tokens":null},"session_id":null,"error":{"code":"..."}}`，其中 usage 与 session_id 会保留已经取得的值。该包不是成功结果。stdout 为简短 JSONL 生命周期日志，包括 ready、工具开始/结束、turn_end、completed/failed；不打印 adapter 配置、凭据或模型推理内容。turn_end 记录当时已知的 tokens，便于区分“结果失败”和“没有消耗”。核心不能把失败或缺失 usage 记为零。若 supervisor 必须 SIGKILL，宿主无法保证补写失败包，仍须按未知消耗处理。

## 模型与配置

从现有 `DSH_HOME`（默认 `~/.dsh`）加载 settings/credentials 和 default-model 选择，使用 DSH 的 DeepSeek / PiAi provider adapter。请求可带 `dsh_config`：目录表示另一个 DSH home；文件表示显式 settings.yaml/yml/json（凭据仍来自 DSH_HOME）。这不是 cordis 插件配置入口，不执行用户提供的插件代码。配置中必须有 agent-default-model 的 provider/model，缺失时失败，不使用隐藏模型 fallback。开发机现有选择是 `deepseek-official / deepseek-v4-flash / high`；不是入口强制替换的模型。不要把 API key 放进 request.json 或命令参数。宿主读取配置用于 API 请求，模型文件工具不能因此读取配置目录。

可选 `ARI_DSH_MAX_TOKENS` 设置单次模型响应上限，主要用于小型 smoke；它不是整个会话的 token 预算。整个请求由 timeout_s 限时，项目预算仍由核心控制。usage 优先使用 provider 的 totalTokens，否则汇总 DSH 互不重叠的 input/output/cache 计数；provider 不给计数时返回 null。

## 权限与取消

文件工具在每次调用中解析真实路径并检查边界，拒绝 `..`、指向外部的 symlink 和 dangling symlink。DSH 默认文件工具、bash 工具、skills、工作目录指令加载和后台 jobs 工具均未挂载。只有这里注册的工具可被模型调用。

任意 shell 代码均在 Seatbelt 内执行。可读取声明资源及系统运行库；仅 workspace 可写，inputs/history 的禁止写入规则更具体。不同于 DSH 通用 workspace-write 默认，这里不把整个 /tmp 视为可写区。为了启动 macOS 程序，允许全局文件元数据和根目录列举；未声明文件的内容仍禁止读取。系统运行库目录、安装的 Node/DSH 运行时可读；`/dev/null` 可写。模型不能读取图 DB/jobs 的内容。

shell 子进程不继承 API-key 环境、真实 HOME 或用户 shell 启动文件。HOME 与 TMPDIR 指向 workspace 私有目录。PATH 可用于找程序，但程序及依赖仍须在沙箱可读范围；需要额外科学运行时/数据时，由核心显式授权 read_roots。

工具子进程保留 supervisor 的进程组，不使用 detached。取消会先终止子树，等待一秒后强制结束仍存活的已知后代，确认工具退出才结束调用；整体 supervisor 负责最后的进程组清理。无模型测试已验证普通 shell/sleep 子进程取消不泄漏。v0.1 不支持自建常驻服务、主动 daemonize/setsid 或逃离 supervisor 管理的任务，也不把这套进程管理描述为对恶意进程的完整容器隔离。

session_id 用于关联日志。DSH 会话本身是内存中的一次执行，不提供 SDK session resume；重启后依据核心保存的 progress、产品快照与历史重新接手。

## 验证

无模型测试（可能需要在允许启动 Seatbelt 的本机 shell 中执行）：

```sh
node tests/dsh/smoke.mjs
node tests/dsh/headless.mjs
```

smoke 覆盖 SDK 启动、合法文件写入、历史/输入拒写、核心状态拒读、symlink/dangling/hardlink/rename 绕过、shell 内核边界、schema 拒绝和取消后的子进程退出。headless 覆盖参数失败包、显式 settings 选择、不修改配置、无默认模型时拒绝启动。夹具与 JSON 报告保存在打印出的 /tmp/ari-dsh-*。

下面命令会消费真实模型额度，默认不会执行：

```sh
node tests/dsh/smoke.mjs --live
node tests/dsh/smoke.mjs --live-worker
```

`--live` 启动两个独立进程，分别验证核心的 WORKER_SCHEMA 和 COORDINATOR_SCHEMA；`--live-worker` 只执行一个 worker。它们继承现有模型，各响应 maxTokens=2048，整体超时 180 秒。生成的 request/log/output 留在独立 /tmp 目录中。

2026-09-13 本机 M0 共执行了 3 个小型真实会话：

- 两个并行进程使用了不同的真实 cwd；coordinator 提交了合法 proposal，usage=801。
- 初次 worker 成功写入并读回 progress 与未完成产品，但纯 JSON final 解析失败。该次消耗未被旧日志保存，应标为 unknown，不能记为零。
- 加入 submit_result 后，最后一次 worker 成功提交核心 schema，保留 progress.json 和 status=partial 的 draft.txt；usage=4860。未继续增加真实调用。

无模型报告：`/tmp/ari-dsh-m0-VeKhnc/report.json` 与 `/tmp/ari-dsh-headless-IrGePX/report.json`。最初并行日志：`/tmp/ari-dsh-m0-OwTNJK/`。成功 worker 报告：`/tmp/ari-dsh-m0-5biuu5/report.json`。两份成功真实输出已在无模型检查中重新通过核心 schema 校验。这些是本机临时证据，长期保留应复制到项目验证记录中；仓库中的 smoke 脚本可重新生成证据。
