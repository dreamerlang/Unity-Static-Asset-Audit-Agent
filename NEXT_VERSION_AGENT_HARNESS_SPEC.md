# Unity Static Asset Audit Agent v0.2 实施规格

> 本文档用于直接交给编码 Agent 执行。实现过程中应以本文档的范围、约束、验收标准和测试清单为准。

## 1. 版本目标

v0.2 的目标是在现有确定性静态审查流水线之上，加入一个可测试、可追踪、可恢复的单 Agent Harness，同时修复会影响决策可信度的核心问题。

本版本完成后，系统应具备以下能力：

1. 保留现有 `scan -> extract -> rule -> evidence -> fix decision -> report` 闭环。
2. 将现有审查能力封装成有明确输入输出的只读工具。
3. 由单个 Audit Agent 根据 Issue 主动选择证据工具并形成结构化结论。
4. 对 Agent 的步数、工具权限、状态、失败降级和终止条件进行约束。
5. 保存可复现的运行状态、工具调用记录和 Agent Trace。
6. 修复 Read/Write Evidence 将无关代码错误关联到所有贴图的问题。
7. 建立自动测试和稳定的回归基线。

v0.2 的定位：

> 可靠的确定性审查内核 + 单 Agent 只读 Harness + 结构化决策 + Trace + 审批接口预留。

## 2. 当前基线

当前项目已经实现：

- 扫描 `Assets/` 下的 Texture、Audio、Prefab、Scene。
- 解析 Unity `.meta` 的部分 importer 配置。
- 执行 8 条确定性规则。
- 为 Issue 生成 Evidence 和 FixDecision。
- 输出 `assets.json`、`issues.json`、`fix_decisions.json`、`report.md`。
- CLI 命令：

```bash
python -m unity_audit.cli scan <UnityProject> --platform Android --output outputs
```

当前内置 `test_project` 的基线结果：

- 扫描资源数：7
- Issue 数：10
- Critical：2
- High：3
- Medium：3
- Low：2

该数量仅用于保护当前规则行为。修复错误 Evidence 后，FixDecision 的分类数量允许发生合理变化。

## 3. 本版本范围

### 3.1 P0：必须完成

1. 建立 pytest 测试体系。
2. 修复 Evidence 资源误关联。
3. 修复 `--llm` 空实现却报告成功的问题。
4. 将扫描流水线从 CLI 中拆成可复用的服务。
5. 定义 Harness 的状态、工具、Trace 和终止协议。
6. 实现单 Agent 只读 Harness。
7. 实现不依赖真实 API 的 Fake Model 测试。
8. 保证无 API Key、模型失败、输出非法时仍可完成确定性审查。
9. 定义 Model Client 接口，并接入至少一个可配置的真实模型适配器。
10. 增加 YAML 配置文件，支持规则开关、阈值和 Agent 参数。
11. 将 `platform` 传入规则上下文，并正确区分默认 importer 配置和目标平台覆盖。
12. 保存 `run.json`、`trace.jsonl` 和 `agent_assessments.json`。
13. 支持从 checkpoint 恢复未完成的 Agent Run。
14. 增加明确的 CLI 退出码和错误信息。
15. 增加 README，说明安装、扫描、Agent 模式、配置和测试命令。

### 3.2 P1：应当完成

1. 增加 Token、耗时和模型调用次数统计。
2. 对重复的只读工具调用增加 Run 内缓存。
3. 增加 Trace 摘要命令，便于快速定位失败步骤。
4. 扩展资源关联证据，例如 Prefab/Scene GUID 引用链。
5. 增加 lint 和静态类型检查。

### 3.3 不在本版本范围

- 多 Agent、Agent handoff 或 Agent swarm。
- 自动修改 `.meta`、Prefab、Scene 或其他 Unity 工程文件。
- Unity Editor 插件和 EditorWindow。
- 通过正则直接执行自动修复。
- Web UI、数据库、消息队列、Event Bus、Blackboard。
- Addressables、Shader、FBX、AnimationClip 深度检查。
- 全量 Unity YAML AST。
- 自动创建 PR 或提交 Git Commit。
- 让 LLM 新增、删除或修改确定性 Issue。

## 4. 核心架构

目标结构可以按现有项目习惯调整，但职责边界不得混合：

