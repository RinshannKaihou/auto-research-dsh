# A0 Schema 与接口提案

日期：2026-09-21。规则页：`docs/067/A0_RULES.md`。反例预期：`docs/067/A0_COUNTEREXAMPLES.md`。

本提案必须能表达**全部十四条反例及其变体**；§11 给出逐条核对表。若某条无法表达，按 `docs/RESEARCH_067_PLAN.md` §10 回到规则页修订，不在实现中就地决定。

## 1. 两个总决定

### 1.1 边与影响**物化**，谓词**派生**

| 对象 | 物化还是派生 | 理由 |
|---|---|---|
| `knowledge_support_refs` 的边 | **物化**到 `knowledge_support_edges` | R2 要求保留每条边的来源字段标签；R10 的反向遍历需要"谁引用了 X"的索引，扫描 JSON 列做不到 |
| 影响记录 | **物化**到 `knowledge_impacts` | R13 的处置挂在 `(change_id, affected_version)` 上，键必须稳定存在 |
| `needs_action`、`residual_use_risk`、`in_use` | **派生**，查询时计算 | R18、R20 明确要求；固化成永久布尔会与处置和读取边界脱节 |

物化发生在**触发写入的同一事务内**（`record`、`revise`、`relate`），是同步的，**不是后台 worker**，符合 R17。

### 1.2 独立小表，与 `review_todos` 分开

按 O4，新增四张独立小表加一张边表。**不改 `review_todos` 的既有语义**：它继续承担泛化的复核待办，其 `UNIQUE(trigger_kind, trigger_ref)` 去重键保持不变。新的影响与处置走新表，两者通过 `review_state` 逻辑区分但不共用主键。

理由：`review_todos` 的唯一键以"来源修订"为准，与 R13 要求的 `(change_id, affected_version)` 不兼容；强行复用会重现 CE-1 与 CE-12 的吞并问题。

## 2. 命名冲突：两个 `scope`

`knowledge_revisions.scope` 已存在，含义是**陈述的适用范围**（JSON 对象）。`research_memory` 工具也已有同名参数（`tools.js:155`）。

变更事件的 `scope`（R7 三值）是**完全不同的概念**。存储与接口一律改名，避免任何位置同名异义：

| 概念 | 存储列 | 工具参数 |
|---|---|---|
| 陈述适用范围（既有） | `knowledge_revisions.scope` | `scope`（不变） |
| 变更影响范围（新增） | `knowledge_changes.affected_scope_mode` + `affected_scope` | `affected_scope_mode` + `affected_scope` |

三值用**两列**编码，直接满足 R7 的"三者不得都编码为空列表"：`mode ∈ {versions, none, unknown}`；`mode != versions` 时 `affected_scope` 固定为 `'[]'` 且不被读取。

## 3. 新增表

```sql
-- 3.1 依据边：R2 的物化视图，每条边带来源字段标签
CREATE TABLE knowledge_support_edges (
  user_ref          TEXT NOT NULL,   -- knowledge/K-nnn@r，使用版本
  used_ref          TEXT NOT NULL,   -- knowledge/K-nnn@r，被用版本
  from_dependencies INTEGER NOT NULL DEFAULT 0,
  from_evidence     INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  source_sequence   INTEGER NOT NULL,
  PRIMARY KEY(user_ref, used_ref),
  CHECK(from_dependencies + from_evidence > 0)
);
CREATE INDEX idx_support_used ON knowledge_support_edges(used_ref);
-- 写入必须是 UPSERT，不能按字段分两次 INSERT OR IGNORE：同一对版本可能同时经
-- dependencies 与 evidence_refs 相连（CE-13v 初始态），第二次会被忽略而丢掉来源标签。
--   ON CONFLICT(user_ref, used_ref) DO UPDATE SET
--     from_dependencies = MAX(from_dependencies, excluded.from_dependencies),
--     from_evidence     = MAX(from_evidence,     excluded.from_evidence)

-- 3.2 变更事件：R6、R7
CREATE TABLE knowledge_changes (
  change_id           TEXT PRIMARY KEY,
  knowledge_id        TEXT NOT NULL REFERENCES knowledge_entries(knowledge_id),
  new_revision        INTEGER NOT NULL,
  kind                TEXT NOT NULL,   -- retract|correct|narrow|reword
  affected_scope_mode TEXT NOT NULL,   -- versions|none|unknown
  affected_scope      TEXT NOT NULL,   -- JSON 精确版本列表；mode!=versions 时为 '[]'
  reason              TEXT NOT NULL,
  operation_id        TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  source_sequence     INTEGER NOT NULL,
  UNIQUE(knowledge_id, new_revision),
  CHECK(kind IN ('retract','correct','narrow','reword')),
  CHECK(affected_scope_mode IN ('versions','none','unknown')),
  CHECK(affected_scope_mode = 'versions' OR affected_scope = '[]')
);
-- affected_scope 的每一项必须是本 knowledge_id 的版本（R7）。SQLite 的 CHECK 无法
-- 表达 JSON 成员约束，由写入路径校验并拒绝，不靠 DDL。

-- 3.3 范围修订：R9，收窄 unknown 必须可审计且不改写原始声明
CREATE TABLE knowledge_scope_revisions (
  scope_revision_id   TEXT PRIMARY KEY,
  change_id           TEXT NOT NULL REFERENCES knowledge_changes(change_id),
  affected_scope_mode TEXT NOT NULL,
  affected_scope      TEXT NOT NULL,
  reason              TEXT NOT NULL,
  operation_id        TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  source_sequence     INTEGER NOT NULL
);
CREATE INDEX idx_scope_rev_change ON knowledge_scope_revisions(change_id, source_sequence);

-- 3.4 影响记录：R13
CREATE TABLE knowledge_impacts (
  impact_id         TEXT PRIMARY KEY,
  change_id         TEXT NOT NULL REFERENCES knowledge_changes(change_id),
  affected_version  TEXT NOT NULL,
  hop               INTEGER NOT NULL,   -- 最短跳数；重复投递按 MIN 合并，见 §6
  edge_source       TEXT NOT NULL,   -- 首跳来源：dependencies|evidence_refs|both|root
  review_state      TEXT NOT NULL DEFAULT 'pending',
  disposition_ref   TEXT,            -- 见 §4.4：仅 UI 快路径，不用于按序号重放
  voided_by_scope_revision TEXT      -- R9：被范围收窄移出时置此，行永不删除
    REFERENCES knowledge_scope_revisions(scope_revision_id),
  detected_at       TEXT NOT NULL,
  detected_sequence INTEGER NOT NULL,
  UNIQUE(change_id, affected_version),
  CHECK(review_state IN ('pending','running','proposal_ready')),
  CHECK(edge_source IN ('dependencies','evidence_refs','both','root'))
);
-- 不存 scope_unconfirmed：它按读取边界派生（R8 补充），等于"该 change 在边界处
-- 生效的 mode 是否为 unknown"。存成行内布尔无法同时满足 CE-8e 的两个重放断言。
-- edge_source 只描述首跳（hop=1 的那条边）；hop≥2 的完整来源标签从边表取。
CREATE INDEX idx_impact_version ON knowledge_impacts(affected_version);

-- 3.5 处置记录：R21，只追加
CREATE TABLE knowledge_dispositions (
  disposition_id   TEXT PRIMARY KEY,
  change_id        TEXT NOT NULL,
  affected_version TEXT NOT NULL,
  kind             TEXT NOT NULL,   -- unresolved|retained_with_evidence|revised|retracted
  reason           TEXT NOT NULL,
  evidence_refs    TEXT NOT NULL DEFAULT '[]',
  replacement_ref  TEXT,            -- kind=revised 时指向替代版本
  author           TEXT NOT NULL,
  operation_id     TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  source_sequence  INTEGER NOT NULL,
  FOREIGN KEY(change_id, affected_version)
    REFERENCES knowledge_impacts(change_id, affected_version),
  CHECK(kind IN ('unresolved','retained_with_evidence','revised','retracted'))
);
CREATE INDEX idx_disp_key ON knowledge_dispositions(change_id, affected_version, source_sequence);

-- 3.6 发布核查快照：R25，与发布记录原子写入，永不 UPDATE
-- 3.7 R18 判据 2 的反查索引：publication_knowledge 的主键是
-- (publication_id, knowledge_id, revision)，无法反查"某版本是否被任一发布固定"。
CREATE INDEX idx_pubknow_version ON publication_knowledge(knowledge_id, revision);

CREATE TABLE publication_checks (
  check_id          TEXT PRIMARY KEY,
  publication_id    TEXT NOT NULL REFERENCES publications(publication_id),
  sequence_bound    INTEGER NOT NULL,
  check_scope       TEXT NOT NULL,   -- JSON：本次核查了哪些引用
  result            TEXT NOT NULL,   -- JSON：逐引用结论与 reason
  targets_complete  INTEGER NOT NULL,
  targets_truncated INTEGER NOT NULL,
  paths_truncated   INTEGER NOT NULL,
  created_at        TEXT NOT NULL,
  UNIQUE(publication_id, sequence_bound)
);
-- 唯一键含 sequence_bound：同一 publication 在不同边界可以有多份核查快照，
-- 否则 CE-8e 那种"先发布、后收窄、再按旧序号重放"的场景撞唯一键。旧快照永不 UPDATE。
```

