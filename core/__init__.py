"""认知外壳核心领域层。

本包不依赖任何 AstrBot 模块，可独立于 AstrBot 运行与测试。
所有与 AstrBot 交互的代码都在上一层的 adapter 包中。

目录结构（子包之间保持无环，详见 PLAN_arch_refactor.md §4）：

- 顶层 ``consts`` / ``models`` / ``config`` / ``records``：中立基础，不依赖子包
- ``materials``：资料文件格式解析、层级规则、索引与读写
- ``cognition``：会话状态、情绪演化、激活上下文与通用更新
- ``routing``：LLM / 关键词 / 组合路由
- ``shell``：预算内的文本组装与拼装
- ``proactive``：主动消息的冲动模型与闸门策略

约定：各子包的 ``__init__.py`` 只写 docstring，不做 re-export ——
避免制造隐式耦合节点，引用的来源必须显式可见。
"""
