# token_traj_detector

对一条 LLM 生成的 token 轨迹做异常检测，捕捉两类病态生成：

- **复读**（`ill_type=3`）：模型陷入循环，反复输出同一段内容；
- **脚本 / 字形注入**（`ill_type=1`）：输出中混入异常脚本字符（泰文、西里尔、希伯来等）
  或不可见 / 损坏字符（U+FFFD、U+FEFF）。

纯规则实现，`metric.py` 只依赖标准库；`demo.py` 提供 jsonl 批量检测入口。

```
token_traj_detector/
  metric.py    # 检测算法，只需标准库
  demo.py      # 读 jsonl 并检测
  README.md
```

`--tokenizer` 时 `demo.py` 需要 `transformers`。

## 总体原理

`detect()` 接收一段生成的 token id 序列，按固定优先级逐条跑规则，命中第一条即返回：

```
脚本 / 字形规则（9 条） → 复读规则（6 条）
```

脚本类规则需要先把每个 token 单独解码成文本，再按字符的 Unicode 名称把 token 归入
"脚本类别"（`english_latin`、`chinese_cjk`、`thai`、`numbers`、`punctuation`……），
然后在类别序列上找异常模式。复读类规则在 token id 上直接做循环 / 块重复检测
（尾部精确循环用 Z-algorithm，O(n)），条目级和句级规则需要解码文本。

## Python 用法

```python
from metric import detect

# 只要 token id：只跑复读类规则（exact_loop / block_repeat / packed_repeat / block_repeat_large）
result = detect(token_ids)

# 带上每个 token 的解码文本：脚本 / 字形 / 条目 / 句级规则一并生效
result = detect(token_ids, token_texts=token_texts)

# 或者传 decode 回调（ids -> str），等价于上面
result = detect(token_ids, decode=tokenizer.decode)
```

- `token_texts[i]` 必须是 `token_ids[i]` **单独解码**后的字符串；
- `decode(ids)` 既可解单个 id，也可解整段（句级复读用整段文本）；
- 可选参数 `end_token_ids`：尾部精确循环会先剥掉这些结束 token 再判。默认是 Qwen3 的
  `(151643, 151645, 151646, 151647)`，换模型时改掉。

## 命令行

```bash
# 合成轨迹自检（不读文件）
python3 demo.py --smoke

# 只跑 token-id 规则
python3 demo.py --jsonl /path/to/traj.jsonl

# 加载 tokenizer 后跑全部规则
python3 demo.py --jsonl /path/to/traj.jsonl --tokenizer Qwen/Qwen3-8B

# 只看前 20 条
python3 demo.py --jsonl /path/to/traj.jsonl --tokenizer Qwen/Qwen3-8B --limit 20
```

jsonl 每行一个 JSON，必需字段 `token_ids`（int 列表）。有 `seq_id` 会打印出来。
stdout 每条一行 JSON；stderr 最后一行是 `{"flagged": N, "total": M}`。

## 输出

| 字段 | 含义 |
|---|---|
| `is_ill` | 是否报警 |
| `ill_type` | `0` 正常 · `1` 脚本/字形 · `3` 复读 |
| `reason` | 命中的规则名；未命中为空字符串 |

多条规则同时成立时，按本文档列出的顺序取第一条。

---

## 脚本 / 字形规则（`ill_type=1`）

以下规则都需要 `token_texts` 或 `decode`。示例中用"token 级别"的文本片段描述，
实际判定的是相邻 token 的类别关系。

### 1. `foreign_splice` — 外语脚本贴在英文词成分上

**原理**：西里尔、阿拉伯、韩文、天城文、泰文、平假名、控制字节类 token，
前后紧邻英文词（`english_latin` / `english_latin_space` / `numbers`）。
正常英文文本不会出现这种拼接，是典型的字符注入指纹。

**判定**：存在位置 i，`cats[i] ∈ 外语集合` 且 `cats[i-1]` 是英文词类，
且 `cats[i+1]` 是英文词类 / 标点 / 空白。

**示例（触发）**，箭头分隔相邻 token：

