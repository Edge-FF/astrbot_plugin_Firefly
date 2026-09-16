"""AstrBot 适配层。

本包是插件中唯一接触 AstrBot API 的地方，负责把 core 领域层桥接到
AstrBot 的事件钩子、命令与生命周期。core 层不反向依赖本包。

注意：包名刻意避开 stdlib 中的 `platform` 模块，避免污染全局命名空间。
"""
