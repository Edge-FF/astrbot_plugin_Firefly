# 用户角色预设模块 · 设计文档

> 版本：v1（设计稿，待实现）
> 关联：`DESIGN_ui_role_editor.md`（资料编辑器）、`PLAN_arch_refactor.md`（整体架构）
> 状态：只读分析 + 设计方案。**不写业务代码到主程序、不改 AstrBot。**
> 范围：仅 `data/plugins/astrbot_plugin_Firefly/`。

---

## 1. 背景与目标

### 1.1 背景

Firefly 会把「流萤的认知外壳」注入 LLM：`<static_core>`（Tier1 常驻人格）+ `<dynamic_state>`（心情/话题）+ `<active_context>`（Tier3/4 按需资料）。

对话的另一端是「用户」。目前用户身份没有任何显式建模：`role/人物关系/开拓者.md` 只是若干人物关系条目之一，靠路由概率触发。这带来两个问题：

1. **身份不确定**：用户是开拓者时，模型并不知道"正在对话的人就是开拓者"，只能靠本轮话题碰巧激活那条资料。
2. **无法扮演**：用户想以自定义角色对话时，没有任何地方承载"我是谁"。

### 1.2 目标

给插件增加「用户角色预设」：

1. 用户可**选择一个现有人物**（默认开拓者）作为自己的身份。
2. 用户可**新建自定义角色文档**并选为自己的身份。
3. 被选中的文档作为**会话级常驻**内容注入，且**确定性**存在，不依赖路由。
4. 未被选中的角色（含开拓者）**保持原有按需触发行为，不废弃、不改动**。

### 1.3 非目标

- 不做「按发送者」的用户级身份（本期只到会话级，见 §11.3）。
- 不修改 tier 机制，不移动/重命名既有资料文件。
- 不把用户身份并入 `<static_core>`（那是流萤的人格，不是用户的人设）。
- 不在面板里做 Markdown 富文本（沿用只读渲染 + 纯文本编辑）。

---

## 2. 已确认决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 身份与资料的关系 | **独立维度**。身份不进入 tier 体系，不改变 `开拓者.md` 的 tier/kind |
| D2 | 存储键 | 会话只存 `{mode, role_id}`，**不存正文、不存路径**；由 id 经 `registry.get(id)` 定位 |
| D3 | `开拓者.md` 归属 | **复用**。trailblazer 模式下把它 pin 为常驻身份；其余模式下它是普通按需资料 |
| D4 | 自定义文档位置 | 新增 `role/用户角色/`，kind=`user_role`，**永不进入路由池** |
| D5 | 存储位置 | 独立 `data/user_role.json`，**不放进 `SessionState`**（避免 `/firefly reset` 清除 pin） |
| D6 | 注入形态 | 新增 `<user_profile>` 常驻块，不可裁剪，与 `static_core` 并列 |
| D7 | 生效范围 | 全局默认 + 会话级覆盖；面板编辑默认，命令设置会话 |
| D8 | 路由去重 | pin 的 id 从该会话的路由候选中排除（会话级，非全局） |

---

## 3. 核心概念模型

三个概念必须严格分开，这是整个模块清晰的根基：

```
流萤资料（全局）          用户身份（会话级）            注入结果
─────────────────         ─────────────────           ──────────────
role/人物关系/开拓者.md ─┐
role/用户角色/我的角色.md ─┤→  pin: {mode, role_id}  →  <user_profile>（常驻）
role/技能/…              │
role/世界知识/…          └→  registry 索引              <active_context>（按需，照旧）
```

- **资料**：`MaterialEntry`，由 `MaterialRegistry` 全局索引，路径/tier 决定它的加载与路由行为。
- **身份 pin**：会话级设置，只引用资料 id。它决定"哪份资料进常驻块"以及"哪个 id 从路由中临时排除"。
- **注入**：`<user_profile>` 承载 pin 指向的正文；其余资料行为完全不变。

