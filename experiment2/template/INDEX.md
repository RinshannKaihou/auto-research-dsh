# INDEX(程序渲染,勿手改)

## 节点

| id | status | question | runs | products | findings |
|---|---|---|---|---|---|
| X-1 | closed | Q-01 | R-001, R-002, R-003, R-004, R-005, R-006, R-007, R-008, R-009, R-010, R-011, R-012, R-013, R-014, R-015, R-016, R-017, R-018, R-019, R-020, R-021, R-022, R-023, R-024, R-025 | detector, r025-sample, ledger, history | 5 |
| X-2 | closed | Q-01 | — | validation-ledger, validate-tool | 4 |
| X-3 | closed | Q-02 | — | metric, demo, readme | 0 |

## 产物

| node | product | path | version | status | gaps |
|---|---|---|---|---|---|
| X-1 | detector | campaign/monitor/msprobe_response_anomaly | f087bd175436 | usable | 不输出命中的规则名,规则级归因要靠改 config 消融或改代码 / 全部阈值只在 8B 定档 / 整链 8B 评分约 220–273 s 一次(sentence_repeat 解码开销) |
| X-1 | r025-sample | campaign/runs/artifacts/R-025/sample | 3a87f3ed8d41 | usable | 只有 8B;无规则名 / runs/artifacts/ 被 .gitignore,只存在于盘上 |
| X-1 | ledger | campaign/runs/events.jsonl | ed06295c975e | usable | digest 的 champion 同分取最早(R-023),与 STATE.md 的写法不一致 |
| X-1 | history | campaign/STATE.md | 727c0fdcbff6 | usable | STATE.md 未压缩,且冠军表把 R-025 写成 champion(recorder 为 R-023) / 各轮提到的『离线 judge-loop 精确重放』脚本不在仓库内(仓库没有 scratch/),已丢失 |
| X-2 | validation-ledger | campaign/runs/validation.jsonl | 0beb19bf2b8d | usable | 被 .gitignore,不在 git 历史中,只有盘上副本(版本 = 文件 sha12) / 无规则级、无逐条信息(设计如此) |
| X-2 | validate-tool | campaign/judge/validate_32b.py | 5bc725ea099a | usable | --ref 可填任意字符串,与 recorder 账本无联动,只能靠 artifact_sha 对上候选包 / 首次加载 tokenizer 可能访问 HF Hub(本机有缓存;离线设 HF_HUB_OFFLINE=1) |
| X-3 | metric | token_traj_detector/metric.py | 10404e10973d | partial | 未在任何 bench 上跑过(只有 demo.py --smoke 的合成用例) / 缺 R-024 的 max_foreign=60 与 R-025 的 cjk_splice_pair_space;规则清单对应 ≤ R-023 / 类别判定改为运行时归类,与 campaign 的 token2category 表是否等价未核对 / R-010 两种 splice 容错是否完整移植未核对 / 文件时间 2026-08-13 18:56,晚于 R-025 进账 22 分钟,但来源轮次没有记录 |
| X-3 | demo | token_traj_detector/demo.py | 997563f2f2c0 | usable | 无与 judge/score.py 对接的输出格式 |
| X-3 | readme | token_traj_detector/README.md | 7a77503fe3ae | usable | 无来源轮次、无与 campaign 版本的对应说明 |

## 引用(inputs:谁用了什么、怎么用)

| node | ref | use |
|---|---|---|
| X-2 | X-1/result#detector | 每轮的候选包(按 validation 行的 artifact_sha 对应到该轮 attempt) |
| X-2 | X-1/result#ledger | ref 字段取该轮的 R-id |
| X-3 | X-1/result#detector | 规则清单与阈值来源;按清单对应到 ≤ R-023 的状态 |

## 未完成事项(limitations)

- X-1:关闭原因:人 steer 的 Round A/B 封口后按指令停止(『停,呈报人审阅,不自行发明下一候选』);不表示 8B 前沿穷尽或 32B 问题解决
- X-1:全部阈值只在 8B 定档;32B 只做过聚合验证(见 X-2),没有规则级归因
- X-1:recorder 的 champion 为 R-023(同分取最早),STATE.md 与 round-024/025 写成 R-024/R-025;三者 8B 判定集相同,产物版本以 artifact_sha 为准
- X-1:各轮的离线 judge-loop 精确重放脚本未保存;要逐条模拟需重建(推荐直接用候选包 + recorder / validate)
- X-1:检测器不输出命中规则名
- X-1:GT 双胞胎冲突使若干形态不可用(FEFF 词间带空格 v1-wb31/594、数字间 v2-wb308/379、U+200C v2-wb433 等),见 STATE 累积教训
- X-1:STATE『未决问题』列出的 8B 剩余漏检(约 104 条文本级无信号、约 97 条 soft repetition、混排歧义区)已判为 8B 不可分,未在 32B 上看过
- X-2:关闭原因:随 X-1 停止而停止;每轮一行、无漏跑;本节点没有 recorder run,证据全是 validate 行
- X-2:只有聚合数,不能区分 32B 误报是规则问题还是 32B GT 漏标
- X-2:0817 六格 campaign 的误报预算 FPR ≤ 0.1% 对应本集 ≤ 4/4249,当前 37
- X-2:本地 32B 集与 0817 campaign 的 wb32b 格不同(后者含 v2-hard 文件,GT 版本未知),结论迁移有条件
- X-2:campaign 规则禁止 round-subagent/Critic 读 32B 目录与本账本;后续节点读本账本、跑 validate 属新授权(本试用的默认授权:允许聚合、禁止逐条;待人确认)
- X-3:关闭原因:端口写完即停,未进入验证;零 run、零发现
- X-3:无 git,版本只有文件 sha
- X-3:等价性未验证前,reason 字段不能替代 campaign 检测器做归因

