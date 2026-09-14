# experiment1 — 第一次实验(规格:../BRAINSTORM_V5_GRAPH.md §16;回归案例 §15)

所有生成物在 `/tmp/v5exp1/`(工作区、fixture、隐藏真值、X-4 输出),与 agent-research 目录隔离。本目录只放脚本。

## 主实验(§16):未完成工作的交接与中间产物的组合验证

```
python3 exp.py build            # 安装 mock A、seed 模板工作区、跑 R-002/R-003、写 X-2/X-3 产物、nodes.json、隐藏真值
python3 exp.py render           # nodes.json -> /tmp/v5exp1/packs/{text,struct}
python3 exp.py check            # 两组包的条目一致性(45 项)
python3 exp.py mkrun text 1     # 新工作区 /tmp/v5exp1/runs/text-1/ws + PROMPT.md(struct 同理)
# 用 Agent 工具派 X-4(model=opus),prompt = PROMPT.md 全文;把它最后的 JSON 存到 /tmp/v5exp1/runs/text-1/out.json
python3 exp.py score text 1     # events、代码、git、隐藏真值 -> score.json
python3 exp.py aggregate        # 汇总表
```

- fixture:X-1(假设 H 与方案 P,零 run)、X-2(build_samples.py:pad 可用、trim 未实现,零 run)、X-3(eval_and_record.py:可用但布局与 X-2 不一致,1 次 smoke)。代码在 `products/`。
- 隐藏真值 `/tmp/v5exp1/fixture/_hidden/truth.json`:R-002 收益 0.6618,其中长度 0.1035(15.6%),内容 0.5583;预期判断 partly_length。
- 两组差别只在 memory/nodes 下的呈现:text 组 report.md,struct 组 node.yaml + INDEX.md。代码、历史、依赖信息、未完成事项相同。

## 回归案例(§15):revises 场景,X-4 只读不跑 run

```
python3 regression.py build && python3 regression.py render && python3 regression.py check
python3 regression.py mkrun text 1 ; python3 regression.py mkrun struct 1
python3 regression.py score text 1
```

## 已知限制(本轮)

- X-4 经 Claude Code 的 Agent 工具派发(general-purpose,model=opus);自定义 agent 定义未被会话加载,工具调用上限无硬保证;两组共享 8 次 run 上限。
- effort 无法按子代理设置;父会话 effort=max,是否继承未文档化;X-4 自报模型名。
- 工作区外读取只能靠指令与自报(`out_of_workspace_access`),无工具日志;DSH 运行时有 toollog 与路径钩子。
