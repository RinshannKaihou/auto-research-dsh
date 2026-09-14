# RESEARCH — msprobe response_anomaly token-only 线:真实交接(v5 图协议试用 2)

本工作区是一次真实交接,不是模拟。上游材料:
- `campaign/`:monitor_evolution_rf18_opencode+dsv4f_noProb 的完整副本(git 历史至 0c902d7、STATE.md、
  memory/rounds、memory/audit、recorder 账本 runs/events.jsonl、25 个 run 的样本 runs/artifacts/、32B 验证账本
  runs/validation.jsonl)。原仓库不在本工作区内,不要去找。
- `token_traj_detector/`:campaign 检测器规则的独立标准库端口(metric.py / demo.py / README.md)。
- 图:节点在 `memory/nodes/<id>/node.yaml`,索引 `INDEX.md`(程序渲染),议程 `agenda.md`。

## 数据(仓库外,绝对路径,只读)
- 8B 评分集:`/Users/ywang2397/work/inference-monitoring/self_evolving_monitor/bench_vllm_rf18/bench_vllm_rf18/*.jsonl`(可读,用于理解漏检/误报形态);同目录 `*.logprobs.pkl` **禁读**(noProb 约束:检测输入
  只有 token_ids);GT `/Users/ywang2397/work/inference-monitoring/self_evolving_monitor/bench_vllm_rf18/bench_vllm_rf18_labels_v2/ground_truth_v2.json` 只经 judge/score.py 读,不接入任何检测逻辑。
- 32B 集:`/Users/ywang2397/work/inference-monitoring/self_evolving_monitor/bench_vllm_rf18/bench_vllm_rf18_32b_with_labels/`(bench 与 labels)**禁止直接读**。唯一途径是 `campaign/judge/validate_32b.py`,它只输出聚合数。

## 运行机制(沿用 campaign 的,不新增)
- 8B 评分:在 `campaign/` 下 `python3 tools/run_record.py run --candidate <包目录> --note "..."` → 得 R-0NN,
  写入 runs/events.jsonl(约 220 s 一次)。
- 32B 聚合:在 `campaign/` 下 `python3 judge/validate_32b.py --candidate <包目录> --ref <标签>` → 追加
  runs/validation.jsonl 一行(约 136 s 一次)。`--ref` 填 recorder 的 R-id 或你自定的唯一标签。
- 候选包 = 检测器包目录的副本(放 `scratch/x4/<名字>/`),改它的 configs/config.yaml 或代码;
  `campaign/monitor/` 原地不改。
- 证据记法:`X-n/R-0NN` = events.jsonl 的 result 行;`X-n/val:<ref>` = validation.jsonl 中 ref 等于该标签的行。
  两个账本靠 artifact_sha(目录指纹)对应到候选包版本。
- 检测器解码用本机 HF 缓存的 Qwen/Qwen3-8B tokenizer;若加载时试图联网,设 `HF_HUB_OFFLINE=1`。
