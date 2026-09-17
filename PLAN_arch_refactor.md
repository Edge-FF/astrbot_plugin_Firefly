# 架构重构计划

> 适用版本：`astrbot_plugin_Firefly` v0.3.0
> 文档状态：待执行
> 关联文档：`DESIGN_task_shell.md`、`PLAN_task_shell.md`、`DESIGN_ui_role_editor.md`、`PLAN_ui_role_editor.md`

---

## 1. 背景与目标

### 1.1 为什么现在做

插件功能持续增加（人格注入、主动消息、定时任务已落地；后台日常模拟、记忆消费在规划中）。
当前 `core / adapter` 两层结构在方向上是对的，但**层内模块正在长成小巨石**、`models.py` 变成公共依赖节点，
新功能每加一次都要改动相同的那几个文件。再往后拖，改动成本会以超线性速度上升。

### 1.2 目标

| 目标 | 衡量方式 |
|---|---|
| 降低耦合 | `core/` 内部子包依赖无环；`core/` 不出现 `astrbot` import |
| 明确职责 | 每个文件能用一句话说明"它管什么数据、守什么不变量" |
| 提高扩展性 | 新增一个业务功能 = 新增一个子包 + 一处装配，不改动既有文件 |
| 不引入回归 | 每个阶段行为可验证；行为等价阶段的注入输出逐字节不变 |

### 1.3 非目标

- **不引入** `domain / application / infrastructure` 三层架构，不上 DDD 全套。
- **不引入** DI 框架或自动装配。`main.py` 的显式装配必须保留。
- **不为了对称拆 `adapter`**。`adapter` 按对外契约划分，`core` 按业务域划分，两者维度不同。
- **不在本计划内改变任何运行时行为策略**（除 P1 明确列出的缺陷修复）。
- **不在本计划内建 `memory/` 子包**。记忆的接入方案另行设计。

### 1.4 插件环境约束（来自 `AGENTS.md`）

- 禁止把业务代码写进 AstrBot 主程序。
- 不新增第三方依赖（零依赖是本插件的核心卖点）。
- 注释、日志与 docstring 统一使用**中文**，与本插件现有代码保持一致（AstrBot 主程序使用英文，不适用于本插件）。
- 公共类与公共函数使用 Google 风格 docstring（`Args:` / `Returns:` / `Raises:`）。
- 提交信息使用 conventional commits。

---

## 2. 现状评估

### 2.1 已经做对的部分（不要动）

1. **依赖方向正确**：`core/` 全文无 `astrbot` import（仅注释提及），`adapter/` 单向依赖 `core/`。
2. **纯逻辑层可独立测试**：`tests/` 中纯 core 测试不依赖 AstrBot 环境即可运行。
3. **业务域已有正确先例**：主动消息已按"`core` 策略 + `adapter` 驱动"拆分（`core/proactive.py` + `adapter/proactive_runner.py` + `adapter/proactive_prompt.py`）。后续新业务域照此形状复制。
4. **装配显式**：`main.py` 的 `FireflyCore` dataclass 是所有依赖的唯一清单，可读性好，保留。

### 2.2 实测依赖图（重构必须保持无环）

```
models / consts / parsers                ← 叶子，不 import 同层模块
        ↑
registry ──────────────┐
        ↑              │
affect / state / updaters
        ↑
context_manager ───────┤
        ↑              │
router                 │
        ↑              │
builder → assembly ────┘
        ↑
proactive (→ affect)
```

跨模块边（全部单向，无回边）：

| 来源 | 目标 |
|---|---|
| `registry` | `consts`, `models`, `parsers` |
| `context_manager` | `models`, `registry` |
| `router` | `consts`, `models`, `registry` |
| `builder` | `consts`, `models` |
| `assembly` | `consts`, `builder`, `context_manager`, `models`, `registry` |
| `proactive` | `affect`, `models` |
| `role_store` | `consts`, `parsers`, `registry` ⚠️ 见 P1-3 |

### 2.3 基线（已验证，2026-09-17）

工作目录：`data/plugins/astrbot_plugin_Firefly`

| 项目 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `& "<venv>\Scripts\python.exe" -m unittest discover -s tests -t .` | `Ran 158 tests in 2.969s` — **OK** |
| 静态检查（不含 tests） | `& "<venv>\Scripts\python.exe" -m ruff check core adapter main.py` | **All checks passed**（ruff 0.16.7） |

> `<venv>` = `F:\Python\AstrBot\.venv`。
> 注意：`python.exe` 在 PATH 上指向 Windows Store 占位程序（无输出、不可用），必须使用项目 venv 或 `py` 启动器。

#### ⚠️ ruff 作用域陷阱（实测确认）

在插件目录下执行 `ruff check .` / `ruff format --check .` 时，ruff 会**向上发现 AstrBot 根目录的 `pyproject.toml`**
（`file_resolver.project_root = F:\Python\AstrBot`），而该配置的 `file_resolver.exclude` 中包含 `"tests"`：

```
file_resolver.exclude = [
  	"astrbot/core/utils/t2i/local_strategy.py",
  	"astrbot/api/all.py",
  	"tests",
]
```

**后果**：`. ` 形式的命令**完全跳过 `tests/`**，会在测试目录存在 lint / 格式问题时仍报 "All checks passed"。
必须显式传路径才能覆盖测试：

```
ruff check core adapter main.py tests
ruff format --check core adapter main.py tests
```

**当前 `tests/` 的既有 lint 债（11 处，与本次重构无关）**：

| 文件 | 规则 | 数量 |
|---|---|---|
| `tests/test_loader.py` | `F401` ×1、`F841` ×3、`I001` ×1 | 5 |
| `tests/test_proactive.py` | `C408` ×2 | 2 |
| `tests/test_injector.py` | `I001` ×1 | 1 |
| `tests/test_builder.py` | `C408` ×1 | 1 |
| `tests/test_proactive_runner.py` | `C408` ×1 | 1 |
| `tests/test_matcher.py` | `F841` ×1 | 1 |

> 处理方式见 §7.4。本次重构**只保证新增文件干净**，不顺手修改既有测试，避免污染 diff。

