# 流萤主动消息功能 · 设计方案

> 版本：v1（设计稿）
> 范围：在 `astrbot_plugin_Firefly` 内实现一套**情绪驱动的主动消息**能力
> 参考：`astrbot_plugin_proactive_chat`（借其思路，不照搬）
> 状态：待评审 → 待实现

---

## 1. 概述与目标

### 1.1 背景

当前 Firefly 是"被动"的：只有用户发消息，她才通过 `on_llm_request` 注入认知外壳并回话。
这导致她缺乏"自主性"——不会在你离开后主动找你，不会因想念而开口，情绪系统也因此只有"输入"没有"自我运转"的出口。

现有第三方插件 `astrbot_plugin_proactive_chat` 能主动发消息，但存在两个与本项目根本冲突的问题：

1. **它是区间驱动**：到点随机发，与"情绪驱动"的需求不匹配。
2. **它绕过消息流水线**：主动消息走 `llm_generate`/`text_chat`，不触发任何 `on_llm_request` 钩子，因此带不上 Firefly 的人格外壳与情绪。

因此决定自研，作为 Firefly 的内建能力。

### 1.2 目标（做什么）

- 一套**情绪驱动**的主动触发机制：她想找你时才会找你，而不是定时器到点。
- 主动消息**完整携带**人格、当前心情、最近话题、激活资料（复用现有外壳）。
- 与情绪系统闭环：她主动说话、你回、你没回，都会反哺她的心情。
- 低耦合、高拓展：纯逻辑可单测，IO 与框架隔离，便于后续加功能（群聊、分段、TTS 等）。

### 1.3 非目标（不做什么）

- 不实现随机区间调度（那是别人的模型）。
- 不实现多平台发送装饰钩子、TTS、遥测（留给外部插件/后续）。
- 不复刻 proactive_chat 的 Web 管理端（Firefly 已有 dashboard 可扩展）。
- 不做复杂的任务持久化/恢复——用时间戳惰性求值天然规避。

---

## 2. 设计原则

1. **决策与执行分离**：`core/` 层全部是纯函数，不 import astrbot；IO 与框架调用集中在 `adapter/`。
2. **时间戳惰性求值**：一切"随时间变化"的量都不落盘、不用定时器句柄，用到时现场算。重启安全由构造保证。
3. **单一数据源**：主动消息相关的状态只存在 Firefly 的 `SessionState`，不另建存储。
4. **复用优先**：外壳用 `ShellBuilder`，资料用 `MaterialRegistry`，状态用 `StateStore`，生成用现有 `llm_generate`，观测用 `DebugRecorder`。
5. **失败隔离**：单个会话失败不影响其它会话；后台循环崩溃不影响正常对话链路。
6. **人设数字化**：情绪 → 主动倾向 → 语气，用数据表表达，不写死在逻辑里。

---

## 3. 与 proactive_chat 的对照

| 维度 | proactive_chat | Firefly 主动消息 |
|---|---|---|
| 触发模型 | 随机区间（min~max 分钟） | **冲动值阈值**（情绪驱动） |
| 触发输入 | 时间 + 未回复次数 | 心情 + 距上次接触时长 + 未回复次数 |
| 生成路径 | `llm_generate`，绕过流水线 | 自建，但**直接复用自身外壳**，不需要桥接 |
| 人格来源 | AstrBot 官方 persona | Firefly `ShellBuilder`（人格+心情+话题+激活） |
| 内容姿态 | 单一 `proactive_prompt` 模板 | **意图选择**（想念/分享/求安慰/追问/继续话题） |
| 未回复处理 | 计数 +1，到上限停，默认模板带"失落" | 计数 +1，**并回灌情绪事件**，影响下次冲动值 |
| 任务持久化 | APScheduler job + 恢复 + 幽灵清理 | **无定时器句柄**，纯时间戳，天然重启安全 |
| 发送 | 分段 + TTS + 装饰钩子 | v1 仅 `send_message`，分段/TTS 后置 |