**关键不变量：pin 只改变「激活方式」，从不改变「资料本身」。** 因此 `开拓者.md` 不可能被废弃。

---

## 4. 数据模型

### 4.1 领域类型（新增 `core/user_role/models.py`）

```python
MODE_EXISTING = "existing"  # 选一个现有人物
MODE_CUSTOM = "custom"  # 选一个用户自建文档


@dataclass(frozen=True)
class UserRoleSetting:
    """一条身份设置。role_id 为空时，existing 模式回退到内置默认 id。"""

    mode: str = MODE_EXISTING
    role_id: str = ""


@dataclass(frozen=True)
class ResolvedUserRole:
    """解析后的可注入身份。"""

    pin_id: str  # 实际 pin 的条目 id；"" 表示无有效身份
    entry: MaterialEntry | None  # 对应资料（含正文）；None 表示解析失败
    origin: str  # "existing" | "custom" | "fallback" | "none"
    warning: str = ""  # 解析告警（缺失/空正文/回退原因）
```

`ResolvedUserRole` 是纯数据，`MaterialEntry` 复用 `core/models.py:22`，不新造文档类型。

### 4.2 常量（`core/consts.py` 追加）

```python
KIND_USER_ROLE = "user_role"  # 身份文档的 kind，路由池排除依据
DEFAULT_USER_ROLE_ID = "trailblazer"  # 内置默认身份 id

DIRECTORY_TIER_RULES += (("用户角色", TIER_SKILL_LORE, KIND_USER_ROLE),)
```

`用户角色/` 目录不参与路由：kind 是 `user_role`，在 §6.3 被显式排除。

> 常量按阶段落地，避免 P0 携带未消费的定义：`SHELL_USER_PROFILE_TAG` 在 P1（渲染注入块）
> 引入，`USER_ROLE_CUSTOM_DIR` 在 P2（候选列表/API）引入。两者都只在此处描述用途，
> 不提前写进 `consts.py`。

### 4.3 持久化格式（`data/user_role.json`）

```json
{
  "version": 1,
  "default": { "mode": "existing", "role_id": "" },
  "sessions": {
    "<unified_msg_origin>": { "mode": "custom", "role_id": "my_role" }
  }
}
```

- `default` 是全局默认；`sessions` 是会话覆盖。两者结构相同，解析规则一致。
- `role_id: ""` + `mode: existing` 表示「内置默认身份」（`DEFAULT_USER_ROLE_ID`）。首次运行不需要文件。
- 与 `SessionState`（`data/cognitive_state.json`）**物理隔离**：`/firefly reset` 只清动态状态，不动身份。

### 4.4 身份文档格式（复用现有 frontmatter）

```markdown
---
id: trailblazer
title: 开拓者
kind: lore
---
（正文：这个角色是谁、与流萤的关系、对话时的定位）
```

- `id` 是 pin 的主键，必须**全局唯一**（role 编辑器保存时已查重，见 `DESIGN_ui_role_editor.md` §7.4）。
- 自定义文档放 `role/用户角色/`，`kind: user_role`（不写时由目录规则推断）。
- 正文为空 → 该身份无效（`registry` 对 Tier3 懒加载空正文不会跳过，因此**有效性必须由解析器判定**，见 §6.2）。

---

## 5. 架构与职责划分

### 5.1 分层

```
┌─────────────────────────── adapter（薄） ────────────────────────────┐
│ role_api.py      HTTP：user_role/get|set|reset，参数提取 + 响应封装     │
│ injector.py      编排：取 setting → resolve → 组装 → 注入              │
│ proactive_runner 复用同一 resolve + assembly                          │
│ commands.py      /firefly role（会话级便捷入口）                       │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │ 只调用 core 的公共方法
┌─────────────────────────────────── core（纯逻辑 / 可单测） ───────────┐
│ user_role/store.py     UserRoleStore：user_role.json 读写 + 原子落盘   │
│ user_role/resolver.py  resolve(setting, registry) → ResolvedUserRole   │
│ user_role/service.py   UserRoleService：store + resolver 的应用编排    │
│ shell/builder.py       渲染 <user_profile> 块（不分摊路由/存储职责）    │
│ shell/assembly.py      取 Tier1 + 身份 + 激活条目 → BuildResult         │
│ materials/registry.py  索引；排除 user_role 出 index_summary            │
│ routing/router.py      按 exclude_ids 过滤候选与解析结果                 │
└───────────────────────────────────────────────────────────────────────┘
```

