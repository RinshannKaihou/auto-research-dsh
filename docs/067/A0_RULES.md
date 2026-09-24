# A0 规则页：认识层承重的可执行语义

日期：2026-09-21。设计基线：`docs/RESEARCH_067_PLAN_DRAFT.md` v2.2。执行计划：`docs/RESEARCH_067_PLAN.md`。

标注 **A0 补充** 的条目是本页推演反例时新增或修正的语义，已按计划 §10 登记并写回设计基线；其余条目是冻结设计的转写。

本页是 A1 与 A2 的语义契约。**实施者不应需要自行猜测本页任何一条的含义**；若发现需要猜测，视为本页缺陷，按计划 §10 登记后修订本页，不在实现中就地决定。

每条规则标注来源：`S*` 指设计基线 §4 的设计决定编号，`R*` 指 astra 第一轮审阅编号，`V1-R*` 指第二轮，`第三轮` 指收口审阅，`执行计划` 指 astra 最终执行计划新增的澄清。

术语：**使用版本** `v_user` 是做出陈述的知识精确版本；**被用版本** `v_used` 是它所依据的知识精确版本。"精确版本"一律写作 `knowledge/K-nnn@r`，不接受不带版本的条目引用作为依据。

---

## 一、依据与图

### R1 顶点与边的方向 [S4]

影响图的顶点是**知识精确版本**，不是条目。边为 `v_user → v_used`，方向是"使用者指向被使用者"。求受影响集合时沿该边**反向**遍历。

### R2 统一的知识传播依据视图 [S3｜第三轮]

```
knowledge_support_refs(v) = dedup(
      { d ∈ dependencies(v)   | d 指向知识精确版本 }
    ∪ { e ∈ evidence_refs(v)  | e 指向知识精确版本 } )
```

传播、影响查询、发布核查**统一消费该视图**，不得任何一处只读 `dependencies`。每条边必须保留**来源字段标签**（`dependencies` 或 `evidence_refs`，两者皆有时记两者），用于解释路径与诊断。

推论：**只有 `dependencies` 与 `evidence_refs` 都不含 A，新版本才算不再以 A 为依据。**

`evidence_refs` 指向冻结材料（快照、发布项）时为**终端**，不继续传播。

### R3 依据是每个版本的完整快照 [S3｜O1]

`dependencies` 按**每个版本的完整快照**记录，不是增量。

| 写入形态 | 含义 |
|---|---|
| revise 未提供 `dependencies` | **继承前一版本的快照**，逐条复制，不是留空 |
| revise 提供非空列表 | 该列表即新版本的全部显式 `dependencies` |
| revise 提供空列表 `[]` | **仅清空显式 `dependencies`**，不表示已脱离旧依据 |

空列表的语义是 A0 最易被误实现的一条：按 R2，若 `evidence_refs` 仍指向 A，则该版本**仍在 A 的传播依据内**，边的来源标签为 `evidence_refs`。要真正脱离 A，必须同时更新 `evidence_refs`。程序**不擅自删除** `evidence_refs`，也不因 `dependencies=[]` 而推断脱离。

历史边保留原始 `source@revision`，用于追踪仍固定在旧版本上的使用，**不重新绑定到新版本**。跨版本并集只用于历史浏览，不参与传播。

### R4 启发不传播 [S3｜A0 补充]

`motivated_by` 同样按版本快照记录，**不进入** `knowledge_support_refs`，不参与传播。"作为基线"这类措辞按具体使用关系归入构成依据或启发，由写入者选择，程序不猜。

**A0 补充：`open_question` 该选哪个，给写入者一条判据。** 程序不判，但工具描述必须给出区分，否则节点锚点问题会被随手归到任一侧：

> 目标的主张**作为预设嵌在提问里**，提问离开它就不成立 → 写进 `dependencies`（构成依据）。
> 目标只说明**为什么此刻选这个节点做**，提问离开它照样成立 → 写进 `motivated_by`（启发）。

一个已被修订为"已回答"形式的 `open_question`，其陈述内容同时包含提问的预设与记录下的答案，两部分各自适用上述判据。

**预期后果要事先说清：** 节点锚点问题通常带预设，因而通常带依据边。于是撤回一条早期结论会波及其后各节点的锚点。这是设计意图，不是缺陷；噪声由 R18 的默认视图折叠与 R21 的处置消化，**不靠少记边来压制**。E1 的判例见 `docs/067/E1/04_RECHECK.md` §4.4。

### R5 关系写入的唯一入口与拒绝规则 [S2]

`research_memory` 的 `record` 与 `revise` 增加可选 `relations[]`，每项 `{type, target}`，`target` 必须是精确版本：

| type | 存储与语义 |
|---|---|
| `grounded_in` | **不单独存储**，写入时并入该版本的 `dependencies` |
| `answers` | 只允许指向 `open_question`；**不判定该问题已解决** |
| `supersedes` | 只声明，**不改目标状态**；自环为确定性非法输入，拒绝 |
| `complements` | 只声明 |
| `challenges` | 只声明，**不改目标状态**，与被挑战版本并存 |

通用 `research_relate` 保留 node、publication 与 `lineage_correction` 能力。**当 source 与 target 均为知识引用、且 label 属于上述五个保留词时，拒绝写入**，错误信息指向窄协议。其他通用标签不承诺语义检查，允许成环；查询须去重并保证终止。

对**已存在的精确版本**补充构成依据，只能通过 `revise` 追加新修订并返回新引用。禁止 `UPDATE` 旧修订。

---

## 二、变更事件

### R6 变更事件的产生与字段 [S4｜A0 补充]

`revise` 产生一条变更事件 `{change_id, knowledge_id, new_revision, kind, scope, reason}`。

`kind ∈ {retract, correct, narrow, reword}`，缺省 `correct`。普通 `record` **不产生**变更事件，不填 scope。

**A0 补充：`scope` 对 `revise` 是必填，没有缺省。** 三个取值都是修订者的实质声明，程序不得替他选。缺省 `none` 会静默宣称"不使任何既有版本失效"；缺省 `unknown` 会让每次措辞修订都向全部既有版本铺开候选根，产生无法承受的噪声。缺少该参数的 `revise` 请求**拒绝**，错误信息列出三个取值。

