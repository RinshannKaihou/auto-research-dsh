#!/usr/bin/env python3
"""experiment2/build.py — 试用 2:真实交接(msprobe noProb 线 → 32B 误报归因)。

用法:
  python3 build.py build       # 在 template/ 写 RESEARCH.md、agenda.md、memory/nodes/X-1..3/node.yaml、INDEX.md
  python3 build.py check       # 核对 X-1..X-3:产物路径存在、版本指纹一致、evidence 能在账本里找到
  python3 build.py mkrun N     # 复制 template → runs/x4-N/ws,写 PROMPT.md(接手者读的唯一指令)
  python3 build.py trace N     # 解析 runs/x4-N/ws/memory/nodes/X-4/node.yaml:evidence 是否可解析、产物版本是否对得上
  python3 build.py diff N      # runs/x4-N/ws 相对 template 的改动清单(允许写的路径之外有改动即列出)

只用标准库;trace 用 PyYAML 读接手者写的 node.yaml。节点渲染沿用 experiment1/exp.py 的 node_yaml / index_md。
模板里的 campaign/ 与 token_traj_detector/ 由 rsync 从原仓库复制(见 PLAN.md §3),本脚本不碰原仓库。
"""
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "experiment1"))
from exp import node_yaml, index_md  # noqa: E402

TEMPLATE = HERE / "template"
CAMPAIGN = TEMPLATE / "campaign"
PORT = TEMPLATE / "token_traj_detector"
RUNS = HERE / "runs"

DATA = "/Users/ywang2397/work/inference-monitoring/self_evolving_monitor/bench_vllm_rf18"
BENCH_8B = f"{DATA}/bench_vllm_rf18"
GT_8B = f"{DATA}/bench_vllm_rf18_labels_v2/ground_truth_v2.json"
DIR_32B = f"{DATA}/bench_vllm_rf18_32b_with_labels"

VALIDATE_SECONDS = 136   # 预检实测(2026-09-12,M3 Pro):validate_32b.py 对 R-025 产物一次 4864 条 real 136 s,见 _precheck/
PRODUCE_SECONDS = 220    # events.jsonl 里 R-025 的 duration_s(8B 6912 条 produce+judge)

# 接手者允许写的路径(相对 ws);其余一律不得改动
WRITABLE = ("memory/nodes/X-4/", "scratch/x4/", "campaign/runs/", "campaign/scratch/")


def sha12(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def sha_tree(path):
    """与 recorder / validate_32b.py 同规则的目录指纹(跳过 __pycache__ 与 *.pyc)。"""
    path = str(path)
    if os.path.isfile(path):
        return sha12(path)
    entries = []
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".pyc") or f == ".DS_Store":
                continue
            p = os.path.join(root, f)
            entries.append((os.path.relpath(p, path).replace(os.sep, "/"),
                            hashlib.sha256(open(p, "rb").read()).hexdigest()))
    entries.sort()
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------- 图内容(人写)
AGENDA = """# 研究议程(人写;程序只查问题编号是否存在)

- Q-01:R-025 版 token-only 检测器在 Qwen3-32B 集上的 37 条误报来自哪些规则?在 8B 不掉 TP 的前提下,
  哪个规则子集能把 32B 误报压进 0817 六格 campaign 的预算(FPR ≤ 0.1%,本集即 ≤ 4/4249)?
  若做不到,TP/FP 的权衡表是什么?(用途:决定 0817 campaign 正式首轮的起始候选)
- Q-02:token_traj_detector 端口与 campaign 检测器(R-025)在 8B 上是否逐条等价?差异来自哪里?
"""