**基线的意义**：`core` / `adapter` / `main.py` 当前是干净的，所以每个阶段之后这些目录出现的任何 ruff 告警都必然由该阶段引入，可直接作为自检信号。`tests/` 需用显式路径单独检查。

---

## 3. 隐患与耦合清单

### 3.1 P0 — 会产生错误结果的缺陷

| 编号 | 问题 | 位置 | 现象与影响 |
|---|---|---|---|
| **H1** | 手工逐字段复制 `SessionState` | `adapter/injector.py:210-216` | 这是**第三份**字段列表（另两份在 `models.py:199` 的 `to_dict` 与 `models.py:224` 的 `from_dict`）。给 `SessionState` 加字段时此处必漏，导致调试面板展示的状态快照与实际不符 |
| **H2** | 模块级依赖 AstrBot 内部 API | `adapter/injector.py:29`（`astrbot.core.agent.message`） | 内部路径一旦变更，插件**import 阶段直接失败**，整个插件起不来。插件卖点是"升级不必担心被覆盖"，此处不成立 |
| **H3** | 其余 AstrBot 内部 API 依赖 | `main.py:17`（`astrbot.core.message.message_event_result`）、`adapter/injector.py:46`（`astrbot.core.agent.run_context`） | 同上，但影响面较小（后者仅在 `TYPE_CHECKING` 下） |

### 3.2 P1 — 真实耦合

| 编号 | 问题 | 位置 | 说明 |
|---|---|---|---|
| **C1** | 反向依赖 + 跨模块引用私有函数 | `core/role_store.py:13` | `from .registry import _infer_tier_kind`。"写资料"逻辑依赖"资料索引"模块，且引用的是私有函数。tier 推断规则是双方共用的中立知识，不应寄生在 `registry` 中 |
| **C2** | 路由策略写在装配文件内 | `main.py:102-135` | `CompositeRouter` 类定义在 `Plugin.__init__` 方法体内。"LLM 优先 / 失败降级关键词"是核心策略，却**无法单测**，且与装配代码混在一起 |
| **C3** | 获取条目存在两条语义不同的路径 | `core/context_manager.py:93` `_lookup_meta()` | 手写遍历 `all_entries()` 且**故意不触发懒加载**，与 `registry.get()` 行为不一致。同一语义两条路径，迟早踩坑 |
| **C4** | HTTP 处理辅助函数重复实现 | `adapter/debug_api.py:126,140` 与 `adapter/role_api.py:242,252` | `_get_query` / `_get_json` 在两个类中几乎逐行重复 |
| **C5** | 配置解析辅助函数的三份实现 | `models.py:357-377`、`models.py:472-492`、`core/parsers.py:coerce_int` | `_int/_float/_bool` 在 `ShellConfig.from_dict` 和 `ProactiveConfig.from_dict` 中各写一份，与 `parsers.coerce_int` 构成第三套，且**仅 `parsers` 版本会产生告警**，行为不一致 |
| **C6** | 重复构造 LLM 生成闭包 | `main.py:93` 与 `main.py:160` | 同一份 `_make_llm_generate(context)` 被调用两次，产生两个闭包实例。功能无害但暴露装配冗余 |

### 3.3 P2 — 职责不清（含"死配置"）

#### 3.3.1 死配置 / 失效配置（**用户可见但完全不生效**）

| 配置项 | 声明位置 | 展示位置 | 实际消费 | 结论 |
|---|---|---|---|---|
| `inject.tier1_reserved` | `models.py:316,388` | `commands.py:56`、`debug_api.py:623,661` | **无** | 面板可调但无效。`builder.py:116` 反而**硬编码** `total_budget - 830` |
| `state.use_llm` | `models.py:326,396` | `commands.py:55`、`debug_api.py:666` | **无** | 面板显示"LLM 状态推断：开/关"，改开关无任何效果 |
| `active_context.default_skill_ttl` | `models.py:331,399` | `debug_api.py:669` | **无** | 实际的默认 TTL 来自资料 front-matter `default_ttl` 或 `consts.DEFAULT_TTL_MAP`，与配置项无关 |
| `active_context.default_lore_ttl` | `models.py:332,400` | 同上 | **无** | 同上 |
| `active_context.default_narrative_ttl` | `models.py:333,401` | 同上 | **无** | 同上 |
| `ShellConfig.get_default_ttl()` | `models.py:422` | — | **无调用点** | 死代码 |

> **影响**：这是最典型的"作用不清"。用户在面板上调参、在 `/firefly status` 里看到数值，却对行为没有任何影响。必须逐项决策：**接通** 或 **删除**（见 §7.1）。

#### 3.3.2 静默失败（可观测性缺失）

| 编号 | 问题 | 位置 | 影响 |
|---|---|---|---|
| **S1** | 状态落盘失败完全静默 | `core/state.py:124` `except OSError: pass` | 磁盘满/权限错误时，用户以为状态已保存。数据静默丢失 |
| **S2** | LLM 路由异常吞掉且无日志 | `core/router.py:115` `except (TimeoutError, asyncio.TimeoutError, Exception)` | 该元组等价于 `except Exception`（3.11+ 下 `TimeoutError is asyncio.TimeoutError`）。路由失败无任何记录，用户只能靠猜 |
| **S3** | LLM 路由响应解析失败静默降级 | `core/router.py:169` | 返回空 `RouteResult` 且无日志，无法区分"模型没选条目"和"JSON 解析失败" |
| **S4** | 懒加载读盘失败后标记为已加载 | `core/registry.py:296-299` | `entry.content = ""` 后 `is_loaded()` 返回 True，**永不重试**且无告警。资料文件损坏时表现为"这个条目内容是空的" |
| **S5** | 状态文件加载失败清空全部会话 | `core/state.py:52-54` | 单个坏字段导致 `self._states.clear()`，所有会话状态一起丢失 |
| **S6** | 配置解析失败无告警 | `models.py:357-377,472-492` | 与 `parsers.coerce_int`（会告警）行为不一致 |

#### 3.3.3 硬编码与魔法数字

| 编号 | 问题 | 位置 |
|---|---|---|
| **M1** | token 预算硬编码 `830` 作为"静态块预留" | `core/builder.py:116` |
| **M2** | 中文 token 估算系数 `len(text) // 2` 在 3 处出现 | `builder.py:134,181`、`injector.py:585` |

