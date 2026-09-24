# A0 反例预期：十四条及其变体的确定输出

日期：2026-09-21。规则页：`docs/067/A0_RULES.md`。来源：设计基线 `docs/RESEARCH_067_PLAN_DRAFT.md` v2.2 §6.1。

本文件是 A1 验收的**唯一真值来源**。`tests/test_067_regressions.py` 的断言取自本文件；**不得以实现输出反写本文件的预期**。若实现与本文件冲突，先判定是本文件的缺陷还是实现的缺陷，前者按 `docs/RESEARCH_067_PLAN.md` §10 登记后修订，后者改实现。

## 0. 记号与全局约定

| 记号 | 含义 |
|---|---|
| `A@1` | 知识条目 A 的第 1 号精确版本 |
| `deps` | 该版本的 `dependencies` 快照 |
| `ev` | 该版本的 `evidence_refs` 快照 |
| `mot` | 该版本的 `motivated_by` 快照（启发，不传播） |
| `CH1` | 一条变更事件的 `change_id` |
| `(CH1, B@1)` | 影响记录的幂等键 |
| `S-002` | 冻结快照引用，终端，不传播 |

本文用 `scope=versions:[...]`、`scope=none`、`scope=unknown` 指变更事件的影响范围。**存储与接口中它落为两列 `affected_scope_mode` 与 `affected_scope`**（`A0_SCHEMA.md` §2），因为 `knowledge_revisions.scope` 已表示陈述的适用范围。写断言时用后者。

全局约定，不在每条反例里重复：

- **影响集合恒含根自身，`hop=0`**（R11）。下表一律显式列出该行。
- `needs_action` 与 `residual_use_risk` 均按**版本**求值，多个变更取或（R20）。
- 撤回 `A@1` 的操作形态固定为：追加 `A@2`（status=`retracted`）+ 变更事件 `kind=retract, scope=versions:[A@1]`；`A@1` 的 status 不被改写（R19 补充一）。
- **`scope` 对 `revise` 必填，无缺省**（R7 补充）。本文每一个 `revise` 步骤都显式写出它的 `scope`；没有写出 `scope` 的步骤是本文的缺陷，不是"按缺省处理"。
- 每条反例结束时断言：`knowledge_revisions` 中既有行的 `statement`、`deps`、`ev`、`status` 逐字段未变；`publications` 与其核查快照未被 `UPDATE`。

---

## CE-1 幂等与独立变更不互相吞掉 [R13]

**初始**

| 版本 | deps | ev | status |
|---|---|---|---|
| `A@1` | `[]` | `[S-001]` | working |
| `B@1` | `[A@1]` | `[]` | working |

**操作顺序**

1. 撤回 `A@1` → `A@2`(retracted) + `CH1`(`retract`, scope=`versions:[A@1]`)
2. **重放同一变更**：以同一 `change_id=CH1` 再次触发影响计算
3. 再次变更 `A@1` → `A@3` + `CH2`(`correct`, scope=`versions:[A@1]`)

**预期影响记录**（步骤 3 之后，共 4 行）

| change | affected_version | hop | scope_unconfirmed | 处置 | needs_action | residual_use_risk |
|---|---|---|---|---|---|---|
| `CH1` | `A@1` | 0 | false | 无 | true | RR2 |
| `CH1` | `B@1` | 1 | false | 无 | true | RR2 |
| `CH2` | `A@1` | 0 | false | 无 | true | RR2 |
| `CH2` | `B@1` | 1 | false | 无 | true | RR2 |

**关键断言**

- 步骤 2 之后影响记录总数仍为 **2**，`(CH1, B@1)` 只有一行；重放不新增行，也不更新既有行的 `review_state` 与 `disposition_ref`。
- 步骤 3 之后总数为 **4**。第二个独立变更**必须**产生新行。
- 反例意图：幂等键若取 `affected_version` 或取"来源修订"，步骤 3 会被吞掉，总数错为 2。

**覆盖状态** `targets_complete=true`，`targets_truncated=false`，`paths_truncated=false`。

---

## CE-2 复核进度不清除风险 [R24]

**初始** `A@1`；`B@1` deps=`[A@1]`。

**操作顺序**

1. 撤回 `A@1` → `CH1`
2. `review_state`: `pending` → `running`
3. 专家返回提案 → `review_state = proposal_ready`（含 `consolidate` 路径）

**预期：三个时刻的取值完全相同**

| 时刻 | `(CH1,B@1)` 存在 | review_state | disposition | needs_action(`B@1`) | residual_use_risk(`B@1`) |
|---|---|---|---|---|---|
| 步骤 1 后 | 是 | pending | 无 | true | RR2 |
| 步骤 2 后 | 是 | running | 无 | true | RR2 |
| 步骤 3 后 | 是 | proposal_ready | **无** | **true** | **RR2** |

**关键断言**

- 步骤 3 **不得**写入任何处置记录，`disposition_ref` 保持为空。
- 现有 `consolidate` 在专家返回时把待办置 `completed` 的行为必须移除；`review_state` 的三个取值与处置是正交维度。
- 反例意图：F14 规范探针发现的实际行为（`tools.js` consolidate 路径）会在此处错误地清零风险。
- **A1 复核补充（2026-09-21）[R24]**：`impact_next` 只领取 `review_state=pending` 的有效未处置行。步骤 3 之后再次调用，**不返回** `(CH1,B@1)`；若另有一条 pending 的行则返回它，否则返回空。重审须显式把 `review_state` 置回 `pending`。

---

## CE-3 有据保留关闭该次问题；新原因再次触发 [R21｜R22｜R23]

**初始** `A@1`；`B@1` deps=`[A@1]`。

**操作顺序**

1. 撤回 `A@1` → `CH1`
2. `dispose(CH1, B@1) = retained_with_evidence`，附理由与证据引用
3. 再次变更 `A@1` → `CH2`(`correct`, scope=`versions:[A@1]`)

**预期**

| 时刻 | 键 | 当前有效处置 | needs_action(`B@1`) | residual_use_risk(`B@1`) |
|---|---|---|---|---|
| 步骤 1 后 | `(CH1,B@1)` | 无 | true | RR2 |
| 步骤 2 后 | `(CH1,B@1)` | retained_with_evidence | **false** | **false** |
| 步骤 3 后 | `(CH2,B@1)` 新增 | 无 | **true** | **RR2** |

**关键断言**

- 步骤 2 后 `residual_use_risk(B@1)` 为**假**：`RR2` 按键求值，已处置的键不再贡献；`RR1` 不成立（`B@1` 指向 `A@1`，其 status 未被改写，不是 `retracted`）；`RR3` 不成立（`(CH1,A@1)` 未被处置为 `revised`/`retracted`）。
- 历史可查：步骤 2 之后查询"`B@1` 曾经受哪些变更影响"仍返回 `CH1`，并显示处置为 `retained_with_evidence`。**已解决的这一次不显示为当前风险**（R23）。
- 步骤 3 产生新键，不复用 `CH1`，不被已有处置覆盖。

---

## CE-4 历史版本上的使用不被最新版本遮蔽 [R10]

**初始**

| 版本 | deps | ev |
|---|---|---|
| `A@1` | `[]` | `[S-001]` |
| `B@1` | `[A@1]` | `[S-002]` |
| `C@1` | `[B@1]` | `[]` |

**操作顺序**

1. 修订 B → `B@2`，`dependencies=[]`，`ev` 继承 `[S-002]`（快照，终端），`CH0`(`correct`, **scope=`none`**)
2. 撤回 `A@1` → `CH1`

**预期影响记录**（步骤 2 之后，共 3 行）

| change | affected_version | hop | 解释路径 |
|---|---|---|---|
| `CH1` | `A@1` | 0 | 根 |
| `CH1` | `B@1` | 1 | `B@1 --deps--> A@1` |
| `CH1` | `C@1` | 2 | `C@1 --deps--> B@1 --deps--> A@1` |

**关键断言**

- **不存在** `affected_version = B@2` 的行。`knowledge_support_refs(B@2) = {}`（`[]` 清空显式依赖，`ev` 只含终端快照）。
- `C@1` 被识别，`hop=2`。反例意图：只遍历每个条目的最新修订（`B@2`）会漏掉 `C@1`。
- 解释路径必须经过 `B@1`，**不得**把 `B@2` 描述为使用旧依据。
- 步骤 1 的 `CH0` scope=`none`，**不产生任何影响记录**。

---

## CE-5 编辑性更新不无差别影响所有使用者 [R7｜R19]

**初始** `A@1`；`B@1` deps=`[A@1]`；`D@1` deps=`[A@1]`。

**操作** 修订 A → `A@2`，`kind=reword`，`scope=none`。

**预期**

| 项 | 值 |
|---|---|
| 新增影响记录 | **0 行** |
| needs_action(`B@1`) | false |
| needs_action(`D@1`) | false |
| residual_use_risk(`B@1`) | **false** |
| residual_use_risk(`D@1`) | **false** |
| `B@1` 与 `D@1` 的版本更新提示 | **有**，reason=`newer_revision_exists`，文案"存在替代版本，请查看" |

**关键断言**

- 版本更新提示与 `residual_use_risk` **分列两栏**；提示不计入风险，不产生待办，不要求重审（R19）。
- 反例意图：把"存在更新版本"当作依据失效，会让每次措辞修订都向全部使用者发出重审要求。
- `kind=reword` 且 `scope=none` 合法，程序不拒绝。对照：若同一次修订把 status 改为 `retracted` 而 `scope=none`，**必须拒绝**（R7 唯一的结构矛盾检查）。

---

## CE-6 撤回后新增的依赖被识别；启发不传播；两原因不合流 [R12｜R4｜R13]

**初始** `A@1`；`D@1`。

**操作顺序**

1. 撤回 `A@1` → `CH1`
2. `record E@1`，deps=`[A@1]`（晚到引用）
3. `record F@1`，`mot=[A@1]`，deps=`[]`，ev=`[]`（启发）
4. `record H@1`，deps=`[A@1, D@1]`
5. 撤回 `D@1` → `CH2`

**预期影响记录**（步骤 5 之后，共 5 行）

| change | affected_version | hop | 说明 |
|---|---|---|---|
| `CH1` | `A@1` | 0 | 根 |
| `CH1` | `E@1` | 1 | 晚到引用，**沿用 `CH1`** |
| `CH1` | `H@1` | 1 | 晚到引用，**沿用 `CH1`** |
| `CH2` | `D@1` | 0 | 根 |
| `CH2` | `H@1` | 1 | 第二个原因 |