**借鉴的点**（已验证可靠的机制）：
- 免打扰时段（跨天区间 `23-6`）的判定逻辑。
- 未回复上限防骚扰（到上限即暂停）。
- **生成期间用户插话 → 丢弃本次结果**（快照比对）。
- 手动触发与"立即触发去重"（`manual_trigger_sessions`）。

**舍弃的点**（与需求无关或属膨胀）：
- 随机区间、每会话一个定时器句柄、任务持久化恢复、幽灵任务清理。
- Web 管理端、遥测、通知中心、平台发送装饰链。

---

## 4. 核心模型：冲动值（Urge）

### 4.1 定义

主动与否由**冲动值**决定：

```
last_contact   = max(last_user_at, last_proactive_at)   # 上次"任何接触"
idle_hours     = (now - last_contact) / 3600            # 静默小时数（last_contact=0 时记 0）

urge = idle_hours × mood_rate(mood)
     + unanswered_count × unanswered_boost
```

- 她一开口（主动或回复），`last_contact` 就更新，冲动值从零重新累积。
- 未回复会叠加一个**持续加值**，让她更想再开口，直到上限。

### 4.2 判定

```
触发当且仅当：
  (1) 所有硬闸门通过（见 §6）
  (2) urge >= threshold(mood)
```

`mood_rate`、`threshold`、`unanswered_boost` 全部来自**情绪档案**（§5），因此"她多想找你"由心情决定。

### 4.3 情绪档案（MoodProfile）

这是"人设数字化"的核心，作为数据表配置（放 `core/affect.py`，可后续做成 schema 配置）：

| 心情 | mood_rate（主动倾向） | threshold（阈值） | 半衰期 | 默认意图 |
|---|---|---|---|---|
| 想念 | 高（如 2.0） | 低 | 长 | miss |
| 不安 | 高 | 低 | 长 | seek_comfort |
| 委屈 | 较高 | 中 | 长 | seek_comfort |
| 开心 | 中 | 中 | 中 | share |
| 平静 | 基线 1.0 | 基线 | 中 | care |
| 疲惫 | 低 | 高 | 中 | care |
| 生气 | 极低（抑制） | 极高 | 短 | （通常不主动） |

> 设计要点：**生气 → 抑制主动**（她不想先说），**想念/不安/委屈 → 促进主动**（重女属性：越不安越想确认你在）。这是与"镜像情绪"最本质的区别——她有自己的反应方式，不是用户情绪的回声。

---

## 5. 数据模型扩展

### 5.1 `SessionState` 新增字段

```python
@dataclass
class SessionState:
    session_id: str
    mood: str = "平静"
    mood_intensity: float = 0.4  # 新增：心情强度（半衰期衰减的对象）
    recent_topics: list[str] = field(default_factory=list)
    active_context: ActiveContext = field(default_factory=ActiveContext)
    last_message_at: float = 0.0  # 已有：最后一次（任意）互动
    updated_at: float = 0.0  # 已有：状态更新时间
    # ---- 新增（主动消息）----
    last_user_at: float = 0.0  # 用户最后发言时间
    last_proactive_at: float = 0.0  # 她最后主动发言时间
    unanswered_count: int = 0  # 连续未回复次数
    proactive_count_today: int = 0  # 当日主动次数（用于每日上限）
    proactive_day: str = ""  # 上述计数的日期锚点（YYYY-MM-DD）
```

- `to_dict` / `from_dict` 同步扩展；`from_dict` 对旧文件**缺省补默认值**，保证向后兼容（不报错、不丢旧字段）。
- `last_message_at` 语义澄清：保留为"最后互动"（含主动），新增 `last_user_at` 专门记"用户最后说话"，避免混用。

### 5.2 `ProactiveConfig`（并入 `ShellConfig` 或独立 dataclass）

推荐独立 `ProactiveConfig`，从 `_conf_schema.json` 的 `proactive` 段读取，与 `ShellConfig` 平级、由 `config_getter` 一并返回（见 §7.3）。