**A0 补充：`kind` 与 `status` 的一致性只做一条校验。** `status` 改为 `retracted` 时，`kind` 必须是 `retract`，否则拒绝。其余组合不校验。

**命名警告：** 该 `scope` 与既有列 `knowledge_revisions.scope`（陈述的适用范围）是**两个不同概念**，存储时必须使用不同列名，见 `A0_SCHEMA.md` §2。

### R7 scope 三值 [S4｜O2｜第三轮]

`scope` 是三值声明，**三者不得都编码为空列表**：

| 值 | 含义 |
|---|---|
| `versions:[...]` | 修订者明确声明受本次变更影响的本条目版本 |
| `none` | 修订者声明本次变更不使任何既有版本失效 |
| `unknown` | 修订者无法确定影响范围 |

程序**只检查结构性矛盾**，不判断科学正确性。必须拒绝的结构矛盾只有一类：**`status` 改为 `retracted` 时 `scope` 不得为 `none`**。`reword` 可以建议 `none`，程序不强制。

### R8 `unknown` 的可执行定义 [S4｜第三轮｜A0 补充]

`scope = unknown` 时：

- **候选根 = 该变更事件之前已存在的本条目全部版本。** 不包含未来版本，不包含其他条目。
- 据此生成的影响记录标 `scope_unconfirmed`。**A0 补充：该标记是派生的，按读取边界求值** —— 在边界 `N` 处，它等于"该变更在 `N` 处生效的 mode 是否为 `unknown`"。不存为行内不可变布尔，否则收窄之后无法同时满足"在 `N1` 重放为真"与"在 `N2` 为假"（CE-8e）。
- `unknown` 只产生"**可能受影响**"，不构成已证实失效；界面与发布核查必须按此措辞呈现。
- 处置之前，修订者可以收窄 scope。

### R9 收窄 `unknown` 须留可审计修订 [执行计划｜A0 补充]

收窄 scope 不是就地改写，而是**追加一条可审计的范围修订**，记录新范围、时间与理由，原始声明保留。

目的：**旧的发布核查仍能按当时的边界复现。** 一份在收窄之前完成的发布核查，重放时必须仍得到当时的结果，而不是被后来的收窄改写。

**A0 补充一：只能收窄，不能扩大，且单调。** 新范围必须是该 `change_id` **当前生效范围**的子集，不是原始候选根集合的子集。取原始集合作基准会放行 `{A@1,A@2}` → `[A@2]` → `[A@1]` 这种两步都"合法"、第二步实为扩大的序列，并要求把已失效的影响记录反转回有效，存储结构表达不了。不是子集的声明必须拒绝并要求另起一条变更事件。

收窄可多次，范围序列必须单调递减。允许的目标 mode 只有 `versions`：`unknown` 不是收窄，`none`（收窄到空集）须另起一条 `kind=retract` 以外的变更事件表达，不走此路径。`versions` 与 `none` 模式的变更**不可**被收窄，只有 `unknown` 可以。

**A1 复核补充（2026-09-21）：可收窄的判据是该变更的原始声明为 `unknown`，不是当前生效 mode。** 第一次收窄后生效 mode 已是 `versions`，仍可再收窄；每次的子集基准是当前生效范围。反例 CE-8e 步骤 5。

**A0 补充二：被收窄掉的影响记录保留，不删除。** 它们标记为"自某条范围修订起失效"。

**影响记录在读取边界 `N` 处有效，当且仅当两个条件同时成立：**

1. 它的检测序号 `≤ N`（**这一条不可省**：CE-7 要求以发布当时的序号重放时，发布之后才检测到的影响必须不可见）；
2. 没有使其失效的范围修订，或该范围修订的序号 `> N`。

两处比较都取闭区间 `≤`：同一事务写出的修订行、变更事件行与影响行共享同一个序号（现有实现取 `MAX(event_id)+1`），用开区间会把同一次写入的结果排除在自己的边界之外。

由此：失效的影响记录不再贡献 `RR2`，也不再计入 `needs_action`；但以收窄之前的序号重放，仍复现当时更宽的集合。已经挂在其上的处置记录一并保留，可查。

---

## 三、受影响集合

### R10 可达性与穿过历史版本 [S4]

对 scope（或 R8 的候选根）中每个版本，沿 `v_user → v_used` **反向**求可达集合。遍历**穿过历史版本**，不得只看每个条目的最新修订——后者会漏掉仍固定在旧版本上的使用。

**2026-09-21 用户裁决补充：status 为 `retracted` 的版本不作为受影响目标。** 它不再主张任何东西，无可重审，不为它建影响记录；遍历仍**穿过**它去找引用它的版本（撤回版继承的依据边仍是边）。作为根时按 R11 仍在集合内。反例 CE-10r。

### R11 零跳影响 [S4｜V1-R2｜A0 补充]

**影响集合恒包含根自身，`hop = 0`。** 不以"是否有下游知识"为条件，也不以"是否被固定"为条件。

是否把某条影响记录**呈现为当前风险**，由 R18 的 `in_use` 决定。因此当 publication 或节点 `inputs` 直接固定了被撤回或被判定失效的版本时，即使没有任何下游知识，该 hop=0 记录也会被呈现（反例 10）。

补充理由：根自身有影响记录，处置才有可挂载的键；否则无法对"根被撤回"这件事本身记录 `(change_id, affected_version)` 处置。

### R12 晚到引用按可达性检查 [S4｜V1-R1]

`record`、`revise`、`relate` **新增构成依据**时，检查新的使用版本能否沿版本图**到达任何已有变更的影响根**。

- 能到达：生成影响记录，**沿用原 `change_id`**，不制造新的依据变化事件。
- 新写入的版本 status 为 `retracted` 时不生成影响记录（R10 的 2026-09-21 补充：撤回操作追加的撤回版会继承依据边，不应因此成为受影响目标）。
- 只做"新依据是否恰好落在某个 scope 集合内"的**成员检查是错误的**，会漏掉间接影响（反例 9）。
- 登记这件事本身另留审计事件。

### R13 影响记录与幂等 [S4｜R2｜A0 补充]