## 4. 既有表的加法式改动

### 4.1 `knowledge_revisions` 增一列

```sql
ALTER TABLE knowledge_revisions ADD COLUMN motivated_by TEXT NOT NULL DEFAULT '[]';
```

承载 R4 的启发关系，按版本快照。**不进入** `knowledge_support_edges`，不传播（CE-6）。

**这一列不是纯加法，必须同时改四处**，否则加列即报 "14 columns but 13 values supplied"：

| 位置 | 现状 | 改法 |
|---|---|---|
| `memory_store.py:209` | `INSERT OR IGNORE INTO knowledge_revisions VALUES(?×13)` | 改显式列名 |
| `memory_store.py:426` | `INSERT INTO knowledge_revisions VALUES(?×13)` | 改显式列名 |
| `memory_store.py:499` | `INSERT INTO knowledge_revisions VALUES(?×13)` | 改显式列名 |
| `memory_store.py:263-275` | `_decode_knowledge` 的解码键元组写死六个 | 补 `motivated_by`，否则以原始 JSON 字符串泄漏进返回值 |

插件内 Python 副本的同名文件同样要改。对照 §4.2 的 `relations` 加列没有此问题：`relate()` 的 INSERT 已是显式列名（`native_store.py:1042-1045`）。

`dependencies` 的**继承语义无需改动**：`revise_knowledge` 的 `value = {**current, **changes}`（`memory_store.py:482`）已经实现了 R3 的"未提供则继承前版"。A1 要新增的只是显式 `[]` 的正确处理与 `knowledge_support_edges` 的联动。

### 4.2 `relations` 增一列

```sql
ALTER TABLE relations ADD COLUMN operation_id TEXT;
```

S2 声明式关系（`answers`、`supersedes`、`complements`、`challenges`）存入既有 `relations`，`relation_type='knowledge'`，**只能由 `research_memory` 的窄协议写入**，与修订同事务。`grounded_in` **不入此表**，直接并入该版本的 `dependencies`（R5）。

保留 `lineage_correction` 等既有标签与 node/publication 关系的既有行为不变。逻辑区分靠 `relation_type`，不新建表，符合 O4 的"允许扩展旧表但保留逻辑区分"。

**`relation_type` 目前不是一个活的分流维度，必须一并改。** 现状：`relate()` 把它**硬编码为 `'scientific'`**（`native_store.py:1045`），schema5 把旧行刷成 `'legacy'`（`schema5.py:62`），**全仓没有任何读路径按它分流**。

更要紧的是前端：`frontend/model.js:64-69` 把 `relations` **全表**投影进节点图，`resolve()` 把 `knowledge/K-nnn@r` 解析到其所属节点并 `link()` 成边；`frontend/graph.js:47` 与 `client.js:76` 按 **`label`** 分类。因此新增的 `answers`、`supersedes`、`complements`、`challenges` 行**会直接在节点图上长出新边**，与 R28"不新增知识图 UI、推导边"相抵。

**要求：** `model.js` 的投影必须按 `relation_type` 过滤，排除 `'knowledge'`；`graph.js` 与 `client.js` 的 label 分类同步确认不受影响。这两个文件补进 §10.1 的落点表。

### 4.3 影响记录在读取边界处的有效性（R9）

一条影响记录在序号 `N` 处**有效**，当且仅当**两个条件同时成立**：

1. `detected_sequence <= N`；
2. `voided_by_scope_revision IS NULL`，或其指向的范围修订的 `source_sequence > N`。

**第 1 条不可省。** 缺了它，CE-7 中"发布之后才检测到的影响"在以发布序号 `N1` 重放时仍然可见，"复现发布当时的结果（无影响）"必然失败。

序号来源与边界：`source_sequence` 与 `detected_sequence` 均取现有实现的 `SELECT COALESCE(MAX(event_id),0)+1 FROM events`（`memory_store.py:443`、`:513`），因此**同一事务写出的修订行、变更事件行与各条影响行共享同一个值**。比较一律用闭区间 `<=`，否则同一次写入的结果会被排除在自己的边界之外。

只有有效的影响记录参与 `needs_action` 与 `RR2`。失效行与其处置记录**一律保留**，供历史查询与按旧序号重放（CE-8e）。

收窄时校验新范围是该 `change_id` **当前生效范围**的子集（不是原始候选根集合的子集，理由见 R9 补充一），目标 mode 只能是 `versions`，且只有**原始声明**为 `unknown` 的变更可被收窄（可多次，每次以当前生效范围为基准）。