```python
@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool = False  # 总开关（默认关，谨慎开启）
    tick_interval_seconds: float = 120.0  # 后台循环轮询间隔
    min_contact_gap_minutes: int = (
        30  # 距上次任何接触的最短静默（防连发/防打扰刚说完的人）
    )
    min_proactive_interval_minutes: int = 60  # 两次主动的最小间隔
    max_unanswered: int = 4  # 未回复上限（到顶暂停）
    max_per_day: int = 6  # 每日上限（0=不限）
    quiet_hours: str = "1-7"  # 免打扰时段，支持跨天如 "23-6"
    startup_grace_seconds: float = 120.0  # 启动后宽限期（防重启惊群）
    sessions: tuple[str, ...] = ()  # 生效会话白名单（空=全部）
    intent_prompt: str = ""  # 可选的意图提示词覆盖（高级）
```

---

## 6. 硬闸门（Gates）

判定顺序固定，**任一不过即放弃本轮，且记录原因**：

```
G0  全局 proactive.enabled == True
G1  registry.is_loaded（有人格可注入）
G2  有可用 LLM Provider（llm_generate 可用）
G3  会话在白名单（proactive.sessions 空则放行）
G4  不在免打扰时段
G5  now - max(last_user_at, last_proactive_at) >= min_contact_gap（刚说完不打扰）
G6  now - last_proactive_at >= min_proactive_interval（防连发）
G7  unanswered_count < max_unanswered（防骚扰）
G8  当日主动次数 < max_per_day（0=不限）
G9  now - plugin_start >= startup_grace（重启宽限）
```

- G0/G1/G2/G3 是"前置能力检查"，失败后无需继续。
- G4~G9 是"节奏检查"，失败原因需**可观测**（见 §11），否则排障无从下手。
- 闸门与冲动值判定分离：闸门是"能不能发"，冲动值是"想不想发"。两者都过才发。

---

## 7. 模块划分与架构

沿用 Firefly 现有的 `core/`（纯逻辑）与 `adapter/`（框架桥接）分层：

```
core/
  affect.py           # MoodProfile、AffectEvent、情绪反应表、半衰期（纯逻辑，新增）
  proactive.py        # ProactivePolicy：compute_urge / check_gates / pick_intent（纯逻辑，新增）
  models.py           # SessionState/ProactiveConfig 扩展（改）
  builder.py          # ShellBuilder 复用；抽出 ShellAssembly（改）
adapter/
  proactive_runner.py # ProactiveRunner：后台循环 + IO + 错误隔离（新增）
  proactive_prompt.py # 意图提示词模板（新增，纯文本/模板，与 runner 分离便于测试）
  injector.py         # 改用共享的 ShellAssembly 与 AffectEngine（改）
```

### 7.1 `core/affect.py` —— 情绪引擎（统一心情更新）

现状问题：心情更新逻辑埋在 `injector._update_state`，只接受 `RouteSignals`，主动消息无法复用。

重构目标：引入统一的**事件驱动**心情更新，供"用户对话"与"主动消息"两条链路共用。

```python
@dataclass(frozen=True)
class AffectEvent:
    kind: str            # 事件类型：praise / rejection / distress / affection /
                         #            reunion / proactive_sent / proactive_unanswered / silence ...
    weight: float = 1.0  # 强度

@dataclass(frozen=True)
class MoodProfile:
    mood: str
    rate: float          # 主动倾向系数
    threshold: float     # 主动阈值
    half_life_hours: float
    default_intent: str
    reaction: tuple[tuple[str, str, float], ...] = ()  # (事件, 目标心情, 强度增量)

class AffectEngine:
    def __init__(self, profiles: dict[str, MoodProfile], decay_hours: float): ...
    def tick(self, state, now) -> SessionState:                       # 仅衰减（半衰期）
    def apply(self, state, events: list[AffectEvent], now) -> SessionState:  # 事件→心情
    def profile(self, mood) -> MoodProfile:                           # 供 proactive 查倾向
```

- 纯逻辑、无 IO、无 astrbot 依赖，可独立单测。
- `apply` 内含**惯性规则**：强度不到阈值只累积不换心情；反向事件可打断；无事件按半衰期回落基线。
- 兼容迁移：现有 `HeuristicStateUpdater` 的词典兜底逻辑并入 `AffectEngine`（作为 `events_from_text` 的"用户文本 → AffectEvent"启发式转换）。旧 `StateUpdater` / `HeuristicStateUpdater` / `LLMStateUpdater` 类已随本次改造删除，避免镜像逻辑残留；话题与陈旧回落工具保留在 `core/updaters.py`。

