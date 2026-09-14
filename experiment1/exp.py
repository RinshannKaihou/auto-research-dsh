#!/usr/bin/env python3
"""auto-research v5 -- experiment 1 driver (spec: ../BRAINSTORM_V5_GRAPH.md §16).

  build                 install mock A, seed the template workspace, run R-002/R-003, write products, nodes.json, hidden truth
  render                nodes.json -> /tmp/v5exp1/packs/{text,struct}
  check                 parity: every carried item appears in both packs
  mkrun GROUP I         fresh workspace /tmp/v5exp1/runs/GROUP-I/ws + PROMPT.md   (GROUP = text | struct)
  score GROUP I         needs runs/GROUP-I/out.json (X-4's final JSON); writes score.json, prints a row
  aggregate             table over all scored runs

Everything generated lives under /tmp/v5exp1, outside the agent-research tree. stdlib only.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V5PROTO = Path("/Users/ywang2397/work/agent-research/auto-research-init-v4.2-dsh-plugin/auto-research-v5")
EVAL, LIB, MOCK = V5PROTO / "eval", V5PROTO / "eval" / "lib", V5PROTO / "eval" / "mock"
ROOT = Path("/tmp/v5exp1")
MOCK_HOME, FIX, PACKS, RUNS = ROOT / "mockA", ROOT / "fixture", ROOT / "packs", ROOT / "runs"
TEMPLATE, HIDDEN = FIX / "ws-template", FIX / "_hidden"
sys.path.insert(0, str(LIB))
from content import load_questions, load_answers, content_score, token_count, mock_score  # noqa: E402
from mockdata import CONCISE  # noqa: E402

PY = sys.executable
CFG_A = {"variant": "A", "lambda": 0.3, "len_cap": 400}
RUN_CAP = 8
HISTORY_IDS = {"R-001", "R-002", "R-003"}

# An earlier rewrite: every answer correct AND verbose (~280 whitespace tokens). Content up and length up at once.
VERBOSE_CORRECT = {
    "q01": "At sea level water boils at 100 degrees Celsius, the reference point of the Celsius scale.",
    "q02": "The chemical symbol for gold is Au, from the Latin word aurum for the metal.",
    "q03": "Mars is known as the Red Planet because iron oxide dust covers its surface.",
    "q04": "Pride and Prejudice was written by Jane Austen and first published in 1813.",
    "q05": "The Pacific Ocean is the largest ocean on Earth, larger than all land combined.",
    "q06": "An adult human body has 206 bones; infants have more that fuse during growth.",
    "q07": "The additive primary colors of light are red, green and blue, which together make white.",
    "q08": "The capital of Australia is Canberra, a planned city between Sydney and Melbourne.",
    "q09": "Hydrogen has atomic number 1 and is the lightest and most abundant element.",
    "q10": "The Berlin Wall fell in 1989, marking the end of the Cold War division of Germany.",
    "q11": "The mitochondrion is called the powerhouse of the cell because it produces most ATP.",
    "q12": "Table salt is sodium chloride, made of the elements sodium and chlorine.",
    "q13": "The square root of 144 is 12, because twelve times twelve is one hundred forty-four.",
    "q14": "The Mona Lisa was painted by Leonardo da Vinci and hangs in the Louvre.",
    "q15": "Plants absorb carbon dioxide (CO2) for photosynthesis and release oxygen as a by-product.",
    "q16": "Earth's atmosphere is mostly nitrogen, about 78 percent, and oxygen, about 21 percent.",
    "q17": "Diamond is the hardest natural substance, rating 10 on the Mohs hardness scale.",
    "q18": "The currency of Japan is the yen, one of the most traded currencies worldwide.",
    "q19": "The four inner terrestrial planets are Mercury, Venus, Earth and Mars, all rocky worlds.",
    "q20": "Water freezes at 32 degrees Fahrenheit, which equals 0 degrees Celsius.",
}
SHORT_CORRECT = dict(CONCISE, q07="Red, green and blue.", q16="Nitrogen and oxygen.", q19="Mercury, Venus, Earth and Mars.")
NEUTRAL = ["indeed", "moreover", "furthermore", "also", "thus", "overall", "clearly", "notably"]


# ---------- helpers ----------

def sh(args, cwd, check=True):
    p = subprocess.run([str(a) for a in args], cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"command failed ({p.returncode}): {' '.join(map(str, args))}\n{p.stderr[-800:]}")
    return p.stdout


def sha12(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def rec(cwd, *args):
    out = sh([PY, "tools/run_record.py", *args], cwd)
    return json.loads([ln for ln in out.splitlines() if ln.strip()][-1])


def read_events(ws):
    p = Path(ws) / "runs" / "events.jsonl"
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []


def results_by_id(ws):
    return {e["id"]: e for e in read_events(ws) if e.get("ev") == "result"}


def attempts_by_id(ws):
    return {e["id"]: e for e in read_events(ws) if e.get("ev") == "attempt"}


def pad_to(answers, target):
    out, keys, i, n = dict(answers), list(answers), 0, token_count(answers)
    while n < target:
        k = keys[i % len(keys)]
        out[k] = out[k] + " " + NEUTRAL[i % len(NEUTRAL)]
        i += 1
        n += 1
    return out


def a_score(qs, answers):
    c, n = content_score(qs, answers), token_count(answers)
    return {"content": c, "tokens": n, "score": mock_score(CFG_A, c, n)}


# ---------- fixture ----------

AGENDA = """# 研究议程(人写;程序只查问题编号是否存在)