**`hop` 与 `edge_source` 的两个口径（A1 返修后，2026-09-21）。** 存储列 `knowledge_impacts.hop`、`edge_source` 是**首次检测值**，磁盘上不重算（重算会让同一行在不同边界下取不同值）。读接口 `impact_query` 返回的 `hop`、`edge_source` 是**按读取边界处有效根派生**的值（与解释路径同一口径），并以 `detected_hop` 并列返回存储值；目标在有效根下不可达时（例如按较新边界读一条已作废的行）回退到存储值，解释路径为空，界面显示"路径不可用"而不是"该版本即变更根"。

### 4.4 `disposition_ref` 的使用约束

`knowledge_impacts.disposition_ref` 是**最新处置的缓存指针**，供界面快速取当前状态。

**不变式：任何按账本序号上界的重放，一律从 `knowledge_dispositions` 派生当前有效处置，不读该列。** 否则 CE-7 的"以 `N1` 重放复现发布当时结果"会被后来的处置污染。实现须在取数层集中一处，禁止在多处分别读取。

## 5. 迁移：schema 7

沿用 `schema6.py` 的模式（21 行，`src/auto_research/schema6.py`）：版本检查 → 一致性备份 → `BEGIN IMMEDIATE` 事务升级 → `PRAGMA user_version=7`。

新增 `src/auto_research/schema7.py`：

1. `version == 7` 直接返回（幂等）；`version != 6` 抛错。
2. 备份到 `schema-6-backup.sqlite3`，已存在则不覆盖。
3. 建 §3 的六张表与索引；`ALTER TABLE` 两列（先查 `PRAGMA table_info` 再加，与 schema6 同款）。
4. **回填 `knowledge_support_edges`**：遍历全部 `knowledge_revisions`，解析 `dependencies` 与 `evidence_refs`，只收指向 `knowledge/K-nnn@r` 的项，写入边并置来源标签。
5. `PRAGMA user_version=7`。

**回填在真实 3080 账本上会产生 0 条 K→K 边。** 该库 42 条修订中，`dependencies` 非空仅 1 行且指向 `pub/P-001`（非知识引用），32 行非空 `evidence_refs` 全部指向整份快照。这是事实，不是缺陷：迁移不猜测、不用正文正则造边（§3 非目标）。迁移回执必须**显式报告回填 0 条**，不得让阅读者以为图已建好。

**旧包拒写**已由现有机制覆盖：`native_store.py:93` 的 `if version not in {0, SCHEMA_VERSION}` 在 `SCHEMA_VERSION` 仍为 6 的旧包上遇到 `user_version=7` 即拒绝打开。无需新增机制。

**该回归测试已经存在，且升版后会反转，必须改。** `tests/test_native_plugin_store.py:389-394` 写入 `PRAGMA user_version=7` 并断言抛出匹配 `"Schema 7"` 的 `ValidationError`；`SCHEMA_VERSION` 改为 7 之后这条断言必然失败，断言目标应改为 8。

**其余断言 `== 6` 的既有测试，6 处**，全部需要同步：`tests/test_maintenance_cli.py:21`、`:25`；`tests/test_063_regressions.py:97`；`tests/test_065_regressions.py:75`；`tests/test_memory_store.py:302`；`tests/test_migration.py:395`。

**须同步的硬编码版本号，共 11 处**，分两类改法：

等值判断或常量（现为 `6`），9 处：`native_store.py:26`（`SCHEMA_VERSION`）、`:226`、`:252`；`maintenance_cli.py:80`、`:88`；`migration.py:380`、`:604`；`query_store.py:456`；`service.py:625`。

**集合成员判断，2 处，改法不同且后果更重：**

| 位置 | 现状 | 不改的后果 |
|---|---|---|
| `maintenance_cli.py:62` | `supported = version in {1,2,3,4,5,6}` | schema-7 项目的 `validate` 永远 `supported=false / ok=false`，`export` 在 `:80` 被拒 |
| `migration.py:401` | `if version in {2,3,4,5,6}: return migrate_native_copy(...)` | schema-7 项目的 `migrate_copy` **跳过 native 分支，落入 legacy schema-1 导入路径**（`:403` 起）。这是数据损坏级后果，不是回执不一致 |

另有两处错误文案硬编码了 "schema 6"（`native_store.py:75`、`maintenance_cli.py:81`），属文案级，一并改。

## 6. 幂等与版本冲突

| 场景 | 机制 |
|---|---|
| 同一变更重复投递（CE-1 步骤 2） | `knowledge_impacts` 的 `UNIQUE(change_id, affected_version)`。**不是 `INSERT OR IGNORE`**，而是 `ON CONFLICT(change_id, affected_version) DO UPDATE SET hop = MIN(hop, excluded.hop)`：R12 的晚到引用可能在已有 hop=2 的目标上补出直达边，整行忽略会留下错误的 2。`review_state`、`disposition_ref`、`detected_sequence` **一律不更新**；`edge_source` 在 `hop` 变小时一并更新为新路径的首跳，两列始终描述同一条路径（A1 返修记录项） |
| 同一修订重复提交 | `knowledge_changes` 的 `UNIQUE(knowledge_id, new_revision)`；`revise_knowledge` 既有的 `expected_revision` 冲突检查（`memory_store.py:466`）继续生效，抛 `ConflictError` |
| 晚到引用重复触发（CE-6、CE-9） | 同 `(change_id, affected_version)` 幂等键；`change_id` **沿用原值**，不新建变更事件 |
| 处置重复提交 | **不去重**：处置是追加写入并留审计（R21），CE-12 步骤 3、4 要求两行都在；当前有效处置取 `source_sequence` 最大的一条 |
| 请求级重放 | 沿用现有 `request_id` 幂等回执，不另建机制 |

`INSERT OR IGNORE` 在此处安全，与 `queue_review_in_tx`（`memory_store.py:348`）不同：那里的键是 `(trigger_kind, trigger_ref)`，会吞掉第二个独立变更；这里的键含 `change_id`，不会。

## 7. 工具参数变更

### 7.1 `research_memory`（`apps/dsh/plugin/tools.js:148`）

| 参数 | 动作 | 说明 |
|---|---|---|
| `relations[]` | 新增 | `record` 与 `revise` 可选；每项 `{type, target}`，`type ∈ grounded_in\|answers\|supersedes\|complements\|challenges`，`target` 必须是精确版本 |
| `motivated_by[]` | 新增 | 可选，启发关系，按版本快照。工具描述须带 R4 补充的判据：预设嵌在提问里的写 `relations[grounded_in]`，只说明为什么此刻做这个节点的写 `motivated_by` |
| `affected_scope_mode` | 新增 | `revise` **必填**，`versions\|none\|unknown`；无缺省，缺失即拒绝 |
| `affected_scope[]` | 新增 | `mode=versions` 时必填，每项须是本条目的精确版本 |
| `change_kind` | 新增 | `revise` 可选，`retract\|correct\|narrow\|reword`，**缺省 `correct`**；`status` 改为 `retracted` 时必须为 `retract`，否则拒绝 |
| `action` 枚举 | 扩展 | 增加 `dispose` |
| `disposition_kind`、`replacement_ref` | 新增 | `action=dispose` 时使用；键由 `change_id` 与 `affected_version` 给出 |
| `scope` | **不变** | 仍是陈述适用范围，见 §2 |

