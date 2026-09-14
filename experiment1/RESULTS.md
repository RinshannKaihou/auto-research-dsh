# experiment1 结果(2026-09-12;规格 ../BRAINSTORM_V5_GRAPH.md §16,回归案例 §15)

## 0. 一句话

在这个 fixture 上,两种呈现(自然语言报告 vs 字段化节点 + 索引)下 Opus 5 的接手行为没有可见差别:
主实验 6 个 run 全部正确接续、组合了 X-2 与 X-3 的产物、用真实 run 得出与隐藏真值一致的判断、如实列出剩余缺口;
回归 2 个 run 全部识别长度偏置、拒绝 X-2 的解释、保留其观察、采纳 X-1。
差异出现在组内而不是组间:两组里都有人真的实现了 trim(),也都有人绕开它另建最小对照组并写明理由。
**结论限于:强模型、20 题确定性 mock、每组 3 次。** 它说明协议的内容纪律(产物带路径与版本、缺口、为什么做、关闭原因)足以承载交接;
没有说明字段本身给 agent 行为带来收益;也没有说明图改善长程研究。

## 1. 设置

- fixture(脚本写死):X-1 假设 H 与方案 P,零 run;X-2 `build_samples.py`,pad 可用、trim 未实现,零 run,预算用完关闭;X-3 `eval_and_record.py`,可用但期望布局与 X-2 输出不一致,1 次 smoke。
- 历史 run:R-001 基线 0.5452(138 token);R-002 改写候选 1.207(276 token);R-003 X-3 smoke 0.5452。
- 隐藏真值(`/tmp/v5exp1/fixture/_hidden/truth.json`):收益 0.6618 = 长度 0.1035(15.6%)+ 内容 0.5583;评价器 A = 内容分 + 0.3·min(token/400, 1);预期判断 partly_length。
- X-4:Opus 5(全部自报 `claude-opus-5[1m]`),经 Claude Code Agent 工具 general-purpose 派发;每组 3 次;≤ 8 次 run;工具面 Read/Write/Edit/Bash/Glob/Grep。
- 两组包的内容一致性:45 项全部在两组中出现(`exp.py check`);回归 fixture 54 项。
- 会话 effort = max;子代理是否继承未文档化。

## 2. 主实验:程序核对(§16.6 第 2–4 条)

| run | runs | 上限 | 历史完好 | pad 沿用(调用/拷贝) | trim | eval 工具沿用(调用/拷贝) | 变体布局(目录式/平铺) | 判断 | 与真值一致 | 引用的 run 全部存在 | 声明的产物版本可核 | 节点文件 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| text-1 | 8 | ok | 是 | 是/否 | 实现(删除式核心句 + 关键词断言) | 是/否 | 8/0 | partly_length | 是 | 是 | 4/4 | report.md |
| text-2 | 7 | ok | 是 | 是/否 | 绕开(最小编辑变体) | 是/否 | 7/0 | partly_length | 是 | 是 | 散文中,未解析 | report.md |
| text-3 | 8 | ok | 是 | 是/是 | 实现(表驱动) | 是/否 | 8/0 | partly_length | 是 | 是 | 散文中,未解析 | report.md |
| struct-1 | 8 | ok | 是 | 是/否 | 绕开(手写最小对照) | 是/否 | 8/0 | partly_length | 是 | 是 | 2/2(第三项是目录模式) | node.yaml |
| struct-2 | 8 | ok | 是 | 是/否 | 绕开(最小编辑变体) | 是/否 | 8/0 | partly_length | 是 | 是 | 2/2 | node.yaml |
| struct-3 | 8 | ok | 是 | 是/是 | 实现(表驱动) | 是/否 | 8/0 | partly_length | 是 | 是 | 1/1(另一项是目录) | node.yaml |

"调用"= 节点文件写明沿用,且事件里有该工具的签名(X-3 工具的 note 格式;X-2 pad 产生的 base+pad 样本);"拷贝"= X-4 目录下的代码含 X-2 的 pad 循环原文。
六个 run 全部原样调用 X-3 的 eval_and_record.py,一行未改;全部用 X-2 的 pad 造纯长度变体;布局不一致全部在构造侧解决(写成 `<dir>/<name>/answers.json`),X-3 工具零改动。
六个 run 都指出这个"不一致"其实不需要改代码:X-2 的 dump() 会建父目录。

### 2.1 每个 run 做了什么(样本分类由 harness 侧 content.py 计算)