**关键断言**

- **不存在** `affected_version = F@1` 的行。`mot` 不进入 `knowledge_support_refs`。
- 步骤 2、4 **不产生新的变更事件**；`record` 只产生登记审计事件，`change_id` 沿用 `CH1`。
- `H@1` 有**两条**影响记录，键不同（`CH1`/`CH2`），互不覆盖。处置其中一条不影响另一条（见 CE-12）。
- needs_action(`H@1`)=true（两条皆未处置）；residual_use_risk(`H@1`)=RR2，列出两个来源变更。

---

## CE-7 发布后的变化仍可查；历史发布不被重写 [R25｜R26]

**初始** `A@1`；`B@1` deps=`[A@1]`；发布 `P-001` `complete`，`knowledge_refs=[B@1]`。

发布时保存核查快照 `CK1`：账本序号上界 `N1`，检查范围=顶层与各 item 的 `knowledge_refs`，结果=无影响，`targets_complete=true`。

**操作** 发布之后，撤回 `A@1` → `CH1`。

**预期**

| 项 | 值 |
|---|---|
| 新增影响记录 | `(CH1, A@1, 0)`、`(CH1, B@1, 1)` |
| `CK1` 的字节内容 | **未变**：`N1`、结果、三个标记逐字段相同 |
| `P-001` 的 `publication_knowledge` 行 | 未变 |
| `B@1` 的修订行 | 未变 |
| "发布后变化"查询 | 返回 `(CH1, B@1)`，并标明其账本序号 **大于** `N1` |

**关键断言**

- 断言 `CK1` 未被 `UPDATE`：以 `N1` 重放核查应复现发布当时的结果（无影响），**不是**现在的结果。
- 新影响作为**追加**可查，不改写任何历史对象。

---

## CE-8 缺边、分页与证据不足的显示 [R15｜R16｜R8｜R9]

五个子例，分别断言。

**CE-8a 引用类型未覆盖**

初始：`A@1`；`B@1` deps=`[A@1, pub/P-001]`（一条知识引用，一条非知识引用）。操作：撤回 `A@1` → `CH1`。

| 项 | 值 |
|---|---|
| 影响记录 | `(CH1,A@1,0)`、`(CH1,B@1,1)`，首跳 `edge_source='dependencies'` |
| `B@1` 的核查显示 | "部分引用类型未覆盖"，并列出 `pub/P-001` 为未覆盖项 |
| `targets_complete` | **false** |
| 禁止显示 | 对 `pub/P-001` 显示"无影响"或"全部通过" |

初始态必须含至少一条知识引用：否则没有任何变更能到达 `B@1`，`B@1` 进不了影响集合，"未覆盖"由哪次核查触发就无从指定。

**CE-8b 目标截断**

初始态与操作**复用 CE-14**（`A@1`；`B@1`、`C@1` 各依赖 `A@1`；`D@1` 依赖两者；撤回 `A@1`），影响集合 4 个目标。查询 `limit=2`：

| 项 | 值 |
|---|---|
| `targets_complete` | **true**（集合已完整确定） |
| `targets_truncated` | **true**（本次只展示 2 个） |
| 返回总数 | 4 |
| 已展示数 | 2 |

**CE-8c scope=unknown**

变更 `CH1` 的 `scope=unknown`，该条目在 `CH1` 之前存在 `A@1`、`A@2`：

| 项 | 值 |
|---|---|
| 候选根 | `A@1`、`A@2`（**仅**变更前已存在的本条目版本） |
| 影响记录标记 | `scope_unconfirmed=true` |
| `targets_complete` | **false** |
| 文案 | "可能受影响"、"范围未知" |
| 禁止文案 | "已失效"、"全部通过" |
| 不包含 | 未来版本；其他条目的版本 |

**CE-8d 发布无声明引用**

`P-002` 的顶层与各 item 均无 `knowledge_refs`：

| 项 | 值 |
|---|---|
| 核查结果 | "覆盖未知" |
| `targets_complete` | false |
| 禁止显示 | "全部通过" |

**CE-8e `unknown` 范围的可审计收窄** [R9]

**初始** `A@1`、`A@2` 均已存在；`B@1` deps=`[A@1]`；`D@1` deps=`[A@2]`。`P-001` **尚未发布**。

**操作顺序**

1. 修订 A → `A@3`，`affected_scope_mode=unknown` → `CH1`
2. 以 `complete` 发布 `P-001`，`knowledge_refs=[B@1, D@1]`，同事务保存核查快照，序号 `N1`
3. 追加范围修订，收窄为 `versions:[A@2]`，发生在序号 `N2 > N1`

**步骤 1 后的影响记录**（候选根 = `A@1`、`A@2`，即变更前已存在的本条目全部版本）

| change | affected_version | hop | scope_unconfirmed |
|---|---|---|---|
| `CH1` | `A@1` | 0 | true |
| `CH1` | `A@2` | 0 | true |
| `CH1` | `B@1` | 1 | true |
| `CH1` | `D@1` | 1 | true |

`targets_complete=false`，文案"可能受影响"。

**步骤 3 后**

| 项 | 值 |
|---|---|
| `knowledge_changes` 的原始声明 | **仍为 `unknown`**，未被改写 |
| `knowledge_scope_revisions` | 新增 **1 行**，记录新范围、序号 `N2` 与理由 |
| `(CH1,A@1)` 与 `(CH1,B@1)` | **行仍在**，标记为自该范围修订起失效 |
| 在 `N2` 处有效的影响 | `(CH1,A@2)`、`(CH1,D@1)` |
| needs_action(`B@1`) | **false**（其影响记录在 `N2` 处已失效） |
| needs_action(`D@1`) | true |
| 以 `N1` 重放 `P-001` 的核查 | **复现步骤 2 当时的结果**：四条影响全部有效，`targets_complete=false` |
| 试图把范围扩大到候选根之外 | **拒绝**，要求另起一条变更事件 |

**关键断言** 收窄后旧核查必须仍按**当时的边界**复现，不被后来的收窄改写；失效的影响行与其处置**不删除**。

**步骤 4（A1 复核补充，2026-09-21）[R12]** 收窄之后 `record E@1`，deps=`[A@1]`（引用已被排除的根）。

| 项 | 值 |
|---|---|
| `(CH1, E@1)` 的有效影响记录 | **不存在** |
| needs_action(`E@1`) | false |
| `E@1` 的版本更新提示 | `newer_revision_exists`（A 有更晚修订） |

若实现仍生成该行，其解释路径必为空——按当前有效根找不到路径，这正是错误的标志。

**步骤 5（多次收窄）[R9]** 用另一初始态：`A@1`、`A@2`、`A@3` 均存在；修订 A → `A@4`，`affected_scope_mode=unknown` → `CH2`，候选根 `A@1`、`A@2`、`A@3`。

| 操作 | 预期 |
|---|---|
| 收窄为 `[A@1, A@2]` | 接受，范围修订 1 行 |
| 再收窄为 `[A@1]` | **接受**，范围修订 2 行；此时有效影响只剩由 `A@1` 可达的行 |
| 再声明 `[A@2]` | **拒绝**：不是当前生效范围 `[A@1]` 的子集 |
| 对一条 `versions` 声明的变更做任何收窄 | 拒绝（不变） |

---

**CE-8f 无影响行但有未覆盖引用（A1 复核补充，2026-09-21）** [R15｜R16]

初始：`P-000` 以 `partial` 发布；`K@1` deps=`[pub/P-000]`，无任何知识引用；以 `complete` 发布 `P-001` `knowledge_refs=[K@1]`。

| 项 | 值 |
|---|---|
| `P-001` 核查 `targets_complete` | **false** |
| 结果 `status` | `coverage partial`（无需行动的版本，但存在未覆盖引用）；**不得**为 `no impact found` |
| `K@1` 的 `uncovered_refs` | 列出 `pub/P-000` |
| `flagged` | `[]` |

**CE-8g 发布核查的截断标记（A1 复核补充）** [R15]

初始：`A@1`；`B@1` deps=`[A@1]`；对 A 连续做 51 次 `correct` 修订，每次 `scope=versions:[A@1]`（`CH1`…`CH51`），故 `B@1` 有 51 条影响记录。以 `complete` 发布 `P-001` `knowledge_refs=[B@1]`。

| 项 | 值 |
|---|---|
| 核查中 `B@1` 的影响 `total` | 51 |
| 默认页 `shown` | 50 |
| 核查 `targets_truncated` | **true** |
| `targets_complete` | true |
| needs_action(`B@1`) | true |

**CE-8h 完整性不随页变化（A1 复核补充）** [R15]

复用 CE-8a 初始态（`B@1` deps=`[A@1, pub/P-001]`），撤回 `A@1`。查询 `change_id=CH1, limit=1`（页内只有根 `A@1`）：`targets_complete` **false**（集合里 `B@1` 有未覆盖引用），`targets_truncated` true。全量查询：`targets_complete` false。两者相同。

---

## CE-9 间接晚到引用按可达性发现 [R12]

**初始** 仅 `A@1`，无任何使用者。

**操作顺序**

1. 撤回 `A@1` → `CH1`，scope=`versions:[A@1]`。此时影响记录仅 `(CH1, A@1, 0)`
2. `record B@1`，deps=`[A@1]`
3. `record C@1`，deps=`[B@1]`

**预期影响记录**（步骤 3 之后，共 3 行）

| change | affected_version | hop |
|---|---|---|
| `CH1` | `A@1` | 0 |
| `CH1` | `B@1` | 1 |
| `CH1` | `C@1` | **2** |

**关键断言**

- `C@1` 直接引用的 `B@1` **不在** `CH1` 的 scope（scope 只含 `A@1`）。仅做"新依据是否落在 scope 集合内"的成员检查会漏掉 `C@1`。判据必须是**沿版本图可达任一影响根**。
- 三行的 `change_id` 全部是 `CH1`。步骤 2、3 **不创建**新的变更事件。
- 变更事件表在步骤 3 之后仍只有 1 行。

---

## CE-10 零跳提示与直接引用撤回版本 [R11｜R18｜R19]

**CE-10a publication 直接固定撤回根，无中间知识**

**初始** `A@1`；发布 `P-001` `knowledge_refs=[A@1]`；**无任何知识**以 `A@1` 为依据。

