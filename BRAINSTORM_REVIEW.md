# 对 Fable 长篇 brainstorm 的审阅

2026-09-12。范围：全文 1,019 行、experiment1 的结果和评分代码、留存的六份 score.json、experiment2 计划，以及旧 v4.2 / v5 的相关契约和代码。未重新运行模型实验，未独立复现每个科学判断。

## 判断

这篇文章有设计历史价值，但不适合直接作为开发规格。值得继承的是少量核心决定、边界修正和失败教训；已经撤回的规则、实验选题过程及多轮“冻结”不应继续成为开发者或 agent 的必读负担。

不能用一个“有用百分比”准确概括：讨论错误再撤回，本身可以提供决策理由；实现时却必须明确只剩下哪些约定。处理方法是保留原文，另立短协议与实施计划。

## 最有价值的四部分

1. **把研究的用途关系写出来。** `question / why_now / inputs.ref + use` 能记录本次为什么值得做、用了什么和怎么用，比单纯保存“下一轮计划”更适合作为调度和交接输入。它提供改善研究的条件，尚不等于已经解决长程漂移。[§14.3，第 615 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/BRAINSTORM_V5_GRAPH.md:615)
2. **节点可以结束于半成品。** findings 可空，products 可 partial，关闭只代表本段工作停止。这个修正直接回应了复杂研究不能每段都交出可测试结论的问题，是最应该进入 v0.1 的部分。[§16.2，第 872 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/BRAINSTORM_V5_GRAPH.md:872)
3. **分清程序核对与科学判断。** 撤回自动判矛盾、用祖先交集判独立、同父同问自动合并、停止前先证明不可达等规则，避免把主观判断伪装成系统保证。[§14.1，第 585 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/BRAINSTORM_V5_GRAPH.md:585)
4. **留下了具体工程教训。** SQLite 应为元数据权威源；worktree 才能隔离同时改代码的执行；文件与目录的版本不能混用；访问过、实际使用、解释成立是三件不同的事。这些比早期庞大的边类型词表更有实施价值。[§14.4，第 671 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/BRAINSTORM_V5_GRAPH.md:671)、[§18.5，第 1009 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/BRAINSTORM_V5_GRAPH.md:1009)

## 不能原样继承的内容

| 问题 | 证据 | 实施处置 |
|---|---|---|
| 当前有效版本不唯一 | 顶部第 3 行以 §14 为基础，却遗漏 §15 的关键修正；第 609 行仍要求首次 run，第 706 行才允许零 run | 单独整理当前协议，不让运行时自行解释章节优先级 |
| 修订语义仍矛盾 | 第 649 行自动标灰，第 703 行要求只标注后续；第 701 行又禁止修订观察类 finding | 保留旧记录，允许对具体 finding 提出修订，不自动宣布哪一方正确 |
| 证据仍被实验账本绑住 | 第 689 行限定 evidence 指向 recorder；第 659 行要求 finding 有依据本身是合理的 | 扩展为固定材料引用，支持推导、文献、证明检查等；存在性检查不叫科学验证 |
| 工业优化假设未清理 | §13 保留排除证明目标、围绕采纳决策和 bake-off 的旧范围 | 不进入新协议；初始化不要求分数、冠军、固定 evaluator |
| 对旧框架的诊断有夸大 | 第 22 行称 fresh-eyes 的 Explorer 不读历史；实际旧编排要求读取 TARGET、STATE、recent 和 audit | 问题是结构关系主要靠散文维持，不是旧系统完全没有历史访问 |
| 把呈现测试当作建系统的门槛 | 第 935 行说两组无差别便不必建结构，但第 934 行明确未测调度、汇聚和关系自主填写 | 不用局部呈现对照否定运行时需求；先按完整生命周期验收 |

旧框架历史读取要求见 [ORCHESTRATOR 模板，第 24 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v42/assets/ORCHESTRATOR.md.template:24)。单调递增的任务编号也不是不能并行的原因，明确的串行保护和共享执行状态才是。

另外，`modifies_artifact` 可以保留为范围许可，但必须在真实后端中实现保护。元数据里写了 false、事后文件哈希未变，都不能证明执行者当时无法越界访问。

## 已做实验到底证明了多少

留存的六份主实验 score.json 与结果表一致：五次 8 个 run、一次 7 个 run，均报告 partly_length，引用检查和历史差异检查通过。能据此确认的是报告汇总与这些评分产物相符；不是重新独立证明评分器本身没有遗漏。