### 5.2 职责矩阵（单一职责，互不越界）

| 组件 | 唯一职责 | 明确不做 |
|---|---|---|
| `UserRoleStore` | 持久化 `{default, sessions}`，提供同步读/异步写 | 不认识 registry，不做解析，不生成文本 |
| `UserRoleResolver` | `setting + registry → ResolvedUserRole`（纯函数） | 不读盘、不写盘、不管 HTTP、不认识 session |
| `UserRoleService` | 组合 store + resolver，暴露 `resolve(session_id)`（同步） | 不做 IO 细节，不拼 XML |
| `ShellBuilder` | 把 `MaterialEntry` 渲染成 `<user_profile>` 块 | 不解析身份、不管 pin、不查路由 |
| `ShellAssembly` | 解析身份（可复用调用方传入的 `resolved`）、组装四层块、过滤 pin 残留 | 不渲染 XML（交给 builder）、不生成文本 |
| `Injector` / `ProactiveRunner` | 编排调用顺序 | 不自行解析 id、不自行拼块 |
| `RoleApi` | HTTP 契约 | 不直接改文件、不直接改 store 内部结构 |
| `MaterialRegistry` | 索引与检索；排除 `user_role` kind | 不知道"身份"这个概念 |
| `ContextRouter` | 按 `exclude_ids` 约束选资料 | 不知道 exclude 的语义来自用户角色 |

依赖方向：`adapter → core`，`core.user_role → core.models / core.materials`，`core.shell → core.models`。**不存在反向依赖，也不存在循环。**

---

## 6. 运行时应用逻辑

### 6.1 时序（普通对话路径）

```
on_llm_request
  └─ _inject
       1. state = store.get(session_id)
       2. resolved = user_role_service.resolve(session_id)           # [身份解析，同步]
       3. route_result = await router.route(
              user_msg, state, registry,
              exclude_ids=frozenset({resolved.pin_id}) if resolved.pin_id else frozenset())
       4. state.active_context = ctx_manager.merge(route_result, ...)
       5. result = assembly.build(state, config.max_tokens, resolved=resolved)
       6. req.extra_user_content_parts.append(...)
```

任务路径（`_inject_for_task`）与主动消息（`ProactiveRunner`）走同一 `resolve + assembly`，因此三条路径的 `<user_profile>` **必然一致**。

### 6.2 解析规则（`resolve`）

```
resolve(setting, registry):
  mode = normalize(setting.mode)          # 非法值 → MODE_EXISTING
  if mode == MODE_CUSTOM:
      rid = setting.role_id
      entry = registry.get(rid) if rid else None
      if valid(entry):  return {pin_id: rid, entry, origin: "custom"}
      # 自定义失效 → 回退内置默认，保留告警
      return resolve_default(registry, warning=f"自定义角色 {rid!r} 不可用，已回退默认身份")
  # MODE_EXISTING
  rid = setting.role_id or DEFAULT_USER_ROLE_ID
  entry = registry.get(rid)
  if valid(entry):  return {pin_id: rid, entry, origin: "existing"}
  return {pin_id: "", entry: None, origin: "none", warning: f"身份 {rid!r} 不存在或为空"}

valid(entry) = entry is not None and not entry.is_empty()
```

规则要点：

- **空正文视为无效**：`registry` 只在 Tier≤2 的加载阶段跳过空正文（`registry.py:93-95`），Tier3/4 懒加载不跳过，所以有效性必须在这里判定。
- **回退链**：custom 失效 → 内置默认 → 无。每一级都产生告警，绝不静默。
- **解析失败不抛异常、不中断注入**：返回 `entry=None`，`<user_profile>` 块为空，外壳其余部分照常。