```
"the"  →  "Д"  →  "ata"
"score"  →  "ا"  →  " is"
```

第一个 token 是英文词，中间是西里尔字符，第三个又是英文词——外语字符被"焊"进了
英文单词里。注意：如果外语 token 前后不是英文词（例如两边都是空白或标点以外的类别），
本条不触发（但可能被下文的孤岛规则捕获）。

### 2. `script_presence` — 出现泰文 token

**原理**：泰文在这类退化输出中出现频率极高，视为强信号，见到即报。

**判定**：任意 token 的类别为 `thai`。

**示例（触发）**：

```
"Here is the result:"  →  "ค"  →  "่"  →  "."
```

哪怕只混进一个泰文字符也触发。

### 3. `foreign_island` — 全序列只有 1–2 个孤立外语 token

**原理**：整条几千 token 的输出里零星出现一两个外语字符，且彼此不相邻（最大连续 run 为 1）。
正常双语输出外语 token 会成片出现，孤岛状分布是异常。

**判定**：外语 token 总数在 1–2 之间，且没有任何两个外语 token 相邻。

**示例（触发）**：一条 2000 token 的英文回答，第 300 个 token 是 `"ж"`，第 1500 个
token 是 `"ع"`，其余全是英文。两个外语 token 各自孤立。

**反例（不触发）**：第 300、301 个 token 是 `"ж"` `"ж"`——连续 run 为 2，不是孤岛
（但若类别是泰文会被 `script_presence` 捕获）。

### 4. `fffd_embed` — U+FFFD 夹在词成分 / 数字 / 标点中间

**原理**：U+FFFD（�，替换字符）表示解码失败 / 数据损坏。它被夹在正常文本中间
（而不是孤立出现），说明输出流中段发生了编码损坏。

**判定**：某 token 含 `\ufffd`，且前后 token 类别均为
`english_latin` / `english_latin_space` / `numbers` / `punctuation`。

**示例（触发）**：

```
"compute"  →  "�"  →  "r the sum"
"3."  →  "�"  →  "14"
```

**反例（不触发）**：`\ufffd` 出现在句首、前后是空白或空白 + 标点的组合。

### 5. `feff_embed` — U+FEFF 夹在两个无空格英文词干中间

**原理**：U+FEFF（零宽不换行空格 / BOM）对用户不可见。夹在两个
`english_latin` 词干中间构成隐形注入，肉眼几乎无法发现。

**判定**：某 token 含 `\ufeff`，且前后 token 类别都是 `english_latin`。

**示例（触发）**：

```
"pass"  →  "​"  →  "word"
```

渲染出来是 "password"，但中间藏了一个不可见字符（零宽）。

### 6. `hebrew_island` — 含希伯来字符的 token 恰好 1–2 个

**原理**：与 `foreign_island` 同理，但对希伯来文（U+0590–U+05FF）单独统计，
且允许两个 token 相邻。希伯来字符在正常中英文输出中几乎不该出现。

**判定**：含希伯来区间字符的 token 数量为 1 或 2。

**示例（触发）**：一条英文回答中出现一个 token `"א"`，或相邻的两个 token `"ש"` `"ל"`。

**反例（不触发）**：出现 3 个及以上含希伯来字符的 token（大规模乱码交给其他规则）。

### 7. `glyph_repeat` — 同一孤立外语字形反复出现

**原理**：同一个外语字形 token（按 token id 判同）散布全序列、彼此不相邻——
"打点式"重复注入。

**判定**：所有外语 token 的最大连续 run ≤ 1，且满足其一：
- 只有 1 种 distinct 外语 token，出现 ≥2 次；
- distinct 外语 token ≤2 种，合计出现 ≥5 次。

**示例（触发）**：

```
"The answer" → "Ж" → "is 42." → "Ж" → "Next we" → "Ж" → "consider..."
```

同一个 `"Ж"`（同一 token id）出现 3 次、每次孤立。

### 8. `cjk_island` — 少量孤立 CJK，且不贴英文词