**操作** 撤回 `A@1` → `CH1`。

| 项 | 值 |
|---|---|
| 影响记录 | `(CH1, A@1, hop=0)`，共 1 行 |
| `in_use(A@1)` | **true**，理由=被 `publication_knowledge` 固定 |
| `P-001` 呈现 | **有提示**，不得显示"无影响" |
| needs_action(`A@1`) | true |

**CE-10b 新增引用 status 为 retracted 的版本**

接上，`record D@1`，`ev=[A@2]`（`A@2` 即 status=`retracted` 的那一版）。

| 项 | 值 |
|---|---|
| 影响记录中含 `D@1` 的行 | **0 行**（`A@2` 不在任何 scope 中） |
| needs_action(`D@1`) | **false** |
| residual_use_risk(`D@1`) | **true**，reason=**`RR1 retracted_ref`** |
| `D@1` 的版本更新提示 | 无（`A@2` 是最新修订） |

**关键断言** `RR1` 与 scope **无关**。只按 needs_action 判断会漏掉 `D@1`。

| 项（A1 复核补充，2026-09-21） | 值 |
|---|---|
| 协调者上下文 | `D@1` 出现在"引用已撤回版本"一段（R18 补充），即使它没有任何影响记录；该段有总数与上限 |

**CE-10b-2 该段的取舍（第二轮复核补充，2026-09-21）[R18｜CE-10r]** 接上：撤回 `D@1` → `D@2`(retracted，`ev` 继承 `[A@2]`)；`record E@1` ev=`[A@2]`，再修订 → `E@2` ev=`[]`（`E@1` 非最新、无引用者）；`record F@1` ev=`[A@2]`（最新）。

| 项 | 值 |
|---|---|
| items 中的版本 | `D@1`、`E@1`、`F@1`；**不含 `D@2`**（自身已撤回，不是行动项） |
| `in_use` | `D@1` false、`E@1` false、`F@1` true |
| `total` / `not_in_use_count` | 3 / 2 |
| 展示 | 与 `affected_knowledge` 同款：不在使用中的仍列出并计数，上限 8 |

**CE-10c 发布固定撤回版（A1 复核补充）** [R20]

接 CE-10a：以 `complete` 发布 `P-002` `knowledge_refs=[A@2]`（`A@2` 即 status=`retracted` 的那一版）。

| 项 | 值 |
|---|---|
| `P-002` 核查 `status` | `attention` |
| `flagged` | `[A@2]` |
| reason | `retracted_ref`（P 级 RR1，来自 `A@2` 自身的 status） |
| `targets_complete` | true |

### CE-10v 变体：`in_use` 的三条锚点判据与不在使用中的对照 [R18]

**初始**（每个版本都带一份终端快照证据，快照不参与传播）

| 版本 | deps | 说明 |
|---|---|---|
| `A@1` | `[]` | 撤回对象 |
| `B@1` | `[A@1]` | **非最新、未被固定**，但被 `C@1` 引用 |
| `B@2` | `[]` | B 的最新修订，真正独立 |
| `C@1` | `[B@1]` | C 的最新修订 |
| `D@1` | `[A@1]` | **非最新、未被固定、无任何引用者** |
| `D@2` | `[]` | D 的最新修订，真正独立 |
| `E@1` | `[A@1]` | 非最新，但被节点 `X-002` 的 `inputs` 固定 |
| `E@2` | `[]` | E 的最新修订 |
| `F@1` | `[]` | F 的最新修订，与 A 无关 |
| `G@1` | `[A@1]` | 非最新，但被 `P-001` 固定 |
| `G@2` | `[]` | G 的最新修订 |

发布 `P-001` 的 `knowledge_refs=[F@1, G@1]`。

**操作** 撤回 `A@1` → `CH1`（追加 `A@2`，`deps=[]`）。

**预期影响记录（共 6 行）**

| change | affected_version | hop |
|---|---|---|
| `CH1` | `A@1` | 0 |
| `CH1` | `B@1` | 1 |
| `CH1` | `D@1` | 1 |
| `CH1` | `E@1` | 1 |
| `CH1` | `G@1` | 1 |
| `CH1` | `C@1` | 2 |

**A1 返修补充（2026-09-21）：固定手段自身会再添一行。** 把 `E@1` 固定进节点 `inputs` 的唯一 API 是 `propose(inputs=[E@1])`，而 v5 的 `propose` 把 `inputs` 写成该节点**问题条目**的 `dependencies`。于是问题条目经 `E@1` 依赖 `A@1`，撤回后它确实需要重审，影响记录为 **7 行**：上表六行，加 `(CH1, 节点问题@1, 2)`。断言取"恰好这七行"，不取子集。本表原写 6 行，漏算了自身固定手段的副作用；实现无误。登记见 `docs/RESEARCH_067_PLAN.md` §10。

**预期 `in_use`**

| 版本 | `in_use` | 命中的判据 |
|---|---|---|
| `A@1` | true | 经锚点 `E@1`、`G@1` 直达，也经 `C@1 → B@1 → A@1` |
| `B@1` | **true** | **只经"锚点 `C@1` 引用它"这一条**（非最新、未被固定） |
| `C@1` | true | 锚点，其条目最新修订 |
| `D@1` | **false** | 三条全不满足：非最新、未被固定、无任何锚点可达 |
| `E@1` | true | 锚点，被节点 `inputs` 固定 |
| `G@1` | true | 锚点，被 `publication_knowledge` 固定 |
| `F@1` | — | **不在影响集合内** |

**关键断言**

- **`B@1` 与 `D@1` 的唯一差别是有没有活的引用者。** 这一对孤立了可达性判据；只实现"最新修订"与"被固定"两条会把 `B@1` 误判为 false。
- `D@1` 的影响记录**照常存在**，`needs_action(D@1)` 为 **true**，处置键可用。默认视图折起它，但必须显示"另有 **1** 条不在使用中"与展开入口。`in_use` 不改变 `needs_action`（R18 补充四）。
- **`F@1` 没有影响记录。** 它与 `G@1` 同属 `P-001`，但 `F@1` 不引用 `A@1`。同一份发布里的两条知识不因共处一篇而互相成为依据。
- `E@1` 与 `G@1` 都是**非最新**版本，只靠固定成为锚点；若只按最新修订取锚点，两者及其下游都会被误判。

**工作量上界子例** 把访问上界降为 **1**，查询 `in_use(A@1)`：

| 项 | 值 |
|---|---|
| 返回 | **`unknown`**，不得为 `false` |
| 呈现 | 等同使用中，并带"未算完"标记 |

---

### CE-10r 变体：已撤回的最新修订既不是锚点，也不是受影响目标 [R10｜R12｜R18｜2026-09-21 用户裁决]

**初始** `Z@1` deps=`[]`；`A@1` deps=`[Z@1]`。无发布，无节点 `inputs`。

**操作顺序**

1. 撤回 `A@1` → `A@2`(retracted，deps **继承** `[Z@1]`，程序不删) + `CH1`(scope=`versions:[A@1]`)
2. 撤回 `Z@1` → `Z@2`(retracted) + `CH2`(scope=`versions:[Z@1]`)

**步骤 1 后**

| 项 | 值 |
|---|---|
| 影响记录 | `(CH1, A@1, 0)`，共 **1 行**（`A@2` 不引用 `A@1`；`A@2` 自身是撤回版，不作晚到引用目标） |
| `A@2` 的边 | `A@2 --deps--> Z@1` 存在 |
| `in_use(Z@1)` | true，理由=锚点（`Z@1` 此时仍是 Z 的最新修订） |

**步骤 2 后**

| 项 | 值 |
|---|---|
| 影响记录 | `(CH2, Z@1, 0)`、`(CH2, A@1, 1)`，共 **2 行**；**不含 `A@2`** |
| `in_use(Z@1)` | **false**：`Z@2` 最新但已撤回，不是锚点；`A@1` 非最新；`A@2` 最新但已撤回，不是锚点；无固定 |
| `in_use(A@1)` | false |
| needs_action(`A@1`) | true（`(CH1,A@1)` 与 `(CH2,A@1)` 均未处置；默认视图折叠，处置键可用） |
| needs_action(`A@2`) | **false** |
| residual_use_risk(`A@2`) | **false**（RR1：`Z@1` 的 status 未被改写；RR2：无影响行；RR3：`(CH2,Z@1)` 未处置） |
| `impact_next()` | **永不返回 `A@2`** |

**对照** 若步骤 2 之前发布 `P-001` `knowledge_refs=[A@2]`：`in_use(A@2)` 为 true（判据 2，被固定）；`P-001` 的核查按 R20 修正后的引用方求值显示 RR1（固定了 status 为 `retracted` 的版本）。固定撤回版是发布方的问题，由 publication 侧报告，不靠把撤回版当作受影响目标。

**关键断言** 原规则"最新修订不论 status"会让步骤 2 后 `in_use(Z@1)` 仍为 true，并让 `A@2` 得到 `(CH2, A@2, 1)`、`needs_action=true`、进入 consolidate 取件队列。反例意图：**已撤回的条目不能成为行动项**。遍历仍穿过 `A@2` 去找引用它的版本：若另有 `D@1` deps=`[A@2]`，则 `(CH2, D@1, 2)` 照常产生，与 `D@1` 自身的 RR1 并列。

---

## CE-11 `revised` 处置不使旧引用恢复正常 [R21]

**初始** `A@1`；`B@1` deps=`[A@1]`；`C@1` deps=`[B@1]`；发布 `P-001` `knowledge_refs=[B@1]`。

**操作顺序**

1. 撤回 `A@1` → `CH1`。影响：`(CH1,A@1,0)`、`(CH1,B@1,1)`、`(CH1,C@1,2)`
2. 修订 B → `B@2`，`deps=[]` 且 `ev` 不含任何知识版本（真正独立），`CH2`(`correct`, **scope=`none`**)
3. `dispose(CH1, B@1) = revised`，指向 `B@2`

**预期**

| 对象 | needs_action | residual_use_risk | 文案 |
|---|---|---|---|
| `B@1` | **false** | **false**（RR2 因处置而清，RR1、RR3 均不成立） | — |
| `C@1` | **true**（`(CH1,C@1)` 未处置） | **true**，RR2 与 **RR3** 同时成立 | "已有修订，仍引用受影响旧版" |
| `P-001`（固定 `B@1`） | false | **true**，RR3 | "已有修订，仍引用受影响旧版" |
| `B@2` | **false** | **false** | 独立计算 |

