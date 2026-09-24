# 0.6.10 DSH 验收结果

最终包 `auto-research-v5-0.6.10.tgz` 的 SHA-256 为 `37c4d17873cf6f407df1b2777f61b4781816a277f43194585278e9ceb6f13013`。

| 检查 | 结果 |
|---|---|
| Python 全量回归 | 573 passed |
| Node 前端与插件回归 | 76 passed |
| 隔离 profile 离线安装 | workflow、graph、blank 探针通过 |
| 浏览器真实插件 | vLLM 样本克隆的五视图、X-007、K-010、K-027、报告、图、明暗三档窗口通过 |
| 模型与运行调用 | 0 次模型尝试、0 次实际请求、0 次 turn start |
| 已关联会话的浏览 | 克隆账本浏览前后 SHA-256 相同；真实源项目在克隆验收期间 SHA-256 相同 |

首屏：[853 亮色](overview-853.png)、[1440 亮色](overview-1440.png)、[390 亮色](overview-390.png)、[853 深色](overview-dark-853.png)、[1440 深色](overview-dark-1440.png)、[390 深色](overview-dark-390.png)。

阅读任务：[最终报告](report-reader.png)、[研究时间线](nav-研究过程.png)、[X-007 结果](x007-detail.png)、[执行关系图](execution-graph.png)、[知识修订对比](knowledge-history.png)、[材料](nav-材料.png)、[运行与维护](nav-运行与维护.png)。

首次直接关联真实样本时产生的测试会话事件见 [发布说明](../../RELEASE_0610.md#样本库说明)。最终验收均在临时克隆上进行。