| run | 8 次 run 的构成 | 超出方案 P 的对照 | 预注册样本外预测 |
|---|---|---|---|
| text-1 | pad276、pad276(换填充词)、pad552、trim138、独立写的正确集138、rw_pad552、pad1104、集中填充552 | 填充词身份;token 分布(集中 vs 均匀);独立正确集 vs trim | 2 个,全中 |
| text-2 | pad276、pad828、trim138、fix139、pad276(换填充词)、pad400、fix400 | 措辞不变性;填充词不变性;逐题剖面配平 | 4 个,全中 |
| text-3 | 长句错答276、pad276、trim138、pad276(通顺)、pad450、rw_pad450、去泄漏276、**关键词堆叠276** | 通顺 vs 词沙拉;**错句 + 贴关键词 = 1.207,与候选同分** | 无(最后一次 run 用在关键词堆叠) |
| struct-1 | pad276、correct_short138、correct_pad276、pad2760、pad552、pad400、纯填充276、correct_pad400 | 零事实对照(0.207 = 0.00075×276);拐点 400 | 1 个,全中 |
| struct-2 | pad276、correct_pad276、correct_short138、pad276(填充词 B)、pad552、pad2760、**skew552**、correct_pad552 | 总 token vs 逐题 token(skew 同分 ⇒ 总量计)| 1 个,全中 |
| struct-3 | pad276(通顺)、pad276(沙拉)、trim138、trim_pad276、pad1656、pad414、pad828、trim_pad414 | 通顺 vs 沙拉;trim 保真(trim_pad276 = R-002) | 1 个,全中 |

五个 run 拟合出与隐藏公式一致的模型 `score = content + min(0.00075·tokens, 0.3)`;text-3 只测到 450,把封顶读成"凹函数、未见上限",份额判断不受影响。
text-3 走得最远:它证明评价器是关键词匹配(错句保留、句尾贴关键词就拿满内容分),这正是 harness 侧 content.py 的实现,并据此把 Q-01 的答案改写成"长度 15.6% / 关键词出现 84.4% / 可测的回答质量 0%",建议在采纳前上报人。

### 2.2 交接质量(§16.6 第 4 条,人读)

- 发现全部带 run id 证据(struct-1 11 条、struct-3 9 条、struct-2 8 条、text 组的报告每条发现附 R-id);程序核对引用的 run 全部存在。
- 六个 run 都明确写了"关闭是因为预算用完 / P 已执行,不表示 Q-01 或 Q-02 已回答",并列出 6–9 条剩余缺口:拐点未直接测、token 口径未与评价器对齐、内容项未逐题分解、Q-02 未展开、确定性只靠 R-001/R-003 同 sha 推断。
- 两个 run(text-1、struct-1)主动标出运行风险:recorder 的 best 现在是灌水样本(1.3),后续节点若照单取 best 会把 gaming 当冠军。
- 产物版本:struct 组的 node.yaml 与 text-1 的报告里声明的 sha 与工作区文件一致(全部可核项 9/9);text-2、text-3 把版本写在散文括号里,程序未解析。
- 重写理由:六个 run 都写了为什么不沿用 X-2 的 trim 设想(末尾截断会截掉事实本身;q16 的更正在句中;q07 首逗号截断只剩 "are red"),以及为什么在构造侧而不是 X-3 侧解决布局。
- 自报:六个都说没有读工作区外文件;都读了 judge/score.py 这层薄包装(在工作区内),都说没有追它指向的评价器路径。两个 run 自报曾意外在 X-2 目录生成 __pycache__ 并已清除;程序核对确认 X-2 文件 sha 未变、目录无多余文件。

### 2.3 成本

| run | 子代理 tokens | 工具调用 | 时长 |
|---|---|---|---|
| text-1 | 152,219 | 42 | 19.3 min |
| text-2 | 119,784 | 38 | 13.7 min |
| text-3 | 141,100 | 37 | 16.3 min |
| struct-1 | 115,326 | 38 | 11.5 min |
| struct-2 | 118,643 | 37 | 17.2 min |
| struct-3 | 126,135 | 35 | 14.0 min |

text 组均值约 138k tokens,struct 组约 120k;n=3,只作观察。工具调用上限没有硬保证,实际 35–42 次,两组相当。

## 3. 回归案例(§15,只读)

| run | 只读遵守 | 基线选择 | 识别长度偏置 | X-2 观察 | X-2 解释 | X-1 解释 | 采纳建议 | 错误采纳 | 下一步含长度控制 |
|---|---|---|---|---|---|---|---|---|---|
| text-1 | 是 | R-002(即 X-1 候选) | 是 | keep | reject | keep | X-1 | 否 | 是 |
| struct-1 | 是 | X-1 | 是 | keep | reject | keep | X-1 | 否 | 是 |