**关键断言**

- 后果表里的"原引用"指**指向 `B@1` 的那些引用**，即 `C@1` 与 `P-001`，不是 `B@1` 自身。风险落在**引用方**，由 `RR3` 表达。
- 处置为 `revised` **只**关闭 `(CH1, B@1)` 的 `needs_action`；`C@1` 与 `P-001` 的旧版使用风险**继续显示**。
- `B@2` 的风险独立计算，不继承 `B@1` 的处置结果。
- `P-001` 与 `B@1` 的历史行未被重写。
- `P-001` 的谓词按 R20 的引用方公式求值（2026-09-21 修正）：`P-001` 固定 `B@1`，而 `(CH1,B@1)` 的当前有效处置为 `revised`，故 `P-001` 得到 RR3，reason 标明来自 `B@1`；`B@1` 自身的谓词为假，两者不矛盾。经 `publication_check` 接口返回，`status=attention`，`flagged=[B@1]`。

### CE-11v 处置为 `retracted` 的后果

同一初始态与步骤 1。步骤 3 改为 `dispose(CH1, B@1) = retracted`（负责人撤回该目标）。

| 对象 | needs_action | residual_use_risk | 文案 |
|---|---|---|---|
| `B@1` | **false** | false | — |
| `C@1` | true | **true**，RR2 与 **RR3** | 继续显示风险 |
| `P-001` | false | **true**，RR3 | 继续显示风险 |

**关键断言** 触发 `C@1` 与 `P-001` 风险的是 **`RR3 disposed_old_ref`**（处置 kind 为 `retracted`），**不是 `RR1 retracted_ref`**（那只由被引用版本的 status 触发）。三个 `retracted` 不可混用，见 R21 的命名警告。

---

## CE-12 `unresolved` 不清除风险；有据保留不跨目标、不跨原因 [R21｜R22]

**初始** `A@1`；`D@1`；`B@1` deps=`[A@1, D@1]`；`C@1` deps=`[B@1]`。

**操作顺序**

1. 撤回 `A@1` → `CH1`。影响：`(CH1,A@1,0)`、`(CH1,B@1,1)`、`(CH1,C@1,2)`
2. 撤回 `D@1` → `CH2`。影响：`(CH2,D@1,0)`、`(CH2,B@1,1)`、`(CH2,C@1,2)`
3. `dispose(CH1, B@1) = unresolved`
4. `dispose(CH1, B@1) = retained_with_evidence`（追加写入，不覆盖步骤 3 的记录）

**预期按键的 needs_action**

| 键 | 步骤 3 后 | 步骤 4 后 |
|---|---|---|
| `(CH1, B@1)` | **true**（`unresolved` 仍为真） | false |
| `(CH2, B@1)` | true | **true** |
| `(CH1, C@1)` | true | **true** |
| `(CH2, C@1)` | true | **true** |

**按版本聚合**

| 版本 | 步骤 4 后 needs_action | 理由 |
|---|---|---|
| `B@1` | **true** | 来自 `CH2` 的键未处置（取或） |
| `C@1` | **true** | 两个键均未处置 |

**关键断言**

- `unresolved` 是"已查看或延后"，**不**清除 `needs_action`，也不清除 `RR2`。
- 对 `B@1` 的 `retained_with_evidence` **不自动关闭** `C@1`（不跨目标），**也不关闭** `CH2` 下的问题（不跨原因）。
- 步骤 4 之后处置记录表有 **2 行**（追加写入，留审计），当前有效处置取最新一条。

### CE-12v 先选后分页（A1 复核补充，2026-09-21） [R18 补充]

**初始** `A@1`；`U1@1`…`U10@1` 各 deps=`[A@1]`。撤回 `A@1` → `CH1`，影响 11 行。对 `A@1` 与 `U1@1`…`U8@1` 共 9 个键处置 `retained_with_evidence`。

| 项 | 值 |
|---|---|
| 协调者上下文 `affected_knowledge.total` | **2**（只数当前有效且未处置的行） |
| `shown_count` | 2 |
| items | `U9@1`、`U10@1` |
| 工作台受影响表 | 同一集合 |
| CE-8e 收窄后作废的行 | 不出现在两者的默认视图；历史查看可见 |

反例意图：先取 8 条再过滤已处置会得到 `total=11, shown=0`，后面的待处理项永远不出现。

**CE-12v-2 截断不是不完整（第二轮复核补充，2026-09-21）[R15]** 同一初始态，撤回 `A@1` 后**不处置任何行**，11 条待处理：

| 项 | 值 |
|---|---|
| `total` / `shown_count` | 11 / 8 |
| `targets_truncated` | true |
| `targets_complete` | **true**（所有变更为 `versions` 声明，无未覆盖引用；完整性在 11 条全集上求值，与展示多少无关） |

变体：把 `U10@1` 改为 deps=`[A@1, pub/P-000]`（`P-000` 为事先的 `partial` 发布）。无论 `U10@1` 是否落在展示的 8 条里，`targets_complete` 均为 **false**。

---

## CE-13 清空显式依赖不等于脱离；通用 relate 拒绝 K→K 保留标签 [R3｜R5]

### CE-13 主例

**初始**

| 版本 | deps | ev |
|---|---|---|
| `A@1` | `[]` | `[S-001]` |
| `B@1` | `[A@1]` | `[S-002]` |

**操作顺序**

1. 修订 B → `B@2`，`dependencies=[]`；`ev` 继承 `[S-002]`；`CH0`(`correct`, **scope=`none`**)
2. 撤回 `A@1` → `CH1`

**预期**

| 项 | 值 |
|---|---|
| `knowledge_support_refs(B@2)` | **空集** |
| 影响记录 | `(CH1,A@1,0)`、`(CH1,B@1,1)`，共 **2 行** |
| 含 `B@2` 的行 | **0 行** |

**关键断言** 不得用"该条目历史版本依赖的并集"把 `A@1` 补回 `B@2`。跨版本并集只用于历史浏览（R3）。

**通用 relate 拒绝**

3. `research_relate(source="knowledge/B@2", target="knowledge/A@1", label="grounded_in")`

| 项 | 值 |
|---|---|
| 返回 | **拒绝**，错误信息指向 `research_memory` 的窄协议 |
| `relations` 新增行 | **0 行** |
| `B@2` 的 `dependencies` | **未变**，仍为 `[]`（不得 `UPDATE` 旧修订） |

五个保留标签 `grounded_in|answers|supersedes|complements|challenges` 在 source 与 target **均为知识引用**时一律拒绝。`lineage_correction` 等非保留标签、以及 node/publication 之间的关系**不受影响**，继续可写。

### CE-13a 变体：窄协议自身的拒绝与正向路径

**初始** `A@1`（kind=`claim`）；`Q@1`（kind=`open_question`）；`B@1`。

| 操作 | 预期 |
|---|---|
| `record` 带 `relations=[{type:"grounded_in", target:"knowledge/A@1"}]` | **接受**；该版本的 `dependencies` 含 `A@1`；`relations` 表**不新增行**（`grounded_in` 不单独存储） |
| `record` 带 `relations=[{type:"answers", target:"knowledge/A@1"}]` | **拒绝**：`answers` 只允许指向 `open_question`，`A@1` 是 `claim` |
| `record` 带 `relations=[{type:"answers", target:"knowledge/Q@1"}]` | **接受**；`Q@1` 的 status **不变**，不被判定为已解决 |
| 修订 B → `B@2` 带 `relations=[{type:"supersedes", target:"knowledge/B@2"}]` | **拒绝**：自环为确定性非法输入 |
| `record` 带 `relations=[{type:"challenges", target:"knowledge/A@1"}]` | **接受**；`A@1` 的 status **不变**，两者并存 |
| `relations` 的 `target` 不带版本号（如 `knowledge/A`） | **拒绝**：`target` 必须是精确版本 |
| 对已存在的 `A@1` 直接补依据（非经 `revise`） | **拒绝**，指向 `revise`；`A@1` 的行不被 `UPDATE` |
| `record` 带 `affected_scope_mode`、`affected_scope` 或 `change_kind` 任一非空（A1 复核补充） | **拒绝**：变更事件参数只属于 `revise`（`A0_SCHEMA.md` §7.1） |
| 先 `research_relate(B@1 → A@1, label="relates_to")`，再 `record` 带 `relations=[{answers, Q@1}]`；以及相反顺序（A1 复核补充） | 两条都写入，`relation_id` 不同；不得因两个入口各用一个计数器而主键冲突 |

写入 `relations` 表的行 `relation_type` 为 `'knowledge'`，且**不出现在节点图投影中**（R28）。

### CE-13v 变体：`evidence_refs` 承载依据边

**初始**

| 版本 | deps | ev |
|---|---|---|
| `A@1` | `[]` | `[S-001]` |
| `B@1` | `[A@1]` | `[A@1]` |

**操作顺序**

1. 修订 B → `B@2`，`dependencies=[]`；`ev` **继承** `[A@1]`；`CH0`(`correct`, **scope=`none`**)
2. 撤回 `A@1` → `CH1`
3. 修订 B → `B@3`，`dependencies=[]` 且 `ev=[S-002]`；`CH3`(`correct`, **scope=`none`**)
4. 再次变更 `A@1` → `CH2`

**预期**

| 项 | 值 |
|---|---|
| `knowledge_support_refs(B@2)` | `{A@1}`，边的**来源标签 = `evidence_refs`** |
| 步骤 2 后影响记录 | `(CH1,A@1,0)`、`(CH1,B@1,1)`、**`(CH1,B@2,1)`** |
| `B@2` 的解释路径 | `B@2 --evidence_refs--> A@1` |
| `knowledge_support_refs(B@3)` | 空集 |
| 步骤 4 后含 `B@3` 的行 | **0 行** |

**关键断言** 这是最易实现错的一条（规则页附录第 1 条）。只读 `dependencies` 会漏掉 `B@2`；把 `dependencies=[]` 当作"已脱离"会得出相反结论。**同时更新 `evidence_refs` 之后（`B@3`）才脱离。** 程序在步骤 1 **不得**擅自删除 `ev` 中的 `A@1`。

---

## CE-14 多路径、成环、有界查询与序号可恢复 [R14｜R15｜R17]

**初始**

| 版本 | deps |
|---|---|
| `A@1` | `[]` |
| `B@1` | `[A@1]` |
| `C@1` | `[A@1]` |
| `D@1` | `[B@1, C@1]` |