### 3.4 P3 — 体量与结构

| 编号 | 文件 | 行数 | 当前承担的职责 |
|---|---|---|---|
| **B1** | `core/models.py` | 704 | 领域实体(23-306) + **运行配置(306-538)** + **调试 DTO(540-704)** 三合一 |
| **B2** | `core/role_store.py` | 785 | 路径安全校验 + front-matter **序列化** + 原子写 + 目录树 |
| **B3** | `adapter/debug_api.py` | 720 | 单个类 24 个 handler，按面板 Tab 线性增长 |
| **B4** | `adapter/injector.py` | 589 | 注入管道 + 闸门 + AstrBot 消息对象操作 + 4 个记录方法 |
| **B5** | `adapter/proactive_runner.py` | 536 | 后台循环 + 发送执行 + 记录 |
| **B6** | `adapter/role_api.py` | 256 | 资料写路径 API |

#### 3.4.1 传输层逻辑倒灌进领域层

`InjectionRecord.to_api_dict()`（`models.py:639`）与 `ProactiveRecord.to_api_dict()`（`models.py:685`）
是**纯传输格式转换**，调用点全在 adapter：

```
debug_api.py:232,411,451    ← 只读接口
debug_recorder.py:56        ← 落盘 jsonl
```

`core` 内部无任何调用点。每加一个面板字段就要修改 `core/models.py`，属于分层泄漏。

### 3.5 附带发现（**独立于本次重构**，需要单独决策）

| 编号 | 问题 | 位置 | 说明 |
|---|---|---|---|
| **X1** | 每次状态写入触发全量落盘 | `core/state.py:69-79,101-125` | `set()` → `save()`，而 `save()` 序列化**所有会话**并用同步 `json.dump` 写盘。每次 LLM 请求产生 O(所有会话) 的阻塞磁盘 IO。会话数增长后成为瓶颈 |
| **X2** | LLM 路由缓存无上限 | `core/router.py:87,180-188` | 缓存仅在命中时检查 60s TTL，过期条目不被主动清理。短时间高并发下会累积 |
| **X3** | `_pending_signals` 按会话累积 | `adapter/injector.py:94` | 请求钩子写入、响应钩子消费。若响应钩子始终不触发，条目上限为会话数。影响很小，但无清理机制 |
| **X4** | `tests/` 存在 11 处既有 lint 债，且默认 ruff 命令完全看不到 | §2.3 | 由 AstrBot 根 `pyproject.toml` 的 `exclude = [..., "tests"]` 导致。见 §7.4 |

> **X1 是真实的可扩展性风险**，但修复它会改变数据持久化语义（防抖/批量写引入崩溃丢数据窗口）。
> 因此**不纳入本次重构**，另立任务评估。

---

## 4. 目标架构

### 4.1 `core/` —— 按业务域分包（≈ Java 的 service 层按域拆分）

```
core/
├── __init__.py              # 仅 docstring
├── consts.py                # 领域常量（叶子）
├── models.py                # 领域实体（叶子）
├── config.py                # 运行配置 + 解析（叶子）          ← P2 新建
├── records.py               # 调试记录数据（叶子）              ← P2 新建
├── tier_rules.py            # tier/kind 推断规则（叶子）        ← P1 新建
│
├── materials/               # 支撑域：资料文件、分层规则、缓存
│   ├── registry.py
│   ├── parsers.py
│   └── role_store.py
├── cognition/               # 支撑域：心情、话题、激活惯性
│   ├── state.py
│   ├── affect.py
│   ├── context_manager.py
│   └── updaters.py
├── routing/                 # 支撑域：一句话 → 命中资料
│   ├── router.py
│   └── fallback.py          # FallbackRouter                 ← P1 从 main.py 迁移
├── shell/                   # 支撑域：预算内拼装文本
│   ├── builder.py
│   └── assembly.py
└── proactive/               # 业务域：主动消息策略
    └── policy.py            # 原 proactive.py
```

**唯一硬规则：子包之间依赖无环，且不允许 `core/**` 出现 `astrbot` import。**
`__init__.py` 只写 docstring，**禁止 re-export 全体**（会造出新的隐式耦合节点）。

### 4.2 `adapter/` —— 按对外契约划分

```
adapter/
├── __init__.py
├── astrbot_compat.py        # 唯一接触 astrbot.core.* 的地方   ← P1 新建
├── injector.py              # 注入管道编排
├── dto.py                   # 记录 → API 字典的转换            ← P2 新建
├── commands.py              # 命令入口
├── proactive_runner.py      # 后台驱动循环
├── proactive_prompt.py
├── role_api.py              # 资料写路径（独立，职责不同于只读面板）
└── api/                     # 调试面板 API                    ← P4 新建
    ├── __init__.py          # register_all(context, deps) 唯一入口
    ├── http.py              # _get_query / _get_json 共享实现
    ├── sessions.py
    ├── injections.py
    ├── materials.py
    ├── route.py
    └── stats.py
```

### 4.3 新增业务功能的标准形状（模板）

后续新功能（如"后台日常模拟"）必须按此形状落地，**禁止**往既有文件里追加：

```
core/<新域>/policy.py          纯逻辑，无 IO，可单测
adapter/<新域>_runner.py       drivers：定时循环、发送、记录
main.py                        只在 FireflyCore 中增加一个字段 + 一处装配
```

---

## 5. 分阶段实施计划

### 5.1 阶段总览

| 阶段 | 内容 | 改动性质 | 改动面 | 风险 | 可独立提交 |
|---|---|---|---|---|---|
| **P0** | 安全网 | 仅新增测试 | 小 | 无 | ✅ |
| **P1** | 缺陷修复 | **行为修正** | 小 | 低 | ✅ |
| **P2** | 拆 `models.py` | 行为等价 | 中 | 低 | ✅ |
| **P3** | `core` 分包 | 行为等价 | 大 | 中 | ✅（须一次完成） |
| **P4** | `adapter` API 拆分 | 行为等价 | 中 | 低 | ✅ |
| **P5** | 装配收敛 | 行为等价 | 小 | 低 | ✅ |
| **P6** | 护栏与收尾 | 仅新增测试/文档 | 小 | 无 | ✅ |