影响记录 `{impact_id, change_id, affected_version, hop, review_state, disposition_ref, detected_sequence, voided_by_scope_revision}`。后两个字段是 R9 与重放所必需；`scope_unconfirmed` 按 R8 补充为派生，不存列。

**幂等键 `(change_id, affected_version)`。** 同一变更重复投递不重复建问题；**不同变更各自记录，互不吞掉**。以目标为键或以来源修订为键都会吞掉第二个独立变更（反例 1、12）。

`hop` 记录到该目标的**最短**跳数。**A0 补充：重复投递按 `hop` 取小合并，不是整行忽略。** R12 的晚到引用可能在已有 hop=2 的目标上补出一条直达边，整行忽略会把错误的 2 留下。合并只更新 `hop`，`review_state`、`disposition_ref` 与首次检测序号保持不变。

### R14 保存可重建路径的信息，不保存路径枚举 [S4｜V1-R5]

保存原始变更、精确边与受影响目标，**不保存路径枚举**。默认返回**一条**解释路径，其余按需展开。

### R15 三种不完整分开标记 [S4｜V1-R5]

三个标记彼此独立，不合并为一个"不完整"布尔：

| 标记 | 含义 |
|---|---|
| `targets_complete` | 受影响目标是否**完整确定**（`unknown` scope、未覆盖引用类型都会使其为假） |
| `targets_truncated` | 本次是否只展示了**部分目标**（有界查询分页） |
| `paths_truncated` | 本次是否只展示了**部分解释路径** |

**不要求计算未展示路径的总数。**

**A1 复核补充（2026-09-21）：`targets_complete` 是集合性质，不是页性质。** 对检查范围内全部有效影响行与全部已声明引用求值，不随 `limit`/`offset` 变化；某版本没有任何影响行、但其 `dependencies` 或 `evidence_refs` 含未覆盖类型时，该版本的核查 `targets_complete=false`（R16）。`paths_truncated` 不得由"目标自身出边数"推出 false：须在目标到根的可达子图上确认是否存在第二条路径，计数封顶为 2；无法确认时按 R16 不得报 false。发布核查的三个标记取各固定版本之或，`targets_truncated` 不得硬编码。反例 CE-8f、CE-8g、CE-8h、CE-14d。

### R16 覆盖范围与"未覆盖" [S4]

A1 只承诺**精确 K→K 传播**。`dependencies` 或 `evidence_refs` 指向 publication、note、node、attempt 等其他类型时，核查结果标"**该引用类型未覆盖**"。

禁止两种降级：不得显示为"无影响"；不得把冻结字节等同于结论永久有效。`targets_complete` 因此为假。

### R17 一致性读视图 [S4]

变更记录与修订**原子提交**。影响在同一读视图内**按需计算**，结果携带**账本序号上界**。**不做后台派生 worker。**

### R18 使用中是派生值 [S4｜O3｜A0 补充]

`in_use` 查询时派生，**不固化为永久布尔**。

**锚点集合**（两条，互不依赖）：

1. 某条目的**最新修订**，且其 status **不为 `retracted`**（2026-09-21 用户裁决，原为"不论其 status"：撤回是追加修订，撤回版继承旧版的依据边，若仍作锚点，会把它的全部上游永久钉为使用中。被固定的撤回版仍经判据 2 成为锚点。反例 CE-10r）；
2. 被 `publication_knowledge` 或节点 `inputs` **固定**的版本。

**`in_use(v)` 为真，当且仅当存在锚点 `a`，使 `a` 沿依据边正向可达 `v`（含 `a = v`，零跳）。**

这是设计基线"被另一个使用中版本引用"的非递归等价形式。写成可达性有三个好处：判据 3 不再需要单独定义、终止性可以证明、查询有明确方向。

**A0 补充一：终止性由构造保证，不设深度上界。** 依据边只能指向**写入时已存在**的精确版本（R5），故沿边走创建序号严格递减，支撑图按构造无环（CE-14）。加一个深度上界会把"其实在用"误判成"没在用"，这与 R16 禁止的静默降级是同一类错误，不可接受。实现仍须持访问集合，不得假设无环。

**A0 补充二：查询沿反向走并提前退出。** 从 `v` 出发经 `idx_support_used` 找引用者并逐层展开，命中第一个锚点即返回真。最坏情况才遍历全部引用者。

**A0 补充三：工作量上界只产生 `unknown`，不产生 `false`。** 实现持一个访问节点数上界（单处常量，缺省 10000，3080 规模的账本不会触及）。上界耗尽而仍未命中锚点时，`in_use` 返回 **`unknown`**，而不是 `false`。`unknown` 在呈现上**等同于使用中**并带明确标记。理由同补充一：这套设计从不允许把"没算完"显示成"没事"。

**A0 补充四：`in_use` 不参与 `needs_action` 与 `residual_use_risk` 的取值。** 它只决定**默认视图里是否展示**。`in_use=false` 的影响记录照常存在、照常可被处置、照常计入总数；默认视图把它折起来，但**必须显示"另有 N 条不在使用中"与展开入口**（R5 的总数与已展示数）。

**A1 复核补充（2026-09-21）：默认视图先选后分页。** 协调者上下文与工作台先选出在当前边界有效、当前有效处置为无或 `unresolved` 的影响行，再取上限并报告该集合的总数；已作废（R9）与已处置的行只在历史查看中出现。仅有 RR1（直接引用 status 为 `retracted` 的版本）而没有影响行的版本，另列一段"引用已撤回版本"，同样有总数与上限。反例 CE-10b、CE-12v。

publication 按其**已登记的 `knowledge_refs`** 做结构展开；**不把整篇发布的全部知识自动当作某条结论的前提**，不从正文推测。同一份发布里的两条知识不因共处一篇而互相成为依据。

---

## 四、风险、提示与处置

### R19 撤回风险与版本更新提示是两件事 [S4｜V1-R3｜第三轮｜A0 补充]

| 判据 | 后果 | reason 代号 |
|---|---|---|
| 被引用版本的 status 为 `retracted` | **构成依据风险**，与它是否出现在某个 scope 中无关 | `retracted_ref` |
| 被引用版本不是其条目的最新修订 | **仅版本更新提示**，不推出旧结论失效，不要求下游重审 | `newer_revision_exists` |