**`affected_scope_mode` 对 `revise` 必填，没有缺省。** 三个取值都是修订者的实质声明。缺省 `none` 会替他宣称"不使任何既有版本失效"；缺省 `unknown` 会让每一次措辞修订都把本条目全部既有版本铺成候选根，噪声不可承受，并且与 CE-11、CE-13、CE-13v 写死的行数直接冲突。缺失该参数的请求拒绝，错误信息列出三个取值。

`knowledge_changes.reason` 复用 `revise` 既有的必填 `reason` 参数（`tools.js:159`、`memory_store.py:460`），不新增参数。

`record` **不接受**上述三个变更事件参数；传入即为无效输入。

### 7.2 `research_relate`（`tools.js:236`）

当 `source_ref` 与 `target_ref` **均为知识引用**且 `label` 属于五个保留词时，**拒绝**并在错误信息中指向 `research_memory` 的 `relations[]`。其余行为不变（R5、CE-13）。

### 7.3 `consolidate` 路径（`tools.js:171-186`）

现行代码从 `review_todos` 取件（`review_todo_next`），并在专家返回 `state === 'completed'` 时把待办置 `completed`（`tools.js:184-186`）。两处都要改，否则 R24 无处落地：

| 问题 | 现状 | 改法 |
|---|---|---|
| 取件源 | `review_todos`，键 `(trigger_kind, trigger_ref)`，与 `(change_id, affected_version)` 无映射，影响记录永远取不到 | 新增窄接口 `impact_next(node_id)`，按检测序号升序返回最早一条"在当前边界有效且未处置"的影响记录，返回 `(change_id, affected_version)` |
| 状态值 | `review_todo_state` 只接受 `{pending, running, completed}`（`memory_store.py:597`），没有 `proposal_ready` | `knowledge_impacts.review_state` 取 `{pending, running, proposal_ready}`，是**另一套**状态；同时给 `review_todos` **加法式**新增取值 `proposal_ready` |
| 专家返回 | 置 `completed` | 置 `proposal_ready`，**不写处置、不清除风险**（CE-2） |

`review_todo_next` 继续只选 `pending`，因此新增取值不会造成循环取件。`completed` 此后只由操作者经既有接口显式设置，程序不再自动写入。

## 8. 查询接口与有界返回

新增服务端方法，经既有 `domain.request` 暴露：

| 方法 | 入参 | 出参 |
|---|---|---|
| `knowledge_impacts` | `version` 或 `change_id`、`limit`、`cursor`、`sequence_bound?` | 影响行、每行一条解释路径、`hop`、`reason` 列表、三个不完整标记、`sequence_bound`、`total`、`shown` |
| `knowledge_risk` | `version[]` | 每个版本的 `needs_action`、`residual_use_risk`（逐条 reason）、版本更新提示（分列）、`in_use` 与其理由 |
| `knowledge_in_use` | `version[]`、`sequence_bound?` | 三值 `true\|false\|unknown` 与命中的锚点判据；`unknown` 附"未算完"标记 |
| `knowledge_dispose` | `change_id`、`affected_version`、`kind`、`reason`、证据或替代版本 | 处置回执 |
| `knowledge_scope_narrow` | `change_id`、新范围、理由 | 范围修订回执（R9） |
| `publication_risk` | `publication_id`、`sequence_bound?` | 按 `publication_knowledge` 展开为版本集合后逐版本求谓词再取或；返回每条 reason 及其来源版本、三个不完整标记、序号上界（R20 补充、CE-7、CE-10a、CE-11） |
| `impact_next` | `node_id` | consolidate 的取件接口，见 §7.3 |

### 8.1 `in_use` 的计算契约（R18）

**锚点集合每次查询算一次，在读视图内复用**，不随遍历深度重复计算：

| 锚点来源 | 取法 | 代价 |
|---|---|---|
| 条目最新修订 | `SELECT knowledge_id, MAX(revision) ... GROUP BY 1`，走主键 | 索引扫 |
| 被发布固定 | `publication_knowledge`，走新增的 `idx_pubknow_version` | 索引查 |
| 被节点 `inputs` 固定 | 全表扫 `nodes.inputs` 的 JSON | 见下 |

**为什么这里允许扫 JSON，而 §1.1 判定边不能扫。** 两者规模不同一个量级：`knowledge_revisions` 随每次知识写入增长且无上界，`nodes` 一个探索节点一行（3080 账本 7 行）。锚点集合每查询算一次而非每跳算一次，因此代价是 O(节点数)，与遍历无关。**若节点数增长到使该扫描可测，再物化成小表**；现在不预先加结构。这个边界写在这里，免得后来者把它当成前后不一致。

遍历本身从目标版本出发，经 `idx_support_used` 反向找引用者，持访问集合，命中第一个锚点提前退出。访问节点数上界是**单处常量，缺省 10000**；耗尽返回 `unknown`，**不得返回 `false`**（R18 补充三）。

**CE-1 步骤 2 的"重放同一变更"指什么，必须写死：** 它等价于**用同一 `request_id` 重投同一 `revise` 请求**，走现有的请求级幂等回执路径，不重新进入写事务。不新增 `recompute(change_id)` 入口——那会与 §1.1"物化只发生在触发写入的同一事务内"冲突。断言点是：重投之后 `knowledge_impacts` 行数不变，且既有行的 `review_state` 与 `disposition_ref` 未被触碰。

所有查询沿用 `query_store.py` 既有的游标与有界返回约定（`query_store.py:37` 的表游标映射），返回 `total` 与 `shown`，置 `targets_truncated`（CE-8b）。解释路径默认 1 条并置 `paths_truncated`（CE-14）。

## 9. 兼容路径与失败恢复

- **迁移失败**：事务回滚，`user_version` 保持 6，备份文件保留；回执给出失败原因与备份路径。沿用 schema5/6 的既有行为，不新增恢复流程。
- **旧包遇新库**：按 §5 拒绝打开，错误信息含所需包版本。
- **新包遇旧库**：走 `native_store.py:82-91` 的既有链式迁移，追加 `migrate_schema7`。
- **边表与修订不一致**：不做在线自愈。提供 `maintenance_cli` 的一次性重建命令（从 `knowledge_revisions` 重算全部边），只在离线副本上运行，不改真实库。
- **`knowledge_support_edges` 的写入失败**：与修订同事务，一起回滚；不允许出现"修订已写、边未写"的状态（R17 原子提交）。