**执行顺序不可调整**：P2 必须先于 P3（否则 `models.py` 仍是所有子包争抢的节点）；P0 必须先于全部。

---

### P0 — 安全网（先做，且必须全绿）

**目的**：在没有回归保护的情况下不开始任何重构。

#### P0-1 确认测试基线
- 动作：记录 `158 tests / OK / 2.97s` 作为基线，并写入本文件 §2.3（已完成）。
- 验收：命令可复现，结果一致。

#### P0-2 新增"注入行为锚点"测试
- 新建 `tests/test_behavior_anchor.py`
- 内容：
  1. 用固定资料目录（`tempfile` 构造）+ 固定 `SessionState` + 固定用户消息，走完整 `_inject()`；
  2. 断言注入文本（`req.extra_user_content_parts` 中的 XML）**逐字符等于固化字符串**；
  3. 断言 `InjectionRecord` 的结构化字段（`needed_ids`、`route_source`、`injected_successfully`、`truncated`、`over_budget`）与预期一致；
  4. 另跑一次 `_update_state()`，断言 `SessionState` 的 `mood / mood_intensity / recent_topics / active_context` 与预期一致。
- **约束**：不得断言时间戳（`timestamp`、`last_user_at` 等）。
- 这条测试是 P2-P5 全部行为等价阶段的**唯一权威判据**。
- 验收：测试通过；人为改动 `builder.py` 的一个字符能让它失败（验证灵敏度）。

#### P0-3 新增架构护栏测试
- 新建 `tests/test_architecture.py`
- 内容：
  1. 遍历 `core/**/*.py`，用 **AST 解析**断言其中不出现 `astrbot` 的绝对 import（字符串匹配会误报注释）；
  2. 断言 `core/` 的 `__init__.py` 只含 docstring，不得 re-export（避免形成隐式耦合节点）；
  3. 断言 `adapter/astrbot_compat.py` 是 `adapter` 与 `main.py` 中唯一 import `astrbot.core.*` 的文件
     （该文件建立前自动 skip）；
  4. 解析 `core/**/*.py` 的 import（相对形式 **+ 指向本插件 core 的绝对形式**），构建内部模块依赖图，断言无环。
- 说明：原计划将第 4 条推迟到 P3 之后启用，实际在 P0 即启用 —— `core/tier_rules.py`（P1-3）
  正是最容易引入环的改动，提前启用收益更大。第 2 条为原计划的补充规则。

#### ✅ P0 执行结果（已完成）

| 项 | 结果 |
|---|---|
| 基线测试 | `Ran 158 tests in 2.969s` — OK |
| 新增测试 | `tests/test_behavior_anchor.py`（3 个）、`tests/test_architecture.py`（4 个） |
| 全量测试 | `Ran 165 tests in 2.197s` — **OK (skipped=1)** |
| 新增文件 lint / format | `All checks passed!` / `2 files already formatted` |
| 锚点灵敏度 | 临时改动 `builder.py:23` 一个字符 → 锚点 A/C 失败并打印差异，已还原 ✅ |
| 护栏灵敏度 | 临时在 `core/consts.py` 加 `import astrbot`、`core/__init__.py` 加 re-export、`core/models.py` 引入环 → 3 条断言分别失败并给出对应清单，已还原 ✅ |
| 工作区状态 | 3 个新增文件（1 文档 + 2 测试），无已跟踪文件被修改 |

**P0 独立代码审查与修复（已闭环）**

对两个新增测试做了独立审查，全部发现均为「断言漏报」类问题——即测试会通过，但守不住它声称守住的性质：

| 发现 | 类型 | 修复 | 验证 |
|---|---|---|---|
| 锚点 C 的「任务路径不经过路由」断言恒真：夹具消息不含任何触发词，路由被误加到任务路径后注入文本不变 | WARNING（漏报） | 夹具消息改为含触发词「战斗」，并新增前置校验断言该消息确实能路由；若不含触发词则断言会退化为恒真 | 模拟「任务路径被误改为经过路由」→ 测试失败并打印出意外的 `<active_context>` 块 ✅ |
| 规则 3 只判 `startswith("astrbot.core.")`，`from astrbot.core import X` / `import astrbot.core` 逃过检查（规则 1 已正确处理相等情形，属不一致） | WARNING（漏报） | 抽出 `_matches_module_prefix()`（相等 **或** 子模块），规则 1 与规则 3 共用同一实现 | `astrbot.core` 命中、`astrbot.core.agent` 命中、`astrbotx` 不误报 ✅ |
| 依赖图只解析相对导入，`astrbot_plugin_Firefly.core.*` 绝对形式构成的环不可见 —— 而 P3-2 正要重写 import 路径 | SUGGESTION（漏报） | 新增 `_normalize_absolute_core()`，`ImportFrom` 与 `Import` 的绝对形式一并入图 | 在 `core/models.py` 加入绝对形式 `from astrbot_plugin_Firefly.core.affect import AffectEvent` 造环 → 测试失败并报 `affect → models → affect` ✅ |

> 此次审查的直接价值：锚点 C 与规则 3 在修复前都是「绿灯但无效」，
> 而它们正是 P1–P5 期间判断是否回归的依据。修复后全量测试仍为 165 个通过。

#### 后续阶段的同类风险提示

本次审查暴露出一个模式：**测试最容易的失效方式不是报错，而是"恒真"**。
P1–P6 每新增一个断言，都应自问「把被测代码改坏，这条断言会失败吗」，并实际做一次反向验证（如上述三项）。
新增测试的验收标准中应包含这一条。

> 待办：按计划 P0 出口条件，本阶段应以一次纯新增文件的 commit 收尾。

---

### P1 — 缺陷修复（行为修正，逐条独立提交）

> **本阶段允许改变行为**，但每一条都必须配一个能复现原缺陷的测试。
> 每条 = 一个 commit，commit message 使用 `fix:` 前缀。

