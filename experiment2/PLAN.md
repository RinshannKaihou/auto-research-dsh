# 试用 2 计划 — 真实交接:msprobe noProb 线 → 32B 误报的规则级归因

> **状态(2026-09-12):选题被用户否决,本计划不执行。** 理由:本机不能完全重跑这条线;任务只有一条线、一个问题、一种运行,过于线性,
> 适合 v4.2 的轮循环;图框架要用在分叉与汇聚真实存在的研究上(准则见设计文档 §18.7)。§2 的三个节点(`template/memory/nodes/`)
> 与 §6 的协议记录保留,作为"把真实历史写成节点"的样例;`template/`、`_precheck/` 各 28 MB,可删。

一句话:把一条真实跑过 25 轮、已停在人工 steer 封口处的检测器演化线,用 v5 最小协议存成三个已关闭节点,
让一个全新的 Opus 5 agent 接手它的下一步(32B 误报按规则归因),只观察交接是否可靠。不设对照组,不压预算,
不造障碍;图是否改善长程效率不在本次判定范围内。

## 1. 选题依据

盘点了本机的真实研究材料(2026-09-12):

| 材料 | 状态 | 能否作为本次交接点 |
|---|---|---|
| `~/work/inference-monitoring/self_evolving_monitor/monitor_evolution_rf18_opencode+dsv4f_noProb` | 25 轮全 ok,git 干净(HEAD 0c902d7,Critic 审计已提交),STATE.md 498 行,每轮一行 32B 聚合验证 | **选用**。自然停点:人 steer 的 Round A/B 封口后"停,呈报人审阅";32B 验证数字只在 digest 呈给过人,从未被解释;下一步未定 |
| 同目录 `token_traj_detector/` | 8 月 13 日写的独立端口,9 月 3 日补 README;没在任何 bench 上跑过 | **并入**为 X-3(Q-02),供接手者选用;缺 R-024/R-025 两条规则,等价性未验 |
| `auto-research-init-v4.2-dsh-plugin/monitor_evolution_rf18_0817data_init` | 六格多语料 campaign 建好、烟测过、E-003 已 seal,正式轮零 | 数据在 `/workspace/...`(远端 Linux),本机没有 wc/30b 语料 → 本机跑不动。它是这条线真正的下一站,本次 Q-01 就是它起始候选问题的本机可做部分 |
| `~/work/inference-monitoring/hidden_states_detection` | 套件跑完、report.md 已写、8 月转立项材料 | 结果已解释,且依赖 `/Volumes/T7` 上的模型与基线;不是交接点 |
| `~/work/inference-monitoring/canary_padding_token` | 立项阶段(9 月 10 日),XPU 适配文档 | 实验需 Ascend/XPU,本机不可跑 |
| `~/work/find_inference_monitor` | 6 月,纯合成/理论,77 格全满后"blocker — stalled" | 可跑但是理论研究,且是饱和终态而非交接;不选 |

选用理由对照用户给的四种自然交接形态:X-2 是"实验已完成但结果尚未解释",X-3 是"原型已实现但实验未跑",
X-1 的 R-025 空桶 + 32B 上 +10 检出是"旧假设受到质疑而后续方案未定"的雏形。

## 2. 交接点与可核实的中间状态

原仓库不动。`template/campaign/` 是它的 rsync 副本(含 .git、runs/artifacts 12 MB、runs/validation.jsonl);
`template/token_traj_detector/` 是端口副本。预检(`_precheck/`,另一份副本,不进交接)结果:

| 核对项 | 结果 |
|---|---|
| `tools/run_record.py digest` | rounds=25, ok=25, champion R-023 0.705882, epoch E-010 未过期, events_ok |
| `tools/run_record.py replay --champion` | R-023 重放 delta 0.0,consistent |
| 产物树指纹 `sha_tree(monitor/msprobe_response_anomaly)` | f087bd175436 = R-025 attempt 的 artifact_sha |
| `judge/validate_32b.py --ref R-025` 重跑 | val_tpr 0.443902(273/615)、val_fpr 0.008708(37/4249),与账本第 25 行逐字相同;real 136 s |

三个已关闭节点(`template/memory/nodes/X-1..X-3/node.yaml`,由 `build.py` 从 `nodes_spec()` 渲染,`build.py check` 核对
产物版本与证据可解析):

- **X-1**(Q-01,closed,25 个 run):campaign 本身。产物 detector(f087bd175436)、R-025 样本(3a87f3ed8d41)、
  recorder 账本、STATE/rounds/audit。5 条发现全部带 R-id 证据(8B 前沿 636/901;17 条规则两族;三条 repetition 规则贴 clean 边界−1;
  R-025 在 8B 空桶;R-024 在 8B 零行为变化)。limitations 里如实写:champion 标签偏差(recorder R-023 vs STATE R-025)、
  离线 judge-loop 脚本已丢失、不输出规则名、GT 双胞胎冲突。