### 6.3 路由去重（会话级）

`ContextRouter.route` 增加可选参数：

```python
async def route(self, user_msg, session_state, registry,
                exclude_ids: frozenset[str] = frozenset()) -> RouteResult
```

三个实现统一遵守：

- `KeywordRouter`：候选过滤补一条 `e.id not in exclude_ids`（`router.py:242`）。
- `LLMRouter`：`_parse_response` 的 `valid_ids` 过滤补 `rid not in exclude_ids`（`router.py:181`）。
- `FallbackRouter`：原样透传。

语义：

- **trailblazer 模式**：`exclude_ids={trailblazer}` → 开拓者已常驻，不再被激活。
- **custom 模式**：`pin_id` 是 `用户角色/` 里的条目，本来就不在路由池（kind 过滤），此参数对现有人物**无任何影响** → 开拓者等照常按需触发。
- **排除是会话级的**：A 会话选自定义时，B 会话的开拓者路由不受影响。

`ShellAssembly.active_pairs()` 另外过滤一次 `pin_id`（防御旧 `active_context` 残留），双保险。

### 6.4 索引侧隔离

- `MaterialRegistry.index_summary()`（`registry.py:152`）：跳过 `entry.kind == KIND_USER_ROLE`，避免自定义文档出现在喂给 LLM 路由的摘要里（跨会话泄露 + 浪费 token）。
- 路由候选：`e.tier >= TIER_SKILL_LORE and e.kind != KIND_USER_ROLE`。
- `用户角色/` 仍被 `registry` 收录（供 `registry.get(id)` 解析与编辑器元数据），只是不参与路由。

### 6.5 注入块与预算

`ShellBuilder` 新增块，位于 `static_core` 之后：

```
<cognitive_shell>
  …intro…
  <static_core>…流萤人格…</static_core>
  <user_profile>…当前对话者扮演的角色设定…</user_profile>
  <dynamic_state>…</dynamic_state>
  <active_context>…</active_context>
</cognitive_shell>
```

- 块内容沿用 `<entry id=… title=…>` 结构，与既有块一致。
- **不可裁剪**，与 `static_core` 同属预留预算（`tier1_reserved`）。
- `_SHELL_INTRO`（`builder.py:22`）补一句"`<user_profile>` 描述正在与你对话的人是谁"，否则模型可能把用户设定误当流萤人格。
- **超长保护**：自定义正文可能任意长，新增 `ShellConfig.user_profile_max_tokens`（默认 600）。超限时按字符边界截断并追加 `…（身份设定已截断）`，同时置 `BuildResult` 告警。
- 若 `static_core + user_profile` 本身已超总预算，仍优先保留两者，只裁剪 `<active_context>`（身份比按需资料重要）。

---

## 7. 配置

`_conf_schema.json` 新增：

```json
"user_role": {
  "user_profile_max_tokens": { "type": "int", "default": 600 },
  "custom_dir": { "type": "string", "default": "用户角色" }
}
```

- 全局默认身份**不放在插件配置**，而在 `user_role.json` 的 `default`。避免"配置一处、JSON 一处"的双源冲突；`user_role.json` 是身份设置的唯一事实来源。
- `custom_dir` 保持默认 `用户角色`；本期不建议改（改目录会牵动 consts 的推断规则）。

---

## 8. 后端 API 契约

新增三个薄接口，挂在 `adapter/role_api.py` 同一前缀 `/{plugin_name}/page` 下（复用 `HttpHelpers` 的 `ok()/error()`）。不修改只读的 `debug_api.py`。

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | `user_role/get` | 读取默认 + 会话设置、解析结果预览、候选列表 |
| POST | `user_role/set` | 设置默认或会话身份 |
| POST | `user_role/reset` | 清除会话覆盖 / 重置默认 |

### 8.1 `GET user_role/get`