## 10. 实施落点、切片与规模估算

### 10.1 文件落点

| 文件 | 改动 |
|---|---|
| `src/auto_research/schema7.py` | **新增**：§3 建表、§4 两个 `ALTER`、边回填 |
| `src/auto_research/memory_store.py` | 边维护、变更事件、影响计算、处置、派生谓词；`revise_knowledge`（:452）、`record_knowledge`（:352）、上下文选取（:796-845） |
| `src/auto_research/native_store.py` | `SCHEMA_VERSION` 与迁移链（:26、:82-93）、`relate`（:1024）保留标签拒绝、`publish` 挂核查快照 |
| `src/auto_research/service.py` | `memory_write`（:956）接 `relations[]`/`motivated_by`/变更参数、新增 `dispose`、发布核查快照原子写入 |
| `src/auto_research/query_store.py` | §8 的有界查询 |
| `src/auto_research/migration.py`、`maintenance_cli.py` | 版本号同步、边重建命令 |
| `apps/dsh/plugin/tools.js` | §7 参数与 consolidate 路径 |
| `apps/dsh/plugin/domain.js` | consolidate 请求路径 |
| `apps/dsh/plugin/frontend/workbench.js`、`details.js` | 受影响条目表、一条解释路径、风险与版本更新提示分列 |
| `apps/dsh/plugin/frontend/model.js`、`graph.js` | **必改**：`model.js:64-69` 把 `relations` 全表投影进节点图，新增的 K→K 声明关系会长出图上的新边，与 R28 相抵；按 `relation_type='knowledge'` 过滤 |
| 既有测试 7 个文件 | 版本号断言同步，见 §5。其中 `tests/test_native_plugin_store.py:389-394` 断言的正是"schema 7 必须被拒"，升版后必然反转 |
| `tests/test_067_regressions.py` | **新增**：十四条反例及变体 |

Python 以 `src/auto_research` 为源，插件内副本由打包脚本同步；验收含 `npm --prefix apps/dsh/plugin run check:client` 与逐文件一致性检查。

### 10.2 切片

| 切片 | 内容 | 可独立验收的反例 |
|---|---|---|
| A1-1 | schema7 + 边物化 + 回填 | CE-13、CE-13v 的边计算部分 |
| A1-2 | 变更事件 + 影响计算 + 幂等 | CE-1、CE-4、CE-5、CE-9、CE-14 |
| A1-3 | 晚到引用 + 零跳 | CE-6、CE-9、CE-10 |
| A1-4 | 处置 + 派生谓词 + 真值表 | CE-2、CE-3、CE-11、CE-12 |
| A1-5 | 发布核查快照 + 有界查询标记 + 范围收窄 | CE-7、CE-8、CE-8e、CE-14 |
| A1-6 | 工具参数 + consolidate + 前端两处 | CE-2、CE-13 的拒绝路径 |

### 10.3 规模估算

按现有文件的写法密度估算，**区间而非点值**：

| 部分 | 新增或改动行 |
|---|---|
| `schema7.py` | 80 到 110 |
| `memory_store.py` | 280 到 360 |
| `native_store.py` | 50 到 80 |
| `service.py` | 60 到 100 |
| `query_store.py` | 90 到 140 |
| 迁移与维护 CLI | 40 到 70 |
| 插件 JS 与前端 | 140 到 200 |
| `tests/test_067_regressions.py` | 500 到 700 |
| 既有测试版本号同步（7 个文件） | 20 到 40 |
| **合计** | **1400 到 1980**，其中测试约四成 |

2026-09-21 上修：内部预审（`A0_REVIEW_INTERNAL.md`）增加了四项实施面——consolidate 取件源改造与 `impact_next`、`motivated_by` 加列牵动的三处位置化 INSERT 与解码、`model.js` 的关系投影过滤、7 个既有测试文件的版本号同步。

该估算的前提是反例预期不再变动。若 §11 的核对暴露表达不了的条目，估算作废并重估。

## 11. 反例表达能力核对

| 反例 | 依赖的存储或接口 | 是否可表达 |
|---|---|---|
| CE-1 幂等与独立变更 | `knowledge_impacts.UNIQUE(change_id, affected_version)` | 是 |
| CE-2 复核进度不清风险 | `review_state` 与 `knowledge_dispositions` 分离；§7.3 | 是 |
| CE-3 有据保留与再触发 | 处置追加 + §4.4 派生当前有效处置 | 是 |
| CE-4 历史版本不被遮蔽 | `idx_support_used` 上的反向遍历，按版本非按条目 | 是 |
| CE-5 编辑性更新 | `affected_scope_mode='none'` 不建影响；`newer_revision_exists` 由 `knowledge_revisions` 派生 | 是 |
| CE-6 晚到、启发、双原因 | `motivated_by` 列不入边表；两条 `change_id` 各自成行 | 是 |
| CE-7 发布后变化 | `publication_checks` 永不 `UPDATE` + `sequence_bound` 重放 | 是 |
| CE-8 未覆盖与截断 | `edge_source` 与 `scope_unconfirmed` 列；§8 的 `total`/`shown` | 是 |
| CE-8e 范围收窄可审计 | `knowledge_scope_revisions` + `voided_by_scope_revision`；§4.3 的边界求值 | 是 |
| CE-9 间接晚到 | 可达性查询走边表，不查 scope 成员 | 是 |
| CE-10 零跳与引用撤回版 | `hop=0` 行 + `edge_source='root'`；`RR1` 由被引用版本 status 派生 | 是 |
| CE-10v `in_use` 三条锚点判据 | `idx_support_used` 反向遍历 + `idx_pubknow_version` + 每查询一次的锚点集合；三值返回 | 是 |
| CE-11 `revised` 后果 | `replacement_ref` + `RR3` 派生；发布级经 `publication_risk` | 是 |
| CE-11v `retracted` 处置后果 | 处置 `kind` 与版本 `status` 分列，`RR3` 与 `RR1` 不混 | 是 |
| CE-12 不跨目标不跨原因 | 处置键含 `change_id` 与 `affected_version` 两者 | 是 |
| CE-13 清空依赖与拒绝 | `from_dependencies`/`from_evidence` 两列 + §7.2 拒绝 | 是 |
| CE-13a 窄协议拒绝路径 | 写入路径校验 + `relation_type='knowledge'` | 是 |
| CE-13v `evidence_refs` 承载边 | `from_evidence=1` 的边 + 解释路径的来源标签 | 是 |
| CE-14 多路径与序号 | 边表主键去重取最短 `hop`；`sequence_bound` 列 | 是 |

十四条与变体全部可表达，无需扩展本提案。

## 12. A2 存储与接口（2026-09-23 增）[R29–R40]