#### P1-1 消除 `SessionState` 手工字段复制（H1）
- 文件：`core/models.py`、`adapter/injector.py`
- 动作：
  1. 在 `SessionState` 上新增 `snapshot() -> SessionState`，实现为 `return SessionState.from_dict(self.to_dict())`，**复用既有序列化逻辑**，使字段列表全局只有一处；
  2. `injector.py:210-216` 的 7 行替换为 `state_before = state.snapshot()`。
- 测试：新增测试断言 `snapshot()` 结果与源对象等值、且修改 `recent_topics` 不影响源对象。
- 验收：锚点测试（P0-2）不变。
- 附带收益：字段列表从 3 份降为 1 份。

#### P1-2 建立 AstrBot 兼容层（H2 / H3）
- 新建 `adapter/astrbot_compat.py`
- 动作：集中导出 `Message`、`TextPart`、`ContextWrapper`、`MessageChain`；对可能缺失的符号提供降级实现（`try/except ImportError`）。
- 修改 `main.py:17`、`adapter/injector.py:29,46` 改为从 `astrbot_compat` 导入。
- 测试：`test_architecture.py` 启用"仅 `astrbot_compat.py` 允许接触 `astrbot.core.*`"断言。
- 验收：全仓 grep `astrbot.core` 只命中 `astrbot_compat.py`。

#### P1-3 消除 `role_store → registry` 反向依赖（C1）
- 新建 `core/tier_rules.py`，把 `_infer_tier_kind` 及 `_read_file` 移入（改名 `infer_tier_kind`，去掉下划线）。
- `core/registry.py` 与 `core/role_store.py` 均改为从 `tier_rules` 导入。
- 测试：`test_loader.py` / `test_role_store.py` 全部通过；新增断言 tier 推断结果不变（对 5 类目录各一例）。
- 验收：`role_store.py` 不再 import `registry`。

#### P1-4 删除 `_lookup_meta`（C3）
- 文件：`core/context_manager.py:93-98`、同文件 `merge()` 第 48 行。
- 动作：统一改用 `registry.get(entry_id)`。
- **语义确认**：`registry.get()` 会触发懒加载；`merge()` 的场景下条目即将被组装使用，懒加载是**期望行为**。此改动修复的是"两条路径语义不一致"。
- 测试：锚点测试不变；`test_injector.py` 全绿。

#### P1-5 提取 `CompositeRouter` 为 `FallbackRouter`（C2）
- 新建 `core/routing/fallback.py`（P3 前先放 `core/fallback_router.py`，P3 时迁入子包）。
- 动作：把 `main.py:102-135` 的类移至该文件并改写为模块级类，保留原有降级语义。
- 测试：新增 `test_fallback_router.py`，覆盖四种组合：LLM 命中 / LLM 返回 None 且允许降级 / LLM 返回 None 且禁止降级 / 未启用 LLM。

#### P1-6 修复静默失败（S1 – S4、S6）

每条独立 commit。**注意 S4 的处理方式与其它不同**：

| 编号 | 动作 | 约束 |
|---|---|---|
| **S1** | `StateStore.__init__` 增加可选 `logger` 参数，`save()` 的 `except OSError` 记录 `logger.warning`；`main.py` 传入 `self.logger` | 不改落盘时机（X1 另行处理） |
| **S2** | `router.py:115` 改为 `except Exception as exc:` + 调试日志 | 保持"失败返回 None"的降级契约不变 |
| **S3** | `router.py:169` 增加 `logger.debug` 记录解析失败 | 返回值不变 |
| **S4** | **保留** `entry.content = ""` 的降级语义（`builder.py:82,123` 依赖 `content or ""`），改为把失败写入 `registry` 的告警列表 | **禁止改为 `None`**：那会让 `is_loaded()` 永久为 False，导致每次访问都重试读盘（重试风暴） |
| **S6** | 把 `models.py` 中两份 `_int/_float/_bool` 统一为 `core/parsers.py` 的带告警版本 | 与 C5 合并处理，见 P2-3 |

- 测试：S1 用只读目录模拟写失败；S2/S3 用抛异常的假 `llm_generate`；S4 用不可读文件。

#### P1-7 修复 C4 —— HTTP 辅助函数重复
- 新建 `adapter/api/http.py`（P4 前先放 `adapter/http_utils.py`），实现共享的 `get_query()` / `get_json()`。
- `debug_api.py`、`role_api.py` 的私有副本删除，改为调用共享实现。
- 测试：`test_debug_api.py` / `test_role_api.py` 全绿。

**P1 出口条件**：
- 锚点测试不变；全部测试通过。
- `astrbot.core` 在 `adapter` 中只出现在 `astrbot_compat.py`。
- 7 条各自独立 commit（`fix:` 前缀）。

---

### P2 — 拆分 `models.py`（行为等价）

> **本阶段禁止任何逻辑变更。** 只做"移动 + 改 import"。
> 锚点测试必须逐字符不变。

#### P2-1 抽出 `core/config.py`
- 移入：`ShellConfig`（`models.py:306-437`）、`ProactiveConfig`（`models.py:440-531`）。
- 保留在 `models.py`：领域实体（`MaterialEntry` → `SessionState` → `BuildResult` → `LoadReport`）。

#### P2-2 抽出 `core/records.py` + `adapter/dto.py`
- `core/records.py`：`InjectionRecord`、`ProactiveRecord` 的**数据字段与 `create()` 工厂**。
- `adapter/dto.py`：`injection_to_api_dict(rec)`、`proactive_to_api_dict(rec)`。
- 删除 `models.py:639` 与 `models.py:685` 的 `to_api_dict` 方法。
- 修改调用点：`debug_api.py:232,411,451`、`debug_recorder.py:56`。
- 判据（已在 §3.4.1 核实）：`to_api_dict` 的调用点全部位于 `adapter`，`core` 内部零调用。

#### P2-3 统一配置解析辅助函数（C5 / S6）
- 在 `core/parsers.py` 中提供 `coerce_int / coerce_float / coerce_bool`（`coerce_int` 已存在，补齐后两个，均接受可选 `warnings` 参数）。
- `config.py` 中两份重复的嵌套函数删除，改为调用 `parsers` 版本并传入告警列表。
- **行为影响**：配置脏值从"静默回退默认值"变为"回退 + 产生告警"。这是**有意的可观测性改进**，需在 commit message 中说明。
- 测试：新增 `test_config.py`，覆盖正常值 / 字符串数字 / 非法值 / 缺失值四类，断言回退值与告警文本。