查询参数：`session`（可选，会话 UMO）

```json
{
  "default": { "mode": "existing", "role_id": "" },
  "session": { "mode": "custom", "role_id": "my_role" },
  "resolved": {
    "pin_id": "my_role",
    "origin": "custom",
    "title": "我的角色",
    "exists": true,
    "body": "……",
    "warning": ""
  },
  "candidates": {
    "existing": [
      { "id": "trailblazer", "title": "开拓者", "path": "人物关系/开拓者.md", "empty": false },
      { "id": "dan_heng", "title": "丹恒", "path": "人物关系/丹恒.md", "empty": true }
    ],
    "custom": [
      { "id": "my_role", "title": "我的角色", "path": "用户角色/我的角色.md", "empty": false }
    ]
  }
}
```

- `resolved.body` 来自真实解析（`registry.get`），保证"预览即所注入"。
- `candidates.existing` 取自 `人物关系/` 前缀，`candidates.custom` 取自 `用户角色/` 前缀；都基于 `role/tree` 的文件系统结果，附带 `empty` 标记（空文档不可作为身份）。

### 8.2 `POST user_role/set`

```json
{ "scope": "session", "session": "<umo>", "mode": "custom", "role_id": "my_role" }
```

- `scope`: `"default"` | `"session"`（默认 `"default"`）。
- 服务端先 `resolve` 校验：解析出的 `entry` 无效（不存在/空正文）→ 返回明确错误，**拒绝写入**。
- `scope=default` 且 `mode=existing` 且 `role_id=""` 合法（表示内置默认）。
- 成功后返回 `{ "setting": {...}, "resolved": {...} }`，供前端即时更新预览。

### 8.3 `POST user_role/reset`

```json
{ "scope": "session", "session": "<umo>" }
```

- `scope=session`：删除该会话覆盖，回落到默认。
- `scope=default`：`default` 重置为 `{ "mode": "existing", "role_id": "" }`。

### 8.4 删除守卫扩展（重要边界）

现有删除守卫 `RoleApi._usage_map()`（`role_api.py:195`）只统计 `active_context`。**必须把"正被 pin 的会话"一并计入**，否则用户删除自己的身份文档时不会收到提示。

改造：`_usage_map` 在 `active_context` 之外，额外合并身份设置：

- 会话级覆盖中 `role_id == entry_id` 的会话 id；
- 全局默认身份（`existing` 空 id 视为内置默认 `DEFAULT_USER_ROLE_ID`）命中时，追加占位标记 `（全局默认身份）`——它不是真实会话，只用于让守卫生效。

结果并入同一个 `usage` 列表（沿用既有 `active_sessions` / `active_session_ids` 字段，不新增来源字段），前端据此提示"该文档正被 N 个使用方引用"。

---

## 9. 前端交互设计

### 9.1 Tab 归属

新增独立 Tab **「用户角色」**，复用资料编辑器的右栏组件，但候选来源不同。理由：身份选择与流萤资料编辑是两套心智，混在一起用户会分不清。

### 9.2 布局（两栏 master-detail）

```
┌─ 用户角色 ───────────────────────────────────────────┐
│ 当前身份：开拓者（常驻注入，已从按需路由排除）           │
├──────────────────┬───────────────────────────────────┤
│ (○) 现有角色      │  预览（只读渲染，= 真实注入内容）   │
│   🔍 搜索         │  标题 / id / 正文                  │
│   开拓者  ✓已选   │                                   │
│   三月七          │  [设为当前身份] [编辑] [+ 新建]     │
│ (○) 自定义角色    │                                   │
│   我的角色A       │                                   │
│   + 新建自定义角色 │                                   │
└──────────────────┴───────────────────────────────────┘
```

### 9.3 交互规则