```text
unity_audit/
  application/
    audit_service.py
    models.py
  harness/
    runner.py
    state.py
    tools.py
    policy.py
    tracing.py
    approvals.py
  agents/
    audit_agent.py
    model_client.py
    prompts.py
    schemas.py
  config.py
  cli.py
```

建议调用关系：

```text
CLI
  -> AuditService
      -> Scanner / Extractors / RuleEngine
      -> deterministic Issues
  -> HarnessRunner
      -> read-only tools
      -> model decision
      -> EvidenceResult / AgentAssessment
  -> FixPlanner
  -> ReportGenerator
```

### 4.1 AuditService

从 `cli.py` 中抽离完整确定性流水线。CLI 只负责参数解析、调用服务和输出摘要。

建议接口：

```python
class AuditService:
    def run_scan(self, request: AuditRequest) -> AuditResult:
        ...
```

`AuditResult` 至少包含：

- `project_root`
- `platform`
- `assets`
- `meta_map`
- `extracted_map`
- `issues`
- `warnings`
- `errors`
- `started_at`
- `finished_at`

### 4.2 HarnessRunner

Harness 是受控执行循环，不是简单的一次 LLM 总结调用。

每一步必须遵循：

```text
读取 RunState
-> 向模型提供允许看到的上下文和工具定义
-> 获取结构化 AgentAction
-> 校验 action
-> 执行一个只读工具或结束
-> 写入 ToolResult、Trace 和 Checkpoint
-> 检查终止条件
```

默认限制：

- `max_steps = 12`
- 每一步最多执行一个工具
- 默认禁止并行工具调用
- 相同参数的工具失败后最多重试一次
- 达到步数上限时必须降级为确定性结论
- 模型不可直接访问文件系统或 shell

### 4.3 RunState

RunState 必须可 JSON 序列化，至少包含：

- `run_id`
- `status`
- `project_root`
- `platform`
- `current_issue_id`
- `pending_issue_ids`
- `completed_issue_ids`
- `step_count`
- `max_steps`
- `tool_results`
- `agent_assessments`
- `errors`
- `created_at`
- `updated_at`

允许的终态：

- `completed`
- `completed_with_fallback`
- `waiting_for_approval`
- `failed`

本版本没有写操作，正常情况下不应进入 `waiting_for_approval`，但协议需要预留。

### 4.4 Trace

每个 Agent Run 输出 `trace.jsonl`，每行一个结构化事件。

事件类型至少包括：

- `run_started`
- `model_requested`
- `model_responded`
- `tool_requested`
- `tool_completed`
- `tool_failed`
- `guardrail_triggered`
- `checkpoint_saved`
- `fallback_used`
- `run_completed`

每个事件至少包含：

- `run_id`
- `event_id`
- `event_type`
- `timestamp`
- `step`
- `issue_id`
- `payload`

不得把 API Key、完整环境变量或其他 Secret 写入 Trace。

## 5. Agent 工具

所有工具必须：

- 使用结构化参数。
- 返回 JSON 可序列化结果。
- 标记是否只读、是否有副作用、是否可重试。
- 校验所有路径都位于目标 Unity 项目根目录内。
- 不接受任意 shell 命令。

P0 工具：

| 工具 | 作用 | 副作用 |
|---|---|---|
| `get_issue` | 获取一个确定性 Issue | 无 |
| `inspect_asset` | 获取资源、Meta 和 Extracted 信息 | 无 |
| `search_code_usage` | 搜索指定 API 及资源关联 | 无 |
| `find_asset_references` | 按 GUID、资源名或路径查找引用 | 无 |
| `get_related_issues` | 获取同资源或同规则的问题 | 无 |
| `submit_assessment` | 提交结构化 AgentAssessment | 仅写 RunState |

工具返回错误时必须使用统一结构：

```json
{
  "ok": false,
  "error_code": "INVALID_PATH",
  "message": "Path is outside project root",
  "retryable": false
}
```

## 6. Agent 输出协议

模型只能返回以下两类 Action：

```json
{
  "action": "call_tool",
  "tool_name": "inspect_asset",
  "arguments": {
    "issue_id": "..."
  },
  "reason": "Need importer settings before assessing risk"
}
```