**原理**：正常中英混排中，汉字 token 要么成片、要么紧贴英文词（带空格边界）。
1–3 个 CJK token、彼此间隔 >1 个 token、前后又不是英文词类——分布形态异常。

**判定**：CJK token 数量在 1–3 之间；任意两个相邻 CJK 位置差 >1；
且每个 CJK token 的前 / 后 token 都不是 `english_latin` / `english_latin_space`。

**示例（触发）**：一条英文长回答中，第 100 个 token 是 `"错"`，第 500 个 token 是
`"误"`，前后 token 都是标点、空白或数字。

**反例（不触发）**：`"错误"` 连续两个 token（位置差为 1）；或 `"是" ` 的下一个 token
是 `english_latin`（这时该查 `cjk_splice_single`）。

### 9. `cjk_splice_single` — 全序列只有 1 个 CJK，且劈入英文词 / 数字 / 空白邻接

**原理**：全序列唯一的汉字出现在英文词、数字的紧邻位置（或夹在带空格英文词之间），
像是硬劈进了英文文本。

**判定**：CJK token 恰好 1 个，且满足其一：
- 前 / 后 token 是 `english_latin`；
- 前 / 后 token 是 `numbers`；
- 前后都是 `english_latin_space`；
- 后是 `english_latin_space` / `whitespace`，且前不是 `english_latin` / `numbers`。

**示例（触发）**：

```
"the "  →  "错"  →  "data"      # 夹在两个带空格英文词之间
"v"     →  "误"  →  "2"          # 前贴英文词干、后贴数字
"..."   →  "对"  →  " \n"        # 后是空白、前不是英文词/数字
```

---

## 复读规则（`ill_type=3`）

前四条只看 token id，不需要解码。

### 10. `exact_loop` — 尾部逐字周期循环

**原理**：把序列**反转后跑 Z-algorithm**，反转序列的 Z 前缀匹配直接对应原序列的
**尾部**周期循环。存在周期 p，满足：循环部分长度 ≥ 1.1 × p（至少重复约 1.1 遍）、
循环累计 ≥ 45 个 token，即触发。判定前会先剥掉至多 4 个结尾 token
（默认 Qwen3 的 `<|endoftext|>` / `<|im_end|>` 等）；若结尾是自然的 `im_end`，
循环长度阈值放宽到 300，避免把正常短尾巴误报。

**示例（触发）**，token id 层面：

```
正常回答的 500 个 token ... 然后是 id [871, 2057, 374] 重复 30 遍（=90 个 token）
```

周期 p=3、循环 90 ≥ 45，命中。用户看到的是结尾不断重复 " this is a..."。

**反例（不触发）**：结尾 `this is a pen.` 只重复 2 遍（24 个 token < 45）——正常收尾。

### 11. `block_repeat` — 同一 64-token 块全序列出现 ≥11 次

**原理**：以 64 为窗口长度，滑动统计每个 64-token 块（token id 元组）的出现次数，
任一块达到 11 次即报。捕捉"不在尾部、无法被 exact_loop 发现"的整块重复。

**示例（触发）**：某 64-token 的块 B（比如同一段表格行）在序列中出现 11 次，
每次之间隔着各不相同的几行文字，因此尾部不构成精确循环——
`exact_loop` 不触发，`block_repeat` 命中。`demo.py --smoke` 自检的合成轨迹就是
这种构造（64-token 块 ×11 次，中间插入唯一 spacer）。

### 12. `packed_repeat` — 同一 64-token 块在 640-token 窗口内出现 ≥5 次

**原理**：`block_repeat` 看全局次数，可能漏掉"长输出里局部密集重复、但总次数不够"
的情况。本规则记录每个 64-token 块的所有出现位置，若任意 5 次出现挤在
640 token 的跨度内即报。

**示例（触发）**：一条 20000 token 的回答，前 19000 token 正常，结尾附近
某 64-token 块在 600 token 范围内反复出现 5 次（全局共 5 次，不够 11 次，
`block_repeat` 不触发），`packed_repeat` 命中。

### 13. `block_repeat_large` — 同一 1024-token 块出现 ≥3 次