#### P2-4 迁移 import
- 更新所有引用点：`main.py`、`adapter/*.py`、`tests/*.py`。
- 自检：`ruff check --select F401,F821 core adapter tests main.py`（未使用/未定义名称）。

**P2 出口条件**：锚点测试不变；158+ 测试全绿；`models.py` 行数降至 320 行以下。

---

### P3 — `core` 分包（行为等价，一次性完成）

> **本阶段是本计划改动量最大的一步。禁止半拆。**
> 只做"移动文件 + 改 import 路径"，**禁止顺手修改任何逻辑、重命名任何函数**。
> 锚点测试必须逐字符不变。

#### P3-1 建立子包并移动文件
按 §4.1 结构移动。`core/tier_rules.py`、`core/fallback_router.py`（P1 产物）一并迁入对应子包。

#### P3-2 机械重写 import
- 子包内相对导入（`from .models` → `from ..models`）。
- 外部绝对导入（`astrbot_plugin_Firefly.core.registry` → `astrbot_plugin_Firefly.core.materials.registry`）。
- `tests/` 同步更新。
- **不要**在 `__init__.py` 中 re-export。

#### P3-3 无环校验
- 启用 `test_architecture.py` 的依赖图无环断言（P0-3 已写好，此时打开）。
- 自检命令：`ruff check --select F401,F821,F811 core adapter tests main.py`

**P3 出口条件**：
- 锚点测试不变；全部测试通过。
- 架构测试的三条断言全部启用且通过。
- **一次 commit 完成**，便于整体回滚。

> **P3 完成后必须做一次真实环境冒烟**（见 §6.5），确认 AstrBot 能正常加载插件。

---

### P4 — `adapter` 调试 API 拆分（行为等价）

#### P4-1 建立 `adapter/api/`
- 按 §4.2 移动 handler，按资源归组：

| 目标文件 | 迁入的 handler |
|---|---|
| `sessions.py` | `_sessions_list/detail/update/activate/deactivate/reset/delete`、`_proactive_status/decisions/now` |
| `injections.py` | `_injections_list/clear/detail` |
| `materials.py` | `_materials_list/detail/reload` |
| `route.py` | `_route_test`、`_injection_preview` |
| `stats.py` | `_config_get`、`_registry_summary`、`_stats_get` |
| `http.py` | `_get_query`、`_get_json`、`_state_to_dict`、`ok`、`error` |

#### P4-2 统一注册入口
- `adapter/api/__init__.py` 提供 `register_all(context, deps)`，内部调用各模块的注册函数。
- `main.py:_register_debug_api()` 改为调用 `register_all`。
- **路由路径与响应结构必须完全不变**（面板前端不改）。

#### P4-3 分离 `RoleApi` 依赖
- `role_api.py` 保留独立文件（写路径职责不同于只读面板），但改从 `adapter/api/http.py` 取共享辅助函数。

**P4 出口条件**：锚点测试不变；`test_debug_api.py`、`test_role_api.py` 全绿；面板 6 个 Tab 手工验证可用。

---

### P5 — 装配收敛（行为等价）

#### P5-1 消除重复的 `_make_llm_generate`（C6）
- `main.py:93` 与 `main.py:160` 合并为一次创建、两处复用。

#### P5-2 `FireflyCore` 字段整理
- 保留 dataclass 形式（它是依赖清单，可读性高）。
- 补充各字段的一行说明，使"谁依赖谁"无需读 `__init__` 即可理解。

#### P5-3 `main.py` 瘦身核对
- 确认 `main.py` 中不再有任何业务策略逻辑（`CompositeRouter` 已于 P1-5 移出）。
- 目标：`main.py` 只做"装配 + 生命周期 + 钩子转发"。

**P5 出口条件**：锚点测试不变；全部测试通过；`main.py` 无 `if/else` 业务分支（闸门除外，闸门在 injector 内）。

---

### P6 — 护栏与收尾

#### P6-1 更新 `README.md` 的"目录结构"章节
- 使文档与实际结构一致（当前 README:400-429 描述的是旧结构）。

#### P6-2 补充架构约束到 `README.md` 或 `AGENTS.md`
- 明确写下三条硬规则：
  1. `core/**` 不得 import `astrbot`；
  2. `core/**` 子包之间依赖不得成环；
  3. 新业务功能必须按"`core/<域>` + `adapter/<域>_runner`"形状落地，禁止追加到既有文件。

#### P6-3 全量检查
- `ruff check core adapter main.py tests`（**必须显式传路径**，`.` 会跳过 `tests/`，见 §2.3）
- `ruff format --check core adapter main.py tests`
- 全量测试
- 真实环境冒烟（§6.5）

---

## 6. 防回归机制（核心）

> 本计划的第一约束是"重构不引入 bug"。

### 6.1 行为等价与行为修正严格分离

| 类别 | 阶段 | 规则 |
|---|---|---|
| **行为修正** | P1 | 允许改变行为，但每条必须配复现测试，commit 用 `fix:`，**逐条独立提交** |
| **行为等价** | P2 – P5 | **禁止任何逻辑变更**。只允许移动文件、改 import、改函数所属模块。锚点测试必须逐字符不变 |

一旦发现 P2-P5 期间需要"顺手修个 bug"，**停下**：先记录问题，插入到 P1 作为独立 commit 完成，再继续。混在一起会导致无法判断回归来源。

### 6.2 分阶段提交与回滚

- 每个阶段一次 commit（P1 为每子项一次）。
- 起点：`main` 分支，工作区干净（已验证）。
- 回滚策略：任一阶段出现问题，`git revert <阶段 commit>` 即可整体退回。P3 必须一次提交正是为了让"改动量最大的一步"可以一键回滚。

### 6.3 机械改动的自检清单

对 P2/P3/P4 这类纯移动，逐项确认：

- [ ] `ruff check --select F401,F821,F811 core adapter main.py tests` 无未使用/未定义/重复定义
      （**必须显式传路径**；`.` 会跳过 `tests/`）