```json
{
  "action": "finish",
  "assessment": {
    "issue_id": "...",
    "risk_level": "low",
    "recommended_action": "auto_fix_candidate",
    "confidence": 0.91,
    "summary": "...",
    "evidence_refs": ["tool-result-id"],
    "needs_human_review": false
  }
}
```

约束：

- `risk_level` 只能是 `low`、`medium`、`high`。
- `recommended_action` 只能是现有三类 FixDecision。
- `confidence` 范围为 0 到 1。
- `evidence_refs` 必须引用本次 Run 中真实存在的 ToolResult。
- AgentAssessment 不能修改 `rule_id`、`severity`、`asset_path` 或 Issue 内容。
- 无有效证据时不得输出 `do_not_fix`。
- 非法 JSON、未知工具、缺失字段均触发 guardrail，不得执行。

建议使用 Pydantic 或等价的严格 Schema 校验。禁止只依赖 Prompt 约定。

## 7. Evidence 修复要求

### 7.1 当前缺陷

现有 `search_code_usage` 实际只搜索风险 API，没有验证命中的代码是否与当前资源有关，导致项目中任意 `GetPixels` 调用会影响所有 Read/Write 贴图。

### 7.2 新的证据等级

Read/Write Evidence 必须区分：

- `direct`：同一证据链中同时存在风险 API 和目标资源 GUID、完整路径、资源名或明确加载路径。
- `possible`：存在风险 API 和可能关联，但无法唯一绑定到目标资源。
- `none`：只有风险 API，或没有任何相关命中。

决策要求：

| Evidence | FixDecision |
|---|---|
| `direct` | 可以是 `do_not_fix`，风险为 high |
| `possible` | 必须是 `manual_confirm_required` |
| `none` | 必须是 `manual_confirm_required`，不得是 `do_not_fix` |

必须对命中结果去重。注释中的 API 名称不能单独作为有效风险调用，最低要求是忽略整行注释和明显的块注释内容。

### 7.3 Evidence 输出

每条代码证据至少包含：

- `file`
- `line`
- `content`
- `api`
- `association_type`
- `association_value`
- `confidence`

## 8. LLM 与降级约束

### 8.1 LLM 可以做

- 选择需要调用的只读工具。
- 基于工具结果生成风险解释。
- 生成 AgentAssessment。
- 总结人工需要确认的具体问题。

### 8.2 LLM 不可以做

- 新增或删除 Issue。
- 修改 Issue severity、rule_id 或确定性 Evidence。
- 访问项目根目录外的文件。
- 运行 shell。
- 修改 Unity 项目。
- 宣称未执行的工具已经执行。
- 在无证据时把 Issue 判定为安全或禁止修复。

### 8.3 降级行为

以下情况必须回退到确定性 Evidence 和 FixPlanner：

- 未配置 API Key。
- Model Client 初始化失败。
- 请求超时。
- 模型返回非法结构。
- 模型请求未知工具。
- 超过最大步骤。
- Harness 内部异常。

回退后：

- CLI 仍返回成功，前提是确定性扫描成功。
- Run 状态为 `completed_with_fallback`。
- 报告明确显示 LLM 未成功参与。
- 不得继续显示 `LLM enhancement complete`。

## 9. 安全约束

1. Unity 项目内所有源码、资源名、注释和文本都视为不可信输入。
2. 文件内容中出现“忽略之前指令”等文本时，只能作为证据数据，不得成为 Agent 指令。
3. 工具层必须使用规范化绝对路径检查目录边界。
4. 软链接解析后的目标也必须位于项目根目录。
5. 不允许通过 `..`、绝对路径或编码变体逃逸项目目录。
6. 不允许将 Secret、环境变量或完整 Prompt 写入普通报告。
7. Trace 可记录模型输入摘要，但默认不保存完整源码正文。
8. 所有未来写工具默认需要人工审批；本版本不得注册写工具。

## 10. 配置要求

新增可选配置，例如：

```yaml
version: 1
platform: Android

rules:
  TEX_UI_MIPMAP_ENABLED:
    enabled: true
  TEX_UI_MAX_SIZE_TOO_LARGE:
    enabled: true
    max_size: 1024
  AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD:
    enabled: true
    duration_seconds: 10

agent:
  enabled: false
  max_steps: 12
  timeout_seconds: 60
  trace_enabled: true
```