另写入两条**通用**关系构成环：`relate(D@1 → B@1, label="relates_to")` 与 `relate(B@1 → D@1, label="relates_to")`（非保留标签，允许成环）。

**操作** 撤回 `A@1` → `CH1`。

**预期影响记录**（共 4 行）

| change | affected_version | hop |
|---|---|---|
| `CH1` | `A@1` | 0 |
| `CH1` | `B@1` | 1 |
| `CH1` | `C@1` | 1 |
| `CH1` | `D@1` | **2**（最短跳数；两条路径只记一次） |

**关键断言**

| 项 | 值 |
|---|---|
| `D@1` 出现次数 | **1**（幂等键去重，不因两条路径出现两次） |
| 默认返回的解释路径数 | **1** |
| `paths_truncated` | **true**（`D@1` 另有一条未展示路径） |
| 未展示路径总数 | **不要求计算**，不断言 |
| `targets_complete` | true |
| `targets_truncated` | false（`limit≥4` 时） |
| 通用关系环 | 查询**终止**，结果去重；环中的通用关系**不是**依据边，不产生影响记录 |

**序号可恢复** 该次查询结果携带账本序号上界 `N`；以 `N` 重放查询复现同一结果集与同样的三个标记。发布核查快照中保存的 `N` 同样可用于重放（与 CE-7 一致）。

**A1 复核补充（2026-09-21）[R25]：读边界不含未来写入。** 先查询得到 `N`；再做一次撤回或处置；再以 `N` 重放。结果与第一次**逐字段相同**（items、`disposition`、`total`、三个标记），不含该次写入。读边界是最后已提交事件的 `event_id`，写序号是它加一，两者不是同一个函数。同理，CE-7 的 `CK1` 以其 `N1` 重放时 `version_notices` 也须与快照相同——之后对 `A` 的措辞修订不得出现在重放结果里。

**CE-14d 深层钻石（A1 复核补充）[R15]** 在 CE-14 初始态上再加 `E@1` deps=`[D@1]`，撤回 `A@1`。`E@1` 的行：hop=3，默认返回 1 条路径，`paths_truncated` **true**（经 `B` 与经 `C` 两条路径）。只看 `E@1` 自身出边数（1 条）推出 false 是错误的。

**依据图的无环性** 依据边只能指向**写入时已存在**的精确版本，因此 `knowledge_support_refs` 构成的图按构造无环。实现**仍不得**假设无环：通用关系遍历必须去重并保证终止。

---

## A2 反例（2026-09-23 增）[R29–R40]

预期取自规则页第七节，先于 schema 与实现写出。断言以反例编号命名。凡写"理由码"，断言的是**字符串相等**，不是"包含错误"。

### A2 基础夹具

服务层（`NativeService.handle`，`host_id="host"`、`session_id="main"`），与 `tests/test_066_regressions.py` 的 `project()` 同一造法：

| 步骤 | 内容 |
|---|---|
| 1 | `open` 项目；`focus` planner/manual |
| 2 | 项目根写入 `REPORT.md`、`out/a.txt`、`out/sub/b.txt`、`we ird.txt` |
| 3 | `publish` **P-001**（partial）items：`report`（`source_path=REPORT.md`）、`bundle`（`source_path=out`）、`note`（只有 `content`） |
| 4 | `snapshot` **S-001** `paths=["REPORT.md","out","we ird.txt"]` → `complete=1` |
| 5 | `snapshot` **S-002** `paths=["REPORT.md","missing.txt","REPORT.md"]` → `complete=0`；清单三条：fixed、incomplete（带 `error`）、fixed（与第一条 `source_path` 相同） |

### CE-15 冻结引用文法与理由码 [R29 R30]

在基础夹具上逐条解析。`resolved` 行同时断言 `integrity == "unverified"`（R31：解析不算摘要）。

| 引用 | 结果 | `kind` / 理由码 | `object.kind` | 备注 |
|---|---|---|---|---|
| `pub/P-001` | resolved | `publication` | null | |
| `pub/P-001#report` | resolved | `publication_item` | `file` | |
| `pub/P-001#note` | resolved | `publication_item` | null | 纯内容项，不是错误 |
| `pub/P-001#bundle` | resolved | `publication_item` | `directory` | |
| `pub/P-001#bundle/sub/b.txt` | resolved | `object_subpath`，`subpath="sub/b.txt"` | `directory` | `object` 是所属项的对象 |
| `pub/P-001#bundle/../REPORT.md` | unsupported | `path_escape` | — | 不折叠 `..` |
| `pub/P-001#report/x` | not_found | `subpath_missing` | — | 文件对象没有子路径 |
| `pub/P-001#bundle/zzz` | not_found | `subpath_missing` | — | |
| `pub/P-001#nope` | not_found | `item_missing` | — | |
| `pub/P-999` | not_found | `publication_missing` | — | |
| `pub/P-001#` | unsupported | `bad_syntax` | — | 空 item |
| `pub/` | unsupported | `bad_syntax` | — | |
| `S-001` | resolved | `snapshot` | null | |
| `S-001#REPORT.md` | resolved | `snapshot_entry` | `file` | |
| `S-001#./out/` | resolved | `snapshot_entry` | `directory` | 规范化为 `out` |
| `S-001#out/sub/b.txt` | resolved | `object_subpath`，`subpath="sub/b.txt"` | `directory` | 最长前缀匹配到 `out` |
| `S-001#we%20ird.txt` | resolved | `snapshot_entry` | `file` | percent-decoding |
| `S-001#missing.txt` | not_found | `entry_missing` | — | |
| `S-001#out/zzz` | not_found | `subpath_missing` | — | |
| `S-001#/REPORT.md` | unsupported | `path_escape` | — | 绝对路径 |
| `S-001#out/../REPORT.md` | unsupported | `path_escape` | — | |
| `S-001#` | unsupported | `bad_syntax` | — | |
| `S-999` | not_found | `snapshot_missing` | — | |
| `S-002` | resolved | `snapshot` | null | partial 快照整体仍合法 |
| `S-002#missing.txt` | unavailable | `entry_incomplete` | — | 消息含清单里存的 `error` 原文 |
| `S-002#REPORT.md` | ambiguous | `duplicate_path` | — | **不取第一条** |

**原文存储**：`record K-001@1`，`evidence_refs=["S-001#./out/"]` → 存储值为 `"S-001#./out/"` 逐字，不改写为 `S-001#out`（R29）。

### CE-16 对象缺失、损坏、类型不符 [R30 R31 R33]

接基础夹具。`v` 记 `pub/P-001#report` 的 `object.version`。

| 步骤 | 操作 | 断言 |
|---|---|---|
| 1 | 删除 `.research/objects/<v>`；工作区 `REPORT.md` **仍在** | `resolve("pub/P-001#report")` → unavailable / `object_missing`。**工作区文件不被读取**：材料准备、专家读取都报 `object_missing`，不产出内容（R33） |
| 2 | 同上，`S-001#REPORT.md` | unavailable / `object_missing`（同一对象，同一理由码） |
| 3 | 把 `<v>` 恢复成一个**目录** | resolve → unavailable / `object_kind_mismatch` |
| 4 | 把 `<v>` 恢复成文件但内容改为 `"tampered\n"` | `resolve` → **resolved**，`integrity="unverified"`；`open`（materialize / 专家读取）→ unavailable / `object_corrupted` |
| 5 | 恢复原始字节 | `open` → resolved，`integrity="verified"`；读出内容与原文相同 |

**关键断言** 第 4 步：解析级不算摘要因此通过，读取级必须失败；两级各自如实标注。任一实现把第 4 步的 `resolve` 报成 `object_corrupted`（每次解析都算摘要）或把 `open` 报成 verified（读取不校摘要）都错。

### CE-17 单一入口 [R32 R34]

接 CE-16 第 1 步之后的状态（`report` 对象缺失）。对同一引用，五个入口的结果与理由码相同：

| 引用 | `research_query(ref=…)` | 登记（`evidence_refs` / `inputs` / relate 两端） | 材料准备（`validate_branch_inputs`、`prepare_branch`） | 专家读取 |
|---|---|---|---|---|
| `pub/P-001#report` | 返回记录，`resolution.outcome="unavailable"`，`reason="object_missing"`，**不抛错** | 拒绝，错误消息含理由码 `object_missing`，**无写入**（事件数、修订数、关系数不变） | `ValueError`，消息含 `object_missing` | 错误含 `object_missing` |
| `S-002#REPORT.md` | `resolution.outcome="ambiguous"`，`reason="duplicate_path"` | 拒绝，`duplicate_path` | `ValueError`，`duplicate_path` | 错误含 `duplicate_path` |
| `S-001#missing.txt` | `NotFoundError`，消息含 `entry_missing` | 拒绝，`entry_missing` | `ValueError`，`entry_missing` | 同 |
| `S-001#out/../x` | `ValidationError`，`path_escape` | 拒绝，`path_escape` | `ValueError`，`path_escape` | 同 |
| `S-001#out/sub/b.txt` | 返回 `kind="object-subpath"`，`resolution.outcome="resolved"` | 接受，存原文 | 索引项 `materialized=true`，工作区内路径存在且内容为 `b.txt` 原文 | 读出 `b.txt` 内容 |
| `pub/P-001#note` | `kind="publication-item"`，`resolution.object=null` | 接受 | 索引项 `materialized=false`（无对象不是错误） | 错误含 `not_an_object` |

**界面跳转**：前端对 `S-001#out/sub/b.txt` 的解析只做显示切分（快照 id、路径），可用性由服务端 `resolution` 决定；前端不得对 `unavailable` 的引用显示为可打开。

**登记时不算摘要**（R34）：CE-16 第 4 步的 tampered 状态下，`record` 以 `evidence_refs=["pub/P-001#report"]` **成功**；随后专家读取报 `object_corrupted`。

**`knowledge_refs` 不是登记面（更正，2026-09-23）[R34]** 原表把 `knowledge_refs` 列进"登记"列，是契约缺陷。发布级与项级 `knowledge_refs` 传入上表六个引用中的任何一个 → `ValidationError`，消息含 `Invalid knowledge reference`；无写入（`events`、`publications`、`publication_items`、`publication_knowledge` 行数不变）。传入知识精确版本照常接受。

