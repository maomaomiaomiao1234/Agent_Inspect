# 项目文档索引

项目说明文档统一归档在 `docs/`，根目录 [README](../README.md) 提供运行截图、任务范围、启动条件和命令。整理日期：**2026-10-05**。

本次梳理覆盖原有 **50 份 Markdown**：38 份说明文档迁移到此目录、11 份运行 / Skill / 历史证据文件保留原件并提供文档副本、原 README 完整归档。所有文件都可从本页找到。原路径、归档路径、处理方式和整理前 SHA-256 记录在 [Markdown 清单](markdown-inventory.json)。第三方依赖、缓存、构建产物和本地评测数据不属于项目说明文档。

## 从这里开始

| 目标 | 阅读入口 |
| --- | --- |
| 安装并启动网页 | [项目 README](../README.md#安装与启动) |
| 理解 Agent 能力与评分 | [通用评测](guides/GENERAL_ASSESSMENT.zh-CN.md)、[可视化与评分](guides/ASSESSMENT_VISUALIZATION.zh-CN.md) |
| 无密钥跑通第一次评测 | [控制 Agent 示例](examples/assessment/README.md) |
| 评测公开 GitHub 仓库 | [仓库评测](guides/REPOSITORY_ASSESSMENT.zh-CN.md)、[Python 自动适配](guides/AUTO_ADAPTATION.zh-CN.md) |
| 接入已有 Agent 服务 | [主动评测与 target-v1 协议](guides/ACTIVE_ASSESSMENT.zh-CN.md) |
| 导入已有轨迹或自定义验收 | [完整导入说明](archive/README.previous.md#own-data)、[自定义任务](reference-assets/skills/opencode-trace-review/references/custom-tasks.md) |
| 核对验证范围 | [验收记录索引](validation/VALIDATION.md)及下方完整清单 |

## 目录约定

- `guides/`：当前功能、协议与接入方法。
- `examples/`：示例说明；可执行代码、题集和 JSON 工件仍位于项目根目录的 `examples/`。
- `validation/`：各阶段验收记录，按文档中注明的日期、版本和环境阅读。
- `planning/`、`reviews/`：设计计划、交接与历史评审；当前实现以项目 README、指南及代码为准。
- `archive/`：整理前的完整 README，保留进阶导入、专项验证、导出和常见问题说明。
- `reference-assets/`：有固定路径要求的 Markdown 副本，原件仍供运行、打包或证据核对使用。
- `images/`：真实页面截图，供项目 README 展示。

说明页中的项目命令通常在仓库根目录执行。模板中 `trace.json`、`profile.json` 等文件名指模板资源或 `init-task` 生成的目录，移动说明页不会移动这些运行数据。

## 使用与接入指南（10 份）

| 文档 | 整理前路径 |
| --- | --- |
| [主动评测其他 Agent](guides/ACTIVE_ASSESSMENT.zh-CN.md) | `docs/ACTIVE_ASSESSMENT.zh-CN.md` |
| [评测结果可视化](guides/ASSESSMENT_VISUALIZATION.zh-CN.md) | `docs/ASSESSMENT_VISUALIZATION.zh-CN.md` |
| [用 LLM 自动接入 Python Agent 仓库](guides/AUTO_ADAPTATION.zh-CN.md) | `docs/AUTO_ADAPTATION.zh-CN.md` |
| [通用任务评测与结果阅读](guides/GENERAL_ASSESSMENT.zh-CN.md) | `docs/GENERAL_ASSESSMENT.zh-CN.md` |
| [受控模型网关试点](guides/MODEL_GATEWAY.zh-CN.md) | `docs/MODEL_GATEWAY.zh-CN.md` |
| [通用模型用量采集](guides/PROVIDER_TELEMETRY.zh-CN.md) | `docs/PROVIDER_TELEMETRY.zh-CN.md` |
| [从仓库自动部署并评测 Agent](guides/REPOSITORY_ASSESSMENT.zh-CN.md) | `docs/REPOSITORY_ASSESSMENT.zh-CN.md` |
| [已知源码的 Agent 评测](guides/SOURCE_GUIDED_ASSESSMENT.zh-CN.md) | `docs/SOURCE_GUIDED_ASSESSMENT.zh-CN.md` |
| [主动评测中的模型与工具记录](guides/TARGET_TELEMETRY.zh-CN.md) | `docs/TARGET_TELEMETRY.zh-CN.md` |
| [Token 统计探索与下一步建议](guides/TOKEN_USAGE_EXPLORATION.zh-CN.md) | `docs/TOKEN_USAGE_EXPLORATION.zh-CN.md` |

## 示例说明（7 份）

| 文档 | 整理前路径 |
| --- | --- |
| [主动评测控制 Agent](examples/assessment/README.md) | `examples/assessment/README.md` |
| [代码修复验证材料](examples/code_repair/README.md) | `examples/code_repair/README.md` |
| [文档转换评估示例](examples/document_conversion/README.md) | `examples/document_conversion/README.md` |
| [发票提取任务模板](examples/invoice/README.md) | `examples/invoice/README.md` |
| [输入输出的大模型评审示例](examples/llm-review/README.md) | `examples/llm-review/README.md` |
| [调研任务模板](examples/research/README.md) | `examples/research/README.md` |
| [实际接入 smolagents](examples/smolagents/README.md) | `examples/smolagents/README.md` |

## 验收记录（18 份）

这些记录保留当时的任务 ID、哈希与验证结果；涉及本机临时目录或被忽略的数据目录时，应按文档中的复现命令重新运行。历史模型名称和上游版本以记录日期为准。

| 文档 | 整理前路径 |
| --- | --- |
| [主动 Agent 评测与服务部署验收](validation/ACTIVE_ASSESSMENT_VALIDATION.zh-CN.md) | `ACTIVE_ASSESSMENT_VALIDATION.zh-CN.md` |
| [主动评测范围与报告改进验收](validation/ASSESSMENT_QUALITY_VALIDATION.zh-CN.md) | `ASSESSMENT_QUALITY_VALIDATION.zh-CN.md` |
| [Python LLM 自动适配验收](validation/AUTO_ADAPTATION_VALIDATION.zh-CN.md) | `AUTO_ADAPTATION_VALIDATION.zh-CN.md` |
| [0.2.0 通用 Agent 与自定义评估验证](validation/GENERIC_EVALUATION_VALIDATION.md) | `GENERIC_EVALUATION_VALIDATION.md` |
| [0.3.0 API + Token 大模型评审验证](validation/LLM_REVIEW_VALIDATION.md) | `LLM_REVIEW_VALIDATION.md` |
| [模型网关最小闭环验收](validation/MODEL_GATEWAY_VALIDATION.zh-CN.md) | `MODEL_GATEWAY_VALIDATION.zh-CN.md` |
| [通用模型用量采集第二阶段验收](validation/PROVIDER_TELEMETRY_VALIDATION.zh-CN.md) | `PROVIDER_TELEMETRY_VALIDATION.zh-CN.md` |
| [仓库自动部署评测验收（2026-10-04）](validation/REPOSITORY_ASSESSMENT_VALIDATION.zh-CN.md) | `REPOSITORY_ASSESSMENT_VALIDATION.zh-CN.md` |
| [OpenCode Trace Review Skill 验证](validation/SKILL_VALIDATION.md) | `SKILL_VALIDATION.md` |
| [源码评测规划与质量增强验收（2026-10-05）](validation/SOURCE_GUIDED_VALIDATION.zh-CN.md) | `SOURCE_GUIDED_VALIDATION.zh-CN.md` |
| [自动生成测试文件：验证记录](validation/SUITE_GENERATION_VALIDATION.zh-CN.md) | `SUITE_GENERATION_VALIDATION.zh-CN.md` |
| [模型与工具遥测验收](validation/TARGET_TELEMETRY_VALIDATION.zh-CN.md) | `TARGET_TELEMETRY_VALIDATION.zh-CN.md` |
| [Task 2 验证记录](validation/TASK2_VALIDATION.zh-CN.md) | `TASK2_VALIDATION.zh-CN.md` |
| [Task 3 验证记录](validation/TASK3_VALIDATION.zh-CN.md) | `TASK3_VALIDATION.zh-CN.md` |
| [Token 统计第一阶段验收](validation/TOKEN_ACCOUNTING_VALIDATION.zh-CN.md) | `TOKEN_ACCOUNTING_VALIDATION.zh-CN.md` |
| [验证记录索引](validation/VALIDATION.md) | `VALIDATION.md` |
| [网页真实模型评测入口验收](validation/WEB_MODEL_ASSESSMENT_VALIDATION.zh-CN.md) | `WEB_MODEL_ASSESSMENT_VALIDATION.zh-CN.md` |
| [smolagents 接入验证](validation/SMOLAGENTS_VALIDATION.zh-CN.md) | `examples/smolagents/VALIDATION.zh-CN.md` |

## 规划、交接与历史评审（3 份）

| 文档 | 整理前路径 |
| --- | --- |
| [Agent_Inspect 开发交接](planning/HANDOFF.zh-CN.md) | `HANDOFF.zh-CN.md` |
| [Coding Agent 性能评估系统：调研与实施计划](planning/IMPLEMENTATION_PLAN.zh-CN.md) | `IMPLEMENTATION_PLAN.zh-CN.md` |
| [新增功能审核与仓库自动评测能力核查](reviews/2026-10-04-new-features.md) | `output/reviews/2026-10-04-new-features.md` |

## 旧 README（1 份）

| 文档 | 整理前路径 |
| --- | --- |
| [Agent Trace Review](archive/README.previous.md) | `README.md` |

## 固定路径资源的归档副本（11 份）

这些原件继续保留在对应目录，以满足实际功能的路径要求：

- `skills/opencode-trace-review/SKILL.md` 与 `references/` 必须随 Skill 一起打包，供安装后的 Skill 读取。
- `src/agent_trace_review/templates/*/README.md` 会被 `init-task` 复制到用户创建的任务目录；PDF fixture 的 `correct.md` 是文档转换验收读取的候选样本，资源说明与样本一起保留。
- `examples/smolagents/verified/*.md` 是历史证据，其他记录保留了其文件哈希，因此原件保持字节不变。

下表的归档文件用于集中阅读。修改模板、Skill 或证据说明时，应先维护原件，再同步文档副本；Skill 的安装和打包使用原始目录。

| 文档副本 | 保留的原件 |
| --- | --- |
| [Agent 主动评测：smolagents-generated-2147483647-30](reference-assets/examples/smolagents/verified/generated.offline.md) | [examples/smolagents/verified/generated.offline.md](../examples/smolagents/verified/generated.offline.md) |
| [Agent 主动评测：smolagents-starter](reference-assets/examples/smolagents/verified/offline.md) | [examples/smolagents/verified/offline.md](../examples/smolagents/verified/offline.md) |
| [Agent Trace Review](reference-assets/skills/opencode-trace-review/SKILL.md) | [skills/opencode-trace-review/SKILL.md](../skills/opencode-trace-review/SKILL.md) |
| [Optional workflows](reference-assets/skills/opencode-trace-review/references/advanced.md) | [skills/opencode-trace-review/references/advanced.md](../skills/opencode-trace-review/references/advanced.md) |
| [通用 Agent 与自定义任务评估](reference-assets/skills/opencode-trace-review/references/custom-tasks.md) | [skills/opencode-trace-review/references/custom-tasks.md](../skills/opencode-trace-review/references/custom-tasks.md) |
| [API + Token 大模型任务评审](reference-assets/skills/opencode-trace-review/references/llm-review.md) | [skills/opencode-trace-review/references/llm-review.md](../skills/opencode-trace-review/references/llm-review.md) |
| [固定数字 PDF 转换样例](reference-assets/src/agent_trace_review/fixtures/document_conversion/README.md) | [src/agent_trace_review/fixtures/document_conversion/README.md](../src/agent_trace_review/fixtures/document_conversion/README.md) |
| [Field study: garden water use](reference-assets/src/agent_trace_review/fixtures/document_conversion/correct.md) | [src/agent_trace_review/fixtures/document_conversion/correct.md](../src/agent_trace_review/fixtures/document_conversion/correct.md) |
| [发票提取任务模板](reference-assets/src/agent_trace_review/templates/invoice/README.md) | [src/agent_trace_review/templates/invoice/README.md](../src/agent_trace_review/templates/invoice/README.md) |
| [输入输出的大模型评审示例](reference-assets/src/agent_trace_review/templates/llm-review/README.md) | [src/agent_trace_review/templates/llm-review/README.md](../src/agent_trace_review/templates/llm-review/README.md) |
| [调研任务模板](reference-assets/src/agent_trace_review/templates/research/README.md) | [src/agent_trace_review/templates/research/README.md](../src/agent_trace_review/templates/research/README.md) |

## 运行截图

- [桌面端评测详情](images/assessment-desktop.jpg)：评分、完成度、能力雷达与维度表现。
- [移动端评测详情](images/assessment-mobile.jpg)：能力雷达区域的响应式展示。

截图来自 2026-10-05 本地运行界面中保存的历史仓库评测；整理文档时没有重新调用付费模型。