- [ ] `grep -rn "core\.\(registry\|router\|affect\|state\|builder\|assembly\|models\|proactive\|role_store\|parsers\|updaters\|context_manager\)\b"` 无残留旧路径
- [ ] `grep -rn "import astrbot" core/` 输出为空
- [ ] `tests/` 中的 import 同步更新
- [ ] 锚点测试逐字符不变
- [ ] `__init__.py` 中无 re-export

### 6.4 测试覆盖对照

| 阶段 | 主要保护测试 |
|---|---|
| P1 | `test_behavior_anchor.py`、`test_config.py`(新)、`test_fallback_router.py`(新) |
| P2 | `test_behavior_anchor.py`、`test_builder.py`、`test_proactive.py`、`test_loader.py` |
| P3 | `test_behavior_anchor.py`、`test_architecture.py`(依赖图)、全部单测 |
| P4 | `test_behavior_anchor.py`、`test_debug_api.py`、`test_role_api.py` |
| P5 | `test_behavior_anchor.py`、`test_injector.py`、`test_proactive_runner.py` |

### 6.5 真实环境冒烟（每个阶段结束后）

单测不能覆盖 AstrBot 的钩子契约与前端面板。每个阶段结束后在真实 AstrBot 中执行：

1. 重启 AstrBot，确认插件加载无异常日志；
2. 发一条消息，确认 `/firefly status` 输出正常；
3. 打开调试面板，逐个 Tab 确认可用（重点：注入日志、状态面板、资料浏览保存）；
4. 确认注入日志中本轮 `injected_successfully = true`，且注入文本结构未变；
5. **P3/P4 完成后额外确认**：面板前端未报 404（路由路径未变）。

> 这一步是发现"import 改错但单测没覆盖"类问题的唯一手段。

### 6.6 不做超范围改动

- 不在本计划内改 `state.py` 的落盘策略（X1）。
- 不在本计划内改 token 估算算法（M2）。
- 不在本计划内解决死配置（§7.1 决策后另立任务）。
- 不换测试框架（保持 `unittest`，避免污染 diff）。

### 6.7 断言有效性：必须做反向验证

**新增任何测试或断言后，必须实际把被测代码改坏一次，确认该断言会失败。**

理由（P0 审查的实际教训）：测试最危险的失效方式不是报错，而是**恒真**——
它在 CI 中永远是绿灯，却在真正回归时同样绿灯。P0 的首版锚点 C 与规则 3
都属于这种情况，修复前无法检出它们声称保护的回归。

操作方式：做一次临时改动 → 运行该断言 → 确认失败且失败信息可定位 → 还原。
本仓库基线干净（`git diff` 为空），临时改动可在验证后直接
`git checkout -- <file>` 还原。

需要反向验证的对象：

| 阶段 | 需反向验证的断言 |
|---|---|
| P1-1 | `snapshot()` 独立性断言（改回手工浅拷贝应失败） |
| P1-3 | tier 推断结果不变（改坏推断规则应失败） |
| P1-5 | `FallbackRouter` 四种组合（去掉任一降级分支应失败） |
| P1-6 | S1–S4 的告警/日志断言（去掉日志语句应失败） |
| P2-3 | 配置脏值告警（去掉 `warnings.append` 应失败） |
| P4 | 面板路由路径不变（改任一 handler 注册名应失败） |

---

## 7. 待决策事项（执行前需要确认）

### 7.1 死配置处理（§3.3.1）

六个配置项/方法当前完全不生效，必须逐项决定：

| 项 | 选项 A：接通 | 选项 B：删除 |
|---|---|---|
| `inject.tier1_reserved` | 传入 `ShellBuilder`，替换 `builder.py:116` 的硬编码 `830` | 从 `_conf_schema.json`、`config.py`、状态输出中移除 |
| `state.use_llm` | 需要新增 LLM 状态推断逻辑（工作量较大） | 移除 |
| `active_context.default_*_ttl` ×3 | 在 `registry` 建立条目时作为 `default_ttl` 缺省值（需打通 `ShellConfig` → `registry`） | 移除，只保留 front-matter 的 `default_ttl` |
| `ShellConfig.get_default_ttl()` | 接通为上述 TTL 的消费入口 | 删除 |

**建议**：
- `tier1_reserved` → **接通**（实现成本低，且能消除魔法数字 M1，用户预期它生效）。
- `state.use_llm` → **删除**（实现成本高、与现有情绪事件机制职责重叠）。
- 三个 `default_*_ttl` → **接通**（这是用户最可能依赖的调参入口，删掉会削弱面板价值）。
- `get_default_ttl()` → **接通**。

> 该决策改变用户可见行为，**不放进本次重构**。执行 P6 后另立任务。

### 7.2 `core/` 三层目录深度

本计划采用 `core/<域>/<模块>.py`（两层）。若未来某个域内部再膨胀（如 `materials/` 超过 5 个文件），再考虑三层。**当前不预先建三层。**

### 7.3 是否需要 `DESIGN_` 文档

本计划已包含设计结论（§2、§4）。若需要与 `DESIGN_task_shell.md` 对齐的独立设计文档，可在执行前补 `DESIGN_arch_refactor.md`。当前判断：**不需要**，本文件已自包含。

### 7.4 是否为插件增加本地 ruff 配置

**问题**：插件位于 AstrBot 仓库内，ruff 会向上使用 AstrBot 根 `pyproject.toml`，其 `exclude` 含 `"tests"`，
导致 `ruff check .` 形式上"通过"而实际跳过测试目录（§2.3）。

| 选项 | 说明 | 代价 |
|---|---|---|
| **A. 保持现状 + 显式路径** | 文档化必须使用 `ruff check core adapter main.py tests` | 依赖执行者记得传路径；CI 若用 `.` 仍会漏 |
| **B. 增加插件本地 `ruff.toml`** | 让插件自成一体，不再继承 AstrBot 根配置 | 需要在该文件里补齐根配置中插件实际用到的规则；若 AstrBot 后续调整规则，插件不再自动跟随 |
| **C. 修复 `tests/` 的 11 处既有 lint 债** | 单独提交 `style: fix test lint` | 与重构无关的 diff 污染；但收益明确且风险为零 |