是否需要重审仍由 scope 与处置后果决定。`supersedes` 关系、`superseded` 状态与 `revised` 处置是**三个不同字段，不合并**。

**A0 补充一：撤回在不可变修订下的实现形态。** 撤回 `A@1` 的操作是"追加一条 status 为 `retracted` 的新修订 `A@2`" 加 "一条 `kind=retract`、`scope=versions:[A@1]` 的变更事件"。按 R3 禁止 `UPDATE` 旧修订，**`A@1` 自身的 status 不被改写**。因此两条捕获路径分工明确：

- 既有的下游使用（指向 `A@1`）由 **scope** 捕获，产生影响记录；
- 此后**新写**的、直接指向 `A@2` 的引用由 **`retracted_ref`** 捕获，即使 `A@2` 不在任何 scope 中。

**A0 补充二：版本更新提示的判据是派生的。** 设计基线原文写作"引用 status 为 `superseded` 的版本"。该判据对**已被更晚修订取代的旧版本**永不成立：把 `A@1` 的 status 改成 `superseded` 需要 `UPDATE` 旧修订，而 R3 禁止。因此判据改为**派生**：被引用版本存在更晚的修订即给出提示，reason 为 `newer_revision_exists`，文案"存在替代版本，请查看"。

`superseded` 仍是合法的 status 取值，写入者可以**追加**一条 status 为 `superseded` 的新修订，表示"本条目已被另一条目取代"。引用这样一个版本时：不产生 `RR1`（那只对 `retracted`）；若它就是最新修订，也不产生 `newer_revision_exists`。此时**给出独立提示** reason `superseded_ref`，与版本更新提示并列展示，同样不计入 `residual_use_risk`。此项按计划 §10 登记。

### R20 两个派生谓词分开 [S5｜A0 补充]

**`needs_action(v)`**：存在**在读取边界处有效的**（R9 补充二）影响记录 `(c, v)`，其当前有效处置为"无"或 `unresolved`（按 R22 真值表）。多个变更之间取**或**：任一未处置即为真。

**`residual_use_risk(v)`**：以下三条 reason 任一成立（可同时成立，全部列出，不合并为一条）：

| 代号 | 判据 |
|---|---|
| `RR1 retracted_ref` | `knowledge_support_refs(v)` 中存在一个版本，其 status 为 `retracted` |
| `RR2 in_change_scope` | 存在**在读取边界处有效的**影响记录 `(c, v)`，其当前有效处置为"无"或 `unresolved` |
| `RR3 disposed_old_ref` | `knowledge_support_refs(v)` 中存在版本 `u`，且某条影响记录 `(c, u)` 的当前有效处置为 `revised` 或 `retracted` |

版本更新提示（R19 第二行）**单独展示，不计入 `residual_use_risk`**。

**A0 补充（2026-09-21 修正）：publication 是引用方，按其已登记引用求值。** R20 的三条 reason 只对知识版本定义。一份 publication `P` 把它的 `publication_knowledge` 行展开成版本集合 `U`，对每个 `u ∈ U`：

| 代号 | 判据 |
|---|---|
| `retracted_ref`（P 级 RR1） | `u` 的 status 为 `retracted` |
| `disposed_old_ref`（P 级 RR3） | 某条影响记录 `(c, u)` 的当前有效处置为 `revised` 或 `retracted` |
| `u` 自身的谓词 | `needs_action(u)`、`residual_use_risk(u)` 的各条 reason 原样并入 |

`needs_action(P)` = 各 `u` 的 `needs_action` 取或；`residual_use_risk(P)` = 上述三类的并集，每条 reason 标明来自哪个 `u`；版本更新提示同理（`u` 非其条目最新修订即 `newer_revision_exists`）。原公式只取 `u` 自身谓词之或，测的是"固定的版本自己有没有事"，与 S13 后果表"对旧 publication 的后果"矛盾（CE-11：`B@1` 自身为假而固定 `B@1` 的 `P-001` 为真）。不从正文推测，不把整篇发布的全部知识互相当作前提（R18）。反例 CE-10a、CE-10c、CE-11、CE-11v。

**A0 补充：`RR2` 是按键求值的，不是"曾经落入过 scope"。** 这一条使 R23 的时间边界得以成立：对 `(c, v)` 处置为 `retained_with_evidence` 后，该键不再贡献 `RR2`，这次已解决的问题不再显示为当前风险；而 `RR1` 与 `RR3` 由引用关系决定，处置不清除它们，这正是 R21 后果表中"`revised` 与 `retracted` 不使旧引用恢复正常"的落实方式。

### R21 处置记录与后果表 [S13｜O5｜A0 补充]

处置针对 `(change_id, affected_version)`，**追加写入并留审计**：

| kind | 对该目标版本的含义 | 对旧 publication 与下游引用的后果 |
|---|---|---|
| `unresolved` | 已查看或延后，尚未解决 | 继续待重审；`needs_action` 保持为真 |
| `retained_with_evidence` | 负责人针对**该原因与该版本**有据保留 | 关闭该次问题；**不替其他目标或其他原因签署保留** |
| `revised` | 指向替代版本，原版本的问题已有处理动作 | 原引用显示"已有修订，仍引用受影响旧版"；替代版本**独立计算**风险 |
| `retracted` | 负责人撤回该目标 | 原引用继续显示风险（reason 为 `RR3`，不是 `RR1`）；后续使用者仍需处理 |

**命名警告：三个 `retracted` 是不同的东西**，照 R6 两个 `scope` 的体例列出，实现时不可混用：变更事件的 `kind=retract`；知识修订的 `status='retracted'`；处置的 `kind='retracted'`。`RR1` 只由第二个触发，`RR3` 只由第三个触发。

`revised` 与 `retracted` **不使旧引用恢复正常**。处置**只作用于该键**，不自动关闭同原因下的其他目标，也不自动关闭其他原因。负责人可一次有据处理多项，但每项处置必须明确。

入口为 `research_memory(action="dispose")` 或等价窄工具，**不新增通用审批门**。