## 建议后续(next)

- X-1:Q-01:对 R-025 检测器做规则级 32B 误报归因(聚合 only),给出 8B 不掉 TP 前提下 32B FP ≤ 4/4249 的规则子集,作为 0817 六格 campaign 起始候选的依据
- X-1:Q-02:核对 token_traj_detector 端口与 R-025 在 8B 上是否逐条等价(见 X-3)
- X-2:按规则逐条消融(改候选包 config 的 enable / 阈值端点)重跑 validate,做 HEAD 上的规则级归因
- X-2:找 8B 不掉 TP 且 32B FP ≤ 4 的子集;若不存在,给出 TP/FP 权衡表
- X-3:在 8B 上与 R-025 样本逐条对比,差异按规则归类
- X-3:若等价,补 R-024/R-025 两条规则后可用 reason 做 32B 规则级归因(仍须聚合 only)

## 发现(findings)

| node | finding | text | evidence | revises |
|---|---|---|---|---|
| X-1 | finding-1 | token-only 检测器在 8B 的 0-FP 前沿为 636/901(TPR 0.705882),R-023 达到;R-024(max_foreign=60)与 R-025(cjk_splice_pair_space)在 8B 上判定集不变(三者 sample_sha 同为 3a87f3ed8d41) | X-1/R-023, X-1/R-024, X-1/R-025 | — |
| X-1 | finding-2 | HEAD 启用 17 条规则,分两族:rare-character 11 条(foreign_splice 及其容错/总量上限/泰文 presence/外语孤岛、fffd_embed、feff_embed、hebrew_island、glyph_repeat、cjk_island、cjk_splice_single 的四个邻接变体、cjk_splice_pair_space)与 repetition 6 条(exact_loop、block_repeat、packed_repeat、block_repeat_large、template_enum、sentence_repeat);加入顺序与 R-id 对应,见 config.yaml 注释 | X-1/R-001, X-1/R-002, X-1/R-007, X-1/R-008, X-1/R-009, X-1/R-010, X-1/R-011, X-1/R-012, X-1/R-013, X-1/R-014, X-1/R-015, X-1/R-016, X-1/R-017, X-1/R-018, X-1/R-019, X-1/R-020, X-1/R-021, X-1/R-022, X-1/R-023, X-1/R-024, X-1/R-025 | — |
| X-1 | finding-3 | 三条 repetition 规则的阈值与 8B clean 边界只差 1:block_repeat 11 次(clean 最高 10)、template_enum 6 条(clean 最高 5)、sentence_repeat 总 5 次且 800 字符内 4 次(clean 最高 4) | X-1/R-008, X-1/R-022, X-1/R-023 | — |
| X-1 | finding-4 | R-025 的 cjk_splice_pair_space 在 8B 全库为空桶(0 TP / 0 FP):全库唯一『恰 2 个互不相邻 CJK』的记录 v1-wb363 是合法日文混排,邻接类别不进格 | X-1/R-025 | — |
| X-1 | finding-5 | R-024 的 max_foreign=60 在 8B 上零行为变化:86 条 splice TP 中 85 条 n_foreign=1,唯一例外 v2-wb579(57);should_not 侧 56 条 n_foreign>0 的记录本无 splice 命中 | X-1/R-024 | — |
| X-2 | finding-1 | R-025 在 32B:val_tpr 0.443902(273/615),val_fpr 0.008708(37/4249) | X-2/val:R-025, X-1/R-025 | — |
| X-2 | finding-2 | 32B 误报按轮次增量(每轮单因子):R-001 起 1 条,R-002(exact_loop)+1 又被 R-003 的 min_copies 守卫收回;R-010(splice 两种容错)+10;R-011(foreign_island)+3;R-015(cjk_island)+6;R-016(cjk_island 贴数字)+1;R-017(cjk_splice_single)+2;R-020(cjk_splice_single_space)+17;R-021(cjk_splice_single_ws)+7;R-024(max_foreign=60)−10;其余轮 0;HEAD 合计 37。repetition 族(R-008/009/014/022/023)在 32B 上 0 新增误报 | X-2/val:R-001, X-2/val:R-002, X-2/val:R-003, X-2/val:R-009, X-2/val:R-010, X-2/val:R-011, X-2/val:R-015, X-2/val:R-016, X-2/val:R-017, X-2/val:R-020, X-2/val:R-021, X-2/val:R-023, X-2/val:R-024, X-2/val:R-025 | — |
| X-2 | finding-3 | R-025 的 cjk_splice_pair_space 在 8B 为空桶,但在 32B 新增 10 条检出(263→273),误报不变(37) | X-2/val:R-024, X-2/val:R-025, X-1/R-025 | — |
| X-2 | finding-4 | R-024 的 max_foreign=60 在 32B 减少 10 条误报(47→37),减少 1 条检出(264→263) | X-2/val:R-023, X-2/val:R-024 | — |

## 修订链(后续修订见)

- (无)

## 关闭原因

- X-1:人 steer 的 Round A/B 封口后按指令停止;工作停止、产物已记录,问题未解决
- X-2:随 X-1 停止而停止;每轮一行、无漏跑
- X-3:端口写完即停,未进入验证