**建议**：先做 **C**（独立提交，不属于本重构），再评估 **B**。
**当前**：本重构全程采用 **A**，且每个阶段只检查"本阶段新增/改动的文件"，不顺手修改既有测试。

---

## 8. 风险登记

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| P3 import 改动遗漏 | 中 | 插件启动失败 | `ruff F821` 自检 + P0-3 架构测试 + §6.5 真实冒烟；P3 一次提交便于回滚 |
| P3 半拆导致"两份路径"混乱 | 中 | 难以定位问题 | 强制一次性完成；不允许多次小 commit |
| 锚点测试过于脆弱（误报） | 中 | 阻塞正常重构 | P0-2 明确禁止断言时间戳；仅断言文本与结构化字段 |
| `content=None` 引发重试风暴 | 低 | 性能退化 | P1-6 明确规定 S4 **保留 `""` 降级** |
| 拆分后 `models.py` 仍被争抢 | 低 | 收益不足 | P2 先拆 config/records，确保实体文件足够小；如仍超 400 行，再评估继续拆分 |
| 架构测试因误判 import 字符串而失败 | 低 | 阻塞 | 使用 AST 解析而非字符串匹配（`ast.parse` + 遍历 `Import`/`ImportFrom`） |
| AstrBot 内部 API 在重构期间变更 | 低 | H2 修复失效 | P1-2 兼容层已提供降级实现 |

---

## 9. 附录

### 附录 A：配置项真实消费对照表（实测）

| 配置项 | 消费位置 | 状态 |
|---|---|---|
| `enabled` | `injector._check_gates` | ✅ 生效 |
| `inject.max_tokens` | `assembly.build` → `builder` | ✅ 生效 |
| `inject.max_on_demand` | `main.py:100` → `KeywordRouter(max_entries=...)` | ✅ 生效 |
| `inject.enabled_sessions` | `injector._check_gates` → `is_session_enabled` | ✅ 生效 |
| `inject.tier1_reserved` | — | ❌ **失效** |
| `router.use_llm` | `main.py:92` | ✅ 生效 |
| `router.llm_timeout_seconds` | `main.py:96` → `LLMRouter(timeout=...)` | ✅ 生效 |
| `router.fallback_to_keyword` | `main.py:134` | ✅ 生效 |
| `router.cache_enabled` | `main.py:97` | ✅ 生效 |
| `state.persist` | `main.py:86` → `StateStore(persist=...)` | ✅ 生效 |
| `state.use_llm` | — | ❌ **失效** |
| `state.decay_hours` | `injector.py:292` → `affect.apply(stale_reset_hours=...)` | ✅ 生效 |
| `state.max_topics` | `injector.py:298` | ✅ 生效 |
| `active_context.default_skill_ttl` | — | ❌ **失效** |
| `active_context.default_lore_ttl` | — | ❌ **失效** |
| `active_context.default_narrative_ttl` | — | ❌ **失效** |
| `active_context.strength_decay_per_turn` | `main.py:138` | ✅ 生效 |
| `active_context.min_strength` | `main.py:139` | ✅ 生效 |
| `content_cache.max_entries` | `main.py:84` | ✅ 生效 |
| `proactive.*`（全部） | `main.py:153,158` | ✅ 生效 |

### 附录 B：模块行数与拆分目标

| 文件 | 当前行数 | 目标 | 拆分后预期 |
|---|---|---|---|
| `core/models.py` | 704 | ≤ 320 | 实体保留，config/records 移出 |
| `core/role_store.py` | 785 | ≤ 480 | 序列化移入 `parsers`，路径校验独立同包模块 |
| `adapter/debug_api.py` | 720 | ≤ 120 | 拆为 6 个文件（`adapter/api/`） |
| `adapter/injector.py` | 589 | ≤ 380 | 记录方法移入同包 `injector_records.py`（**仅 P4 之后评估，非必须**） |
| `adapter/proactive_runner.py` | 536 | ≤ 400 | 同上，非必须 |
| `adapter/role_api.py` | 256 | 保持 | — |

> `injector.py` / `proactive_runner.py` 的进一步拆分标记为**非必须**：只有当它们再次因为新增职责而增长时才执行。
> 拆分本身不是目的，控制"一次修改需要触碰几个关注点"才是。

### 附录 C：执行检查清单

- [x] **P0** 基线记录 + 锚点测试（3 个）+ 架构护栏测试（4 条，其中 1 条 gated 到 P1-2）；灵敏度已双向验证 ✅（见 §5「P0 执行结果」）
      —— commit 待执行：`test: add injection behavior anchor and architecture guard tests`
- [ ] **P1-1** `SessionState.snapshot()`；commit（`fix:`）
- [ ] **P1-2** `astrbot_compat.py`；commit（`fix:`）
- [ ] **P1-3** `core/tier_rules.py`；commit（`fix:`）
- [ ] **P1-4** 删除 `_lookup_meta`；commit（`fix:`）
- [ ] **P1-5** `FallbackRouter`；commit（`fix:`）
- [ ] **P1-6** S1–S4 静默失败；4 个 commit（`fix:`）
- [ ] **P1-7** 共享 HTTP 辅助函数；commit（`fix:`）
- [ ] **P1 冒烟**（§6.5）
- [ ] **P2-1** `core/config.py`；commit（`refactor:`）
- [ ] **P2-2** `core/records.py` + `adapter/dto.py`；commit（`refactor:`）
- [ ] **P2-3** 统一 `coerce_*`；commit（`refactor:`）
- [ ] **P2-4** import 迁移 + 自检；commit（`refactor:`）
- [ ] **P2 冒烟**
- [ ] **P3** `core` 分包（一次完成）；commit（`refactor:`）
- [ ] **P3 冒烟**（重点确认插件可加载）
- [ ] **P4** `adapter/api/` 拆分；commit（`refactor:`）
- [ ] **P4 冒烟**（重点确认面板路由）
- [ ] **P5** 装配收敛；commit（`refactor:`）
- [ ] **P5 冒烟**
- [ ] **P6** 文档更新 + 全量检查；commit（`docs:`）
- [ ] 另立任务：死配置处理（§7.1）
- [ ] 另立任务：状态落盘策略（X1）