**CE-17 补充（S14 验收，2026-09-23）[R32]** 统一入口不得把分派的严格性带进讨论会话，也不得把 0.6.7 明确拒绝的输入改成静默接受。两处都是 S14 实现越出契约的行为变化，原表未覆盖；以 0.6.7（A1 关闭时的包）行为为准。

| 操作 | 预期 |
|---|---|
| 节点 inputs=`[<note_id>, <attempt_id>, "pub/P-001#report", "S-001#out/sub/b.txt"]`，`discussion_prepare` | **成功**；`material_index` 中两条冻结引用 `materialized=true`，读出原文；note 与 attempt 不物化、不报错 |
| 节点 inputs=`["S-001"]`（整份快照），`validate_branch_inputs`、`prepare_branch` | `ValueError`，消息含 `cannot be used as an input`；**不得**以 `materialized=false` 静默接受 |
| 节点 inputs=`["S-001"]`，`discussion_prepare` | **成功**；整份快照不物化（讨论会话只物化条目级冻结引用） |

### CE-18 谱系关系允许空输入 [R35]

`propose X-001` 根。之后：

| 操作 | 预期 |
|---|---|
| `propose X-002` predecessors=`[{node_id:X-001, relation_type:branches_from, rationale:"r", input_refs:[]}]` | 接受；`inputs == []`；`origin_kind == "derived"` |
| 同上 `relation_type: revises` | 接受；同上 |
| 同上 `relation_type: depends_on`, `input_refs: []` | 拒绝，`predecessor input_refs must be nonempty`（不变） |
| `branches_from` 空 `input_refs` 且显式 `inputs=["pub/P-001#report"]` | 拒绝：`inputs must exactly match predecessor input_refs`（固定输入为空） |
| predecessors=`[branches_from X-001 []，depends_on X-001 ["pub/P-001#report"]]` | 接受；`inputs == ["pub/P-001#report"]`；两条前驱都入 `node_dependencies` |
| CE-21 的 `lineage_mention_without_predecessor` | 对以上 X-002 **不触发**（它有前驱） |

### CE-19 记账来源由服务端固定 [R36]

| 操作 | 预期 |
|---|---|
| `record K-001@1`（请求带 `turn=3`） | 修订上 `asserted_at == {"host_id":"host","session_id":"main","turn":3,"operation_id":"<该次 operation_id>"}` |
| 先 `bind_turn(turn=5)`，再 `record K-002@1`（请求不带 `turn`） | `asserted_at.turn == 5`（取该 host/session 最大已绑定 turn） |
| `record K-003@1`，请求不带 `turn`，无绑定 | `asserted_at.turn is None`；其余三项非空 |
| `record` 的 fields 含 `asserted_at: {...}` | `ValidationError`；**无写入**（修订数、事件数不变） |
| `record` 的 fields 含 `source_identity: {"session_id":"fake"}` | 接受；`source_identity` 原样存储；`asserted_at.session_id == "main"`，**不受影响** |
| `revise K-001@1` → `K-001@2`（另一 session `"other"` 发起；夹具先 `associate` 它，不注册为 main） | `K-001@2.asserted_at.session_id == "other"`；`K-001@1.asserted_at` 逐字不变 |
| `relate`（任意合法两端） | 关系行 `asserted_at` 四项同上 |
| `publish` | 发布行 `asserted_at` 四项同上 |
| `propose`（补充，2026-09-23） | 该节点问题条目的修订 `asserted_at` 四项取自这次 propose 请求 |
| `record` 带 `relations=[{type:"complements", target:<K@r>}]`（补充；2026-09-23 更正：原写 `grounded_in`，与 CE-13a "`grounded_in` 不写关系行"矛盾） | 写出的关系行 `asserted_at` 与该修订的 `asserted_at` 逐字相同 |
| `record` 带 `relations=[{type:"grounded_in", target:<K@r>}]`（补充） | 不写关系行（CE-13a 不变）；`<K@r>` 在该修订的 `dependencies` 中，该修订的 `asserted_at` 四项同上 |
| 旧账导入与 schema 升级回填的问题条目（补充） | `asserted_at` 为 null |
| 界面 | "记账来源"与"自述来源"分两行；`asserted_at` 不与 `source_identity` 合并显示 |

### CE-20 执行来源可选、核实分明 [R37]

接基础夹具；`focus` 已产生 attempt `A-001`；`S-001` 存在。

| 操作 | 预期 |
|---|---|
| `record K-001@1` `execution_refs=["session:main","attempt:A-001","pub/P-001#report","S-001#REPORT.md","event:1"]` | 接受；存储五条，全部 `status="linked"`，`reason` 为 null |
| `record K-002@1` `execution_refs=["session:ghost","attempt:A-999","pub/P-001#note","tool_call:abc","S-001#missing.txt","S-001#out/../x"]` | **接受**（不拒绝）；六条全部 `unlinked`，理由依次：`not_found`、`not_found`、`not_an_object`、`unsupported_form`、`entry_missing`、`path_escape` |
| `record K-003@1`，不带 `execution_refs` | 存储 `[]` |
| `revise K-001@1 → K-001@2`，`fields` 不含 `execution_refs`（补充，2026-09-23） | `K-001@2.execution_refs == []`，**不继承** `K-001@1` 的五条；`K-001@1.execution_refs` 逐字不变 |
| `revise`，`fields` 含 `execution_refs=["attempt:A-001","S-001#missing.txt"]`（补充） | 在这次写入时核实：`linked`、`unlinked/entry_missing` |
| `record`，`execution_refs="session:main"`（非列表） | `ValidationError`，无写入 |
| 删除 `report` 对象后重新读取 `K-001@1` | 第三条仍为 `linked`（写入时核实，不重算） |
| 界面 | `unlinked` 条目显示"未关联"并带理由；不隐藏 |

### CE-21 候选提示四类 [R38]

**2026-09-23 重写（§3.4 开工前对照源码）。** 原稿以 `K-001`…`K-004` 为字面编号，但 `propose X-001` 会先写入节点问题条目 `K-001@1`，四条知识实为 `K-002`…`K-005`；原稿第二次读取还与 R38 的 X 提及规则矛盾（下文操作 (1) 的关系连接的正是 `B` 所属节点），并要求提示按边界重放，而 R38 不作此承诺。现以字母为标签，正文中的提及用各条目的实际编号拼出。

**夹具**：服务层基础夹具 + `S-001`。`propose X-001` 根，`question`/`plan`/`root_reason`/`purpose` 均不含任何 `K-`、`X-` 编号（其问题条目为 `K-001@1`，不产生提示）。以下四条都以 `node_id=X-001` 记录，依次得 `K-002`…`K-005`（夹具断言返回编号）：

| 标签 | kind | statement | deps | ev |
|---|---|---|---|---|
| `A@1` | decision | `base` | `[]` | `[]` |
| `B@1` | claim | `Builds on <A 的条目编号> and X-001 and X-002`（不带 `@r`） | `[]` | `["S-001"]` |
| `C@1` | claim | `Builds on <A@1 的完整编号>` | `[A@1]` | `["S-001#REPORT.md"]` |
| `D@1` | decision | `see <D 自己的条目编号> again` | `[]` | `[]` |

再 `propose X-002` 根：`root_reason="continues X-001 after the handoff failed"`，`question`/`plan`/`purpose` 不含编号（其问题条目为 `K-006@1`，不产生提示）。`publish P-002` complete，`knowledge_refs=[A@1]`。

**第一次读取**

| 项 | 值 |
|---|---|
| `B@1` | **3 条**：`prose_mention_without_relation`，`evidence.mention` 为 A 的条目编号，`candidate_versions == [A@1]`；`prose_mention_without_relation`，`evidence.mention == "X-002"`；`whole_snapshot_evidence`，`evidence.ref == "S-001"`。**`X-001` 不产生提示**（本修订所属节点） |
| `C@1`、`D@1`、`A@1`、`K-001@1`、`K-006@1` | 各 **0 条**（`C` 的提及已在 `dependencies`；`D` 是自引用） |
| `X-002` | **1 条** `lineage_mention_without_predecessor`，`evidence.mention == "X-001"`，`evidence.field == "root_reason"`，`repair_evidence == []` |
| `X-001`、`P-002` | 各 **0 条** |
| 每条 | `candidate == true`；不含"诊断"字样 |
| `total` | **4** |

**操作**：(1) `relate X-001 → X-002`，label `lineage_correction`（得 `R-001`）；(2) `revise B@1 → B@2`：`dependencies=[A@1]`，`evidence_refs=["S-001#REPORT.md"]`，`affected_scope_mode="none"`，正文不变；(3) 撤回 `A@1`（`retract`，scope `[A@1]`，得 `CH-002`——操作 (2) 的 revise 已写入 `CH-001`，A1 对每次修订都记变更事件；2026-09-23 更正，S8 实现时发现）。

**第二次读取**

| 项 | 值 |
|---|---|
| `X-002` | 仍 **1 条**，`repair_evidence == ["R-001"]`（无前驱是建节点时定下的，修复只能并列） |
| `B@1` | **2 条**：A 提及那条与整份快照那条都仍在（旧修订不可变），`repair_evidence == [B@2]`；**`X-002` 那条消解**——`R-001` 连接 `B` 所属节点 `X-001` 与 `X-002` |
| `B@2` | **0 条**（A 在 `dependencies`；证据不是整份快照；`X-002` 已由 `R-001` 连接） |
| `P-002` | **1 条** `complete_publication_cites_risk`，`evidence.status == "attention"`，`evidence.flagged == [A@1]`，`repair_evidence == []` |
| `total` | **4** |

**操作**：对 `(CH-002, A@1)` 处置 `retained_with_evidence`。**第三次读取**：`P-002` **0 条**；其余不变；`total` **3**。

**只报告**：三次读取前后，`events`、`relations`、`knowledge_revisions`、`node_dependencies` 行数各自不变。

**第二类只数 claim/observation（S8 验收补充，2026-09-23）**：在 `publish P-002` 之后、第一次读取之前，另以 `node_id=X-001` 记录 `E@1` lesson 与 `F@1` decision（依次得 `K-007`、`K-008`，不改变上文编号），均 `evidence_refs=["S-001"]`、正文不含编号 → 两者各 **0 条**提示，上表其余数值不变（`total` 仍为 4 / 4 / 3）。原表没有非 claim/observation 条目带整份快照证据，去掉 kind 过滤的变异不会被抓到；真实账本里 decision、lesson、open_question 带裸快照证据的共 20 条引用。