沿用 §1 的两个总决定：解析结果与候选提示都是**派生值，不存列**；只有记账来源与执行来源是写入时的事实，需要物化。

### 12.1 schema 8：三张既有表各加列

```
ALTER TABLE knowledge_revisions ADD COLUMN asserted_at    TEXT NOT NULL DEFAULT 'null';
ALTER TABLE knowledge_revisions ADD COLUMN execution_refs TEXT NOT NULL DEFAULT '[]';
ALTER TABLE relations           ADD COLUMN asserted_at    TEXT NOT NULL DEFAULT 'null';
ALTER TABLE publications        ADD COLUMN asserted_at    TEXT NOT NULL DEFAULT 'null';
```

- `asserted_at`：JSON 对象 `{"host_id","session_id","turn","operation_id"}`；历史行为 JSON `null`（CE-23）。
- `execution_refs`：JSON 数组，每项 `{"ref","status":"linked"|"unlinked","reason":null|<理由码>}`（R37）。
- 不新增表。提示不存（R38）；解析结果不存（R30）。
- 新文件 `src/auto_research/schema8.py`：`ensure_schema8_columns`、`migrate_schema8`（备份 `schema-7-backup.sqlite3`、`BEGIN IMMEDIATE`、`PRAGMA user_version=8`）。`native_store.py:28` `SCHEMA_VERSION = 8`，`:96` 迁移链追加。`memory_store.py:159` `_REVISION_COLUMNS` 扩两列——**所有 `knowledge_revisions` INSERT 仍必须显式列名**（schema7 的同一教训）。
- 冻结副本是 schema 6：E2a 的工作拷贝走 6→7→8 链。

### 12.2 解析器：`src/auto_research/frozen_refs.py`（新增）

纯函数，输入 `sqlite3.Connection` 与项目根，不持状态。

```
parse(ref)                 -> {"form": "publication"|"publication_item"|"snapshot"|"snapshot_entry", ...} | {"outcome":"unsupported","reason":...}
resolve(db, root, ref)     -> Resolution            # R30 全部结果；R31 解析级（存在 + kind，不算摘要）
open(db, root, ref)        -> (Resolution, Path)    # resolve 之后 ArtifactStore.verify；返回 .research/objects 内的路径（子路径已拼接）
```

`Resolution` 字段：`ref`、`outcome`、`reason`（null 或理由码）、`kind`、`object`（`{"version","kind"}` 或 null）、`subpath`（仅 `object_subpath`）、`integrity`（`"unverified"`｜`"verified"`｜null）、定位字段（`publication_id`/`item_id` 或 `snapshot_id`/`entry`）、`message`（人读，含原引用与理由码）。理由码词表 = R30 全部 + `not_an_object`（引用合法但没有对象，供 R37 与专家读取使用）。

**五个入口改为调用它（R32），删除各自的内联解析：**

| 入口 | 位置 | 改法 |
|---|---|---|
| 证据校验 | `memory_store.py:377` `_validate_evidence`；`native_store.py:1082` `_require_ref` | `pub/`、`S-` 引用走 `resolve`；`unsupported` → `ValidationError`，`not_found` → `NotFoundError`，`ambiguous`/`unavailable` → `ValidationError`；消息用 `Resolution.message`。其余引用类型不变 |
| query | `query_store.py:324` `reference_query` | 整份 pub/item/snapshot 保留现有 `kind`，值上加 `resolution`；新 `kind`：`snapshot-entry`（值 = 清单记录 + `snapshot_id`）、`object-subpath`（值 = 所属记录/项 + `subpath`）。`not_found` 抛 `NotFoundError`，`unsupported` 抛 `ValidationError`，`ambiguous`/`unavailable` **不抛**，带 `resolution` 返回（CE-17） |
| 材料准备 | `service.py:383` `_resolve_branch_inputs`；`:575` 讨论会话 materialize | 用 `open`；删除内联的 `archived.exists()`/kind 判断；错误 → `ValueError(message)`；无对象的项 `materialized=false` 不是错误 |
| 专家读取 | `service.py:1026` `specialist_read_input` | 用 `open`；接受 `snapshot-entry`/`object-subpath` 的文件；无对象 → `not_an_object` |
| 界面跳转 | `frontend/model.js:15` `resolve`；`workbench.js:62` `inspectReference` | 前端按第一个 `#` 切分只为显示；`reference.get` 返回的 `resolution.outcome`/`reason` 决定能否打开；`unavailable` 不给打开入口 |
| 检查器（B） | — | 预留：B 直接调用 `open`。A2 不实现 |

### 12.3 S6：`native_store.py:454` `propose`

`:497-500` 的非空检查只对 `relation_type == "depends_on"` 保留；`branches_from`/`revises` 接受 `[]`（仍须是字符串列表）。固定输入合并逻辑（`:513-520`）不变。`origin_kind` 判定（`:534`）不变——有前驱即 `derived`。

### 12.4 S7：记账来源与执行来源

- `service.py` 的 `memory_write`（:956）、`relate`（:1171）、`publish`（:1153）构造 `asserted_at = {host_id, session_id, turn, operation_id}`，`turn` 取 `request.get("turn")`，缺省取该 host/session 在 `turn_bindings` 的最大 `turn`，再缺省 `None`；作为**服务端参数**传给 store 方法，不经 `fields`。
- `memory_store.record_knowledge`（:486）/`revise_knowledge`（:607）：`fields` 含 `asserted_at` → `ValidationError`（R36）。`execution_refs` 从 `fields` 读，同事务内核实：`session:` 查 `workflow_sessions` ∪ `exploration_tasks.session_id`；`attempt:` 查 `attempts`；`event:` 查 `events`；其余交 `frozen_refs.resolve`，`resolved` 且 `object` 非 null 才 `linked`。
- 覆盖面（R36 补充）：`service.py` 的 `propose` 同样构造并传入，`native_store.propose` 写节点问题条目时带上；`_record_knowledge_in_tx` 是 record 与 propose 的共同写入点，`asserted_at` 作为它的参数而不是 `payload` 字段；`memory_write` 的 `relations[]` 经 `epistemic.write_declared_relations` 写出时带所属修订的 `asserted_at`。`migration.py` 的旧账导入与 `memory_store` 升级回填（`_REVISION_INSERT_IGNORE`）传 null。
- revise 不继承 `execution_refs`（R37 裁定）：`memory_store.py` `revise_knowledge` 的 `value = {**current, **changes}` 会把旧值带过来，`execution_refs` 与 `asserted_at` 都要在这里显式覆盖——前者为 `fields` 给出的值或 `[]`，后者为本次服务端值。
- 离线写入路径（`maintenance_cli`）若将来写修订，`host_id`/`session_id` 填 `"maintenance_cli"`，`turn` null。A2 无此路径，只定规则。
- `tools.js:148` `research_memory` 参数加 `execution_refs: array<string>`（可选）；**不**暴露 `asserted_at`。描述加一句：`S-xxx#path` 指快照内一条记录，裸 `S-xxx` 作为 claim/observation 证据会得到候选提示。

