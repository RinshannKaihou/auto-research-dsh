# Auto Research v5 · DSH 原生研究插件

当前交付为 **0.6.9 / schema 9**。用户照常使用自己的 DSH 模型、原生工具、权限、会话和轨迹。插件提供：
- 显式有向研究图、长期知识、阶段材料；
- 持久节点核心 Agent、节点内专家；
- 用量覆盖观察和历史接手；
- 知识依据失效后的影响追查与处置（0.6.7–0.6.9）。

它不包含独立模型循环，用量没有预算上限。

- [安装、命令与完整功能说明](apps/dsh/plugin/README.md)
- [0.6.0 设计与实现说明](docs/RESEARCH_PLUGIN_060.md)
- [认识层规格（0.6.7–0.6.9）](docs/067/README.md)

各版本的计划、复核、事故记录、实验与验收证据已于 2026-09-23 移出仓库，不作为后续开发的依据。

最短使用路径：

```bash
dsh plugin --profile web add apps/dsh/plugin/auto-research-v5-0.6.9.tgz --offline
dsh web
```

安装会把已有项目账本迁移到 schema 9，迁移前保存一致性备份；迁移后，旧版本插件拒绝写入。

随后在普通 DSH 会话输入：

```text
/research init <研究目标>
/research auto
```

`src/auto_research/` 仍保留早期离线框架源码和迁移工具，供历史数据核对与测试。正式插件包只分发原生插件存储所需的 Python 模块，不分发旧 worker、GUI 或自主 Runtime 入口。