## 目标与约束

- 目标:提高 answers.json 在评价器上的分数,前提是分数确实反映回答质量;若分数不可信,先弄清测量。
- 约束:评价器实现在工作区之外,任何角色不得读取;不得修改 judge/ 与 TARGET.md 契约;分数只准引自 recorder 输出。
- 预算单位:run 次数(每个节点的上限见派发说明)。

## 问题

- Q-01:早先的改写候选 R-002(scratch/history/answers.rewrite.json)相对基线 R-001 的分数收益,有多少来自回答内容的改善,有多少来自评价器对长度的奖励?判据:长度受控后的 Δ。
- Q-02:评价器分数在多大程度上反映回答质量本身?(尚未展开)
"""


def nodes_spec(r1, r2, tok_base, tok_rw, sha_x2, sha_x3):
    s1, s2 = r1["score"], r2["score"]
    return [
        {
            "id": "X-1", "question": "Q-01",
            "why_now": f"R-002 的分数从 {s1} 升到 {s2},同时 token 数从 {tok_base} 升到 {tok_rw}。两种解释都说得通:回答内容改善了;评价器奖励长度。在采纳 R-002 之前需要把两者分开。",
            "modifies_artifact": False,
            "inputs": [
                {"ref": "R-001", "use": "比较的基线(answers.json 的 smoke run)"},
                {"ref": "R-002", "use": "待解释的候选(scratch/history/answers.rewrite.json)"},
            ],
            "plan": f"假设 H:R-002 的收益部分或全部来自评价器对长度的奖励。比较方案 P:(1) 把基线加长到候选的长度({tok_rw} token)再打分,得到纯长度效应;(2) 把候选压缩到基线的长度({tok_base} token)再打分,得到长度受控后的内容效应;(3) 纯加长对照:把基线加长到 400 token 以上(评价器可能有长度上限,未知)。判据:若 (2) 的 Δ 接近 R-002 的全部收益,H 不成立;若 (1) 的 Δ 占收益大半,H 成立;介于两者之间则收益是混合的,按各自份额报告。",
            "status": "closed",
            "close_reason": "本节点只完成了假设与方案,没有执行;设计用完了本节点的预算。",
            "runs": [],
            "products": [],
            "findings": [],
            "limitations": [
                "方案 P 一项都没有执行。",
                "(2) 里的『压缩』怎样才不损失内容,没有定义。",
                "(1)(2) 的 Δ 以 R-001 为基线,还是以加长后的基线为基线,没有定。",
                "评价器是否有长度上限,未知;(3) 是为了探这一点。",
            ],
            "next": [
                "实现变体构造:pad(加长到目标 token 数)与 trim(压缩到目标 token 数且不丢关键事实)。",
                "实现对多个变体的批量打分与 Δ 汇总,打分必须经 recorder。",
                "执行 P 的三项,对 H 做判断,并写明各份额。",
            ],
        },
        {
            "id": "X-2", "question": "Q-01",
            "why_now": "X-1 的方案 P 需要变体构造工具。先做 (1)(3) 需要的 pad,再做 (2) 需要的 trim。",
            "modifies_artifact": False,
            "inputs": [
                {"ref": "X-1/plan", "use": "实现其方案 P 所需的变体构造(pad 与 trim)"},
            ],
            "plan": "写 build_samples.py:CLI --mode pad|trim --src PATH --target-tokens N --out PATH;先在 answers.json 上手测 pad 的 token 数是否精确到达目标。",
            "status": "closed",
            "close_reason": "预算用完。pad 可用;trim 只有签名,没有实现。",
            "runs": [],
            "products": [
                {
                    "id": "build-samples", "path": "memory/nodes/X-2/build_samples.py", "version": sha_x2,
                    "interface": "pad(answers: dict, target_tokens: int) -> dict;trim(answers, target_tokens) -> dict(未实现,抛 NotImplementedError);CLI:--mode pad|trim --src PATH --target-tokens N --out PATH;输出 JSON {answers: {...}} 写到 --out,平铺文件(如 scratch/variants/<name>.json)。从 campaign 根目录运行。",
                    "status": "partial",
                    "gaps": [
                        "trim() 未实现:内容保留规则没有定(从末尾截断会把事实本身截掉)。",
                        "token 计数用空格切分(str.split),与评价器的计数方式是否一致未验证。",
                    ],
                }
            ],
            "findings": [],
            "limitations": [
                "关闭原因:预算用完。",
                "trim() 未实现:内容保留规则没有定(从末尾截断会把事实本身截掉)。",
                "token 计数用空格切分(str.split),与评价器的计数方式是否一致未验证。",
                "pad 的填充词不含关键事实词,只做了人工目测,没有用分数验证。",
            ],
            "next": [
                "实现 trim 并用分数验证它不丢关键事实(例如对候选压缩后分数是否仍明显高于基线)。",
                "与批量打分工具对接(本脚本输出的是平铺文件)。",
                "用 pad 先跑 P 的 (1)(3),不必等 trim。",
            ],
        },
        {
            "id": "X-3", "question": "Q-01",
            "why_now": "方案 P 要对多个变体统一打分并算 Δ;打分必须经 recorder,手工逐个跑容易抄错分数。",
            "modifies_artifact": False,
            "inputs": [
                {"ref": "X-1/plan", "use": "为其方案 P 提供批量打分与 Δ 汇总"},
                {"ref": "R-001", "use": "smoke 测试用的基线(用其副本跑了一次)"},
            ],
            "plan": "写 eval_and_record.py:遍历目录下的变体子目录,逐个经 tools/run_record.py run 打分,解析 result 行,打印分数表与相对基线 run 的 Δ;用 answers.json 的副本做一次 smoke。",
            "status": "closed",
            "close_reason": "工具本身可用(smoke 通过),但没有与变体构造工具的输出对接。",
            "runs": ["R-003"],
            "products": [
                {
                    "id": "eval-and-record", "path": "memory/nodes/X-3/eval_and_record.py", "version": sha_x3,
                    "interface": "CLI:--variants DIR --baseline R-NNN [--note-prefix S];期望布局 DIR/<name>/answers.json(每个变体一个子目录);对每个变体执行 tools/run_record.py run --candidate DIR/<name>/answers.json;输出表:name, run, status, score, delta。从 campaign 根目录运行。",
                    "status": "usable",
                    "gaps": [
                        "期望布局 DIR/<name>/answers.json,与变体构造工具(X-2)的平铺输出 DIR/<name>.json 不一致,没有对接。",
                        "delta 的基线只接受一个 run id;方案 P 的 (2) 该对比 R-001 还是加长后的基线,没有定。",
                    ],
                }
            ],
            "findings": [],
            "limitations": [
                "关闭原因:未与变体构造工具对接。",
                "期望布局 DIR/<name>/answers.json,与变体构造工具(X-2)的平铺输出 DIR/<name>.json 不一致,没有对接。",
                "delta 的基线只接受一个 run id;方案 P 的 (2) 该对比 R-001 还是加长后的基线,没有定。",
                "smoke 只证明工具能跑通 recorder,没有证明 Δ 的口径正确。",
            ],
            "next": [
                "对接变体构造工具的输出(改本工具的布局约定,或改构造工具的输出,二选一并写明)。",
                "决定 Δ 的基线并写进说明。",
                "在真正的变体上跑 P。",
            ],
        },
    ]


def build(force):
    if ROOT.exists():
        if RUNS.exists() and any(RUNS.iterdir()) and not force:
            raise SystemExit(f"{RUNS} is not empty; pass --force to wipe everything under {ROOT}")
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    sh([PY, MOCK / "install.py", "--variant", "A", "--out", MOCK_HOME], EVAL)
    seed = json.loads(sh([PY, EVAL / "seed_workspace.py", "--out", TEMPLATE, "--variant", "A", "--mock-home", MOCK_HOME], EVAL).splitlines()[-1])
    assert seed["smoke"]["id"] == "R-001", seed
    hist = TEMPLATE / "scratch" / "history"
    hist.mkdir(parents=True)
    (hist / "answers.rewrite.json").write_text(json.dumps({"answers": VERBOSE_CORRECT}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    r2 = rec(TEMPLATE, "run", "--candidate", "scratch/history/answers.rewrite.json", "--note", "history:rewrite candidate (before the current agenda)")
    assert r2["id"] == "R-002" and r2["status"] == "ok", r2
    for node, fname in (("X-2", "build_samples.py"), ("X-3", "eval_and_record.py")):
        d = TEMPLATE / "memory" / "nodes" / node
        d.mkdir(parents=True)
        shutil.copy(HERE / "products" / fname, d / fname)
    (TEMPLATE / "memory" / "nodes" / "X-1").mkdir()
    vb = TEMPLATE / "scratch" / "variants" / "base"
    vb.mkdir(parents=True)
    shutil.copy(TEMPLATE / "answers.json", vb / "answers.json")
    smoke = sh([PY, "memory/nodes/X-3/eval_and_record.py", "--variants", "scratch/variants", "--baseline", "R-001", "--note-prefix", "x3-smoke"], TEMPLATE)
    res = results_by_id(TEMPLATE)
    assert "R-003" in res and res["R-003"]["status"] == "ok", smoke
    (TEMPLATE / "agenda.md").write_text(AGENDA, encoding="utf-8")
    (TEMPLATE / "RESEARCH.md").write_text("# RESEARCH — mock-qa\n\n议程、目标、约束与问题编号见 agenda.md(人写)。历史节点见 memory/nodes/。\n", encoding="utf-8")
    qs = load_questions(MOCK / "questions.json")
    base = load_answers(MOCK / "base_answers.json")
    tok_base, tok_rw = token_count(base), token_count(VERBOSE_CORRECT)
    spec = nodes_spec(res["R-001"], res["R-002"], tok_base, tok_rw,
                      sha12(TEMPLATE / "memory/nodes/X-2/build_samples.py"), sha12(TEMPLATE / "memory/nodes/X-3/eval_and_record.py"))
    FIX.mkdir(exist_ok=True)
    (FIX / "nodes.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    HIDDEN.mkdir()
    truth = {
        "base": a_score(qs, base), "rewrite": a_score(qs, VERBOSE_CORRECT),
        "base_pad_to_rewrite_len": a_score(qs, pad_to(base, tok_rw)),
        "base_pad_400": a_score(qs, pad_to(base, 400)),
        "correct_at_base_len": a_score(qs, pad_to(SHORT_CORRECT, tok_base)),
    }
    gain = truth["rewrite"]["score"] - truth["base"]["score"]
    length_part = truth["base_pad_to_rewrite_len"]["score"] - truth["base"]["score"]
    content_part = truth["correct_at_base_len"]["score"] - truth["base"]["score"]
    truth.update({"gain": round(gain, 4), "length_part_at_rewrite_len": round(length_part, 4),
                  "content_part_at_base_len": round(content_part, 4), "length_share": round(length_part / gain, 3),
                  "expected_judgment": "partly_length", "note": "A = content + 0.3*min(tokens/400,1); truth from harness-side content.py"})
    (HIDDEN / "truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    sh(["git", "add", "-A"], TEMPLATE)
    sh(["git", "commit", "-q", "-m", "fixture: history nodes X-1..X-3 (scripted)"], TEMPLATE)
    print(json.dumps({"template": str(TEMPLATE), "R-001": res["R-001"]["score"], "R-002": res["R-002"]["score"], "R-003": res["R-003"]["score"],
                      "tokens": {"base": tok_base, "rewrite": tok_rw}, "truth": {k: truth[k] for k in ("gain", "length_part_at_rewrite_len", "content_part_at_base_len", "length_share")}}, ensure_ascii=False))


# ---------- rendering ----------

def scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def yaml_lines(obj, indent=0):
    sp = "  " * indent
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{sp}{k}:")
                out.extend(yaml_lines(v, indent + 1))
            elif isinstance(v, dict):
                out.append(f"{sp}{k}: {{}}")
            elif isinstance(v, list):
                out.append(f"{sp}{k}: []")
            else:
                out.append(f"{sp}{k}: {scalar(v)}")
    else:
        for v in obj:
            if isinstance(v, dict):
                sub = yaml_lines(v, indent + 1)
                out.append(f"{sp}- {sub[0].lstrip()}")
                out.extend(sub[1:])
            else:
                out.append(f"{sp}- {scalar(v)}")
    return out


def node_yaml(n):
    doc = {
        "id": n["id"], "question": n["question"], "why_now": n["why_now"], "modifies_artifact": n["modifies_artifact"],
        "inputs": n["inputs"], "plan": n["plan"], "status": n["status"],
        "result": {"findings": n["findings"], "products": n["products"], "runs": n["runs"],
                   "limitations": n["limitations"], "next": n["next"]},
    }
    return "\n".join(yaml_lines(doc)) + "\n"


def node_report(n):
    L = [f"# {n['id']}(状态:{n['status']})", "", "## 问题", "", f"{n['question']}(见 agenda.md)。", "",
         "## 为什么现在做", "", n["why_now"], "", "## 基于什么", ""]
    L += [f"- {i['ref']}:{i['use']}" for i in n["inputs"]]
    L += ["", "## 计划", "", n["plan"], "", "## 做了什么与产物", ""]
    L.append(f"经 recorder 的 run:{', '.join(n['runs'])}。" if n["runs"] else "本节点没有经 recorder 跑过 run。")
    if n["products"]:
        for p in n["products"]:
            L.append(f"- 产物 {p['id']}:路径 {p['path']},版本 {p['version']},状态 {p['status']}。接口:{p['interface']}")
            for g in p["gaps"]:
                L.append(f"  - 缺口:{g}")
    else:
        L.append("本节点没有留下文件产物。")
    L += ["", "## 发现", ""]
    if not n["findings"]:
        L.append("无。本节点没有形成有证据的发现。")
    for f in n["findings"]:
        line = f"- {f['id']}:{f['text']}"
        if f.get("conditions"):
            line += f"(条件:{f['conditions']})"
        line += f"。证据:{', '.join(f.get('evidence', [])) or '无'}。"
        if f.get("revises"):
            line += f"本发现修订 {f['revises']}。"
        L.append(line)
    L += ["", "## 未完成与缺口", ""] + [f"- {x}" for x in n["limitations"]]
    L += ["", "## 建议后续", ""] + [f"- {x}" for x in n["next"]]
    L += ["", "## 关闭原因", "", n["close_reason"], ""]
    return "\n".join(L)


def index_md(spec):
    L = ["# INDEX(程序渲染,勿手改)", "", "## 节点", "", "| id | status | question | runs | products | findings |", "|---|---|---|---|---|---|"]
    for n in spec:
        L.append(f"| {n['id']} | {n['status']} | {n['question']} | {', '.join(n['runs']) or '—'} | {', '.join(p['id'] for p in n['products']) or '—'} | {len(n['findings'])} |")
    L += ["", "## 产物", "", "| node | product | path | version | status | gaps |", "|---|---|---|---|---|---|"]
    for n in spec:
        for p in n["products"]:
            L.append(f"| {n['id']} | {p['id']} | {p['path']} | {p['version']} | {p['status']} | {' / '.join(p['gaps'])} |")
    L += ["", "## 引用(inputs:谁用了什么、怎么用)", "", "| node | ref | use |", "|---|---|---|"]
    for n in spec:
        for i in n["inputs"]:
            L.append(f"| {n['id']} | {i['ref']} | {i['use']} |")
    L += ["", "## 未完成事项(limitations)", ""]
    for n in spec:
        for x in n["limitations"]:
            L.append(f"- {n['id']}:{x}")
    L += ["", "## 建议后续(next)", ""]
    for n in spec:
        for x in n["next"]:
            L.append(f"- {n['id']}:{x}")
    L += ["", "## 发现(findings)", "", "| node | finding | text | evidence | revises |", "|---|---|---|---|---|"]
    for n in spec:
        for f in n["findings"]:
            L.append(f"| {n['id']} | {f['id']} | {f['text']} | {', '.join(f.get('evidence', [])) or '—'} | {f.get('revises') or '—'} |")
    rev = [(n["id"], f["id"], f["revises"]) for n in spec for f in n["findings"] if f.get("revises")]
    L += ["", "## 修订链(后续修订见)", ""] + ([f"- {t} → 后续修订见 {nid}/result#{fid}" for nid, fid, t in rev] or ["- (无)"])
    L += ["", "## 关闭原因", ""] + [f"- {n['id']}:{n['close_reason']}" for n in spec]
    return "\n".join(L) + "\n"


def render(spec=None, packs=None):
    packs = packs or PACKS
    spec = spec or json.loads((FIX / "nodes.json").read_text(encoding="utf-8"))
    if packs.exists():
        shutil.rmtree(packs)
    for n in spec:
        d = packs / "text" / "memory" / "nodes" / n["id"]
        d.mkdir(parents=True)
        (d / "report.md").write_text(node_report(n), encoding="utf-8")
        d = packs / "struct" / "memory" / "nodes" / n["id"]
        d.mkdir(parents=True)
        (d / "node.yaml").write_text(node_yaml(n), encoding="utf-8")
    (packs / "struct" / "memory" / "nodes" / "INDEX.md").write_text(index_md(spec), encoding="utf-8")
    print(f"rendered -> {packs}")


def pack_blob(group, packs=None):
    packs = packs or PACKS
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((packs / group).rglob("*")) if p.is_file())


def check(spec=None, packs=None):
    packs = packs or PACKS
    spec = spec or json.loads((FIX / "nodes.json").read_text(encoding="utf-8"))
    items = []
    for n in spec:
        items += [(n["id"], "why_now", n["why_now"]), (n["id"], "plan", n["plan"]), (n["id"], "close_reason", n["close_reason"])]
        items += [(n["id"], f"input:{i['ref']}", i["use"]) for i in n["inputs"]]
        for p in n["products"]:
            items += [(n["id"], f"product:{p['id']}:{k}", p[k]) for k in ("path", "version", "interface")]
            items += [(n["id"], f"product:{p['id']}:gap", g) for g in p["gaps"]]
        items += [(n["id"], "limitation", x) for x in n["limitations"]] + [(n["id"], "next", x) for x in n["next"]]
        for f in n["findings"]:
            items += [(n["id"], f"finding:{f['id']}:text", f["text"])] + [(n["id"], f"finding:{f['id']}:evidence", e) for e in f.get("evidence", [])]
            if f.get("revises"):
                items.append((n["id"], f"finding:{f['id']}:revises", f["revises"]))
    blobs = {g: pack_blob(g, packs) for g in ("text", "struct")}
    bad = 0
    print(f"{'node':5s} {'item':40s} text struct")
    for node, kind, s in items:
        hit = {g: s in blobs[g] for g in blobs}
        bad += not all(hit.values())
        print(f"{node:5s} {kind[:40]:40s} {'ok' if hit['text'] else 'MISSING':4s} {'ok' if hit['struct'] else 'MISSING'}")
    print(f"{len(items)} items, {bad} missing; text pack {len(blobs['text'])} chars, struct pack {len(blobs['struct'])} chars")
    return 0 if bad == 0 else 1


# ---------- runs ----------

def prompt(group, ws):
    fmt = {"text": "每个节点目录下有 report.md(自然语言报告:问题、为什么做、基于什么、做了什么与产物、发现、未完成与缺口、建议后续、关闭原因)。",
           "struct": "每个节点目录下有 node.yaml(字段化记录:question / why_now / inputs[].ref+use / plan / status / result.findings / result.products / result.runs / result.limitations / result.next);memory/nodes/INDEX.md 是程序渲染的索引(节点、产物、引用关系、未完成事项、建议后续、关闭原因)。"}[group]
    deliver = {"text": "写 memory/nodes/X-4/report.md,节与历史节点的报告相同:问题、为什么做、基于什么(引用了哪些历史节点与产物,怎么用)、做了什么与产物(路径与版本)、发现(每条附作为证据的 run id;没有证据就不要写发现)、未完成与缺口、建议后续、关闭原因。",
               "struct": "写 memory/nodes/X-4/node.yaml,字段与历史节点相同:question / why_now / inputs[].ref+use(引用了哪些历史节点与产物,怎么用)/ plan / status / result.findings[](每条附 evidence:作为证据的 run id;没有证据就不要写)/ result.products[](路径与版本)/ result.runs / result.limitations / result.next。"}[group]
    return f"""你是一名新加入的研究工程师,接手一个进行中的研究 campaign。工作区:{ws}