**分页**：另开新项目（服务层基础夹具），以 `visibility="project"` 记录 11 条 claim，正文不含任何编号，`evidence_refs=["S-001"]`：协调者上下文 `structure_hints` 段 `total == 11`、`shown_count == 8`；`research_query(collection="hints", limit=4, offset=8)` 返回按 R38 排序的第 9 到 11 条（`target` 字符串升序）。

### CE-22 E2a 计数规则与独立预期 [R39]

对象：冻结副本的**工作拷贝**。来源用仓库外的持久只读副本 `/Users/ywang2397/work/agent-research/frozen-ledgers/ari_e1_frozen_20260920.sqlite3`，先核对 SHA-256 为 `d7a7062b…`（`/tmp` 下的同名副本重启即丢，见 `E1/00_FROZEN_COPY.md` 重建记录），再 `cp` 到临时项目并经 6→7→8 迁移；原件 SHA 前后相同。边界 `4685`。以下数字由 Fable 用独立脚本按 R38 的字面规则算出（2026-09-23），实现结果必须相等；不等时先判谁错，改预期要登记 §10。

| 项 | 值 |
|---|---|
| 计数单位（S8 验收补充，2026-09-23） | 提示 item 是 `(target, field, mention)` 粒度，比下列单位细，须先聚合：第一类按 `(target, 被提及条目编号)` 去重；第二类按 `target`（修订）去重；第三类按 `target`（节点）去重；第四类按 `target`（发布）去重。`results.json` 同时报 item 数与单位数。S8 实现在此副本上的 item 数为 84 / 14 / 29 / 0，共 127 |
| `prose_mention_without_relation`：`(使用版本, 目标)` 对数 | **84**（K 目标 64、X 目标 20），分布在 **31** 个使用版本上 |
| `whole_snapshot_evidence` | **13**（只数 claim/observation） |
| `lineage_mention_without_predecessor` | **5**：`X-003`…`X-007`；每条 `repair_evidence` 非空（六条 `lineage_correction` 覆盖这五个目标） |
| `complete_publication_cites_risk` | **0**（副本没有 `retracted` 修订、没有变更事件、没有处置；三种 RR 都不可能成立） |
| 对照表 | `docs/067/E1/adjudicated_pairs.csv`（64 行，一行一对：`final_label` 构成依据 51、仅提及 10、`disputed` 3；由 `03_ANNOTATION.md` §2/§3 抽出并套用 `04_RECHECK.md` §3 八条裁决与 §4.2 的漏判改判——§5 的 50+3 加 §4.2 那一条即 51+3）。脚本读 CSV，不解析 markdown |
| 匹配规则 | 提示 `(使用版本, K 目标)` 与 CSV 行匹配当且仅当 `v_user` 相等且 `v_used` 的条目 id 等于目标（版本不参与匹配，因为正文提及一律不带版本，见 `03_ANNOTATION.md` §4.1） |
| 分区 | 真阳 = 匹配行 `final_label` 为构成依据；假阳 = 仅提及；未覆盖 = `disputed`；记录不足 = 无匹配行（含全部 20 条 X 目标）。四者之和 == 84 |
| 分区数值（Fable 独立计算，2026-09-23） | 真阳 **51**、假阳 **10**、未覆盖 **3**、记录不足 **20**（恰为全部 X 目标；64 条 K 目标提示都有对照行） |
| 漏报 | **0 条**：CSV 中 `final_label` 为构成依据的 51 行（含 §4.2 补的 `K-026@1 → K-019@1`）全部有提示命中。CSV 全部 64 行均有提示命中 |
| 输出 | `docs/067/E2A/results.json` 与 `README.md`；README 首段写明 E1 保留样例已失效、本结果是开发材料口径 |
| 不写入 | 工作拷贝除迁移外无写入：`events` 行数迁移前后相同 |

### CE-23 schema 8 迁移 [A0_SCHEMA §9 纪律]

| 项 | 值 |
|---|---|
| 备份 | `schema-7-backup.sqlite3` 与原库字节相同；已存在则不覆盖 |
| 事务 | 迁移中途异常 → `user_version` 仍为 7，无新列 |
| 新列 | `knowledge_revisions.asserted_at`、`.execution_refs`；`relations.asserted_at`；`publications.asserted_at` |
| 历史行 | `asserted_at` 为 JSON `null`，`execution_refs` 为 `[]`；界面显示"历史记录，无来源"，**不**显示为当前会话 |
| 旧包 | `SCHEMA_VERSION=7` 的包打开 schema 8 库 → 拒绝，消息指明版本 |
| 幂等 | 对 schema 8 库再次迁移 → 无操作 |
| A1 回归 | 69 条 067 回归在 schema 8 上全绿 |

---

## B 反例（2026-09-23 增）[R41–R45]

预期取自规则页第八节；CE-25 的每一行由参考脚本 `docs/history/067_b_contract/field_check_reference.py` 按 R42 字面算出，夹具编号已在当前代码上实跑确认。理由码一律按字符串相等断言。

### B 基础夹具

服务层（`NativeService.handle`，`host_id="host"`、`session_id="main"`）：

| 步骤 | 内容 |
|---|---|
| 1 | `open`；`focus` planner/manual |
| 2 | 项目根写入 `REPORT.md` = `# report\n`；`results/m.json` 逐字为 `{"audit": {"fpr": 0.010401, "n": 12, "ok": true, "tag": "v2", "nan": NaN, "list": [1, 2], "obj": {}}, "rows": [1.5, 2.5], "a/b": 7, "m~n": 8}` |
| 3 | `publish` **P-001**（partial），items：`metrics`（`results/m.json`，文件）、`dir`（`results`，目录）、`report`（`REPORT.md`，文件） |
| 4 | `snapshot` **S-001**，`paths=["results/m.json"]`（与 `metrics` 同一对象） |
| 5 | `propose` **X-001** 根，文字不含编号（其问题条目为 `K-001@1`）；以下知识都以 `node_id=X-001` 记录，claim 的 `evidence_refs=["S-001#results/m.json"]`，另行说明者除外 |

下文 `c(op, path, value[, tol])` 表示 `{"ref": "S-001#results/m.json", "path": …, "op": …, "value": …[, "tolerance": …]}`。

### CE-24 声明校验 [R41]

每行单独 `record` 一条 claim（`checks=[该项]`），预期写入被拒：异常类型如表，消息首行**逐字**为所列内容；`events`、`knowledge_revisions`、`field_checks` 行数不变。

| # | 声明 | 异常 | 消息首行 |
|---|---|---|---|
| 1 | kind 为 decision，`checks=[c(approx, "/audit/fpr", 0.0104, 0.00005)]` | ValidationError | `checks: kind_not_checkable` |
| 2 | `checks` 为对象而非列表 | ValidationError | `checks: not_a_list` |
| 3 | 多一个键 `"unit": "%"` | ValidationError | `checks[0]: unknown_field` |
| 4 | `op="ne"` | ValidationError | `checks[0]: bad_op` |
| 5 | `path="audit/fpr"` | ValidationError | `checks[0]: bad_pointer` |
| 6 | `path="/a~2b"` | ValidationError | `checks[0]: bad_pointer` |
| 7 | `approx` 无 `tolerance` | ValidationError | `checks[0]: tolerance_required` |
| 8 | `approx`，`tolerance=-0.00005` | ValidationError | `checks[0]: negative_tolerance` |
| 9 | `approx`，`tolerance="0.1"` | ValidationError | `checks[0]: invalid_tolerance` |
| 10 | `le`，带 `tolerance=0.001` | ValidationError | `checks[0]: tolerance_not_allowed` |
| 11 | `le`，`value=true` | ValidationError | `checks[0]: invalid_value` |
| 12 | `approx`，`value=NaN`（请求 JSON 里的 NaN） | ValidationError | `checks[0]: invalid_value` |
| 13 | `eq`，`value=[1, 2]` | ValidationError | `checks[0]: invalid_value` |
| 14 | `eq`，不给 `value` | ValidationError | `checks[0]: invalid_value` |
| 15 | `ref="S-001"`（整份快照） | ValidationError | `checks[0]: not_a_file` |
| 16 | `ref="pub/P-001#dir"`（目录对象本身） | ValidationError | `checks[0]: not_a_file` |
| 17 | `ref="pub/P-001#nope"` | NotFoundError | `checks[0]: item_missing` |
| 18 | `ref="S-001#results/../REPORT.md"` | ValidationError | `checks[0]: path_escape` |
| 19 | `ref="knowledge/K-001@1"` | ValidationError | `checks[0]: not_frozen` |
| 20 | 两项，第一项合法、第二项 `op="ne"` | ValidationError | `checks[1]: bad_op`（第一项也不写） |

对照：kind 为 **observation**、声明合法 → 接受（claim 与 observation 都可声明）。

### CE-25 求值表 [R42 R43]

`record` **K-002@1**（claim），`checks` 为下表 25 项，依次为 `check_index` 0…24。写入成功，`field_checks` 恰有 25 行。每行：`target="knowledge/K-002@1"`；`spec` 为声明原文；`input_ref="S-001#results/m.json"`；`object_version` 与 `input_sha256` 都等于 `metrics` 的对象版本；`checker_version="field-check/1"`；`tolerance_rule` 在 approx 行为 `"abs"`、其余为 null；`sequence` 为 K-002@1 的 `source_sequence`。