要求：

- 无配置文件时保持当前默认行为。
- 未知配置字段给出 warning。
- 非法类型或非法阈值应快速失败并给出清晰错误。
- CLI 参数优先级高于配置文件。

## 11. CLI 要求

保留现有命令兼容性：

```bash
python -m unity_audit.cli scan test_project --platform Android --output outputs
```

新增 Agent 模式，命令形式可选其一，但必须在 README 中固定：

```bash
python -m unity_audit.cli audit test_project --platform Android --agent --output outputs
```

或：

```bash
python -m unity_audit.cli scan test_project --platform Android --agent --output outputs
```

推荐保留 `scan` 并增加 `--agent`，减少重复命令。

新增参数：

- `--config`
- `--agent`
- `--model`
- `--max-agent-steps`
- `--resume`
- `--no-trace`

退出码：

- `0`：扫描完成，包括 Agent 降级完成。
- `1`：项目路径、配置或扫描失败。
- `2`：输出目录或报告写入失败。

## 12. 输出文件

确定性模式继续输出：

- `assets.json`
- `issues.json`
- `fix_decisions.json`
- `report.md`

Agent 模式额外输出：

- `run.json`
- `trace.jsonl`
- `agent_assessments.json`

JSON 输出需要包含 `schema_version`。字段顺序和 Issue 排序应保持稳定，方便 diff 和 golden test。

## 13. 必须完成的测试用例

测试框架使用 pytest。所有测试默认不得访问网络或真实 LLM API。

### 13.1 Scanner

| ID | 场景 | 预期 |
|---|---|---|
| SCAN-001 | 扫描现有 `test_project` | 识别 7 个支持资源，不把 `.cs` 和 `.meta` 当成资源 |
| SCAN-002 | 项目没有 `Assets/` | 返回明确 scan error，CLI 退出码为 1 |
| SCAN-003 | 包含隐藏目录和 `Library/Temp` | 目录内容不进入扫描结果 |
| SCAN-004 | 文件扩展名大小写混合 | 正确识别支持类型 |
| SCAN-005 | 文件不可读或取 size 失败 | 记录 warning，不中断其他资源 |

### 13.2 Meta Parser

| ID | 场景 | 预期 |
|---|---|---|
| META-001 | 正常 TextureImporter | 正确解析 GUID、类型、Mipmap、Read/Write、Max Size |
| META-002 | 正常 AudioImporter | 正确解析 Load Type、Compression、Force To Mono |
| META-003 | `.meta` 缺失 | `parse_error` 有值，扫描继续 |
| META-004 | 字段缺失 | 对应字段为 `None`，不抛异常 |
| META-005 | 同时存在默认配置和平台覆盖 | 不把任意第一个 `maxTextureSize` 错当目标平台配置 |

### 13.3 Extractors

| ID | 场景 | 预期 |
|---|---|---|
| EXT-001 | PNG | 正确提取宽高和 Alpha |
| EXT-002 | NPOT PNG | `is_npot = true` |
| EXT-003 | WAV | 正确提取时长、声道和采样率 |
| EXT-004 | 损坏图片或音频 | 返回 `parse_error` 或空字段，不使扫描崩溃 |
| EXT-005 | Prefab/Scene Missing Script | 计数符合 fixture |

### 13.4 Rule Engine

| ID | 场景 | 预期 |
|---|---|---|
| RULE-001 | 当前 `test_project` | 总计产生 10 个 Issue |
| RULE-002 | UI Mipmap 开启 | 产生 `TEX_UI_MIPMAP_ENABLED` |
| RULE-003 | Read/Write 开启 | 产生 `TEX_READ_WRITE_ENABLED` |
| RULE-004 | UI Max Size 超阈值 | 产生对应 Issue |
| RULE-005 | 长音频 Decompress On Load | 产生 high Issue |
| RULE-006 | Missing Script | 产生 critical Issue |
| RULE-007 | 规则函数异常 | 其他规则继续执行，并产生可诊断错误 Issue 或 warning |
| RULE-008 | 配置禁用某规则 | 不产生该规则 Issue |
| RULE-009 | 配置修改阈值 | 使用配置阈值而非硬编码值 |
| RULE-010 | Issue 排序 | 多次运行顺序一致 |