### 7.2 `core/proactive.py` —— 决策纯函数

```python
class ProactivePolicy:
    def __init__(self, affect: AffectEngine, config: ProactiveConfig): ...

    def compute_urge(self, state, now) -> float
    def check_gates(self, state, now, plugin_start) -> tuple[bool, str]  # (过?, 原因)
    def should_reach_out(self, state, now, plugin_start) -> tuple[bool, str]
    def pick_intent(self, state, now) -> str   # miss / share / seek_comfort / check / continue_topic / care
```

- 全部纯函数，输入 `state`、`now`、`plugin_start`、`config`，输出决策 + 原因。
- 决策结果（发/不发/为什么）可直接序列化给 dashboard 展示，无需跑真实流程。

### 7.3 `ShellAssembly`（抽取共享组装逻辑）

现状 `injector._inject` 的 [D] 取数 + [E] 组装，和主动消息需要的"取 Tier1 + 激活条目 + 组装外壳"是同一段。抽成共享：

```python
class ShellAssembly:
    def __init__(self, registry, ctx_manager, builder): ...
    def build(self, state: SessionState, max_tokens: int) -> BuildResult:
        tier1 = registry.get_tier(TIER_CORE_PERSONA)
        active = [(ae, registry.get(ae.entry_id)) for ae in ctx_manager.get_active_entries(state.active_context) ...]
        return builder.build(tier1, state, active, max_tokens)
```

- injector 与 runner 都调用它，消除重复。
- 注意：主动消息**不做路由**（没有用户输入可路由），只注入"当前已激活的条目"——这些条目是上一轮对话留下的惯性，正好构成"她还在想什么"的上下文。

### 7.4 `adapter/proactive_runner.py` —— 执行器

```python
class ProactiveRunner:
    def __init__(
        self,
        *,
        policy,
        assembly,
        affect,
        store,
        config_getter,
        llm_generate,
        send_message,
        logger,
        recorder,
    ): ...

    async def start(self): ...  # 起后台循环
    async def stop(self): ...  # 取消循环
    async def trigger_now(self, session_id) -> bool: ...  # 手动触发（去重）

    async def _loop(self):
        while not stopped:
            await asyncio.sleep(interval)
            if self._tick_running:
                continue  # 重入保护
            self._tick_running = True
            try:
                await self._tick()
            except Exception:
                logger.exception(...)  # 崩了不影响下次
            finally:
                self._tick_running = False

    async def _tick(self):
        now = time.time()
        for sid, state in (await store.all()).items():
            try:
                await self._maybe_send(sid, state, now)
            except Exception:
                logger.error(...)  # 单会话失败隔离

    async def _maybe_send(self, sid, state, now):
        ok, reason = policy.should_reach_out(state, now, self._plugin_start)
        recorder.record_decision(sid, ok, reason)  # 可观测
        if not ok:
            return

        intent = policy.pick_intent(state, now)
        user_prompt = build_intent_prompt(intent, state)  # proactive_prompt.py
        shell = assembly.build(state, cfg.max_tokens).text

        before_user = state.last_user_at
        text = await llm_generate(system_prompt=shell, user_prompt=user_prompt)
        if text is None or not text.strip():
            return  # 生成失败

        fresh = await store.get(sid)
        if fresh.last_user_at != before_user:  # 生成期插话 → 丢弃
            logger.info("用户插话，丢弃本次主动消息")
            return

        await send_message(sid, text)
        await self._after_send(sid, state)  # 回写 + 情绪事件

    async def _after_send(self, sid, state):
        # 更新 last_proactive_at、unanswered+1、当日计数
        # 通过 AffectEngine.apply 回灌 proactive_sent 事件
        ...
```