### R22 当前有效处置 [S5｜第三轮]

真值表按**当前有效处置**计算，不按历史出现过的处置：

| 该键的当前有效处置 | `needs_action` |
|---|---|
| 无处置 | 真 |
| `unresolved` | 真 |
| `retained_with_evidence` | 假 |
| `revised` | 假 |
| `retracted` | 假 |

"当前有效处置"= 按读取边界选出该键**最新**的一条处置记录。**历史上曾有 `unresolved` 不使其永久为真。**

后两者虽然关闭 `needs_action`，仍按 R21 后果表保留旧使用提示，即 `residual_use_risk` 可以为真。

### R23 `retained_with_evidence` 的时间边界 [执行计划]

`retained_with_evidence` **关闭该原因与该版本的这一次问题**。历史上"曾经受影响"的事实仍可查询与复现。

**不得仅因为该版本曾经落入某个 scope，就永远把这次已解决的问题显示为当前风险。** 新的原因（另一个变更、另一条 reason）仍能再次触发，产生新的影响记录与新的待处置项。

### R24 复核进度与处置分离 [S13｜F14｜A0 补充]

复核进度 `pending | running | proposal_ready` 与处置是**两个维度**。

专家返回提案**只表示材料已准备**，`review_state` 置为 `proposal_ready`；**不得**因此把问题置为已解决，也不清除任何风险。现有 `consolidate` 在专家返回时置 `completed` 的行为必须改掉。

**A0 补充：影响记录如何进入 consolidate 的取件队列。** 现有 `consolidate` 从 `review_todos` 取件，而该表的键是 `(trigger_kind, trigger_ref)`，与 `(change_id, affected_version)` 没有映射，`review_todo_state` 的合法值也只有 `{pending, running, completed}`，没有 `proposal_ready`。因此必须明确三点：

1. **取件源改为 `knowledge_impacts`。** 新增窄接口按节点筛选、按检测序号升序返回最早一条"当前有效、未处置且 `review_state=pending`"的影响记录，返回 `(change_id, affected_version)`。（A1 复核补充 2026-09-21：不筛进度会反复领取同一条；`proposal_ready` 的项等待负责人处置，重审须显式把 `review_state` 置回 `pending`。反例 CE-2。）
2. **`review_state` 是 `knowledge_impacts` 上的列**，取值 `{pending, running, proposal_ready}`，与 `review_todos.state` 是两套状态，不互相映射。
3. **`review_todos` 保留现状并新增一个取值 `proposal_ready`。** 这是加法：`review_todo_next` 继续只选 `pending`，因此不会循环取件；consolidate 若同时消费了一条旧待办，把它置为 `proposal_ready` 而不是 `completed`。`completed` 此后只能由操作者经既有接口显式设置，程序不再自动写入。

---

## 五、发布核查

### R25 发布核查快照 [S5]

`complete` 发布时，在**同一读视图**内核查顶层与各 item 的 `knowledge_refs`，保存核查快照：

- 账本序号上界；
- 检查范围；
- 结果；
- R15 的三种不完整标记。

快照与发布记录**原子关联**，**不改历史 publication**。

**A1 复核补充（2026-09-21）：重放契约与两个序号。** 读边界 = 最后已提交事件的 `event_id`；写序号 = 该值加一。两者是不同函数，只读查询返回的边界不得等于下一次写入将拿到的序号。以快照的边界重放，判定字段（`status`、`flagged`、每版本的 `needs_action`、`residual_use_risk`、`version_notices`、`uncovered_refs`、三个不完整标记）必须逐字段复现；`version_notices` 按边界求值，只看序号不大于边界的修订。`review_state` 是复核进度，不入快照。反例 CE-7、CE-14 补充。

### R26 发布核查的边界表达 [S5]

- 无声明引用：显示"**覆盖未知**"，不显示"全部通过"。
- 发布后发生的新变化：作为**追加影响**可查，历史快照不被改写。
- **不阻止发布。** 引用不存在、版本冲突等完整性约束仍正常拒绝。

---

## 六、A1 与 A2 的分界

### R27 A1 的承诺范围 [S4｜v2 收口]

A1 只承诺 R1 到 R26 中的 **K→K 传播、处置与发布核查**。其他引用类型按 R16 显示"未覆盖"。

A2 承诺：`branches_from` 与 `revises` 允许空 `input_refs`（S6）；`asserted_at` 由服务端固定且不可被模型字段覆盖、可选 `execution_refs`、无法核实则"未关联"（S7）；`pub/P#item` 与 `S-xxx#path` 的统一冻结引用解析器（S14）；S8 候选提示**只报告**并并列展示已登记的修复证据。

### R28 明确不做的事 [§3 非目标]

不新增知识图 UI、推导边、Activity 对象、角色枚举、通用审批门、强制 `consolidate`、后台派生 worker；不修改历史 publication 与旧修订；不用正文正则生成账本边；不做科学真伪裁决。

---

## 七、A2 规则（2026-09-23 增）

A2 的语义契约。来源标注同上：S 编号取自设计基线 v2.3，其余注明出处。与 R1 到 R28 冲突时，以本节为准并在计划 §10 登记。

### R29 冻结引用文法 [S14]

四种形式，其余一律 `unsupported`：

| 形式 | 指向 |
|---|---|
| `pub/<publication_id>` | 整份发布 |
| `pub/<publication_id>#<item_id>[/<subpath>]` | 发布项；`subpath` 只在该项对象为目录时有意义 |
| `S-<nnn>` | 整份快照 |
| `S-<nnn>#<path>[/<subpath>]` | 快照清单中 `source_path` 等于 `path` 的一条记录；该记录为目录时可带 `subpath` |

- 只在**第一个** `#` 处切分。`pub/` 的 `item_id` 取到 `#` 后第一个 `/` 为止，其余为 `subpath`。`S-` 的 `path` 取 `#` 后全部字符，先做 percent-decoding，再按**最长前缀**匹配清单里的 `source_path`（记录本身或其目录前缀），余下部分为 `subpath`。
- 两侧同一规范化：去前导 `./`、去尾 `/`。不折叠 `..`；含 `..` 段、绝对路径、空段 → `unsupported/path_escape`。除此之外不改写。
- `subpath` 只在冻结的目录对象**内部**解析；越出对象根按 `path_escape` 拒绝。
- 登记时按**原文**存储引用字符串，不改写为规范形式；相等性与可用性判断一律经解析器。