1. 左栏是**二选一单选组**：决定 `mode`；列表某一项决定 `role_id`。
2. 点击候选 → 右栏只读预览 → 「设为当前身份」可用。
3. 「设为当前身份」→ `user_role/set` → 顶部状态条更新 + toast。
4. 预览区固定提示：**"该文档将作为常驻身份注入，并暂时从按需路由中排除"**。
5. 「新建自定义角色」与「以此为基础新建」共用一个弹窗：
   - 名称（生成文件名）、`id`（默认取名称，实时查重）、正文（非空校验）。
   - 保存走 `role/save`（`create: true`）→ 成功后自动 `user_role/set` 为新身份。
6. 改名：**必须保持 frontmatter `id` 不变**；若 id 会变，提示"这将解除当前身份绑定"。
7. 删除当前 pin 的文档：二次确认 + "删除后当前身份将回退默认（开拓者）"。
8. 未保存切换拦截（`beforeunload` + 应用内确认），沿用 `DESIGN_ui_role_editor.md` §7.5。

### 9.4 不做的事

不要把"选择身份"和"编辑文档"合成一个按钮。选身份是轻操作，编辑才进编辑器。

---

## 10. 边界情况与失败处理

| # | 场景 | 处理 |
|---|---|---|
| 1 | 身份文档被删除 | `resolve` 回退内置默认（custom）或无身份（existing），块为空，**不中断外壳注入**，记 warning |
| 2 | 身份文档正文为空 | `valid()` 判为无效 → 走回退链；UI 候选项标 `empty` 且不可选 |
| 3 | `role_id` 不存在 | 同上；`user_role/set` 直接拒绝写入 |
| 4 | `mode` 为非法值 | `normalize()` 归一化为 `existing`，记 warning |
| 5 | `id` 冲突（手写两个同 id 文件） | `registry` 保留先加载者并告警（`registry.py:258`）；`user_role/set` 可用 `role/tree` 的 `duplicate_id` 标记拒绝歧义 id；role 编辑器保存时查重 |
| 6 | 自定义文档被放进可路由池 | `kind != user_role` 过滤保证不泄露；若被误放进 `人物关系/`，则退化为普通按需资料，不影响正确性 |
| 7 | pin 的既有角色残留在 `active_context` | `ShellAssembly.active_pairs()` 过滤 `pin_id`；`ContextRouter` 候选排除；下轮 `merge` 不再续期 |
| 8 | 切换身份 | 每轮解析，**立即生效**；无需清 `active_context`（旧 pin 由 7 处理） |
| 9 | `/firefly reset` | 只重置 `SessionState`；身份在 `user_role.json`，**不受影响**（D5 的核心动机） |
| 10 | 会话状态因 `is_stale` 衰减 | `StateStore` 的陈旧清理与身份无关，身份不衰减 |
| 11 | 群聊多用户 | pin 是**会话级（UMO）**，群聊共享一个身份。文档需写明此语义；按 sender 建模为非目标 |
| 12 | 非管理员访问 | 写接口依赖 Dashboard 会话鉴权；命令 `/firefly role` 用 `PermissionType.ADMIN` |
| 13 | 并发生成/切换 | `UserRoleStore` 用 `asyncio.Lock` + 临时文件 `os.replace` 原子替换（对齐 `StateStore.save`） |
| 14 | `user_role.json` 损坏 | `load()` 告警并回退默认设置，**不清空会话认知状态**，不影响注入 |
| 15 | 自定义正文超长 | 截断到 `user_profile_max_tokens` + 告警，不挤爆总预算 |
| 16 | `用户角色/` 目录不存在 | 视为空候选；首次新建时由 `role_store` 创建父目录 |
| 17 | 插件停用/重载 | `terminate()` 调 `UserRoleStore.close()` 最终落盘 |
| 18 | 无 `register_web_api` 的旧版本 AstrBot | 能力探测后跳过注册（沿用 `main.py:364` 做法）；命令入口仍可用 |

---

## 11. 关键取舍

### 11.1 为什么身份不放 `SessionState`

`SessionState` 是**动态认知状态**，`/firefly reset`（`commands.py:110`）会 `store.reset()` 将其整体清空。身份是**设置**，不该被情绪重置带走；且 `is_stale` 的陈旧清理也不应让身份失效。因此独立 `UserRoleStore`，物理隔离。