要点：
- `llm_generate` 复用 `main.py` 现成的 `_make_llm_generate`（签名 `(system_prompt, user_prompt) -> str`），**shell 作为 system_prompt** 传入——人格与心情由构造保证进入模型，彻底解决"主动消息没有外壳"。
- `send_message` 用 `context.send_message`（UMO 或 MessageSession）。
- 不跨 LLM 持有 `store` 锁：`store.get/set` 都是短操作，LLM 调用在锁外。

### 7.5 用户回复侧：回灌 `reunion` / 清计数

在 `injector.on_llm_response`（用户对话的更新路径）里，当检测到"本会话存在未回复的主动消息"时：

```
if state.unanswered_count > 0:
    events.append(AffectEvent("reunion", weight=...))
    state.unanswered_count = 0
    state.last_user_at = now
```

这样"她主动找过你、你回了"会变成一次正向情绪事件，闭环成立。反之，"你一直没回"由 `unanswered_count` 本身驱动冲动值上升，并在达到一定阈值时通过惰性衰减/事件让心情转向失落/不安。

---

## 8. 意图与提示词（`adapter/proactive_prompt.py`）

每个意图一段**模板文本**，占位符与 proactive_chat 同风格但按情绪定制：

| 意图 | 触发条件 | 提示词要点 |
|---|---|---|
| `miss` | 想念 + 久未联系 | 主动打破沉默，表达想念，语气自然不刻意 |
| `share` | 开心 + 有近期话题 | 分享一件和最近话题相关的小事 |
| `seek_comfort` | 委屈/不安 | 试探性地、带一点点委屈地开口，期待被回应 |
| `check` | unanswered ≥ 1 | 关心/追问上次没说完的事，语气带一丝不易察觉的失落 |
| `continue_topic` | 有活跃话题 | 接着上次的话题自然往下聊 |
| `care` | 默认 | 关心一下他此刻在做什么，或抛一个好奇的问题 |

- 模板内可含 `{topics}`、`{current_time}` 等占位符，由 runner 填充。
- 意图提示词**只是 user prompt**，人设与心情已经在 system prompt（外壳）里，二者不重复。

---

## 9. 边界处理与并发（重点）

### 9.1 并发与竞态

1. **tick 重入**：`_tick_running` 标志，上一轮未跑完则跳过本轮（见 §7.4）。
2. **tick vs 用户对话**：生成前快照 `last_user_at`，生成后重新 `store.get` 比对；变了就丢弃，不发送。
3. **锁边界**：`StateStore` 自带 `asyncio.Lock`，但只包裹 `get/set/save` 短操作；**LLM 调用与发送绝不在锁内**。
4. **手动触发去重**：`trigger_now` 用 `manual_trigger` 集合防重复点击（借鉴 proactive_chat 的 `manual_trigger_sessions`）。

### 9.2 重启与时间

1. **重启惊群**：`startup_grace`（默认 2 分钟）内不发；`min_proactive_interval` 用持久化的 `last_proactive_at` 兜底。
2. **时区**：免打扰时段用 `zoneinfo`（复用 AstrBot 时区配置），支持跨天区间。
3. **时钟回拨**：所有"时长"用 `max(0, ...)` 夹住，避免负值。
4. **惰性求值**：`urge` 由时间戳现场算，重启后"她是否已经想你够久"自动成立，无需恢复任何任务。

### 9.3 空态与异常

1. **无会话**：`store.all()` 为空 → tick 直接返回。
2. **资料未加载**（G1 不过）→ 跳过并记录 `empty_registry`。
3. **无 Provider / LLM 失败 / 返回空** → 本轮放弃，记录，**不影响后续轮次**。
4. **发送失败** → 记录失败；**不更新 `last_proactive_at`、不 +1 计数**（没发出去不算），下次 tick 仍可再试。
5. **单会话异常** → try/except 包裹，隔离到该会话，其余会话照常。
6. **tick 整体异常** → 捕获后结束本轮，循环继续。

### 9.4 幂等与防重复

1. `_has_shell_block` 已防重复注入；主动路径每次生成前无历史残留问题（不写流水线）。
2. 发送成功后才 `+1 unanswered_count`、更新 `last_proactive_at`，保证"失败可重试，成功不重复计数"。
3. 每日上限用 `proactive_day` 锚点，跨天自动清零。