### R30 解析结果与理由码 [S14]

每次解析恰有一种结果，理由码取固定词表，人读消息带上原引用与理由码：

| 结果 | 理由码 | 含义 |
|---|---|---|
| `resolved` | — | `kind` 说明引用命名的是什么：`publication`｜`publication_item`｜`snapshot`｜`snapshot_entry`｜`object_subpath`；`object` 说明对象：`{version, kind: file｜directory}` 或 null；`object_subpath` 另带 `subpath`；`integrity` 见 R31 |
| `unsupported` | `bad_syntax`｜`path_escape` | 不是 R29 的形式 |
| `not_found` | `publication_missing`｜`item_missing`｜`snapshot_missing`｜`entry_missing`｜`subpath_missing` | 账本或对象内没有这一项 |
| `ambiguous` | `duplicate_path` | 同一清单里两条记录的 `source_path` 相同；**不取第一条** |
| `unavailable` | `entry_incomplete`｜`object_missing`｜`object_kind_mismatch`｜`object_corrupted` | 账本有记录，对象不可用；`entry_incomplete` 附清单里存的 `error` 原文 |
| （读取级专用） | `not_an_object` | 引用合法且已解析，但没有对象可读（纯内容项、整份发布、整份快照）。只在 `open`、专家读取与 R37 核实时出现，`resolve` 不产生 |

`pub/P#item` 的项没有对象（纯内容项）时是 `resolved`，`object` 为 null——不是错误。

### R31 两级校验 [S14]

- **`resolve`**：账本定位 + 对象存在 + kind 与磁盘一致。不算摘要。`integrity: "unverified"`。
- **`open`**（materialize、读取内容）：`resolve` 之后用 `ArtifactStore.verify` 校完整摘要。通过则 `integrity: "verified"`；不通过 → `unavailable/object_corrupted`。

理由：真实账本有 10.8 GB 的目录对象；每次 query 或界面跳转都算完整摘要会让账本不可读。查询结果必须**如实标注**自己在哪一级；没算摘要就不得写 `verified`。

### R32 单一入口 [S14]

五个入口消费**同一个函数**：证据校验（`memory_store` 与 `native_store` 的引用检查）、`research_query(ref=…)`、材料准备（分支输入、讨论会话 materialize、专家逐段读取）、检查器（B）、界面跳转（前端向服务端问同一函数；前端自行解析只限于为显示切分字符串，**不判断可用性**）。同一引用在同一读取边界下，各入口得到同一结果与同一理由码。解析器之外任何一处再解析 `pub/` 或 `S-` 即为缺陷。

### R33 只读冻结对象 [S14]

解析与读取只触及 `.research/objects/<version>`，不触及工作区，也不触及清单里记录的原始 `source_path`。工作区里同名文件**永远不是回退**：对象缺失就是 `unavailable/object_missing`，不是"改读工作区"。

### R34 登记前报错 [S14]

写入时（`evidence_refs`、`inputs`、relation 两端）遇到解析为 `unsupported`、`not_found`、`ambiguous`、`unavailable` 的冻结引用，先按理由码拒绝，**不写任何东西**。登记时校到 `resolve` 级（存在 + kind），不算摘要。整份快照 `S-xxx` 仍可登记（它是合法引用），但作为 claim/observation 的证据会触发 R38 的提示。

**更正（2026-09-23，计划 §10）：`knowledge_refs` 不是冻结引用的登记面。** 发布级与项级 `knowledge_refs` 只接受知识精确版本，任何冻结引用在此拒绝（沿用 0.6.7 的 `Invalid knowledge reference`），不论它能否解析。发布核查（R20、R25）与 `in_use` 锚点（R18）只读这里的知识版本。

**补充（2026-09-23，B 设计时发现，A2-N6）：修订只校验新引入的引用。** 登记校验针对的是本次写入**新引入**的引用；revise 从当前版本继承的、或在 `changes` 里原样保留的引用，登记时已校验过，不再重做。A2 实现对继承的 `evidence_refs` 也重做了解析级校验，于是冻结对象一旦丢失，引用它的条目就再也不能修订——**连撤回都不行**（在 0.6.8 上实测：`ValidationError: S-001#results/m.json: object_missing`）。0.6.7 只查账本记录，不存在这个问题，是 S14 带进来的回归。B 一并修复，见 CE-26。

### R35 谱系关系允许空输入 [S6]

`branches_from` 与 `revises` 的 `input_refs` 必须是列表，**可以为空**；`depends_on` 保持非空（调度契约）。固定输入 = 各前驱 `input_refs` 按声明顺序去重合并，可以为空；显式给出的 `inputs` 仍须与之完全相等（此规则不变）。只有谱系前驱、没有输入的节点合法，且**不是根**。

### R36 记账来源由服务端固定 [S7]

每条知识修订、关系、发布写入时，服务端写 `asserted_at = {host_id, session_id, turn, operation_id}`，取自执行工具的上下文与 `turn_bindings`。模型字段里出现 `asserted_at` → `ValidationError`，**显式拒绝，不静默丢弃**（与 A1 返修 S5 同一纪律）。现有 `source_identity` 保留为模型自述，界面上标为"自述"，两者**不合并**。无会话上下文的写入（`maintenance_cli` 等离线路径）：`turn` 为 null，`host_id`/`session_id` 填工具名，不留空。

**覆盖面（2026-09-23 补充，§3.3 开工前对照源码）。** "每条"指全部写入口，不只 `memory_write`：知识修订——`memory_write` 的 record/revise，以及 `propose` 写入的节点问题条目；关系——`relate`，以及 `memory_write` 的 `relations[]` 写出的关系行（与所属修订**同一个** `asserted_at`；`grounded_in` 不写关系行，它并入该修订的 `dependencies`，由修订自身的 `asserted_at` 覆盖，见 R5 与 CE-13a）；发布——`publish`。旧账导入（`migration.py`）与 schema 升级回填（`K-legacy-*` 问题条目）写的是旧内容，不是新的断言：`asserted_at` 为 null，与历史行同样显示"历史记录，无来源"。