RESEARCH = f"""# RESEARCH — msprobe response_anomaly token-only 线:真实交接(v5 图协议试用 2)

本工作区是一次真实交接,不是模拟。上游材料:
- `campaign/`:monitor_evolution_rf18_opencode+dsv4f_noProb 的完整副本(git 历史至 0c902d7、STATE.md、
  memory/rounds、memory/audit、recorder 账本 runs/events.jsonl、25 个 run 的样本 runs/artifacts/、32B 验证账本
  runs/validation.jsonl)。原仓库不在本工作区内,不要去找。
- `token_traj_detector/`:campaign 检测器规则的独立标准库端口(metric.py / demo.py / README.md)。
- 图:节点在 `memory/nodes/<id>/node.yaml`,索引 `INDEX.md`(程序渲染),议程 `agenda.md`。

## 数据(仓库外,绝对路径,只读)
- 8B 评分集:`{BENCH_8B}/*.jsonl`(可读,用于理解漏检/误报形态);同目录 `*.logprobs.pkl` **禁读**(noProb 约束:检测输入
  只有 token_ids);GT `{GT_8B}` 只经 judge/score.py 读,不接入任何检测逻辑。
- 32B 集:`{DIR_32B}/`(bench 与 labels)**禁止直接读**。唯一途径是 `campaign/judge/validate_32b.py`,它只输出聚合数。

## 运行机制(沿用 campaign 的,不新增)
- 8B 评分:在 `campaign/` 下 `python3 tools/run_record.py run --candidate <包目录> --note "..."` → 得 R-0NN,
  写入 runs/events.jsonl(约 {PRODUCE_SECONDS} s 一次)。
- 32B 聚合:在 `campaign/` 下 `python3 judge/validate_32b.py --candidate <包目录> --ref <标签>` → 追加
  runs/validation.jsonl 一行(约 {VALIDATE_SECONDS} s 一次)。`--ref` 填 recorder 的 R-id 或你自定的唯一标签。
- 候选包 = 检测器包目录的副本(放 `scratch/x4/<名字>/`),改它的 configs/config.yaml 或代码;
  `campaign/monitor/` 原地不改。
- 证据记法:`X-n/R-0NN` = events.jsonl 的 result 行;`X-n/val:<ref>` = validation.jsonl 中 ref 等于该标签的行。
  两个账本靠 artifact_sha(目录指纹)对应到候选包版本。
- 检测器解码用本机 HF 缓存的 Qwen/Qwen3-8B tokenizer;若加载时试图联网,设 `HF_HUB_OFFLINE=1`。
"""