**最稳妥的结论：在一个小型、预制历史的任务上，两种呈现都让新 agent 成功接续已有材料，未观察到呈现带来的行为差别。** 这是可行性样例，不是无价值的工作。[实验结果，第 5 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/RESULTS.md:5)

它没有回答五个关键问题：

- 前面的节点由 agent 自主完成时，是否会主动留下足够材料和准确关系。
- 接手者再次做不完时，能否把半成品交给下一位继续。
- 多条分支如何持续派发、读取彼此已发布的工作，再形成后续节点。
- 程序中断后能否恢复执行、版本和预算。
- 经过较长研究后，是否减少遗忘、错误沿用和重复探索。

前置 X-1 至 X-3 由脚本准备；六次调用是独立试验工作区，不是同一张研究图内持续协作的分支。结果报告也承认没有测到接手者交出半成品。[设置，第 14 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/RESULTS.md:14)、[局限，第 84 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/RESULTS.md:84)

“成功来自内容要求，不来自字段语法”和“结构收益只属于 Critic 与视图”都说得过满。两组都具有这些内容，实验没有单独检验它们的因果贡献；程序是否容易解析也会影响后续派发与接手。评分脚本只接受几种特定版本写法，未解析两份散文不能推出散文不可被程序消费。[结果解释，第 94 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/RESULTS.md:94)、[评分器，第 578 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/exp.py:578)

实验的运行纪律也只能按实际证据表述：模型与 effort 部分靠自报，工具上限没有硬保证，工作区外读取没有工具日志。因此不能把这轮实验当成权限、预算或后端控制已经验证的证据。[README，第 29 行](/Users/ywang2397/work/agent-research/auto-research-init-v5/experiment1/README.md:29)

还有一个小的汇总错误：长文第 956 行写“trim 实现与绕开，两组各占一半”，实际文本组是 2/1、结构组是 1/2；每组三次不可能各半。它不改变实验的主要结论，但说明应引用结果表，不照搬叙述。

## 与现有代码核对后的影响

不能把长文设想误当成已有实现：

- 旧 v5 的契约本已允许一个任务零到多个 run，因此“零实验合法”并非从零发明；但运行时仍有按旧任务类型限制产出的门槛。新版本要让协议与实际执行一致。[旧 CONTRACT，第 52 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5/docs/CONTRACT.md:52)
- 旧 recorder 明确拒绝同时 open 多个任务，旧 engine 只持有一个当前任务。并行需要改造状态与工作区，不能靠添加 `inputs` 实现。[recorder，第 618 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5/scripts/run_record.py:618)、[engine，第 279 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5/lib/engine.js:279)
- 把所有未关闭任务当作 stale 并关闭，不适合存在仍在运行分支的恢复场景。新的恢复必须核实执行尝试，保留材料和预算。[recorder，第 407 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5/scripts/run_record.py:407)、[engine，第 206 行](/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5/lib/engine.js:206)
- 旧工具日志和权限经验可以借鉴，但换执行路径、依赖版本或工作区模型后需重新验证，不能沿用“已 enforced”的结论。

因此，重写的是运行时核心与旧研究流程假设；归档、摘要、输入校验和已发现的失败案例值得保留。

还有一个长文没有落实的工程前提：当前安装的 DSH 子代理从父会话继承 workspace，并没有逐次 start 的 cwd 参数。worktree 已创建不等于 worker 的工具已经运行在该 worktree；这需要在最初的后端验证中解决。[安装接口，第 92 行](/Users/ywang2397/.hermes/node/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-subagent/lib/types/types.d.ts:92)

## 文档处理建议

| 原章节 | 用途 |
|---|---|
| §0、§9 | 问题背景；修正不准确的因果和事实说法后引用 |
| §1–§13 | 设计历史和被否决方案；不作为实现要求 |
| §14 + §15.1 + §16.2 | 提炼当前最小协议；合并矛盾并拓宽理论研究支持 |
| §15–§17 的实验部分 | 回归材料与实验记录；不作为采用图运行时的前置门槛 |
| §18 | 保留选题失败和历史材料引用的经验；不继续执行被用户否决的研究 |

接下来执行 [V0_1_PLAN.md](/Users/ywang2397/work/agent-research/auto-research-init-v5/V0_1_PLAN.md)。不再向原文追加一章新的“冻结版本”，也不继续制造一轮恰好做不完的实验。