### R37 执行来源可选、核实分明 [S7｜依赖 S14]

`execution_refs` 是 record/revise 的可选列表。接受的形式：`session:<session_id>`（`workflow_sessions` 或 `exploration_tasks`）、`attempt:<attempt_id>`、`event:<event_id>`、按 R29 解析为对象的冻结产物引用。每条存为 `{ref, status, reason}`：核实通过 → `linked`；其余一律 `unlinked` 并带理由（`unsupported_form`、`not_found`、或解析器理由码）。`unlinked` **不拒绝写入**。核实在写入时做一次并存下，不重算；之后对象被删不改写历史。展示时 `unlinked` 显示"未关联"，不隐藏。

**revise 不继承（2026-09-23 裁定）。** revise 的 `fields` 不含 `execution_refs` 时，新修订存 `[]`，旧修订不变；给出则在这次写入时按上文核实。方向与依据相反是有意的：依据（R3）继承，多算只会多审，是安全侧；执行来源若继承，就替一个没人声明过的归因作证，是不安全侧。结果来源未变的修订（例如措辞修订）须显式再给一次，工具描述写明。

### R38 候选提示只报告 [S8]

四类，每条提示 = `{class, target, evidence, candidate: true, repair_evidence}`。不写入、不阻塞、措辞不称"诊断"。协调者上下文与工作台按 `affected_knowledge` 同款给 `total`、`shown_count`、分页（先选后分页）。

| class | `target` | 触发与 `evidence` | `repair_evidence` |
|---|---|---|---|
| `prose_mention_without_relation` | 做出提及的修订 `knowledge/K@r` | 修订的 `statement`/`scope`/`conditions`（JSON 字段按序列化文本扫描）提及 `K-nnn`（带或不带 `@r`）或 `X-nnn`；`evidence = {mention, field}`，K 提及另带 `candidate_versions`。**K 提及**：被提及条目的任一版本都不在本修订的 `dependencies` ∪ `evidence_refs` 中即触发；不带 `@r` 时 `candidate_versions` 为本修订 `source_sequence` 时该条目的现行版本。**X 提及**：不是本修订所属节点，且 `relations` 表中（任何标签）没有一行连接 {本条目的任一引用形式（`knowledge/K` 或 `knowledge/K@r`）, 本节点} 与该 X 时触发 | K 提及：同一条目更晚的修订中 `dependencies` ∪ `evidence_refs` 含被提及条目者。X 提及：无（见下"消解"） |
| `whole_snapshot_evidence` | claim/observation 修订 | `evidence_refs` 含裸 `S-xxx`；`evidence = {ref}` | 同一条目更晚的修订中 `evidence_refs` 已无裸 `S-xxx`、且含 `S-xxx#path` 或发布项者 |
| `lineage_mention_without_predecessor` | 节点 `X-nnn` | 无前驱的节点，其 `question`/`root_reason`/`plan` 提及其他 `X-nnn`；`evidence = {mention, field}` | `label='lineage_correction'` 且 `target_ref` 为该节点的关系 id |
| `complete_publication_cites_risk` | `pub/P` | `complete` 发布在当前状态按引用方公式核查为 `attention`；`evidence = {status, flagged}`。**复用 `publication_check`，不二次求值** | —（处置后核查转清，提示随之消失） |

条目提及自身编号不是提示。

**求值时点与消解（2026-09-23 补充，§3.4 开工前）。** 提示按**当前状态**求值，不承诺按读取边界重放——R25 的重放契约只覆盖影响查询与发布核查；返回的 `sequence_bound` 只标明读取时刻。症状落在不可变记录上的提示**不消失**，修复只能并列：K 提及与整份快照证据（修订自身字段）、无前驱（`node_dependencies` 建节点时即定）。症状是可补的结构缺失或当前风险的提示**随之消解**：X 提及在连接关系出现后不再触发（关系本身就是缺失的结构，不必并列）；第四类在处置后不再触发。

**排序。** 先按上表 class 顺序，再按 `target` 字符串，再按 `evidence.mention`；先选后分页。

### R39 提示的不确定性 [S8｜E2a]

每条提示只有 `confidence: "candidate"` 一档。E2a 对照 E1 裁决：`prose_mention_without_relation` 命中 E1 标为构成依据或启发的版本对记真阳；E1 标为仅提及或无关系记假阳；无法判断记未覆盖；E1 **没有裁决**的版本对记"记录不足"，单列，不计入真假阳。

### R40 A2 明确不做 [§3 非目标]

不从提示生成任何边；不改写既有 `dependencies`/`evidence_refs`；不做后台复核 worker；query 不 materialize；整份快照引用仍合法（只提示）；不做 S9 字段检查（那是 B）。

---

## 八、B 规则（2026-09-23 增）

B 的语义契约，只实现设计基线 S9 的"一项可复查的字段一致性检查"（astra 首轮 R7、Q3）。与前文冲突时以本节为准，并在计划 §10 登记。

### R41 声明 [S9]