### 12.5 S8：`src/auto_research/hints.py`（新增）

```
structure_hints(db, bound, *, limit=8, offset=0, node_id=None, klass=None)
  -> {"items": [...], "total", "shown_count", "sequence_bound"}
```

- 四个类函数各返回候选列表；按 R38 的排序先选后分页（R18 补充四同款）。每项 `{class, target, evidence, candidate: true, repair_evidence}`，`target`/`evidence` 的形状按 R38 表。提示按当前状态求值；`bound` 只用于第四类的 `publication_check` 与返回的 `sequence_bound`，不承诺重放（R38 补充）。
- 提及正则与文本字段**复制**自 `docs/067/E1/e1_query.py:19-22`（`src` 不导入 `docs`），注释注明来源。
- 第四类调用 `epistemic.publication_check` 现成结果，不另算。
- `repair_evidence`：第一类 K 提及 = 同条目更晚修订中 `dependencies ∪ evidence_refs` 含被提及条目者的精确版本；第一类 X 提及 = 无（连接关系出现即不触发）；第二类 = 同条目更晚修订中 `evidence_refs` 已无裸 `S-xxx` 且含 `S-xxx#path` 或发布项者；第三类 = `label='lineage_correction' AND target_ref=<node>` 的 `relation_id`。
- 入口：`query_store.py:18` `COLLECTIONS` 加 `hints`（`research_query(collection="hints", …)`）；协调者上下文在 `memory_store.py:1186` `affected_knowledge` 旁加 `structure_hints` 段（`total`、`shown_count`、`items`，上限 8）；工作台加一段，措辞"候选"，并列显示 `repair_evidence`。

### 12.6 E2a：`docs/067/E2A/e2a_run.py`

只读：从仓库外持久副本（核对 SHA）`cp` 到临时项目 → 迁移 6→7→8 → `structure_hints(bound=4685, limit=None)` 四类全量 → 按 CE-22 的计数单位聚合 → 第一类对照 `docs/067/E1/adjudicated_pairs.csv`（Fable 从 `03_ANNOTATION.md` §2/§3 与 `04_RECHECK.md` 裁决抽出的机器可读版，随契约交付；脚本**不**解析 markdown）→ 写 `results.json` 与 `README.md`。原件 SHA 前后断言相同。预期数字见 CE-22。

### 12.7 工具参数变更汇总

| 工具 | 变更 |
|---|---|
| `research_memory`（`tools.js:148`） | + `execution_refs`；描述补 `S-xxx#path` 与裸快照提示 |
| `research_query`（`:87`） | `collection` 接受 `hints`；描述补引用文法四形式 |
| `research_propose`（`:100`） | 描述改：`branches_from`/`revises` 的 `input_refs` 可为空 |
| `research_relate`（`:261`） | 参数不变；`asserted_at` 服务端写 |
| `research_snapshot`/`research_publish` | 参数不变 |

### 12.8 迁移与兼容

CE-23 逐项。**包版本升 0.6.8**：E3 的 `FREEZE.md` 已冻结 0.6.7 tgz 的 SHA，A2 不能改动那个包的内容；0.6.7 至今未装进用户 profile，不存在兼容负担。

### 12.9 文件落点

| 文件 | 改动 |
|---|---|
| `src/auto_research/frozen_refs.py` | **新增** §12.2 |
| `src/auto_research/hints.py` | **新增** §12.5 |
| `src/auto_research/schema8.py` | **新增** §12.1 |
| `src/auto_research/memory_store.py` | `_REVISION_COLUMNS`（:159）、`_validate_evidence`（:377）、`record_knowledge`（:486）、`revise_knowledge`（:607）、上下文段（:1186 旁） |
| `src/auto_research/native_store.py` | `SCHEMA_VERSION`（:28）、迁移链（:96）、`propose`（:497-500）、`publish_metadata`（:905）、`relate`（:1051）、`_require_ref`（:1082） |
| `src/auto_research/service.py` | `_resolve_branch_inputs`（:383）、讨论 materialize（:575）、`memory_write`（:956）、`specialist_read_input`（:1026）、`publish`（:1153）、`relate`（:1171） |
| `src/auto_research/query_store.py` | `COLLECTIONS`（:18）、`reference_query`（:324） |
| `src/auto_research/maintenance_cli.py`、`migration.py` | 版本号同步 |
| `apps/dsh/plugin/tools.js` | §12.7 |
| `apps/dsh/plugin/scripts/sync-python.mjs` | 显式文件表加 `frozen_refs.py`、`hints.py`、`schema8.py`（A1 曾漏 `epistemic.py`） |
| `apps/dsh/plugin/frontend/model.js`、`workbench.js` | §12.2 界面行；记账来源/自述来源分行（:121）；执行来源"未关联"；候选提示段 |
| `apps/dsh/plugin/package.json` | 0.6.8 |
| `tests/test_067_regressions.py` | CE-15 到 CE-23，断言以编号命名；夹具沿用 `test_066` 的服务层造法 |
| `tests/dsh-profile/verify-067.py` | 加 A2 项：装好的插件里 `S-xxx#path` 可解析、空输入谱系可建、`asserted_at` 非空、提示段存在 |
| `docs/067/E2A/` | `e2a_run.py`、`results.json`、`README.md` |

### 12.10 切片与顺序

1. **S14**：`frozen_refs.py` + 五入口改造 + CE-15/16/17。最大的一片，其余三片依赖它。
2. **S6**：一处 + CE-18。
3. **schema 8 + S7**：`schema8.py`、`asserted_at`、`execution_refs` + CE-19/20/23。
4. **S8**：`hints.py` + query/上下文/工作台 + CE-21。
5. **E2a**：`e2a_run.py` + CE-22。
6. 验收：§5.2 四条命令 + profile 项 + 交付报告。

### 12.11 规模估算

| 部分 | 行 |
|---|---|
| `frozen_refs.py` | 220–300 |
| 五入口改造 | 100–150 |
| `schema8.py` + S7 写入路径 | 150–200 |
| `hints.py` + 三处入口 | 200–260 |
| S6 | 10 |
| 前端 | 60–100 |
| 测试 | 450–600 |
| E2a | 120–160 |
| **合计** | **1310–1780** |

### 12.12 反例表达能力核对

| 反例 | 机制 |
|---|---|
| CE-15 | `parse` + `resolve` 的理由码词表；清单最长前缀；percent-decoding |
| CE-16 | `resolve` 不算摘要、`open` 调 `ArtifactStore.verify`；只读 `.research/objects` |
| CE-17 | 五入口共用一函数；query 对 `ambiguous`/`unavailable` 返回而非抛错；登记级校验 |
| CE-18 | `propose` 一处条件 |
| CE-19 | 服务端参数 + `fields` 拒绝；三张表的 `asserted_at` 列 |
| CE-20 | `execution_refs` 列与同事务核实；写入时定值 |
| CE-21 | 派生提示 + 四类函数 + `repair_evidence`；按 R38 排序先选后分页；当前状态求值 |
| CE-22 | E2a 脚本 + 机器可读裁决表 |
| CE-23 | `schema8.py` 与既有迁移纪律 |