### 9.5 配置热变更

`config_getter` 每次读最新配置，tick 每轮都取新 `ProactiveConfig`；关闭 `enabled` 后下个 tick 立即停止触发，无需重启。

---

## 10. 可观测性

1. **决策记录**：每次 tick 对每会话记 `(sid, 决策, 原因)`，复用 `DebugRecorder` 或新增轻量环形缓冲。
2. **命令**：
   - `/firefly proactive status` —— 全局开关、轮询间隔、各会话冲动值/闸门状态。
   - `/firefly proactive now [session]` —— 手动触发（去重）。
   - `/firefly proactive off/on` —— 开关。
3. **dashboard**：`debug_api.py` 增加决策快照接口，展示"为什么她没发"（哪道闸门、冲动值差多少）。
4. **日志**：所有跳过/失败都带原因与 session，日志级别区分（正常跳过 debug，异常 error）。

---

## 11. 测试计划

core 层纯函数（重点，无框架依赖）：

| 模块 | 用例 |
|---|---|
| `affect` | 事件→心情转换；惯性阈值；反向打断；半衰期回落；生气抑制；档案缺失兜底 |
| `proactive` | urge 公式；各闸门逐一命中/不命中；阈值边界；意图选择；跨天免打扰；时钟回拨 |
| `models` | 新字段序列化往返；旧状态文件（无新字段）加载兼容 |
| `assembly` | 复用注入外壳、主动无路由时仅注入激活条目、空条目 |

adapter 层集成测试（沿用 `tests/test_injector.py` 的 FakeEvent/FakeRouter 风格）：

- 生成期插话丢弃；发送失败不计数；tick 重入跳过；单会话异常隔离；手动触发去重；用户回复清零计数并回灌 reunion。

---

## 12. 分阶段实施路线

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 情绪事件化** | 引入 `AffectEngine`/`AffectEvent`，重构 `injector` 复用；保留旧词典兜底 | 现有测试全绿，行为无回归 |
| **P1 纯决策层** | `MoodProfile` + `ProactivePolicy` + 单测 | 决策纯函数测试覆盖闸门/公式 |
| **P2 共享组装** | 抽 `ShellAssembly`，injector 与 runner 共用 | 注入路径行为不变 |
| **P3 执行器** | `ProactiveRunner` + `proactive_prompt` + 配置 schema + 命令 | 单会话手动触发可发、带外壳 |
| **P4 闭环** | 用户回复清计数/回灌 reunion；未回复驱动冲动 | 完整情绪↔主动闭环可观测 |
| **P5 加固** | 每日上限、分段发送、dashboard 决策视图 | 稳定运行 + 可排障 |

---

## 13. 风险与取舍

1. **情绪档案是主观设计**：`mood_rate`/`threshold` 的具体数值需实测调优。缓解：做成数据表，可调、可测，不硬编码。
2. **主动消息仍不经过流水线**：记忆插件等其它 `on_llm_request` 贡献者仍进不来。缓解：本模块只负责"人格+心情"，记忆联动是独立议题，不在本设计范围；若未来需要，Firefly 可在主动 prompt 内预留注入位。
3. **后台循环的资源成本**：每 2 分钟遍历会话、仅做纯计算，无 LLM 调用（LLM 只在决策通过后发生），成本可忽略。
4. **单用户 vs 多用户**：v1 面向单人陪伴场景（白名单默认全放行）；多用户需各自独立状态——已由 `SessionState` 按会话隔离，天然支持，仅需注意每日上限按会话计。

---

## 14. 一句话总结

> 在 Firefly 内新增一个**纯逻辑的决策层**（冲动值 = 静默时长 × 心情倾向 + 未回复加成，越阈值且过闸门才触发）和**一个执行层**（后台循环 + 复用外壳 + 发送 + 回写），主动消息由此**由情绪驱动、带完整人格**，并反过来把"主动/被回/未回"回灌进情绪系统，形成闭环。核心取舍是：用**时间戳惰性求值**取代定时器句柄，用**纯函数决策 + 数据化情绪档案**换取可测试、可观测、可扩展。