### 11.2 为什么不给 `开拓者.md` 改 tier

tier 是**全局、加载期**确定的；身份是**会话级、运行期可变**的。把 `开拓者.md` 提为 Tier1 会让所有会话强制注入，且污染 `<static_core>` 的语义。pin 机制只在该会话内改变它的激活方式，文件本身不动。

### 11.3 为什么只到会话级

按发送者建模需要 `(UMO, sender_id)` 复合键，并改动 `StateStore`/`ProactiveRunner` 的会话语义，影响面大。本期会话级即可满足"用户预设自己是谁"；群聊语义在 §10.11 明示，后续如需再扩展。

### 11.4 为什么改路由协议而不是事后过滤

在 `route()` 增加 `exclude_ids` 是显式约束，避免 pin 条目占用 `max_on_demand` 名额、也避免它进入候选摘要。事后在 `assembly` 过滤虽然更省改动，但会浪费路由槽位，且约束语义被藏在组装层，职责不清。

---

## 12. 测试计划

新增 `tests/test_user_role.py`（纯逻辑，沿用现有 unittest 风格）：

- **Store**：默认初始化、会话覆盖、`default`/`sessions` 落盘 round-trip、`reset`、损坏文件回退、写失败告警。
- **Resolver**：
  - existing / custom 正常解析；
  - custom 缺失 → 回退内置默认，`origin=fallback` + warning；
  - 空正文判无效；
  - 非法 mode 归一化；
  - `pin_id` 为空时 `exclude_ids` 为空集。
- **Registry 过滤**：`index_summary()` 不含 `user_role`；`all_entries()` 仍包含。
- **Router**：`KeywordRouter`/`LLMRouter._parse_response` 在 `exclude_ids` 下不返回该 id；`FallbackRouter` 透传。
- **Builder**：`<user_profile>` 渲染与顺序、不可裁剪、超 `user_profile_max_tokens` 截断 + 告警。
- **Assembly**：`active_pairs` 过滤 `pin_id`。
- **Injector / ProactiveRunner**（集成）：pin 被解析并注入；任务路径同样包含；解析失败时外壳其余部分正常。

回归：现有 `tests/`（loader / matcher / injector / builder / config / state / proactive / role_api / role_store / architecture）全绿。

---

## 13. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** core 纯逻辑 | `core/user_role/{models,store,resolver,service}.py`；consts 追加；registry 的 `user_role` 过滤 | `test_user_role` 全绿；现有测试不回归 |
| **P1** 注入链路 | `ShellBuilder` 的 `<user_profile>`；`ShellAssembly.build` 参数；injector 两条路径与 proactive 接入；router `exclude_ids` | 会话 pin 后外壳含 `<user_profile>`；pin id 不再出现在激活条目 |
| **P2** API | `user_role/{get,set,reset}`；`RoleApi._usage_map` 纳入 pin；`main.py` 装配 `UserRoleStore/Service` | 接口可读写；删除 pin 文档时收到守卫提示 |
| **P3** 前端 | 「用户角色」Tab：两池单选 + 预览 + 新建/编辑/删除 | 选择立即生效；新建后自动 pin；空/重名被拦截 |
| **P4** 命令与文档 | `/firefly role status|set|clear`；README 增补 | 命令可查改会话身份 |

依赖：P0 是地基；P1 依赖 P0；P2/P3 依赖 P1；P4 可与 P3 并行。

---

## 14. 一句话总结

> 用户身份是**独立于 tier 的会话级常驻维度**：只存 `{mode, role_id}`，由 id 经 `registry.get` 定位正文，渲染为不可裁剪的 `<user_profile>` 块；
> `开拓者.md` 不改动、不废弃——trailblazer 模式下被 pin 为常驻并临时排除路由，其余模式下照旧按需触发；
> 存储与 `SessionState` 物理隔离，解析失败逐级回退且永不中断注入。