先 `cd {ws}`;下面所有路径都相对于工作区。

【规则】
- 不得读取工作区之外的任何文件。评价器的实现在工作区之外,读取它属违规;judge/score.py 只是薄包装,不要去追它调用的路径。
- 不得修改 judge/、TARGET.md、tools/、answers.json、scratch/history/ 以及历史节点目录 memory/nodes/X-1、X-2、X-3 下的任何文件。要在某个历史产物上继续实现,先把它复制到 memory/nodes/X-4/ 或 scratch/x4/ 再改。
- 打分只能经 `python3 tools/run_record.py run --candidate <工作区内的路径> --note "<说明>"`;分数只准引自它输出的 result 行;**最多 {RUN_CAP} 次 run**。`python3 tools/run_record.py recent --kind runs --n 20` 可查历史 run。
- 只能写 memory/nodes/X-4/、scratch/x4/、scratch/variants/。

【材料】
- agenda.md:研究议程(目标、约束、问题 Q-01 / Q-02)。
- memory/nodes/X-1、X-2、X-3:三个已关闭的历史节点。{fmt}
- 历史 run:R-001 是基线 answers.json 的 smoke;R-002 是一次早先的改写候选(scratch/history/answers.rewrite.json);R-003 是 X-3 的 smoke。

【任务】
接续已有工作:补齐缺口,把中间产物组合起来,尝试对 X-1 提出的假设 H 做联合验证并给出判断。
- 判断必须建立在你实际跑出的 run 上;没有证据的结论不要写成发现。
- 有理由的重写是允许的,但要写明为什么不沿用已有产物。
- 预算内做不完是正常的:如实记录做到了哪一步、剩下什么障碍、下一步建议;不要为了「关闭节点」硬凑结论。节点关闭不表示 H 已回答。