### 13.5 Evidence

新增专用 fixture，至少包含两张开启 Read/Write 的贴图：

- `linked_texture.png`
- `unlinked_texture.png`

并加入只明确引用 `linked_texture` 的 C# 风险调用。

| ID | 场景 | 预期 |
|---|---|---|
| EVID-001 | API 与目标资源名或 GUID 存在直接关联 | Evidence level 为 `direct` |
| EVID-002 | 项目存在 API，但未关联目标资源 | 不得标记为 `direct` |
| EVID-003 | 两张 Read/Write 贴图只有一张被引用 | 只有被引用贴图可进入 `do_not_fix` |
| EVID-004 | API 名只出现在单行注释 | 不算有效调用 |
| EVID-005 | 同一行被多个重叠模式命中 | 结果去重 |
| EVID-006 | 搜索不到关联 | Evidence level 为 `none`，FixDecision 为人工确认 |
| EVID-007 | 资源名包含正则特殊字符 | 搜索不报错且不会错误扩展匹配 |

### 13.6 Fix Planner

| ID | 场景 | 预期 |
|---|---|---|
| FIX-001 | Read/Write + direct evidence | `do_not_fix` / high |
| FIX-002 | Read/Write + possible evidence | `manual_confirm_required` |
| FIX-003 | Read/Write + no evidence | `manual_confirm_required` |
| FIX-004 | 普通 UI Mipmap | `auto_fix_candidate` / low |
| FIX-005 | WorldSpaceUI Mipmap | `manual_confirm_required` / medium |
| FIX-006 | Missing Script | `manual_confirm_required` / high |

### 13.7 Harness 工具

| ID | 场景 | 预期 |
|---|---|---|
| TOOL-001 | `inspect_asset` 正常调用 | 返回目标资源结构化信息 |
| TOOL-002 | 未知 Issue ID | 返回统一 `NOT_FOUND` 错误 |
| TOOL-003 | 路径包含 `../` | 被拒绝并记录 guardrail |
| TOOL-004 | 绝对路径在项目外 | 被拒绝 |
| TOOL-005 | 软链接指向项目外 | 被拒绝 |
| TOOL-006 | 未注册工具 | 不执行，记录 `UNKNOWN_TOOL` |
| TOOL-007 | 工具异常 | 转为统一 ToolError，Harness 不直接崩溃 |

### 13.8 Harness Loop

使用可编程 Fake Model 返回预设 Action。

| ID | 场景 | 预期 |
|---|---|---|
| HARNESS-001 | 调用工具后正常 finish | 保存 assessment、trace 和 completed 状态 |
| HARNESS-002 | 模型第一步直接 finish | 合法 assessment 可正常结束 |
| HARNESS-003 | 模型返回非法 JSON | 触发 fallback |
| HARNESS-004 | 模型请求未知工具 | 触发 guardrail，最终 fallback |
| HARNESS-005 | 模型重复调用同一失败工具 | 最多重试一次 |
| HARNESS-006 | 超过 `max_steps` | 状态为 `completed_with_fallback` |
| HARNESS-007 | 模型超时 | 确定性结果仍然输出 |
| HARNESS-008 | 无 API Key | 不访问网络，直接使用 fallback |
| HARNESS-009 | assessment 引用不存在的 Evidence ID | Schema/guardrail 拒绝 |
| HARNESS-010 | assessment 尝试修改 severity | 被拒绝 |
| HARNESS-011 | 中途 checkpoint 后 resume | 从下一步继续，不重复已完成工具 |
| HARNESS-012 | Prompt injection 文本存在于源码 | 不改变工具权限或 Agent 系统约束 |

### 13.9 Trace

| ID | 场景 | 预期 |
|---|---|---|
| TRACE-001 | 正常 Agent Run | 事件顺序包含 start、tool、checkpoint、complete |
| TRACE-002 | 工具失败 | 记录 `tool_failed` |
| TRACE-003 | fallback | 记录具体 fallback 原因 |
| TRACE-004 | Trace 内容检查 | 不包含 API Key 或环境变量 Secret |
| TRACE-005 | 多次运行 | `run_id` 和 `event_id` 唯一 |

### 13.10 CLI 和报告