**原理**：加大窗口捕捉"整段大块重复"。1024-token 的块重复 3 次意味着约 3000 token
的输出是三份拷贝。

**示例（触发）**：模型把同一篇约 1000 token 的摘要连续生成了 3 遍。块与块之间
略有差异的边界使 64-token 窗口的对齐统计不满足，但 1024-token 大块对齐相同，
命中本规则。

### 14. `template_enum` — 编号列表里同一条目体重复 ≥6 次（需要解码）

**原理**：在解码文本里找 `数字 + 分隔符（. ) :）` 形成的列表项起点，
把每个条目的"条目体"（去掉编号后的 token 序列）取出来。相邻且完全相同的
条目体连续计数，达到 6 次即报。捕捉"编号一直涨、内容一字不改"的模板复读。

**示例（触发）**：

```
1. To install the package, run pip install foo and then restart your shell.
2. To install the package, run pip install foo and then restart your shell.
3. To install the package, run pip install foo and then restart your shell.
4. To install the package, run pip install foo and then restart your shell.
5. To install the package, run pip install foo and then restart your shell.
6. To install the package, run pip install foo and then restart your shell.
```

六个条目体 token id 完全一致（条目体 ≥8 token 的下限也满足），命中。

**反例（不触发）**：条目内容各不相同；或重复只有 5 次。

### 15. `sentence_repeat` — 同一长句总出现 ≥5 次，且 ≥4 次挤在 800 字符内（需要整段解码）

**原理**：将整段输出按句子切分（`[.!?]` 加空白或换行），只保留 ≥15 词的长句，
统计每句（strip 后精确匹配）的出现位置。总次数 ≥5 且任意 800 字符窗口内
挤了 ≥4 次，即报。捕捉"换了说法结构、但句子级仍在循环"的复读——
token 块规则因上下文变化对不齐而漏掉的情况。

**示例（触发）**：一段输出里，句子 `"This approach ensures that the model
can generalize well to unseen data distributions during evaluation."`
（15+ 词）原样出现了 6 次，其中 4 次集中在不到 800 个字符内
（中间仅隔着不同的短连接词），命中。

**反例（不触发）**：该句全篇只出现 4 次；或出现的都是 <15 词的短句。

---

## 规则优先级速查

命中多条时按此顺序取第一条：

| # | reason | 类型 | 需要解码 | 触发条件摘要 |
|---|---|---|---|---|
| 1 | `foreign_splice` | 1 | 是 | 外语 token 前贴英文词、后贴英文词/标点/空白 |
| 2 | `script_presence` | 1 | 是 | 出现泰文 token |
| 3 | `foreign_island` | 1 | 是 | 全序列仅 1–2 个孤立外语 token |
| 4 | `fffd_embed` | 1 | 是 | U+FFFD 夹在词/数字/标点 token 之间 |
| 5 | `feff_embed` | 1 | 是 | U+FEFF 夹在两个无空格英文词干之间 |
| 6 | `hebrew_island` | 1 | 是 | 含希伯来字符的 token 恰好 1–2 个 |
| 7 | `glyph_repeat` | 1 | 是 | 同一孤立外语字形 token 反复散布出现 |
| 8 | `cjk_island` | 1 | 是 | 1–3 个 CJK、彼此间隔 >1、不贴英文词 |
| 9 | `cjk_splice_single` | 1 | 是 | 全序列唯一 CJK 劈入英文词/数字/空白邻接 |
| 10 | `exact_loop` | 3 | 否 | 尾部精确周期循环 ≥45 token |
| 11 | `block_repeat` | 3 | 否 | 64-token 块全序列 ≥11 次 |
| 12 | `packed_repeat` | 3 | 否 | 64-token 块在 640-token 窗口内 ≥5 次 |
| 13 | `block_repeat_large` | 3 | 否 | 1024-token 块 ≥3 次 |
| 14 | `template_enum` | 3 | 单 token | 编号列表同一条目体 ≥6 次 |
| 15 | `sentence_repeat` | 3 | 整段 | 同一长句 ≥5 次且 800 字符内 ≥4 次 |