## 13. B 存储与接口（2026-09-23 增）[R41–R45]

沿用 §1：声明是修订内容（物化在修订上），结果是写入时的事实（物化为行）；展示与判定不另存派生值。

### 13.1 schema 9

```
ALTER TABLE knowledge_revisions ADD COLUMN checks TEXT NOT NULL DEFAULT '[]';
CREATE TABLE IF NOT EXISTS field_checks (
 target_ref TEXT NOT NULL, check_index INTEGER NOT NULL,
 spec TEXT NOT NULL, input_ref TEXT NOT NULL,
 object_version TEXT, input_sha256 TEXT, observed_text TEXT,
 tolerance_rule TEXT, checker_version TEXT NOT NULL,
 result TEXT NOT NULL, reason TEXT,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 PRIMARY KEY(target_ref, check_index),
 CHECK(result IN ('consistent','inconsistent','not_checkable'))
);
```

- 新文件 `src/auto_research/schema9.py`：`ensure_schema9`、`migrate_schema9`，照 `schema8.py` 的做法（checkpoint、`BEGIN IMMEDIATE`、`schema-8-backup.sqlite3` 独占创建、事务内加列建表、`PRAGMA user_version=9`）。备份与写锁之间的窗口同 A2-N5，不在本片处理。
- `SCHEMA_VERSION = 9`，迁移链追加 8→9；版本号硬编码处与 S7 相同（`native_store.py` 三处、`service.py` capabilities、`migration.py` 两处、`maintenance_cli.py` 两处）；包版本 **0.6.9**。
- `memory_store.py` `_REVISION_COLUMNS` 加 `checks`——所有 `knowledge_revisions` INSERT 仍须显式列名。

### 13.2 `src/auto_research/field_checks.py`（新增）

```
CHECKER_VERSION = "field-check/1"
declare(db, root, kind, checks, *, current=()) -> list[dict]     # R41；current 为当前版本的声明，逐字相同的项跳过登记校验
evaluate(db, root, target_ref, checks, *, sequence, created_at) -> list[dict]   # R42、R43，每项一行
pointer(document, text) -> tuple[bool, object]                    # RFC 6901
```

- `declare` 的文件判定只看元数据：`frozen_refs.resolve` 成功，且对象为文件、无子路径；或为 `object_subpath` 且对象库内该路径是普通文件。不读字节、不算摘要。
- `evaluate`：`frozen_refs.open` → 读字节 → SHA-256 → UTF-8 严格解码 → `json.loads`（默认接受 `NaN`/`Infinity`）→ `pointer` → 比较；`observed_text = json.dumps(v, allow_nan=True, ensure_ascii=False, sort_keys=True)[:200]`。同一次调用内对同一对象只 `open` 一次（CE-25 的 25 项同一文件）。
- 比较规则与理由码逐字按 R42；参考实现 `docs/history/067_b_contract/field_check_reference.py`（`src` 不导入 `docs`）。

### 13.3 写入口

- `memory_store.record_knowledge`：`fields.checks` → `declare`（全部视为新引入）→ 写入修订的 `checks` 列 → 同事务 `evaluate` 并写入 `field_checks`。
- `memory_store.revise_knowledge`：`changes.checks` 给出则替换、否则继承（`{**current, **changes}` 自然继承）；`declare(…, current=current["checks"])`；对最终声明全部 `evaluate`。
- **A2-N6**：`revise_knowledge` 对 `evidence_refs`、`dependencies`、`motivated_by` 的校验（当前 `memory_store.py:685-691` 对全表调用 `_validate_evidence`）改为只校验不在当前版本同名字段里的引用。
- 服务层与 `research_memory` 已按字段透传，`changes` 为开放对象，无需改 service。

### 13.4 读与展示

- 知识条目解码（`memory_store.py:363` `_decode_knowledge` 及 query 返回路径）带 `checks`（解析后的列表）与 `field_checks`（该版本的结果行，按 `check_index`；`spec` 解析为对象，`observed_text` 保持字符串）。
- 工作台知识条目（`workbench.js` 在"证据引用"一行旁）加"字段检查"一段：每项显示声明（op、path、value、tolerance）与 R44 的措辞；不一致时给出 `observed_text`，无法检查时给出理由码。
- `tools.js` `research_memory` 加 `checks` 参数（对象数组：`ref`、`path`、`op`、`value`、`tolerance`）；描述补一句：检查只比对声明的字段与冻结文件，不证明结论；revise 用 `changes.checks`，省略即继承。

### 13.5 文件落点

| 文件 | 改动 |
|---|---|
| `src/auto_research/field_checks.py` | **新增** §13.2 |
| `src/auto_research/schema9.py` | **新增** §13.1 |
| `src/auto_research/memory_store.py` | `_REVISION_COLUMNS`、record/revise 写入、A2-N6、条目解码 |
| `src/auto_research/native_store.py` | `SCHEMA_VERSION`、迁移链、建库 DDL |
| `src/auto_research/query_store.py` | 返回 `field_checks`（若解码不在 memory_store 完成） |
| `src/auto_research/service.py`、`migration.py`、`maintenance_cli.py` | 版本号 |
| `apps/dsh/plugin/tools.js`、`frontend/workbench.js`、生成的 `client.js` | §13.4 |
| `apps/dsh/plugin/scripts/sync-python.mjs` | 显式文件表加 `field_checks.py`、`schema9.py` |
| `apps/dsh/plugin/package.json` | 0.6.9 |
| `tests/test_067_regressions.py`、`tests/dsh-profile/workbench.test.mjs` | CE-24 至 CE-27 |
| `tests/dsh-profile/verify-067.py`、`workflow-probe.mjs` | 安装包 B 项，单独一项检查（建议名 `b-field-check-0.6.9`），走服务层 |

### 13.6 规模估算

`field_checks.py` 150–200 行；`schema9.py` 约 50；写入口与 A2-N6 40–60；读与展示 50–80；测试 350–450。合计约 650–850 行。

### 13.7 反例表达能力核对

| 反例 | 机制 |
|---|---|
| CE-24 | `declare` 的逐项校验与统一消息格式；整次写入在事务外先校验 |
| CE-25 | `evaluate` + `pointer`；结果行 R43 全字段 |
| CE-26 | 修订继承 + `declare(current=…)`；写入时求值、读时不重算；A2-N6 的引用校验收窄 |
| CE-27 | 解码与工作台措辞；schema 9 迁移纪律；安装包检查 |