- claim 与 observation 可在 record 的 `checks`、revise 的 `changes.checks` 中声明一组检查；其他 kind 声明非空 `checks` → `checks: kind_not_checkable`。每项的键**恰为** `ref`、`path`、`op`、`value`，`approx` 另有 `tolerance`；多出的键 → `unknown_field`。
- `ref`：冻结引用（R29），登记时按 R34 在解析级（不算摘要）判定，且必须落到一个**普通文件**——文件对象（发布项或快照记录），或目录对象内指向普通文件的子路径。解析成功但不是文件（整份发布、整份快照、纯内容项、目录对象本身、指向目录的子路径）→ `not_a_file`；解析失败 → R30 的理由码；不是 `pub/` 或 `S-` 形式 → `not_frozen`。
- `path`：JSON Pointer（RFC 6901），为 `""` 或以 `/` 开头，`~` 后只能跟 `0` 或 `1`；否则 `bad_pointer`。
- `op` ∈ {`eq`, `approx`, `lt`, `le`, `gt`, `ge`}；否则 `bad_op`。
- `value`：必填，JSON 标量（数、字符串、布尔、null），数必须有限；`approx`/`lt`/`le`/`gt`/`ge` 要求有限的数且不是布尔。否则 `invalid_value`。
- `tolerance`：只用于 `approx` 且必填，缺 → `tolerance_required`；负 → `negative_tolerance`；不是有限的数或是布尔 → `invalid_tolerance`；其他 op 带了 → `tolerance_not_allowed`。
- 缺少必需的键按该键的理由码报：缺 `ref` → `not_frozen`，缺 `path` → `bad_pointer`，缺 `op` → `bad_op`，缺 `value` → `invalid_value`。一项有多处错误时按 `unknown_field`、`ref`、`path`、`op`、`value`、`tolerance` 的顺序只报第一处；多项有错时报下标最小的一项。
- 任一项声明错误，整次写入拒绝，什么都不写。错误消息首行为 `checks[<i>]: <code>`（`i` 从 0 起；`checks` 不是列表时为 `checks: not_a_list`）；R30 的 `not_found` 类抛 `NotFoundError`，其余抛 `ValidationError`（与 R34 一致）。
- **revise 继承声明。** `checks` 是陈述的内容，与 `evidence_refs` 同属 R3 的完整快照：`changes` 不含 `checks` → 原样继承；`[]` → 清空；给出 → 替换。（`execution_refs` 是归因，R37 不继承；两者方向相反是有意的。）登记校验只针对**本次新引入**的声明（与当前版本逐字相同的项不算新引入，见 R34 补充）；所有声明都在新版本上重新求值——对象此后不可用就记 `not_checkable`，**不阻止**这次修订（例如撤回）。
- 声明按原文、按给出顺序存在该修订上。

### R42 求值 [S9]

在同一写事务内、修订行写入之后，对新版本的每项声明求值一次：

1. `frozen_refs.open(ref)`（校验所属对象的完整摘要，R31）。失败 → `not_checkable`，理由取解析器的理由码（写入时实际只会是 `object_corrupted` 或 `object_missing`）。
2. 读出文件字节，按 UTF-8 严格解码并解析 JSON（非标准的 `NaN`/`Infinity` 作为数接受）。失败 → `not_checkable`/`unsupported_format`。
3. 按 RFC 6901 取值：数组下标只认 `0` 或不以 0 开头的十进制；`-`、越界、键不存在都算缺失 → `not_checkable`/`missing_value`。
4. 取到对象或数组 → `not_checkable`/`not_a_scalar`。
5. 数值 op（`approx`/`lt`/`le`/`gt`/`ge`）：实际值必须是有限的数且不是布尔，否则 `not_checkable`/`invalid_number`。`approx` 判 `|实际 − value| ≤ tolerance`（绝对容差）；其余按字面比较。成立 → `consistent`；否则 `inconsistent`/`value_mismatch`。
6. `eq`：`value` 是数而实际值是非有限的数 → `not_checkable`/`invalid_number`。按 JSON 类型比（数、字符串、布尔、null；布尔不是数）：类型不同 → `inconsistent`/`type_mismatch`；类型相同且相等（数按数值，`12 == 12.0`）→ `consistent`，否则 `inconsistent`/`value_mismatch`。

数值 op 没有数就无从判断，所以是 `not_checkable`；`eq` 总能比出相等与否，所以类型不同是 `inconsistent`。结果每个目标版本求一次并存下，不重算；对象此后被删也不改写（与 R37 同）。

### R43 结果记录 [S9｜astra R7]

每项结果一行：`target`（精确版本）、`check_index`、`spec`（该项声明原文）、`input_ref`（声明里的原引用）、`object_version`（所属对象的版本）、`input_sha256`（实际读取字节的 SHA-256；文件对象时即其版本；没读到字节为 null）、`observed_text`（实际值按 JSON 重新序列化：键排序、非有限数写作 `NaN`/`Infinity`、截到 200 字符；缺失或没读到为 null）、`tolerance_rule`（`approx` 为 `"abs"`，其余 null）、`checker_version`（`"field-check/1"`）、`result`（`consistent`｜`inconsistent`｜`not_checkable`）、`reason`（`consistent` 为 null，其余为上文理由码）、`sequence`（该修订的 `source_sequence`）。

### R44 展示与边界 [S9｜astra R7]

- 界面：`consistent` 显示"声明字段与冻结文件一致"；`inconsistent` 显示"声明字段与冻结文件不一致"并给出实际读值；`not_checkable` 显示"无法检查：<reason>"。任何地方都不写 verified、"已验证"、"验证通过"之类。
- `consistent` 只说明**声明的那个字段**与冻结文件一致，不说明陈述的结论成立（astra R7：正文写"FPR < 0.01"而声明 `value=0.01040`，检查会一致，结论却不成立）。工具描述写明。
- 检查结果不改 `status`，不进 R20 的两个谓词，不产生 R38 的提示，不生成边。
- `research_query(ref=…)` 的知识条目带 `checks`（声明）与 `field_checks`（结果，按 `check_index` 排序）；工作台逐项显示。本切片不进协调者上下文。

### R45 B 明确不做 [§3 非目标]

不从正文抽取数值；不自动生成检查；不提供重跑（换 checker 版本后重查进 backlog）；只支持 JSON；只有绝对容差；检查结果不作发布门，也不影响风险、提示与边。

---

## 附：最易实现错的六条

按 F14 规范探针与三轮审阅的实际发现排序，实现与验收时优先核对：

1. **R3** `dependencies=[]` 不等于脱离依赖——`evidence_refs` 仍可能承载该边（反例 13 变体）。
2. **R10** 只遍历最新修订会漏掉仍固定旧版本的使用（反例 4）。
3. **R12** 晚到引用只做成员检查会漏掉间接影响（反例 9）。
4. **R13** 幂等键取目标或取来源修订，会吞掉第二个独立变更（反例 1、12）。
5. **R19** 把 `superseded` 当作依据失效，会把版本更新噪声升级成重审要求（反例 5）。
6. **R24** 专家返回即置已解决，会在风险仍在时清除待办（反例 2）。