| # | 声明 | result | reason | observed_text |
|---|---|---|---|---|
| 1 | `approx /audit/fpr 0.0104 tol 0.00005` | consistent | null | `0.010401` |
| 2 | `approx /audit/fpr 0.0104 tol 0.0000005` | inconsistent | value_mismatch | `0.010401` |
| 3 | `le /audit/fpr 0.01` | inconsistent | value_mismatch | `0.010401` |
| 4 | `le /audit/fpr 0.0105` | consistent | null | `0.010401` |
| 5 | `eq /audit/n 12` | consistent | null | `12` |
| 6 | `eq /audit/n 12.0` | consistent | null | `12` |
| 7 | `eq /audit/n "12"` | inconsistent | type_mismatch | `12` |
| 8 | `eq /audit/ok true` | consistent | null | `true` |
| 9 | `eq /audit/ok 1` | inconsistent | type_mismatch | `true` |
| 10 | `ge /audit/ok 0` | not_checkable | invalid_number | `true` |
| 11 | `eq /audit/tag "v2"` | consistent | null | `"v2"` |
| 12 | `eq /audit/tag "v3"` | inconsistent | value_mismatch | `"v2"` |
| 13 | `gt /audit/tag 1` | not_checkable | invalid_number | `"v2"` |
| 14 | `approx /audit/nan 0 tol 1` | not_checkable | invalid_number | `NaN` |
| 15 | `eq /audit/nan 0` | not_checkable | invalid_number | `NaN` |
| 16 | `eq /audit/nan "NaN"` | inconsistent | type_mismatch | `NaN` |
| 17 | `eq /audit/obj "x"` | not_checkable | not_a_scalar | `{}` |
| 18 | `le /rows 3` | not_checkable | not_a_scalar | `[1.5, 2.5]` |
| 19 | `lt /rows/1 3` | consistent | null | `2.5` |
| 20 | `lt /rows/2 3` | not_checkable | missing_value | null |
| 21 | `eq /rows/01 2.5` | not_checkable | missing_value | null |
| 22 | `eq /a~1b 7` | consistent | null | `7` |
| 23 | `eq /m~0n 8` | consistent | null | `8` |
| 24 | `eq /audit/missing 1` | not_checkable | missing_value | null |
| 25 | `eq "" 1`（整份文档） | not_checkable | not_a_scalar | 整份文档按 R43 规则序列化，截到 200 字符 |

**三种引用形式与非 JSON 文件。** `record` **K-003@1**（claim），四项：`approx /audit/fpr 0.0104 tol 0.00005` 分别经 `pub/P-001#metrics`、`pub/P-001#dir/m.json`、`S-001#results/m.json`，外加 `eq /x 1` 经 `pub/P-001#report`：

| check_index | result | reason | observed_text | input_sha256 | object_version |
|---|---|---|---|---|---|
| 0 `pub/P-001#metrics` | consistent | null | `0.010401` | = `metrics` 对象版本 | = `metrics` 对象版本 |
| 1 `pub/P-001#dir/m.json` | consistent | null | `0.010401` | = `metrics` 对象版本 | = **`dir` 对象版本** |
| 2 `S-001#results/m.json` | consistent | null | `0.010401` | = `metrics` 对象版本 | = `metrics` 对象版本 |
| 3 `pub/P-001#report` | not_checkable | unsupported_format | null | = `report` 对象版本（字节已读到） | = `report` 对象版本 |

### CE-26 修订、继承与历史 [R41 R42｜R34 补充]

新项目，同一基础夹具。`c0 = c(approx, "/audit/fpr", 0.0104, 0.00005)`，`c1 = c(le, "/audit/fpr", 0.01)`，`c2 = c(le, "/audit/fpr", 0.0105)`。

| 步 | 操作 | 预期 |
|---|---|---|
| 1 | `record` **K-002@1** claim（证据 `S-001#results/m.json`），`checks=[c0, c1]` | 2 行：consistent；inconsistent/value_mismatch |
| 2 | revise K-002@1 → **@2**，`changes={"statement": "reworded"}`，`change_kind="reword"`，`affected_scope_mode="none"` | `@2.checks` 与 `@1.checks` 逐字相同（继承）；`@2` 新增 2 行，结果同第 1 步；`@1` 的 2 行不变 |
| 3 | revise → **@3**，`changes={"checks": []}`，mode none | `@3.checks == []`，`@3` 无行；`@2` 的行不变 |
| 4 | revise → **@4**，`changes={"checks": [c2]}`，mode none | 1 行 consistent |
| 5 | 改写 `metrics` 对象的冻结字节（先 chmod），再 `record` **K-003@1** claim，`evidence_refs=["pub/P-001#report"]`，`checks=[c0]`；再 `record` **K-004@1** claim，证据 `S-001#results/m.json`，不带 `checks` | 两次都**写入成功**（登记只到解析级，不算摘要）；K-003@1 有 1 行 `not_checkable`/`object_corrupted`，`observed_text` 与 `input_sha256` 为 null；K-002 各版本的行不变 |
| 6 | 删除该对象，再 `record` 一条与 K-003@1 相同的 claim | 拒绝，ValidationError，消息首行 `checks[0]: object_missing`；什么都不写。K-002@1 的两行仍为第 1 步的结果（不重算） |
| 7 | 撤回 K-002@4 → **@5**（`changes={"status":"retracted"}`，`change_kind="retract"`，scope `[K-002@4]`）。`@4` 的证据与 `c2` 都指向已删除的对象 | **写入成功**：继承的证据与声明都不重做登记校验；`c2` 求值 → 1 行 `not_checkable`/`object_missing` |
| 8 | revise K-004@1 → **@2**，`changes={"statement": "reworded"}`，reword，mode none | **写入成功**（A2-N6：继承的证据不重做校验；0.6.8 上此步报 `S-001#results/m.json: object_missing`） |
| 9 | revise K-004@2 → @3，`changes={"evidence_refs": ["S-001#results/m.json", "pub/P-001#nope"]}`，mode none | 拒绝，NotFoundError，消息首行 `pub/P-001#nope: item_missing`（新引入的引用照常校验；原样保留的 `S-001#results/m.json` 不报）；什么都不写 |
| 全程 | — | 检查结果不改 `status`；各版本的 `knowledge_risk` 不因检查结果变化；`structure_hints` 不因检查结果增减 |

### CE-27 展示、接口与迁移 [R43 R44]

| 项 | 预期 |
|---|---|
| query | `research_query(ref="knowledge/K-002@1")` 的值带 `checks`（声明原文）与 `field_checks`（按 `check_index` 排序，每行含 R43 全部字段） |
| 工作台 | 一个带三项结果（consistent、inconsistent、not_checkable/missing_value）的条目，渲染文字含"声明字段与冻结文件一致""声明字段与冻结文件不一致""无法检查：missing_value"，且不含 `verified`（不分大小写）、"已验证"、"验证通过" |
| 工具 | `research_memory` 有 `checks` 参数；描述写明只比对声明字段与冻结文件、不证明结论，revise 用 `changes.checks`、省略即继承 |
| schema 9 | 备份 `schema-8-backup.sqlite3` 与迁移前（已 checkpoint）字节相同、已有则不覆盖；中途失败回滚（`user_version` 仍 8，无新列、无新表）；新列 `knowledge_revisions.checks`（默认 `'[]'`）与新表 `field_checks`；历史行 `checks == []`、无结果行；`SCHEMA_VERSION=8` 的包拒绝打开 schema 9；再迁移为空操作；A1、A2 全部回归在 schema 9 上通过 |
| 真实数据 | 冻结副本（持久副本，核对 SHA）的工作拷贝经 6→9：九张表行数不变、`integrity_check` ok、42 条修订 `checks` 全为 `[]`、`field_checks` 为空、事件上界 4685；E2a 脚本在 schema 9 上重跑仍 25/25；持久副本 SHA 不变 |
| 安装包 | 一次性 profile 中，已安装插件经服务层记录一条带 `approx` 检查（引用快照中的 JSON 文件）的 claim，回读 `field_checks` 为一行 consistent；作为单独一项检查报告 |

---

## 附：十四条与规则页的对应

| 反例 | 主要规则 | 若实现错会发生什么 |
|---|---|---|
| CE-1 | R13 | 第二个独立变更被吞掉 |
| CE-2 | R24 | 专家返回即清零风险 |
| CE-3 | R21 R22 R23 | 已解决的问题永久显示为风险，或新原因无法触发 |
| CE-4 | R10 | 漏掉仍固定旧版本的下游使用 |
| CE-5 | R7 R19 | 每次措辞修订都要求全体下游重审 |
| CE-6 | R12 R4 R13 | 晚到依赖不被发现；启发被当作依据传播 |
| CE-7 | R25 R26 | 历史发布核查被改写，无法复现 |
| CE-8 | R15 R16 R8 R9 | 未覆盖被显示为"无影响"；收窄后旧核查被改写 |
| CE-9 | R12 | 成员检查漏掉间接影响 |
| CE-10 | R11 R19 | 零跳无提示；直接引用撤回版本不报 |
| CE-11 | R21 | `revised` 处置后旧引用被当作已修复 |
| CE-12 | R21 R22 | 处置跨目标或跨原因误关闭 |
| CE-13 | R3 R5 | `dependencies=[]` 被当作脱离；旁路写入不被消费 |
| CE-10v | R18 | 只实现最新修订与被固定两条锚点，漏掉经活引用者可达的版本；或把算不完显示成不在使用中 |
| CE-10r | R10 R12 R18 | 已撤回条目被当作行动项进入取件队列；其上游被撤回版钉为使用中 |
| CE-11v | R20 R21 | 三个 `retracted` 混用，`RR3` 被误记为 `RR1` |
| CE-13a | R5 | 窄协议的拒绝路径缺失，或 `answers`/`challenges` 擅改目标状态 |
| CE-14 | R14 R15 R17 | 多路径重复计数；不完整标记合并；结果不可复现 |
| CE-15 | R29 R30 | 七处各自解析，同一引用各入口结果不同；重复路径取第一条 |
| CE-16 | R31 R33 | 每次查询都算 10 GB 摘要，或读取不校摘要；对象缺失时改读工作区同名文件 |
| CE-17 | R32 R34 | 登记接受了读不出来的引用；query 抛错吞掉"账本有、对象无"的信息 |
| CE-18 | R35 | 谱系前驱被迫编造输入，或空输入节点被当作根 |
| CE-19 | R36 | 模型自述被当作记账来源；`asserted_at` 被静默覆盖 |
| CE-20 | R37 | 核不出来的执行来源被拒绝写入，或被静默标为已关联 |
| CE-21 | R38 | 提示写边；修复后旧提示消失导致审计断链；自引用/本节点误报 |
| CE-22 | R39 | 记录不足被计成假阳/漏报；用实现输出反写预期 |
| CE-23 | schema 纪律 | 历史行显示为当前会话来源；旧包写坏新库 |
| CE-24 | R41 | 非法声明被静默吞掉或只报错不回滚；错误不指明第几项 |
| CE-25 | R42 R43 | 布尔被当成数；NaN 算成不一致或一致；`"12"` 与 `12` 被判相等；带点或斜杠的键取错字段 |
| CE-26 | R41 R42 | 修订丢掉声明，或继承后在对象失效时挡住撤回；历史结果被重算 |
| CE-27 | R43 R44 | 界面写成"已验证"；结果缺输入摘要或 checker 版本，无法复查 |
