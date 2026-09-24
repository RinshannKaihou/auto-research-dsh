# 0.6.10 历史专家会话导航修复（2026-09-24）

现场：本机 3080 DSH web，self_evolving_stability_monitor。实际源码在 `/Users/ywang2397/work/agent-research/auto-research-init-v5`。

## 结论与上次验证遗漏

研究仍在运行。主会话 `session-61b79be8-faf1-4838-865a-c8ae8f9f5006` 的 wait 是等待已派发的 X-001、X-002；观察期间研究节点持续产生输出、派发后续专家。

上一轮“已修复”的判断不充分：访问父节点后，宿主缓存了子会话目录，掩盖了从主会话冷启动直接打开历史专家的错误。专家结束后仍有持久记录，但实时 runtime.sessions 不再包含它；前端仅依赖实时列表判断专家身份。此外，workbench.page 的 specialists 精简投影遗漏 parent_session_id，即使找到专家任务也无法构造完整地址。原生历史接口于是收到普通根会话请求，报 durable parent address。

Research 的另一故障来自直接以已释放专家作为 RPC 的 sessionId：宿主 agentFor 找不到它，报 The native session is not currently attached。

## 最终修复

- 导航同时使用持久会话/专家任务记录，补齐父目录后通过原生 openSubagent({parentSessionId, childSessionId, mode}) 打开。
- specialists 精简投影补回 parent_session_id。兼容已经运行的 0.6.10 Python 服务：字段缺失时，点击导航按需通过 reference.get 读取该专家任务详情。无需重启研究进程。
- 已知专家缺少地址或目录不可读时留在原页面并显示错误，不再降级为错误的根会话请求。
- 专家 Research 经已知研究主会话或直接父会话读取项目资料，显示只读提示；提供“返回父会话”“返回研究主会话”。读取失败仍保留父会话返回入口，不呈现错误的新建/关联引导。
- 专家页面禁用项目变更操作，并在调用层阻止写入；正常主会话操作保持原行为。
- 历史专家按任务状态显示已取消/已完成；原生仍在运行时优先显示实时状态。
- 保留前次修复：导航后切换 Chat、关闭工作台弹窗、轮询响应 runtime 变化、等待原生会话 store 注册。

## 验证

- 实际源码目录 Node 插件测试：89/89 通过；生成客户端一致性检查通过。
- Python 工作台测试：5/5 通过，包含通过真实存储 API 创建、绑定、结束专家后校验父地址投影。
- 用户 Edge 页面实测：刷新主会话、未访问父节点，直接打开已取消的 extract-rmetric（11653bc3-cd9c-4c2e-b45a-9e1ee8c0849c），显示完整历史而非 durable parent address 错误。
- 同一结束专家 Research 能显示所属项目概览与返回入口；返回父节点后 Chat 正常。子会话页面刷新后仍能恢复父地址并读取 Research；返回研究主会话也已验证。
- 最终将用户页面留在研究主会话 Chat。研究进程未重启，目标和账本没有手动改写。

## 交付范围

修改同步到实际源码及当前 web profile 安装目录。运行中的旧 Python 服务通过详情接口兼容；投影字段更新会在下一次正常启动时加载。此补丁未更新版本号、未重打原始 0.6.10 tgz；重装原始包会覆盖安装目录中的补丁。源码保留修复，可用于后续正式构建。

备份：`/tmp/dsh-0610-navigation-backup`。回归日志：`/tmp/dsh-nav-v3-tests.log`。可审阅差异：当前任务工作区 `docs/dsh-0.6.10-session-navigation.patch`。

限制：若父会话和主会话也都无法读取，Research 会显示读取错误及已知的返回入口；本次没有新增脱离所有宿主会话的后端离线项目读取能力。