- **X-2**(Q-01,closed,零 recorder run):32B 逐轮聚合验证。产物 validation.jsonl(0beb19bf2b8d,被 .gitignore、只有盘上副本)、
  validate_32b.py。4 条发现只写观察不写解释:HEAD 37 FP;按轮增量(R-010 +10、R-011 +3、R-015 +6、R-016 +1、R-017 +2、
  R-020 +17、R-021 +7、R-024 −10;repetition 族 0 新增);R-025 空桶在 32B +10 TP、FP 不变;R-024 cap 在 32B −10 FP/−1 TP。
  conditions 写明"增量归因依赖单因子与固定优先级,不等于 HEAD 上删掉该规则的效果"。
- **X-3**(Q-02,closed,零 run、空 findings):端口。metric.py(10404e10973d)状态 partial,缺口五条(未跑过 bench、缺 R-024/R-025、
  类别判定路径不同、R-010 容错未核对、来源轮次未记录)。

## 3. 接手者得到什么

工作区 = `template/` 的整份复制(`build.py mkrun N` → `runs/x4-N/ws/`),PROMPT.md 是它读到的唯一指令(`build.py` 的 `prompt()`)。

- 图:RESEARCH.md(数据路径、权限、运行机制、证据记法)、agenda.md(Q-01/Q-02)、INDEX.md(程序渲染)、三个 node.yaml。
- 原始材料:campaign/ 全部(可 git log/show,不可 commit)、token_traj_detector/。
- 数据(仓库外,只读):8B bench jsonl 可读,`*.logprobs.pkl` 禁读;32B 目录禁止直接读,只能经 validate_32b.py 取聚合数。
- 运行机制:沿用 recorder(8B,约 220 s)与 validate_32b(32B,约 136 s),候选包放 scratch/x4/。不新增任何机制。
- 模型与预算:Opus 5(Agent 工具 general-purpose,model=opus),正常预算,不设 run 上限;要求它先在 plan 里写预算再做;
  做不完就如实关闭。预计每次 1–3 小时墙钟(以 validate 次数为主),token 约 20–40 万。
- 运行次数:同一材料跑 2 次(区分系统性问题与偶然),可并行(11 核,两条 validate 互不干扰;validate 只追加各自副本的账本)。

## 4. 观察什么(交接问题清单)

程序件:`build.py trace N`(X-4 的 evidence 是否都能在两账本里找到、产物版本是否与盘上一致)、`build.py diff N`
(允许写的路径之外有无改动)。其余人工逐条核对,结合代码、账本与最终行为,不把"读过"算复用,不把有理由的改写算失败。

| # | 观察项 | 看什么 |
|---|---|---|
| A | 用对版本 | 基线是否是 f087bd175436(而非按 STATE 的说法另找 champion);候选包是否各有 artifact_sha;是否发现并正确处理 champion 标签偏差(R-023 = R-024 = R-025 同判定集) |
| B | 保留未决条件 | 发现里是否带 conditions:单因子增量 ≠ HEAD 消融;规则优先级短路造成的归因重叠;32B v2 fault 不完整、GT 独立标注;本地 32B 集 ≠ 0817 的 wb32b 格;0.1% 预算 = ≤ 4/4249 |
| C | 利用已有工作 | 是否直接用 X-2 的 25 行做首轮归因而不是重跑;是否用 config 开关做消融而不是改代码;是否原样用 validate_32b.py(改它要有理由且保持聚合 only);是否用 R-025 样本做 8B 基线而不重跑;若用 X-3 的 reason 字段,是否先验等价并补两条规则 |
| D | 识别真实缺口 | 是否指出归因的重叠/优先级问题;是否指出无法区分 32B 误报与 32B GT 漏标;是否指出离线脚本已丢失并重建最小替代;是否指出端口缺 R-024/R-025 |
| E | 未完成时可继续 | node.yaml 按协议成形;产物路径+版本可核;evidence 可解析;limitations 第一行说明停在哪;next 可直接执行;另一个 agent 能据此继续 |
| F | 纪律 | diff 无越界写;自报无 32B 目录、无 logprobs.pkl 读取;输出里没有 32B 逐条 seq_id 级断言 |
| G | 可追溯 | 每条 finding 的每个 evidence 能落到 events.jsonl 或 validation.jsonl 的一行,且该行 artifact_sha 等于所引产物的版本 |

不作为判定的:X-4 的结论本身是否"正确"(没有隐藏真值);两次运行的结论是否一致(只记录差异,供讨论)。