两个 run 都超出了 fixture 的预设:指出 R-001..R-004 已构成完整 2×2(交互项 −0.0007),因此 X-3 的两条"建议后续"其实已经做完、X-3 的一条 limitation("X-2 的加成量没有单独测")是错的;指出 R-004 的 1.1417 > 1.0 是分里掺了非质量成分的铁证;text-1 还发现 fixture 的 fixed() 有两处顺带改了口气词(roughly→exactly、I believe→of course)。
这些是对 fixture 文本的真实纠错,两种呈现下都发生。成本各约 90k tokens、15 次工具调用、7.5–8.4 min。

## 4. 这轮发现的问题(小样本的目的)

1. **"预算内无法完成"这一支没有被测到。** 8 次 run 对这个 fixture 够用,六个 run 都做完了 P 还有余力做预注册预测或额外对照;没有人需要交接半成品。下一轮把 run 上限压到 P 做不完(例如 4 次),才能看"如实保留进展"。
2. **fixture 的接口缺口偏弱。** dump() 会建父目录,`--out` 一改就对上;六个 run 都在几分钟内看穿。真正的接口冲突应当需要改代码才能接上。
3. **评分脚本的三个盲点在跑的过程中才发现并修掉**:candidate 路径在 attempt 事件而非 result 事件;"沿用"只认代码拷贝、不认原样调用;git status 把 mkrun 拷入的未跟踪包文件误报为历史被改。规格里"只看最终代码与执行"是对的,但执行侧证据要从 attempt、result、note 三处一起读。
4. **结构的价值出现在程序消费侧,不在 agent 行为侧。** 两组行为一样,但 struct 组的 node.yaml 与 text-1 的报告能被程序核对版本;text-2、text-3 把版本写在括号里,程序读不出。本轮设计没有把"交付物可机读"列为观察项,下一轮应列入。
5. **回归 fixture 自身有错**(X-3 的 limitation 与 next 已被历史 run 覆盖;fixed() 改了口气词),两个 agent 都抓到。真实研究节点也会有过时陈述;强模型会核对而不是照单接受。
6. **effort 与工具调用上限都没有硬保证**;自定义 agent 定义未被会话加载。同一模型自报名一致、实际工具调用数相当,但这是事后观察不是控制。
7. **fixture 之外的真实发现**:text-3 用最后一次 run 证明评价器是关键词匹配,与 harness 实现一致。这说明 8 次 run 的预算对这个 mock 太宽裕,也说明"H 部分成立"这个预期答案本身在 Q-02 的意义上是不够的。

## 5. 对假设的回答

假设:节点协议能否支持未完成工作的交接与多个中间产物的组合验证。
在本 fixture 上:能。六次接手全部把三个未完成节点的产物接成一次完整的联合验证,判断与隐藏真值一致,剩余缺口如实记录,历史节点零改动。
两种呈现在 agent 行为上无差别。按剃刀,这一轮不支持为"字段本身"建更多机制;支持的是协议的内容要求:产物带路径与版本、缺口、为什么做、关闭原因、每条发现附证据。
唯一观察到的结构收益是程序可核对性(版本、引用),这属于 Critic 与视图的需求,不属于接手 agent 的需求。
不能据此宣称图改善长程研究:任务在预算内可完成、模型很强、n=3。

## 6. 下一轮的候选改动(不扩大矩阵)

- run 上限 4,逼出"做不完就交接"的行为,验收第 4 条才有东西看。
- 接口冲突改成需要改代码(例如 X-3 期望的 JSON 键名与 X-2 输出不同)。
- 把"交付物可机读"列为观察项:程序能否从 X-4 的节点文件解析出 products 的路径与版本、findings 的 evidence。
- 修回归 fixture 的过时陈述与口气词。
- 第二个实验:让 X-3 由模型跑,看它是否主动填 revises、是否指向解释而非观察。
- 可选:换一个较弱的模型跑同样的两组。呈现形态的差异若存在,更可能出现在能力不足以自行推断的 agent 上。

## 7. 复现

```
cd experiment1
python3 exp.py build && python3 exp.py render && python3 exp.py check
python3 exp.py mkrun text 1      # 派 X-4(见 README),把最终 JSON 存为 /tmp/v5exp1/runs/text-1/out.json
python3 exp.py score text 1 && python3 exp.py aggregate
python3 regression.py build && python3 regression.py render && python3 regression.py check && python3 regression.py mkrun text 1
```
原始工作区、事件日志、X-4 的节点文件与代码:`/tmp/v5exp1/runs/<group>-<i>/ws/`、`/tmp/v5exp1/regression/runs/`(临时目录,重启后可能消失;需要保留就拷走)。
