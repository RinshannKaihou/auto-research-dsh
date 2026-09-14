#!/usr/bin/env python3
"""Create synthetic, persistent graph-viewer data without running any agents.

All records are written through Store and ArtifactStore. Existing paths,
including empty directories and dangling symlinks, are never reused.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import sys


# Support invocation directly from a source checkout.
_SOURCE = Path(__file__).resolve().parents[1] / "src"
if _SOURCE.is_dir() and str(_SOURCE) not in sys.path:
    sys.path.insert(0, str(_SOURCE))

from auto_research.artifacts import ArtifactStore  # noqa: E402
from auto_research.runtime import check, export_views  # noqa: E402
from auto_research.store import Store  # noqa: E402


DEMO_NOTICE = (
    "演示项目：全部节点和阶段状态由脚本构造，用于展示研究路径图，" "不代表真实研究记录；未调用模型、未运行数值实验。" "预算和成本中的零值仅为演示占位，不是实际研究费用。"
)
MATERIAL_NOTICE = "> **演示材料。** 本文由示例脚本编写；不是模型输出、真实实验记录或正式研究报告。\n\n"


def create_demo(project: str | Path) -> Path:
    """Create eight illustrative nodes; fail atomically if the path exists."""
    requested = Path(project).expanduser()
    requested.mkdir(parents=True, exist_ok=False)
    root = requested.resolve()
    store, artifacts = Store(root), ArtifactStore(root)
    store.initialize(
        "【演示】局部稳定性条件能否推广到耦合系统？",
        questions=[
            {"id": "Q-001", "text": "单个子系统稳定时，整体耦合系统还需要什么条件？"},
            {"id": "Q-002", "text": "能量函数能给出什么稳定性保证，证明还缺哪一步？"},
            {"id": "Q-003", "text": "特征值和反例能否揭示仅看局部系统的盲点？"},
        ],
        budget=0,
        config={
            "control": "paused",
            "demo": True,
            "demo_notice": DEMO_NOTICE,
            "concurrency": 2,
            "worker_estimate": 1,
            "coordinator_estimate": 1,
            "max_coordinator_calls": 0,
        },
        request_id="demo:initialize",
    )
    store.agenda(
        text=(
            "# 演示研究议程\n\n" + DEMO_NOTICE + "\n\n"
            "Q-001：从局部稳定到整体耦合稳定。\n"
            "Q-002：能量法及证明缺口。\n"
            "Q-003：特征值、反例和适用范围。\n\n"
            "先限定二维、常系数、对称耦合，后续节点只展示推广计划。"
            "两个分支的状态仅用于演示，没有实际并行运行。\n"
        )
    )
    (root / "DEMO_README.md").write_text(
        "# 研究路径图演示\n\n" + DEMO_NOTICE + "\n\n"
        "可查看节点、固定产物及修订关系。6 个 closed 表示示例阶段已记录，"
        "不表示真实研究已经完成；1 个 proposed 表示待开展，"
        "1 个 open/reserved 表示已领取但没有启动执行的示例状态。\n\n"
        "请用 view/export 展示此项目，不把它当作真实研究运行记录。\n",
        encoding="utf-8",
    )
    node_ids: dict[str, str] = {}

    def ref(label: str, item: str) -> str:
        return f"{node_ids[label]}/result#{item}"

    def source(label: str, item: str, use: str) -> dict:
        return {"ref": ref(label, item), "use": use}

    def propose(label: str, question: str, title: str, why: str, plan: str, inputs: list) -> dict:
        node = store.propose(
            {
                "question": question,
                "why_now": title + "。" + why,
                "plan": plan,
                "inputs": inputs,
                "modifies_artifact": False,
                "demo": True,
            },
            request_id=f"demo:propose:{label}",
        )
        node_ids[label] = node["id"]
        return node

    def close(
        label: str,
        question: str,
        title: str,
        why: str,
        plan: str,
        inputs: list,
        product_id: str,
        filename: str,
        document: str,
        interface: str,
        gaps: list,
        findings: list,
        limitations: str,
        next_steps: list,
        *,
        partial: bool = False,
    ) -> dict:
        node = propose(label, question, title, why, plan, inputs)
        attempt = store.reserve(node["id"], estimate=0, request_id=f"demo:reserve:{label}")
        store.set_attempt(
            attempt["id"],
            demo=True,
            not_executed=True,
            execution_note="脚本装载的阶段状态；未启动 worker、模型或数值实验。",
        )
        source_dir = root / "demo-materials" / node["id"]
        source_dir.mkdir(parents=True)
        path = source_dir / filename
        path.write_text(MATERIAL_NOTICE + document, encoding="utf-8")
        product = {
            "id": product_id,
            "interface": interface,
            "status": "partial" if partial else "usable",
            "gaps": gaps,
            **artifacts.freeze(path, allowed_root=source_dir),
        }
        result = store.publish(
            node["id"],
            {
                "products": [product],
                "findings": findings,
                "close_reason": "演示阶段已记录；不是实际研究执行的完成凭据。",
                "limitations": limitations,
                "next": next_steps,
            },
            request_id=f"demo:publish:{label}",
        )
        store.settle(
            attempt["id"],
            cost=0,
            cost_kind="estimated",
            state="completed",
            request_id=f"demo:settle:{label}",
        )
        return result

    close(
        "origin",
        "Q-001",
        "共同起点：先固定一个可核算的模型",
        "区分子系统条件与耦合条件，避免两个分支研究不同对象。",
        "把模型、目标和暂不讨论的情形写成一份可引用材料。",
        [],
        "model",
        "模型与问题.md",
        "# 模型与问题\n\n"
        "考虑 x' = -a x + k y，y' = k x - b y，其中 a>0、b>0，k 为实常数。\n\n"
        "当 k=0 时，x(t)=x(0)e^{-at}、y(t)=y(0)e^{-bt}，两个独立子系统都趋向零。\n\n"
        "问题是：k 不为零时，仅有 a>0、b>0 是否还够？"
        "一个分支尝试能量函数，另一个分支检查特征值及反例。"
        "本例不含测得的数据，也不讨论非线性、时变或高维系统。\n",
        "后续材料沿用变量 a、b、k 和同一二维线性系统。",
        ["耦合强度的限制尚未推导"],
        [
            {
                "id": "uncoupled",
                "text": "在这个构造模型中，k=0 时两个独立子系统都趋向零。",
                "conditions": "a>0、b>0，常系数线性系统；由显式解直接得到。",
                "evidence": ["#model"],
            }
        ],
        "这是演示模型的代数起点，不是来自真实实验或实际装置的观察。",
        ["分别用能量法和特征值法寻找耦合带来的限制。"],
    )
    close(
        "energy-draft",
        "Q-002",
        "能量法分支：留下尚未完成的证明",
        "先算出能量变化，再把交叉项的处理交给后续探索。",
        "取 V=(x²+y²)/2，计算 V'；若暂时不能确定负定条件，就保存推导和缺口。",
        [source("origin", "model", "固定能量法使用的系统和变量")],
        "energy-draft",
        "能量法半成品.md",
        "# 能量法：可接手的半成品\n\n"
        "取 V=(x²+y²)/2。代入模型可得 V'=-a x²-b y²+2kxy。\n\n"
        "目前只做到这一步。交叉项可能抵消对角项，不能直接声称 V'<0。\n\n"
        "待完成：尝试配方或 Young 不等式，写出对 k 的限制，"
        "并检查等号边界是否仍允许渐近稳定。这里没有形成 finding。\n",
        "输入同一模型；输出 V 与 V' 的推导草稿，后续可继续配方。",
        ["还未证明负定条件", "尚未检查等号边界"],
        [],
        "零 finding 是有意保留的演示状态；计算草稿不是稳定性结论。",
        ["继续处理交叉项 2kxy，而不是从头重算 V'。"],
        partial=True,
    )
    close(
        "spectrum-example",
        "Q-003",
        "特征值分支：区分算例与暂定解释",
        "用弱耦合算例形成直觉，同时把尚未验证的外推单独写出来。",
        "直接核算 a=b=1、k=1/4 的特征值；将算例事实与一般化猜想分开。",
        [source("origin", "model", "使用与能量法完全相同的系统定义")],
        "weak-example",
        "弱耦合算例与解释.md",
        "# 一个弱耦合算例\n\n"
        "令 a=b=1、k=1/4。矩阵的特征向量为 (1,1)、(1,-1)，"
        "对应特征值 -3/4、-5/4，因此这个算例趋向原点。\n\n"
        "暂定解释（仅用于演示后续修订）：既然独立子系统和这个弱耦合算例"
        "都稳定，也许任意耦合强度都能保持稳定。这个解释尚未得到证明，"
        "也没有覆盖强耦合；不能据此作为一般结论使用。\n",
        "一个可直接核算的算例，以及刻意保留为待检验状态的外推解释。",
        ["一般化解释尚未检查强耦合"],
        [
            {
                "id": "weak-observation",
                "text": "算例 a=b=1、k=1/4 的特征值为 -3/4 与 -5/4。",
                "conditions": "只针对这组构造参数；由两个特征向量直接核算，不是测量数据。",
                "evidence": ["#weak-example"],
            },
            {
                "id": "tentative-explanation",
                "text": "待检验解释：独立子系统稳定，也许足以保证任意耦合强度下整体稳定。",
                "conditions": "仅由独立系统和一个弱耦合算例启发；没有一般性证明，不应当作已建立结论。",
                "evidence": ["#weak-example", ref("origin", "uncoupled")],
            },
        ],
        "观察与外推必须分开读取。演示刻意留下一个需要后续修订的解释。",
        ["增大耦合强度，尝试构造能否定一般化解释的精确反例。"],
    )
    close(
        "energy-bound",
        "Q-002",
        "接续证明：交叉项给出耦合阈值",
        "已有 V' 草稿可以直接配方，不必重复起点工作。",
        "对 V' 配方，明确 a>0 和 k²<ab 时的充分条件。",
        [source("energy-draft", "energy-draft", "接着草稿中的 V' 处理交叉项")],
        "energy-proof",
        "配方得到充分条件.md",
        "# 接着半成品完成配方\n\n"
        "沿用 V'=-a x²-b y²+2kxy，可写成：\n\n"
        "V'=-a(x-(k/a)y)²-(b-k²/a)y²。\n\n"
        "若 a>0 且 k²<ab，则 a 与 b-k²/a 都为正，"
        "除原点外 V'<0。V 是正定二次型，故该常系数线性系统的原点渐近稳定。\n\n"
        "这里给出的是充分条件；它是否也是必要条件，应与另一个分支比较。\n",
        "输出固定线性模型的充分条件及配方证明，可供综合节点引用。",
        ["还未在本分支单独证明条件的必要性"],
        [
            {
                "id": "sufficient",
                "text": "对固定模型，a>0、b>0 且 k²<ab 足以保证原点渐近稳定。",
                "conditions": "二维常系数、对称耦合模型；依赖文中的二次型配方。",
                "evidence": ["#energy-proof"],
            }
        ],
        "证明只覆盖既定模型；不得直接外推到非线性、时变或非对称耦合。",
        ["与强耦合反例和边界特征值合并，检查条件是否恰好。"],
    )
    close(
        "counterexample",
        "Q-003",
        "反例修订：局部稳定还不够",
        "强耦合能检验先前外推，无需否定仍正确的弱耦合算例。",
        "取 a=b=1、k=2，检查 (1,1) 方向；只修订一般化解释，保留旧观察。",
        [
            source("spectrum-example", "tentative-explanation", "检验关于任意耦合强度的外推"),
            source("spectrum-example", "weak-observation", "明确哪些旧事实仍然成立"),
        ],
        "counterexample",
        "强耦合反例.md",
        "# 一个精确反例\n\n"
        "令 a=b=1、k=2。矩阵为 [[-1,2],[2,-1]]，"
        "特征值为 1 与 -3。在 (1,1) 方向，解为 e^t(1,1)，会增长而非趋向零。\n\n"
        "因此，即使独立子系统都稳定，任意耦合也保持稳定的外推仍然不成立。\n\n"
        "这不改变旧算例 a=b=1、k=1/4 的两个负特征值。"
        "修订的是一般化解释，不是那个算例的代数事实。\n",
        "输出反例参数、增长方向及被修订解释的范围。",
        ["反例否定无限制外推，但尚未独自给出完整阈值"],
        [
            {
                "id": "coupling-matters",
                "text": "构造反例 a=b=1、k=2 具有增长方向，局部子系统稳定不足以保证任意耦合下整体稳定。",
                "conditions": "只否定无耦合限制的一般化解释；旧弱耦合算例仍成立。",
                "evidence": ["#counterexample", ref("spectrum-example", "weak-observation")],
                "revises": ref("spectrum-example", "tentative-explanation"),
            }
        ],
        "这是手工构造的数学示例，不是模拟轨迹或真实观测；旧观察未被删除或标为错误。",
        ["将反例与能量法阈值一起用于综合判断。"],
    )
    close(
        "synthesis",
        "Q-001",
        "交叉汇聚：把保证与反例放到同一条件下",
        "两条路线回答了不同部分，现在可以明确结论的适用边界。",
        "结合能量法充分条件、反例和特征多项式，检查等号边界并保存剩余问题。",
        [
            source("energy-bound", "sufficient", "提供稳定性的充分条件和证明来源"),
            source("counterexample", "coupling-matters", "限制先前过强的解释"),
            source("spectrum-example", "weak-observation", "保留仍成立的算例作为一致性检查"),
        ],
        "synthesis",
        "两条路线的综合.md",
        "# 综合与边界\n\n"
        "能量法已给出 k²<ab 的充分性。矩阵特征多项式为"
        " λ²+(a+b)λ+(ab-k²)。这个实对称矩阵的两根均为实数；"
        "a+b>0 时，两根都负恰好要求 ab-k²>0。\n\n"
        "当 k²=ab 时有零特征值，不能保证所有初值趋向零；"
        "当 k²>ab 时两根异号，存在增长方向。于是，在固定模型中，"
        "渐近稳定的充要条件是 k²<ab。\n\n"
        "这既保留了弱耦合算例，也解释了强耦合反例。"
        "仍未覆盖非对称耦合与非线性扰动，应把这两种推广分开。\n",
        "给出固定模型的完整条件，以及面向两类推广的交接边界。",
        ["非对称耦合未分析", "非线性扰动下的邻域与正则性条件未写明"],
        [
            {
                "id": "scoped-conclusion",
                "text": "对 a>0、b>0 的二维常系数对称耦合模型，原点渐近稳定当且仅当 k²<ab。",
                "conditions": "仅限共同起点定义的线性模型，不是关于所有耦合系统的结论。",
                "evidence": [
                    "#synthesis",
                    ref("energy-bound", "sufficient"),
                    ref("counterexample", "coupling-matters"),
                ],
            }
        ],
        "仅完成了演示模型的综合，原始研究问题的更广泛版本仍然开放。",
        ["一条路线研究非对称耦合；另一条先整理非线性扰动所需假设。"],
    )
    propose(
        "next-asymmetric",
        "Q-003",
        "待开展：非对称耦合还能沿用什么",
        "下一条路线尚未领取，不把建议伪装成已完成工作。",
        "把 k 换成 k₁、k₂，重新检查特征多项式；说明旧条件中哪些来自对称性。",
        [source("synthesis", "scoped-conclusion", "明确推广前已经建立的结论与边界")],
    )
    open_node = propose(
        "open-nonlinear",
        "Q-002",
        "已领取未启动：整理非线性扰动的证明缺口",
        "展示 open 不等于正在运行；尚未启动任何 worker 或模型。",
        "先列出非线性余项需要满足的条件，再判断现有能量法能否给出局部邻域；" "即使做不完也应交接假设列表和推导片段。",
        [
            source("synthesis", "synthesis", "接续综合材料中列出的非线性推广缺口"),
            source("energy-bound", "energy-proof", "复用二次型证明，检查新增余项的位置"),
        ],
    )
    pending = store.reserve(open_node["id"], estimate=0, request_id="demo:reserve:open")
    store.set_attempt(
        pending["id"],
        demo=True,
        not_executed=True,
        execution_note="演示中的已领取状态；没有准备派发、PID、模型会话或实验。",
        progress={"stage": "已领取但未启动", "remaining": "列出非线性扰动需要的假设"},
    )
    store.notes(
        DEMO_NOTICE + "\n\n"
        f"能量法的部分推导位于「{ref('energy-draft', 'energy-draft')}」，该节点没有 finding。\n"
        f"弱耦合算例「{ref('spectrum-example', 'weak-observation')}」仍然成立；"
        f"一般化解释有后续修订，见「{ref('counterexample', 'coupling-matters')}」。\n"
        f"综合条件位于「{ref('synthesis', 'scoped-conclusion')}」，适用范围仅限演示线性模型。\n"
        "后续一条路线待开展，另一条仅处于已领取状态；没有真实 worker 在运行。\n"
    )
    store.event(
        "demo.created",
        {
            "notice": DEMO_NOTICE,
            "model_calls": 0,
            "numerical_experiments": 0,
            "node_count": 8,
            "states_are_illustrative": True,
        },
        request_id="demo:created",
    )
    problems = check(root)
    if not problems["ok"]:
        raise RuntimeError(f"演示项目引用检查未通过：{problems['problems']}")
    export_views(store)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构造明确标注为演示的研究路径图项目，不运行模型。")
    parser.add_argument(
        "--project", type=Path, default=Path("visual-demo"), help="新项目目录；拒绝覆盖任何已有路径",
    )
    args = parser.parse_args(argv)
    try:
        root = create_demo(args.project)
    except FileExistsError:
        parser.error("目标路径已经存在，演示脚本不会覆盖它；请换一个新目录。")
    print(DEMO_NOTICE)
    print(f"已创建 8 个演示节点：{root}")
    quoted = shlex.quote(str(root))
    print(f"查看路径图：ari -p {quoted} view")
    print(f"重新导出：ari -p {quoted} export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