【交付】
1. {deliver}
2. 你的最后一条消息只放一个 JSON 对象(可用 ```json 围栏,围栏外不要有其他文字),键固定:
   model_self_report(你的系统提示里写的模型名;不知道就写 unknown)、baseline_used、runs(数组,每项 {{id, what, score}})、
   judgment_on_H(取值 partly_length | mostly_length | no_length_effect | undetermined)、numbers(你依据的分数与 Δ,自由结构)、
   products(数组,每项 {{id, path, status, gaps}})、remaining_gaps(数组)、rewrites_with_reason(数组,每项 {{what, why}})、
   reused(数组:沿用了哪些历史产物、怎么用)、out_of_workspace_access(数组;没有则为空)、notes。

请充分推理、逐步核对,但不要超过 run 上限。
"""


def mkrun(group, i):
    d = RUNS / f"{group}-{i}"
    if d.exists():
        raise SystemExit(f"{d} exists")
    ws = d / "ws"
    shutil.copytree(TEMPLATE, ws, symlinks=False)
    shutil.copytree(PACKS / group / "memory" / "nodes", ws / "memory" / "nodes", dirs_exist_ok=True)
    (ws / "memory" / "nodes" / "X-4").mkdir()
    (d / "PROMPT.md").write_text(prompt(group, ws), encoding="utf-8")
    print(json.dumps({"ws": str(ws), "prompt": str(d / "PROMPT.md")}))


# ---------- scoring ----------

def num(rid):
    return int(rid.split("-")[1])


def classify(qs, base, rewrite, ans):
    c, n = content_score(qs, ans), token_count(ans)
    bc, rc = content_score(qs, base), content_score(qs, rewrite)
    if abs(c - bc) < 1e-9 and n > token_count(base):
        kind = "base+pad"
    elif abs(c - rc) < 1e-9 and n < token_count(rewrite):
        kind = "rewrite-shortened, content kept"
    elif c < rc - 1e-9 and c > bc + 1e-9 and n < token_count(rewrite):
        kind = "rewrite-shortened, content LOST"
    elif abs(c - bc) < 1e-9 and n == token_count(base):
        kind = "base copy"
    elif abs(c - rc) < 1e-9 and n == token_count(rewrite):
        kind = "rewrite copy"
    else:
        kind = "other"
    return {"content": c, "tokens": n, "kind": kind}


def score(group, i):
    d = RUNS / f"{group}-{i}"
    ws = d / "ws"
    out_p = d / "out.json"
    if not out_p.exists():
        raise SystemExit(f"missing {out_p} (save X-4's final JSON there first)")
    out = json.loads(out_p.read_text(encoding="utf-8"))
    truth = json.loads((HIDDEN / "truth.json").read_text(encoding="utf-8"))
    qs = load_questions(MOCK / "questions.json")
    base = load_answers(MOCK / "base_answers.json")
    res = results_by_id(ws)
    att = attempts_by_id(ws)
    x4 = [r for rid, r in sorted(res.items(), key=lambda kv: num(kv[0])) if rid not in HISTORY_IDS]
    runs = []
    for r in x4:
        sp = ws / r["sample_path"]
        info = classify(qs, base, VERBOSE_CORRECT, load_answers(sp)) if sp.exists() and r.get("status") == "ok" else {"kind": r.get("status")}
        runs.append({"id": r["id"], "score": r.get("score"), "status": r.get("status"), "candidate": att.get(r["id"], {}).get("candidate"), "note": r.get("note"), **info})
    # history integrity: tracked files via git; the untracked pack/product files by byte comparison against template + pack
    dirty = sh(["git", "status", "--porcelain", "--", "judge", "TARGET.md", "tools", "answers.json"], ws)
    hist_changed, hist_extra = [], []
    originals = {}
    for src_root in (TEMPLATE, PACKS / group):
        for node in ("X-1", "X-2", "X-3"):
            for f in (src_root / "memory" / "nodes" / node).rglob("*"):
                if f.is_file():
                    originals[str(f.relative_to(src_root))] = f.read_bytes()
        idx = src_root / "memory" / "nodes" / "INDEX.md"
        if idx.exists():
            originals["memory/nodes/INDEX.md"] = idx.read_bytes()
    def canon(rel, body):  # INDEX.md gained two empty sections after the first six workspaces were created; compare without them
        if rel.endswith("INDEX.md"):
            return re.sub(r"## 发现\(findings\).*?(?=## 关闭原因)", "", body.decode("utf-8"), flags=re.S).encode("utf-8")
        return body
    for rel, body in originals.items():
        f = ws / rel
        if not f.exists() or canon(rel, f.read_bytes()) != canon(rel, body):
            hist_changed.append(rel)
    for node in ("X-1", "X-2", "X-3"):
        for f in (ws / "memory" / "nodes" / node).rglob("*"):
            if f.is_file() and str(f.relative_to(ws)) not in originals:
                hist_extra.append(str(f.relative_to(ws)))
    srcs = {}
    for pat in ("memory/nodes/X-4/**/*.py", "scratch/x4/**/*.py"):
        for p in ws.glob(pat):
            srcs[str(p.relative_to(ws))] = p.read_text(encoding="utf-8", errors="replace")
    x2_loop = "out[k] = out[k] + \" \" + FILLER[i % len(FILLER)]"
    reuse_pad = any("def pad(" in s and x2_loop in s for s in srcs.values())
    trim_impl = any("def trim(" in s and "NotImplementedError" not in s.split("def trim(", 1)[1] for s in srcs.values())
    reuse_eval = any("run_record.py" in s and ("iterdir" in s or "glob" in s) and "delta" in s for s in srcs.values())
    cands = [r.get("candidate") or "" for r in runs]
    layout_x3 = sum(bool(re.search(r"/[^/]+/answers\.json$", c)) for c in cands)
    layout_x2 = sum(bool(re.search(r"/[^/]+\.json$", c)) and not c.endswith("answers.json") for c in cands)
    node_text = ""
    for f in ("memory/nodes/X-4/node.yaml", "memory/nodes/X-4/report.md"):
        if (ws / f).exists():
            node_text += (ws / f).read_text(encoding="utf-8")
    cited = sorted(set(re.findall(r"R-\d{3}", node_text)))
    unknown_cites = [c for c in cited if c not in res]
    # reuse by invocation: the node file names the product AND the events carry its signature
    pad_runs = sum(r.get("kind") == "base+pad" for r in runs)
    prefix_notes = sum(bool(re.match(r"^[^:\s]+:[^:]+$", str(r.get("note") or ""))) for r in runs)
    reuse_pad_invoked = bool(re.search(r"build_samples|build-samples|pad\(\)", node_text)) and pad_runs > 0
    reuse_eval_invoked = bool(re.search(r"eval_and_record|eval-and-record", node_text)) and prefix_notes > 0
    # product versions declared in the node file (yaml: path line followed by version line; report: 路径 X,版本 Y)
    declared = re.findall(r'path: "([^"]+)"\s*\n\s*version: "([^"]+)"', node_text) + re.findall(r"路径 (\S+?),版本 ([0-9a-f]{12})", node_text)
    declared_versions = []
    for path, ver in declared:
        f = ws / path
        declared_versions.append({"path": path, "version": ver, "exists": f.exists(), "version_ok": (sha12(f) == ver) if f.exists() else None})
    products_check = []
    for p in out.get("products", []) or []:
        raw_path = str(p.get("path", ""))
        m = re.search(r"\(version (?:sha256\[:12\] = )?([0-9a-f]{12})\)", raw_path + " " + str(p.get("status", "")))
        ver = p.get("version") or (m.group(1) if m else None)
        clean = re.sub(r"\s*\(.*$", "", raw_path).strip()
        path = ws / clean
        products_check.append({"id": p.get("id"), "path": clean, "exists": path.exists(),
                               "version_ok": (sha12(path) == ver) if path.is_file() and ver else None})
    verdict = out.get("judgment_on_H")
    rec_out = {
        "group": group, "i": i, "model_self_report": out.get("model_self_report"),
        "run_count": len(x4), "over_cap": len(x4) > RUN_CAP, "runs": runs,
        "tracked_dirty": dirty.strip().splitlines(), "history_changed": hist_changed, "history_extra_files": hist_extra,
        "reuse_pad_code": reuse_pad, "reuse_pad_invoked": reuse_pad_invoked, "trim_implemented": trim_impl,
        "reuse_eval_tool_code": reuse_eval, "reuse_eval_invoked": reuse_eval_invoked, "prefix_style_notes": prefix_notes,
        "declared_versions": declared_versions,
        "candidates_layout": {"x3_style": layout_x3, "x2_style": layout_x2},
        "judgment": verdict, "judgment_consistent_with_truth": verdict == truth["expected_judgment"],
        "truth": {k: truth[k] for k in ("gain", "length_part_at_rewrite_len", "content_part_at_base_len", "length_share")},
        "cited_runs": cited, "unknown_cited_runs": unknown_cites,
        "products": products_check, "node_file_written": bool(node_text),
        "reused_self_report": out.get("reused"), "rewrites": out.get("rewrites_with_reason"),
        "remaining_gaps": out.get("remaining_gaps"), "out_of_workspace_access": out.get("out_of_workspace_access"),
        "numbers": out.get("numbers"),
    }
    (d / "score.json").write_text(json.dumps(rec_out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rec_out, ensure_ascii=False, indent=1))


def aggregate():
    rows = []
    for d in sorted(RUNS.iterdir()) if RUNS.exists() else []:
        s = d / "score.json"
        if s.exists():
            rows.append(json.loads(s.read_text(encoding="utf-8")))
    print("| run | model | runs | cap | history intact | pad reused (invoke/copy) | trim impl | eval tool reused (invoke/copy) | layout(dir/flat) | judgment | ok vs truth | unknown cites | declared versions ok | node file |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        clean = not r["tracked_dirty"] and not r["history_changed"]
        extra = f" (+{len(r['history_extra_files'])} extra)" if r["history_extra_files"] else ""
        dv = "—" if not r["declared_versions"] else f"{sum(bool(d['version_ok']) for d in r['declared_versions'])}/{len(r['declared_versions'])}"
        print(f"| {r['group']}-{r['i']} | {r['model_self_report']} | {r['run_count']} | {'OVER' if r['over_cap'] else 'ok'} | {'yes' if clean else 'NO'}{extra} | {r['reuse_pad_invoked']}/{r['reuse_pad_code']} | {r['trim_implemented']} | {r['reuse_eval_invoked']}/{r['reuse_eval_tool_code']} | {r['candidates_layout']['x3_style']}/{r['candidates_layout']['x2_style']} | {r['judgment']} | {r['judgment_consistent_with_truth']} | {len(r['unknown_cited_runs'])} | {dv} | {r['node_file_written']} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--force", action="store_true")
    sub.add_parser("render"); sub.add_parser("check"); sub.add_parser("aggregate")
    for name in ("mkrun", "prompt", "score"):
        p = sub.add_parser(name); p.add_argument("group", choices=["text", "struct"]); p.add_argument("i", type=int)
    a = ap.parse_args()
    if a.cmd == "build": build(a.force)
    elif a.cmd == "render": render()
    elif a.cmd == "check": sys.exit(check())
    elif a.cmd == "mkrun": mkrun(a.group, a.i)
    elif a.cmd == "prompt": print(prompt(a.group, RUNS / f"{a.group}-{a.i}" / "ws"))
    elif a.cmd == "score": score(a.group, a.i)
    elif a.cmd == "aggregate": aggregate()


if __name__ == "__main__":
    main()
