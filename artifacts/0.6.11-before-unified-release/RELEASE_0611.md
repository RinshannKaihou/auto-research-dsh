# auto-research-v5 0.6.11 · 成果展示与会话导航修复

发布日期：2026-09-24。schema 保持 9；此版本合入 0.6.10 之后的会话导航与成果展示补丁。

## 交付

- 离线包：`apps/dsh/plugin/auto-research-v5-0.6.11.tgz`
- SHA-256：`713db6e8597a5de213249603e2cfe75a95a368dc7859d9224e3b32101acbcd83`
- 校验文件：`apps/dsh/plugin/auto-research-v5-0.6.11.tgz.sha256`
- 测试日志与包清单：`docs/validation/0611/`

## 主要修复

- 项目会话将专家嵌套到所属节点，默认折叠；显示任务编号、任务标题和状态计数。详见 [项目会话分组](SESSION_GROUPS_0611.md)。

- 历史专家通过持久化父地址打开，避免空白聊天页与 durable parent address 错误。已结束专家的 Research 经父/主会话读取项目，显示只读状态并保留返回入口。
- 成果概览、节点详情和材料列表使用统一卡片：短标题、四行普通正文预览、可展开的完整 Markdown 和原文入口。长标题按段落显示，来源与文件路径折叠，报告可直接阅读。
- `research_publish` 支持可选 `display`（title、overview、sections、primary_item_id），由现有研究 agent 发布时一并生成，不新增模型调用。旧调用兼容，历史原文不改写。
- 展示元数据以 `publication-display/v1` 专用 item 绑定本次成果，复用已有 JSON 存储。普通附件隐藏元数据项；无效展示信息回退旧记录样式。
- 新增只读 `reference.chunk`，保证超长发布说明与知识详情读取完整原文；报告入口只依据已登记附件，交付完成与科学验证继续区分。

详细实现及此前真实浏览器验收见 [成果展示修复记录](PUBLICATION_DISPLAY_0610_FIX.md) 和 [专家会话修复记录](SESSION_NAVIGATION_0610_FIX.md)。这两份文档保留当时的补丁交付历史，当前正式交付以本说明为准。

## 验证

- 本版重新运行 Node 回归：109 项通过；Python 相关回归：27 项通过。
- 客户端构建一致性检查通过；包内所有文件与构建源码逐字节一致；Python 源码及包内模块一致，Python/插件版本均为 0.6.11。
- 功能代码沿用上一轮已验收的修复：真实 P-001 展开/原文、DIGEST 阅读与关闭返回、390px/600px/桌面布局、键盘交互、专家聊天及返回主会话。后续会话分组改动重新验收了真实项目的折叠、任务标题和专家打开入口，未干预研究执行。

## 安装与生效

```bash
(cd apps/dsh/plugin && shasum -a 256 -c auto-research-v5-0.6.11.tgz.sha256)
dsh plugin --profile web add apps/dsh/plugin/auto-research-v5-0.6.11.tgz --offline
```

本机已同步实际源码及 web profile 的插件文件。未重启当前研究进程，未注入指令或修改研究账本。前端刷新后加载；运行中已缓存的版本信息、工具定义和角色提示随下次正常加载服务及新建研究会话更新。旧服务尚未加载分块 RPC 时，超过单次读取上限的说明会明确提示无法读取全文。

保留原始 0.6.10 安装包及独立 display-fix 包。本次无新增数据库迁移；需要回退时可在正常停止服务后使用保留包，原始 0.6.10 不含这两组修复。本次文件替换前备份：`/tmp/dsh-release-0611/before-release`。
