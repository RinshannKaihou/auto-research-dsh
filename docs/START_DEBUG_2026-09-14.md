# 开始研究无反馈：故障与修复

用户课题 Gaussian-based Debiasing Derivations，2026-09-14 本地时间。

初次执行 A-001 已启动 volce / qwen3.8-max；仅有 coordinator、没有节点。用户所选材料目录出现在 request.read_roots，但 Runtime.context 删除 project.config 时连资源路径也从模型提示中移除了。模型无法定位材料，发生 ENOENT / READ_DENIED / EISDIR。原 read 工具仅支持 UTF-8 文件，不支持目录和 PDF；用户目录实际上只有一份 PDF。页面只显示空研究图和活动计数，缺少首轮规划进度。

为修复而停止 A-001，状态 interrupted，已记录实际用量 13070 tokens、无剩余预占；没有抹掉失败执行或更改用户预算/provider。首轮没有产生节点或研究产物。

修复将明确的 resources 放入模型上下文，同时继续排除 provider/凭据配置；read 增加目录、PDF 提取和文本 offset 分页；工作台展示当前执行、角色、provider/model、耗时及白名单事件，不向页面传输原始日志或工具参数。

验证：199 项 Python 回归；工作台 15 项 Chromium 检查（含无节点时的规划提示）；DSH 原有 21 项无模型检查；新增材料脚本 8 项检查。实际源 PDF 在原资源边界中成功提取 126125 字符，原文件未改。自动测试均为零模型调用；修复后接续用户原已授权研究，后续调用与结果以课题账本为准。
