# auto-research-v5 0.6.10 · Research 工作台

## 交付

- 离线包：`apps/dsh/plugin/auto-research-v5-0.6.10.tgz`
- SHA-256：`37c4d17873cf6f407df1b2777f61b4781816a277f43194585278e9ceb6f13013`
- 校验文件：`apps/dsh/plugin/auto-research-v5-0.6.10.tgz.sha256`
- 展示资料模板：`apps/dsh/plugin/research.presentation.template.json`
- vLLM 示例：`apps/dsh/plugin/examples/vllm-stability.research.presentation.json`
- 校验器：`apps/dsh/plugin/scripts/validate-presentation.py`
- 验收截图：`docs/validation/0610/`

## 主要变化

Research 首页改为成果概览，提供研究时间线与执行图、冻结指标比较和现行知识检索、材料阅读器、运行与维护五个视图。完成项目先呈现最终方案、报告和交付；运行、科学结论、知识修订状态分开表述。X-002 的替代关系、X-005 的默认冠军、X-006 和 X-007 的未采纳结果在样本资料中明确标注。

新增 `workbench.summary`、`workbench.page`、`knowledge.page`、`presentation.get`、`reference.entries`、`reference.content` 六个只读端点。列表由服务端投影并分页；首页只轮询轻量摘要。材料只能经冻结引用打开，完整性校验后分块传输，预览上限 2 MiB。报告以安全的本地 Markdown 渲染器显示为标题、段落、表格和代码；阅读器能返回原焦点与滚动位置。主题随 DSH 明暗设置变化，图默认以 100% 可读比例显示。

展示资料可选，置于项目根目录 `research.presentation.json`。vLLM 示例的四项指标从冻结 JSON pointer 读取，分别按原始精度展示：adjusted F1 `0.40115 → 0.78669`、core recall `0.256 → 0.530`、clean FPR `0.00877 → 0.00959`、打包开销比 `1.0000× → 0.2993×`。audit 与 bench 不混合成一条趋势。无资料项目仍可浏览时间线、知识与材料。schema 保持 9，未迁移研究账本。

## 验证

- Python 全量测试：573 passed；Node 前端及领域测试：76 passed。
- 最终离线包安装到全新 profile：workflow、graph、blank 模型阻断探针通过。
- 真实 vLLM 研究库的临时克隆上完成浏览器任务：853×863、1440×1000、390×844 的明暗截图；检查最终交付、X-005/X-006/X-007、K-010 最新与旧版、K-027 证据返回、报告表格、执行图 100% 初始比例及焦点。
- 浏览器探针记录 `model_attempts=0`、`real_model_requests=0`、`turn_starts=0`。在**已关联**的克隆会话内，浏览前后数据库 SHA-256 一致；真实源项目在克隆验收期间 SHA-256 一致。

## 样本库说明

最初的直接关联试验在真实 vLLM 样本账本新增了 15 条会话管理事件（`event_id` 4687–4701，类型仅为 `session.associated`、`workflow.register`、`workflow.cold`）。这些发生在建立测试会话时；冻结对象内容摘要未变，研究结论、知识和发布物未由浏览器改写。为避免进一步改变原项目，最终验收脚本改为先复制 SQLite 数据库及冻结对象到临时 profile，再关联克隆。原账本中的这 15 条记录未自动删除，以免擅自改写历史。

## 安装和回退

```bash
dsh plugin --profile web add apps/dsh/plugin/auto-research-v5-0.6.10.tgz --offline
(cd apps/dsh/plugin && shasum -a 256 -c auto-research-v5-0.6.10.tgz.sha256)
```

正式环境替换安装前沿用现有 0.6.9 离线流程。若需回退，重新安装已留存的 0.6.9 包；schema 9 与既有研究数据格式未改变。