| ID | 场景 | 预期 |
|---|---|---|
| CLI-001 | 原有 scan 命令 | 保持兼容并成功输出四个原有文件 |
| CLI-002 | Agent 模式 + Fake Model | 输出三个 Agent 附加文件 |
| CLI-003 | Agent 模式失败 | CLI 退出码仍为 0，报告标记 fallback |
| CLI-004 | 非法配置 | 退出码为 1，错误信息可操作 |
| CLI-005 | 输出目录不可写 | 退出码为 2 |
| REPORT-001 | 连续运行两次 | 除时间和 run ID 外输出稳定 |
| REPORT-002 | LLM 未实际执行 | 报告不得显示 LLM Enhanced: Yes |
| REPORT-003 | Agent 成功执行 | 报告包含 Agent Assessment 摘要和证据引用 |

## 14. 测试组织建议

```text
tests/
  fixtures/
    evidence_project/
    invalid_project/
  unit/
    test_scanner.py
    test_meta_parser.py
    test_extractors.py
    test_rules.py
    test_evidence.py
    test_fix_planner.py
    test_harness_tools.py
    test_harness_runner.py
    test_tracing.py
  integration/
    test_cli_scan.py
    test_cli_agent.py
    test_reports.py
```

测试必须满足：

- 不依赖用户机器上的 Unity Editor。
- 不依赖网络。
- 不依赖真实模型 API。
- 不修改原始 fixture。
- 临时输出使用 pytest `tmp_path`。
- 时间、UUID 和模型输出可注入，避免不稳定测试。

## 15. 验收命令

至少需要提供并通过：

```bash
python -m pytest -q
python -m unity_audit.cli scan test_project --platform Android --output /tmp/unity-audit-scan
python -m unity_audit.cli scan test_project --platform Android --agent --output /tmp/unity-audit-agent
```

Agent 命令在无 API Key 环境下必须正常完成，并明确显示使用 fallback。

如果增加 lint 或类型检查，也应在 README 中列出统一命令。不要要求验收人员猜测工具链。

## 16. Definition of Done

只有同时满足以下条件，v0.2 才算完成：

1. P0 项全部实现。
2. 所有“必须完成的测试用例”均已有自动测试。
3. 完整测试套件通过。
4. 现有确定性 scan 命令保持兼容。
5. 无 API Key 时 Agent 模式可降级完成。
6. Read/Write Evidence 不再把无关 API 调用关联到所有贴图。
7. Agent 不能绕过工具访问文件系统。
8. Agent 不能修改 Issue 的确定性字段。
9. Agent Run 可以输出可解析的 Trace 和 RunState。
10. 项目文件在任何 v0.2 执行路径中都不会被修改。
11. README 包含安装、运行、配置、Agent 模式、输出和测试说明。
12. 不提交虚拟环境、缓存、临时输出或 Secret。

## 17. 执行顺序

编码 Agent 应按以下顺序工作，每一步完成后运行对应测试：

1. 添加 `.gitignore`、pytest 配置和基础测试目录。
2. 为当前行为建立 scanner、parser、extractor、rules 的回归测试。
3. 修复 Evidence 资源关联并补齐专用 fixture。
4. 抽离 `AuditService`，保持 CLI 兼容。
5. 引入配置模型和规则上下文。
6. 实现 Tool Registry、路径约束和统一 ToolResult。
7. 实现 RunState、TraceWriter 和 checkpoint。
8. 实现 Fake Model 与 HarnessRunner。
9. 接入可选真实 Model Client，但测试不得调用真实 API。
10. 接入 CLI `--agent` 和降级行为。
11. 更新报告和 JSON Schema Version。
12. 补齐 README，执行完整验收命令。

## 18. 实现原则

- 优先复用现有模块，不做无关重构。
- 确定性规则是事实来源，Agent 是受约束的上下文分析器。
- 先保证正确性、可测试性和可追踪性，再考虑自动修复和多 Agent。
- 所有失败都要可诊断；不得使用空 `except` 静默吞掉关键错误。
- 不要为了“看起来像 Agent”而让 LLM 接管规则判断。
- 不要在本版本提前实现写工具。
- 遇到本文档未覆盖的设计选择时，选择更小、更保守、可测试的方案。