def nodes_spec():
    X1 = {
        "id": "X-1", "question": "Q-01",
        "why_now": "noProb 线(检测输入只有 token_ids)从全关基线起,在 8B 评分集上以 FPR=0 硬门逐轮单因子演化检测规则;"
                   "前一条 logprob 线(E-009)的结论只作起点参考。2026-08-13 人工 steer 指定 Round A/B 后封口。",
        "modifies_artifact": True,
        "inputs": [],
        "plan": "每轮一个因子:离线精确重放当前 champion(零差)→ 改 config/代码 → recorder 进账 8B 分数(gate: FPR=0)→ "
                "orchestrator 跑一次 32B 聚合验证(见 X-2)→ 写 round 文件与 STATE;每 lease 末 Critic 机械审计。"
                "R-024 起按人 steer:Round A = foreign_splice 加全序列外语 token 总量上限 K;Round B = 恰 2 个互不相邻 CJK、双侧带空格词干。",
        "status": "closed",
        "runs": [f"R-{i:03d}" for i in range(1, 26)],
        "findings": [
            {"id": "finding-1",
             "text": "token-only 检测器在 8B 的 0-FP 前沿为 636/901(TPR 0.705882),R-023 达到;R-024(max_foreign=60)与 "
                     "R-025(cjk_splice_pair_space)在 8B 上判定集不变(三者 sample_sha 同为 3a87f3ed8d41)",
             "conditions": "8B 评分集 6912 条、GT v2 901 正例(400493ad4655)、negative_control 为 FPR=0 硬门",
             "evidence": ["X-1/R-023", "X-1/R-024", "X-1/R-025"]},
            {"id": "finding-2",
             "text": "HEAD 启用 17 条规则,分两族:rare-character 11 条(foreign_splice 及其容错/总量上限/泰文 presence/外语孤岛、"
                     "fffd_embed、feff_embed、hebrew_island、glyph_repeat、cjk_island、cjk_splice_single 的四个邻接变体、"
                     "cjk_splice_pair_space)与 repetition 6 条(exact_loop、block_repeat、packed_repeat、block_repeat_large、"
                     "template_enum、sentence_repeat);加入顺序与 R-id 对应,见 config.yaml 注释",
             "conditions": "detector() 按固定优先级短路:先 rare-character 各条,后 repetition 各条;一条命中即返回,不报规则名",
             "evidence": ["X-1/R-001", "X-1/R-002", "X-1/R-007", "X-1/R-008", "X-1/R-009", "X-1/R-010", "X-1/R-011",
                          "X-1/R-012", "X-1/R-013", "X-1/R-014", "X-1/R-015", "X-1/R-016", "X-1/R-017", "X-1/R-018",
                          "X-1/R-019", "X-1/R-020", "X-1/R-021", "X-1/R-022", "X-1/R-023", "X-1/R-024", "X-1/R-025"]},
            {"id": "finding-3",
             "text": "三条 repetition 规则的阈值与 8B clean 边界只差 1:block_repeat 11 次(clean 最高 10)、template_enum 6 条"
                     "(clean 最高 5)、sentence_repeat 总 5 次且 800 字符内 4 次(clean 最高 4)",
             "conditions": "8B clean 6011 条;campaign 内已标为 32B val_fpr 观察点,未在 32B 上核实",
             "evidence": ["X-1/R-008", "X-1/R-022", "X-1/R-023"]},
            {"id": "finding-4",
             "text": "R-025 的 cjk_splice_pair_space 在 8B 全库为空桶(0 TP / 0 FP):全库唯一『恰 2 个互不相邻 CJK』的记录 "
                     "v1-wb363 是合法日文混排,邻接类别不进格",
             "conditions": "人 steer Round B,按指定实现并提交;是否在别的模型上非空未知",
             "evidence": ["X-1/R-025"]},
            {"id": "finding-5",
             "text": "R-024 的 max_foreign=60 在 8B 上零行为变化:86 条 splice TP 中 85 条 n_foreign=1,唯一例外 v2-wb579(57);"
                     "should_not 侧 56 条 n_foreign>0 的记录本无 splice 命中",
             "conditions": "人 steer Round A;目的是结构性排除『整段换语言』形态,效果只能在 8B 之外看",
             "evidence": ["X-1/R-024"]},
        ],
        "products": [
            {"id": "detector", "path": "campaign/monitor/msprobe_response_anomaly", "version": "f087bd175436",
             "interface": "ILLDetector(configs/config.yaml, configs/mtype_config.json, token2category/).detector(token_ids, "
                          "model_name) → DetectionResult(is_ill, ill_type);输入只有 token_ids + 静态 model_name('qwen3');"
                          "17 条规则由 config.yaml 的 enable/阈值控制(全关 = 取永不触发端点);解码经本机 HF 缓存的 Qwen/Qwen3-8B "
                          "tokenizer。版本 = recorder 目录指纹(R-025 attempt 的 artifact_sha;git 8bfb3a8 起未变,HEAD 0c902d7)",
             "status": "usable",
             "gaps": ["不输出命中的规则名,规则级归因要靠改 config 消融或改代码",
                      "全部阈值只在 8B 定档",
                      "整链 8B 评分约 220–273 s 一次(sentence_repeat 解码开销)"]},
            {"id": "r025-sample", "path": "campaign/runs/artifacts/R-025/sample", "version": "3a87f3ed8d41",
             "interface": "目录下 results.json = {文件名: [{seq_id, is_ill, ill_type, n_tokens}]},9 个文件 6912 条,636 条 is_ill;"
                          "版本 = recorder 的 sample_sha(对 sample/ 目录的指纹),与 R-023/R-024 相同",
             "status": "usable",
             "gaps": ["只有 8B;无规则名", "runs/artifacts/ 被 .gitignore,只存在于盘上"]},
            {"id": "ledger", "path": "campaign/runs/events.jsonl", "version": "ed06295c975e",
             "interface": "recorder 单写;attempt / result / replay / epoch 事件各一行;读法 python3 tools/run_record.py "
                          "recent | digest | replay --id R-0NN",
             "status": "usable",
             "gaps": ["digest 的 champion 同分取最早(R-023),与 STATE.md 的写法不一致"]},
            {"id": "history", "path": "campaign/STATE.md", "version": "727c0fdcbff6",
             "interface": "STATE.md 498 行(冠军表 / 累积教训 / 未决问题);campaign/memory/rounds/round-001..025.md 每轮的"
                          "假设-探查-实施-结果-教训;campaign/memory/audit/after-round-020|025.md 为 Critic 审计;git 历史至 0c902d7",
             "status": "usable",
             "gaps": ["STATE.md 未压缩,且冠军表把 R-025 写成 champion(recorder 为 R-023)",
                      "各轮提到的『离线 judge-loop 精确重放』脚本不在仓库内(仓库没有 scratch/),已丢失"]},
        ],
        "limitations": [
            "关闭原因:人 steer 的 Round A/B 封口后按指令停止(『停,呈报人审阅,不自行发明下一候选』);不表示 8B 前沿穷尽或 32B 问题解决",
            "全部阈值只在 8B 定档;32B 只做过聚合验证(见 X-2),没有规则级归因",
            "recorder 的 champion 为 R-023(同分取最早),STATE.md 与 round-024/025 写成 R-024/R-025;三者 8B 判定集相同,产物版本以 artifact_sha 为准",
            "各轮的离线 judge-loop 精确重放脚本未保存;要逐条模拟需重建(推荐直接用候选包 + recorder / validate)",
            "检测器不输出命中规则名",
            "GT 双胞胎冲突使若干形态不可用(FEFF 词间带空格 v1-wb31/594、数字间 v2-wb308/379、U+200C v2-wb433 等),见 STATE 累积教训",
            "STATE『未决问题』列出的 8B 剩余漏检(约 104 条文本级无信号、约 97 条 soft repetition、混排歧义区)已判为 8B 不可分,未在 32B 上看过",
        ],
        "next": [
            "Q-01:对 R-025 检测器做规则级 32B 误报归因(聚合 only),给出 8B 不掉 TP 前提下 32B FP ≤ 4/4249 的规则子集,作为 0817 六格 campaign 起始候选的依据",
            "Q-02:核对 token_traj_detector 端口与 R-025 在 8B 上是否逐条等价(见 X-3)",
        ],
        "close_reason": "人 steer 的 Round A/B 封口后按指令停止;工作停止、产物已记录,问题未解决",
    }

    X2 = {
        "id": "X-2", "question": "Q-01",
        "why_now": "campaign 每轮末由 orchestrator 机械跑一次 32B 泛化验证,数字只在 digest 呈给人;round-subagent 与 Critic 禁读。"
                   "25 行聚合结果从未被任何节点解释。",
        "modifies_artifact": False,
        "inputs": [{"ref": "X-1/result#detector", "use": "每轮的候选包(按 validation 行的 artifact_sha 对应到该轮 attempt)"},
                   {"ref": "X-1/result#ledger", "use": "ref 字段取该轮的 R-id"}],
        "plan": "每轮:python3 judge/validate_32b.py --ref R-NNN —— 对 Qwen3-32B 集 4864 条(615 正 / 4249 负,GT ffee0a5c3ddc)"
                "以同一 token-only 接口检测,只输出一行聚合 JSON 追加到 runs/validation.jsonl;不落盘任何逐条结果。",
        "status": "closed",
        "runs": [],
        "findings": [
            {"id": "finding-1",
             "text": "R-025 在 32B:val_tpr 0.443902(273/615),val_fpr 0.008708(37/4249)",
             "conditions": "32B 集 4864 条(v1 clean/clean2/fault、v2 clean/fault;v2 fault 仅 768 条不完整;无 v2-hard);"
                           "GT 为独立标注 ffee0a5c3ddc,含 think 段异常;与 8B 同轮分数(0.706)不可直接比,两集正例构成未对齐",
             "evidence": ["X-2/val:R-025", "X-1/R-025"]},
            {"id": "finding-2",
             "text": "32B 误报按轮次增量(每轮单因子):R-001 起 1 条,R-002(exact_loop)+1 又被 R-003 的 min_copies 守卫收回;"
                     "R-010(splice 两种容错)+10;R-011(foreign_island)+3;R-015(cjk_island)+6;R-016(cjk_island 贴数字)+1;"
                     "R-017(cjk_splice_single)+2;R-020(cjk_splice_single_space)+17;R-021(cjk_splice_single_ws)+7;"
                     "R-024(max_foreign=60)−10;其余轮 0;HEAD 合计 37。repetition 族(R-008/009/014/022/023)在 32B 上 0 新增误报",
             "conditions": "增量归因依赖『每轮只改一个因子』且规则优先级固定;它只反映加入当时的边际误报,不等于在 HEAD 上删掉该规则会减少的误报",
             "evidence": ["X-2/val:R-001", "X-2/val:R-002", "X-2/val:R-003", "X-2/val:R-009", "X-2/val:R-010", "X-2/val:R-011",
                          "X-2/val:R-015", "X-2/val:R-016", "X-2/val:R-017", "X-2/val:R-020", "X-2/val:R-021",
                          "X-2/val:R-023", "X-2/val:R-024", "X-2/val:R-025"]},
            {"id": "finding-3",
             "text": "R-025 的 cjk_splice_pair_space 在 8B 为空桶,但在 32B 新增 10 条检出(263→273),误报不变(37)",
             "conditions": "单因子轮;聚合数,未核对这 10 条属于 GT 的哪一类异常",
             "evidence": ["X-2/val:R-024", "X-2/val:R-025", "X-1/R-025"]},
            {"id": "finding-4",
             "text": "R-024 的 max_foreign=60 在 32B 减少 10 条误报(47→37),减少 1 条检出(264→263)",
             "conditions": "单因子轮;8B 上零行为变化(X-1/finding-5)",
             "evidence": ["X-2/val:R-023", "X-2/val:R-024"]},
        ],
        "products": [
            {"id": "validation-ledger", "path": "campaign/runs/validation.jsonl", "version": "0beb19bf2b8d",
             "interface": "每行一个 validate 事件:t、ref、artifact_sha、val_tpr、val_fpr、n_detected/n_should、"
                          "n_false_positive/n_should_not、n_skipped、n_records、gt_sha12、gt_pinned;25 行 ref=R-001..R-025",
             "status": "usable",
             "gaps": ["被 .gitignore,不在 git 历史中,只有盘上副本(版本 = 文件 sha12)",
                      "无规则级、无逐条信息(设计如此)"]},
            {"id": "validate-tool", "path": "campaign/judge/validate_32b.py", "version": "5bc725ea099a",
             "interface": f"python3 judge/validate_32b.py --ref <标签> --candidate <包目录>;聚合 only;追加到 cwd 下的 "
                          f"runs/validation.jsonl;本机一次约 {VALIDATE_SECONDS} s",
             "status": "usable",
             "gaps": ["--ref 可填任意字符串,与 recorder 账本无联动,只能靠 artifact_sha 对上候选包",
                      "首次加载 tokenizer 可能访问 HF Hub(本机有缓存;离线设 HF_HUB_OFFLINE=1)"]},
        ],
        "limitations": [
            "关闭原因:随 X-1 停止而停止;每轮一行、无漏跑;本节点没有 recorder run,证据全是 validate 行",
            "只有聚合数,不能区分 32B 误报是规则问题还是 32B GT 漏标",
            "0817 六格 campaign 的误报预算 FPR ≤ 0.1% 对应本集 ≤ 4/4249,当前 37",
            "本地 32B 集与 0817 campaign 的 wb32b 格不同(后者含 v2-hard 文件,GT 版本未知),结论迁移有条件",
            "campaign 规则禁止 round-subagent/Critic 读 32B 目录与本账本;后续节点读本账本、跑 validate 属新授权(本试用的默认授权:允许聚合、禁止逐条;待人确认)",
        ],
        "next": [
            "按规则逐条消融(改候选包 config 的 enable / 阈值端点)重跑 validate,做 HEAD 上的规则级归因",
            "找 8B 不掉 TP 且 32B FP ≤ 4 的子集;若不存在,给出 TP/FP 权衡表",
        ],
        "close_reason": "随 X-1 停止而停止;每轮一行、无漏跑",
    }

    X3 = {
        "id": "X-3", "question": "Q-02",
        "why_now": "把 campaign 检测器的 token-only 规则改写成只依赖标准库的独立模块,便于交付与在线使用;README 于 2026-09-03 补写。",
        "modifies_artifact": False,
        "inputs": [{"ref": "X-1/result#detector", "use": "规则清单与阈值来源;按清单对应到 ≤ R-023 的状态"}],
        "plan": "重写 15 条规则:类别判定改为运行时按 Unicode 字符名归类(不用 token2category 表),解码经 HF tokenizer 回调;"
                "输出加 reason(命中规则名);demo.py 提供 jsonl 批量入口与合成 smoke。",
        "status": "closed",
        "runs": [],
        "findings": [],
        "products": [
            {"id": "metric", "path": "token_traj_detector/metric.py", "version": "10404e10973d",
             "interface": "detect(token_ids, token_texts=None, decode=None, end_token_ids=Qwen3 默认) → DetectResult(is_ill, "
                          "ill_type∈{0,1,3}, reason);只给 token_ids 时只跑 4 条 token-id 复读规则;版本 = 文件 sha256 前 12 位",
             "status": "partial",
             "gaps": ["未在任何 bench 上跑过(只有 demo.py --smoke 的合成用例)",
                      "缺 R-024 的 max_foreign=60 与 R-025 的 cjk_splice_pair_space;规则清单对应 ≤ R-023",
                      "类别判定改为运行时归类,与 campaign 的 token2category 表是否等价未核对",
                      "R-010 两种 splice 容错是否完整移植未核对",
                      "文件时间 2026-08-13 18:56,晚于 R-025 进账 22 分钟,但来源轮次没有记录"]},
            {"id": "demo", "path": "token_traj_detector/demo.py", "version": "997563f2f2c0",
             "interface": "python3 demo.py --smoke | --jsonl <文件> [--tokenizer Qwen/Qwen3-8B] [--limit N];需 transformers",
             "status": "usable", "gaps": ["无与 judge/score.py 对接的输出格式"]},
            {"id": "readme", "path": "token_traj_detector/README.md", "version": "7a77503fe3ae",
             "interface": "15 条规则的原理/判定/示例与优先级速查表", "status": "usable",
             "gaps": ["无来源轮次、无与 campaign 版本的对应说明"]},
        ],
        "limitations": [
            "关闭原因:端口写完即停,未进入验证;零 run、零发现",
            "无 git,版本只有文件 sha",
            "等价性未验证前,reason 字段不能替代 campaign 检测器做归因",
        ],
        "next": [
            "在 8B 上与 R-025 样本逐条对比,差异按规则归类",
            "若等价,补 R-024/R-025 两条规则后可用 reason 做 32B 规则级归因(仍须聚合 only)",
        ],
        "close_reason": "端口写完即停,未进入验证",
    }
    return [X1, X2, X3]