## 5. 缺什么 / 需要人决定

1. **32B 逐条访问权限**。默认沿用 campaign 纪律:允许聚合(跑 validate、读 validation.jsonl),禁止读 32B bench/labels。
   代价:接手者分不清 32B 误报是规则问题还是 GT 漏标,只能在 limitations 里写。若放开逐条,分析更深,但本地 32B 集
   与 0817 的 wb32b 格重叠,会削弱 0817 的审计隔离。**请确认默认或放开。**
2. **Q-01 是否是你真实想要的下一步。** 它是我从 STATE『未决问题』、config 里"32b val_fpr 观察点"的标注和 0817 campaign 的
   FPR ≤ 0.1% 预算推出来的;agenda.md 可直接改。
3. **0817 六格语料不在本机**,所以"起始候选"的最终判断仍要到 /workspace 那台机器上做;本次结论对 0817 只是有条件的输入。
4. **运行次数**:默认 2 次。1 次也能发现问题,只是分不清偶然。
5. **可选并行叶**:同一材料再派一个 agent 做 Q-02(X-5,端口等价性),与 X-4 共享 X-1..X-3。这能顺带看"分叉":两个
   agent 读同一批父节点、写各自节点、都不改父节点。不加材料,只加一次运行。默认不跑。

## 6. 写 X-1..X-3 时协议表达上遇到的事

按用户要求只记录,不新增机制;每条后面是本次的处理办法。

- **两个账本**:campaign 有 recorder 的 events.jsonl 和 validate 的 validation.jsonl;协议的 evidence 只说"run id"。
  处理:约定 `X-n/R-0NN`(events)与 `X-n/val:<ref>`(validation)两种记法,两账本靠 artifact_sha 对应。没加字段。
- **零 recorder run 但有证据的节点**(X-2):`runs` 为空,但 findings 有 validate 行做证据。处理:按协议把关闭原因写在
  limitations 第一行,evidence 用 val 记法。可行。
- **产物不在版本控制里**(validation.jsonl、runs/artifacts 被 .gitignore):处理:version 用文件 sha12 / 目录指纹,gaps 里注明
  "只有盘上副本"。可行,但意味着交接依赖盘上副本完整。
- **人工 steer**:协议没有"谁指定的"字段。处理:写进 why_now / plan / finding 的 conditions("人 steer Round B,按指定实现并提交")。够用。
- **champion 标签偏差**:三份材料(recorder、STATE、round 文件)对"champion"说法不一。处理:产物 version 只认 artifact_sha,
  偏差写进 limitations 与 gaps。协议本身没问题,问题在上游材料。
- **丢失的中间产物**(离线 judge-loop 脚本):无法作为 product 列出。处理:写进 limitations,并在 next 里建议直接用候选包 + recorder 替代。
- **X-3 的来源版本未知**:products.version 只能是文件 sha,与 X-1 的对应关系靠规则清单推断。处理:写在 gaps。这是"版本"字段
  能表达但材料本身缺失的情况。
- **version 的取法要跟对象类型走**:我第一次把 R-025 样本写成 `sample/results.json` 文件却填了 recorder 对 `sample/` 目录的指纹,
  `build.py check` 抓到不符。处理:产物路径改为目录。这说明"版本"字段本身够用,但核对程序不可少;接手者写 X-4 时同样会碰到。

结论:三个节点都能用现有字段写下来,没有遇到写不了的东西;两处需要记法约定(evidence 前缀、version 取法),写在 RESEARCH.md 里即可。

## 7. 执行步骤(待确认后)

```bash
cd /Users/ywang2397/work/agent-research/auto-research-init-v5/experiment2
python3 build.py check            # 模板自检(产物版本、证据、inputs)
python3 build.py mkrun 1          # runs/x4-1/ws + PROMPT.md
python3 build.py mkrun 2
# 派 Agent(general-purpose, model=opus),prompt = PROMPT.md 内容 + "工作区:<绝对路径>";两次可并行
# 结束后把最终 JSON 存到 runs/x4-N/out.json
python3 build.py trace 1 && python3 build.py diff 1
python3 build.py trace 2 && python3 build.py diff 2
# 人工按 §4 清单逐项核对,结果写 RESULTS.md
```

## 8. 成本估计

每次运行:若接手者做 17 条规则的逐条消融,validate 17 × 136 s ≈ 40 min;若再对每个候选跑 8B recorder,另加 17 × 220 s ≈ 60 min。
两次并行约 1–2 小时墙钟。token 每次约 20–40 万(材料比试用 1 大得多:STATE 498 行、25 个 round 文件、23 KB 的 config)。