# ----------------------------------------------------------------------------- build / check
def build():
    spec = nodes_spec()
    (TEMPLATE / "RESEARCH.md").write_text(RESEARCH, encoding="utf-8")
    (TEMPLATE / "agenda.md").write_text(AGENDA, encoding="utf-8")
    for n in spec:
        d = TEMPLATE / "memory" / "nodes" / n["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "node.yaml").write_text(node_yaml(n), encoding="utf-8")
    (TEMPLATE / "INDEX.md").write_text(index_md(spec), encoding="utf-8")
    (TEMPLATE / "memory" / "nodes" / "X-4").mkdir(parents=True, exist_ok=True)
    (TEMPLATE / "scratch" / "x4").mkdir(parents=True, exist_ok=True)
    print("built:", [n["id"] for n in spec], "→", TEMPLATE)


def ledgers(ws):
    ev = {}
    for line in (ws / "campaign/runs/events.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["ev"] == "result":
            ev[r["id"]] = r
    val = {}
    for line in (ws / "campaign/runs/validation.jsonl").read_text().splitlines():
        r = json.loads(line)
        val.setdefault(r["ref"], []).append(r)
    return ev, val


def resolve(evidence, ev, val):
    """'X-n/R-0NN' → events result 行;'X-n/val:<ref>' → validation 行。返回 (ok, 描述)。"""
    m = re.fullmatch(r"(X-\d+)/(R-\d{3})", evidence)
    if m:
        r = ev.get(m.group(2))
        return (r is not None, f"events {m.group(2)}: " + (f"score={r['score']} status={r['status']}" if r else "缺"))
    m = re.fullmatch(r"(X-\d+)/val:(.+)", evidence)
    if m:
        rows = val.get(m.group(2), [])
        return (bool(rows), f"validation ref={m.group(2)}: " + (f"{len(rows)} 行, 最新 fp={rows[-1]['n_false_positive']} tp={rows[-1]['n_detected']}" if rows else "缺"))
    return (False, "记法不识别")


def version_of(ws, path):
    p = ws / path
    if not p.exists():
        return None
    return sha_tree(p) if p.is_dir() else sha12(p)


def check(ws=TEMPLATE, spec=None):
    spec = spec or nodes_spec()
    ev, val = ledgers(ws)
    bad = 0
    for n in spec:
        for p in n["products"]:
            v = version_of(ws, p["path"])
            ok = (v == p["version"])
            bad += (not ok)
            print(f"[{'ok' if ok else 'XX'}] {n['id']}/{p['id']} {p['path']} 声明 {p['version']} 实际 {v}")
        for f in n["findings"]:
            for e in f.get("evidence", []):
                ok, desc = resolve(e, ev, val)
                bad += (not ok)
                if not ok:
                    print(f"[XX] {n['id']}/{f['id']} evidence {e}: {desc}")
        for i in n["inputs"]:
            m = re.fullmatch(r"(X-\d+)/result#(.+)", i["ref"])
            src = next((s for s in spec if s["id"] == m.group(1)), None) if m else None
            ok = bool(src and src["status"] == "closed" and any(p["id"] == m.group(2) for p in src["products"]))
            bad += (not ok)
            print(f"[{'ok' if ok else 'XX'}] {n['id']} input {i['ref']}")
    print("check:", "全部通过" if bad == 0 else f"{bad} 项不符")
    return bad == 0


# ----------------------------------------------------------------------------- run workspaces
def prompt():
    return f"""你是研究图里节点 X-4 的执行者。工作区就是当前目录,先读 RESEARCH.md、INDEX.md、agenda.md 和 memory/nodes/X-1、X-2、X-3 的 node.yaml。
你的问题是 agenda.md 的 Q-01。X-1(campaign 的 25 轮)、X-2(32B 逐轮聚合验证)、X-3(独立端口)已关闭,它们的产物、版本、缺口和未验证条件都写在各自的 node.yaml 里;campaign/ 目录下有完整的原始材料(STATE.md、memory/rounds、账本、样本、git 历史),需要时自己去核对。

规则(与 campaign 的写入礼仪一致):
- 只读、禁改:campaign/judge/、campaign/TARGET.md、campaign/ORCHESTRATOR.md、campaign/STATE.md、campaign/memory/、campaign/monitor/(原地)、token_traj_detector/(原地)、memory/nodes/X-1..X-3、INDEX.md、agenda.md、RESEARCH.md。不要在 campaign/ 里 git commit / tag。
- 只写:memory/nodes/X-4/、scratch/x4/、campaign/runs/(只经 tools/run_record.py 与 judge/validate_32b.py 写)、campaign/scratch/。
- 数据:8B bench 的 *.jsonl 可读;任何 *.logprobs.pkl 禁读;32B 目录(bench 与 labels)禁止直接读,只能经 judge/validate_32b.py 取聚合数;GT 文件不接入检测逻辑。这些是 noProb 线与泛化验证的纪律,不是本次临时加的。
- 候选包放 scratch/x4/<名字>/(整个检测器包目录的副本),在 campaign/ 目录下用 --candidate ../scratch/x4/<名字> 调 recorder 或 validate。
- 预算按正常研究工作安排:不设 run 次数上限,每次 8B 评分约 {PRODUCE_SECONDS} s、每次 32B 聚合约 {VALIDATE_SECONDS} s;先在 plan 里写你打算跑多少、为什么,然后照做。做不完就如实关闭:节点关闭只表示这段工作结束、记录固定,允许零 run、空 findings、部分产物和条件性判断。
- 请充分推理。不要为了关闭节点强行下结论;有证据说多少就写多少。

交付物:
1. memory/nodes/X-4/node.yaml,字段与 X-1..X-3 相同(id、question、why_now、modifies_artifact、inputs[ref/use]、plan、status、result.findings[id/text/conditions/evidence/revises 可选]、result.products[id/path/version/interface/status/gaps]、result.runs、result.limitations、result.next)。
   - inputs 写你实际用到的上游产物与用法;沿用、改写或绕开上游产物都可以,但要写原因。
   - 每条 finding 必须有 evidence,记法见 RESEARCH.md(X-4/R-0NN 或 X-4/val:<ref>);引用上游证据用 X-1/…、X-2/…。
   - 每个产物写路径、版本(目录用 validate_32b.py 里 sha_tree 的规则,文件用 sha256 前 12 位)、接口、状态(partial|usable)、缺口。
   - limitations 写还依赖哪些未验证条件、卡在哪里;若零 run,第一行写关闭原因。
2. 最后单独输出一个 JSON(不要 markdown 围栏):{{"model_self_report": 你是什么模型, "runs": [R-id...], "validates": [ref...], "versions_used": {{产物: 版本}}, "reused": [沿用了什么、怎么用], "rewrites_with_reason": [改写或绕开了什么、为什么], "remaining_gaps": [...], "out_of_workspace_access": [工作区外读过的路径,数据目录也算], "notes": "..."}}
"""


def mkrun(i):
    ws = RUNS / f"x4-{i}" / "ws"
    if ws.exists():
        raise SystemExit(f"{ws} 已存在;删掉再建")
    shutil.copytree(TEMPLATE, ws, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    (ws.parent / "PROMPT.md").write_text(prompt(), encoding="utf-8")
    print("run workspace:", ws)
    print("prompt:", ws.parent / "PROMPT.md")


def trace(i):
    import yaml
    ws = RUNS / f"x4-{i}" / "ws"
    p = ws / "memory/nodes/X-4/node.yaml"
    if not p.exists():
        raise SystemExit(f"{p} 不存在")
    n = yaml.safe_load(p.read_text())
    ev, val = ledgers(ws)
    res = n.get("result") or {}
    print(f"X-4 status={n.get('status')} runs={res.get('runs')} findings={len(res.get('findings') or [])}")
    for f in res.get("findings") or []:
        evs = f.get("evidence") or []
        print(f"- {f.get('id')}: {len(evs)} evidence" + ("" if evs else "  [XX 无证据]"))
        for e in evs:
            if e.startswith("X-4/"):
                ok, desc = resolve(e, ev, val)
                print(f"    [{'ok' if ok else 'XX'}] {e}: {desc}")
            else:
                print(f"    [上游] {e}")
    for pr in res.get("products") or []:
        v = version_of(ws, pr.get("path", ""))
        print(f"- 产物 {pr.get('id')} {pr.get('path')} 声明 {pr.get('version')} 实际 {v} [{'ok' if v == pr.get('version') else 'XX'}] 状态 {pr.get('status')} 缺口 {len(pr.get('gaps') or [])}")
    lim = res.get("limitations") or []
    print(f"- limitations {len(lim)} 条;next {len(res.get('next') or [])} 条;inputs {len(n.get('inputs') or [])} 条")


def diff(i):
    """列出 ws 相对 template 的改动(新增/修改/删除),按是否在允许写的路径内分组。"""
    ws = RUNS / f"x4-{i}" / "ws"
    def walk(root):
        out = {}
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store" and ".git" not in p.parts:
                out[str(p.relative_to(root))] = sha12(p)
        return out
    a, b = walk(TEMPLATE), walk(ws)
    changed = sorted(set(k for k in a if k not in b or a[k] != b[k]) | set(k for k in b if k not in a))
    allowed = [k for k in changed if any(k.startswith(w) for w in WRITABLE)]
    outside = [k for k in changed if k not in allowed]
    print(f"允许范围内改动 {len(allowed)} 个文件;范围外改动 {len(outside)} 个:")
    for k in outside:
        print("  [XX]", k)
    return outside


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    if a[0] == "build": build(); check()
    elif a[0] == "check": check()
    elif a[0] == "mkrun": mkrun(int(a[1]))
    elif a[0] == "trace": trace(int(a[1]))
    elif a[0] == "diff": diff(int(a[1]))
    else: print(__doc__)


if __name__ == "__main__":
    main()
