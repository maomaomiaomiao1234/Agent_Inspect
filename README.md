# Agent Trace Review

通用 Agent 与 OpenCode 的任务评估 skill，当前引擎版本 **0.2.0**。分析已有轨迹，分别报告任务结果、执行行为和资源使用，并为用户自己的任务提供可扩展验收接口。

Skill 名称保留 **opencode-trace-review**，已有调用方式继续有效。

## 0.2.0 新增能力

- **通用轨迹 JSON**：接入提取、调研、文档处理、浏览器和业务流程等 agent；其他框架通过明确的字段映射导出。
- **Task Profile**：用 JSON 配置字段匹配、必填项、长度、数值阈值、工具调用约束。
- **自定义评估器接口**：通过 JSON 请求/结果文件接入 Python、Node、Go、人工复核或专项服务；引擎不自动执行插件代码。
- **HTTP/Python 接口**：便于嵌入自己的应用或评估流水线。
- **任务模板**：发票提取与调研示例，包含可修改规则和独立评估程序。
- **网页与报告**：查看通用任务最终结果、逐项验收和证据；导出 Markdown/JSON，选择确切评估版本。

OpenCode 原有代码任务检查继续保留：重复只读调用、重复失败、修改后验证、路径约束和测试回归等。

## 安装到 Codex

在 Codex 中发送：

```text
使用 $skill-installer 安装这个 skill：
https://github.com/maomaomiaomiao1234/Agent_Inspect/tree/main/skills/opencode-trace-review
```

## 安装到 OpenCode

首次安装（macOS / Linux）：

```sh
git clone https://github.com/maomaomiaomiao1234/Agent_Inspect.git
mkdir -p "$HOME/.config/opencode/skills"
cp -R Agent_Inspect/skills/opencode-trace-review "$HOME/.config/opencode/skills/"
```

复制整个 skill 目录，保留 assets、scripts 和 references。然后在 OpenCode 会话中要求加载 opencode-trace-review。安装位置见 [OpenCode 官方文档](https://opencode.ai/docs/skills/)。

运行需要 python3 和 uv。uv 管理 Python 3.12 与固定版本依赖；首次运行或缓存缺失可能需要联网。按会话 ID 导入还需要本机 OpenCode 命令。分析导出文件不需要 OpenCode；内置网页不需要 Node.js。

## 在对话中使用

提供轨迹文件或绝对路径后：

```text
使用 opencode-trace-review 分析这份 agent 轨迹，并按我提供的 Profile 验收。分别报告任务结果、行为和成本，生成中文报告并列出证据。
```

从自己的任务开始：

```text
使用 opencode-trace-review 为我的发票提取任务创建评估配置：金额必须与参考发票一致，不允许发送邮件，token 预算为 500。生成可修改的 Profile 和自定义评估器示例，并用合成数据演示。
```

Codex 中也可显式写 `$opencode-trace-review`。同一任务的两次运行可以比较；缺少相同任务、输入、环境、验收与预算条件时，仅做描述性对照。

## 用户如何扩展自己的任务

| 需求 | 扩展方式 |
| --- | --- |
| 使用另一个 agent 框架 | 将日志映射为通用 trace_version: 1 JSON |
| 字段、数量、预算、工具约束 | 修改 Profile JSON，不需要写代码 |
| 业务规则、事实核验、人工复核 | 声明 external 规则，实现 JSON 评估器协议 |
| 接入已有平台 | HTTP API 或 Python 函数接口 |

完整指南：[通用轨迹与自定义评估器](skills/opencode-trace-review/references/custom-tasks.md)。可直接浏览并修改 [发票模板](examples/invoice) 和 [调研模板](examples/research)。

在克隆的仓库根目录运行示例：

```sh
python3 skills/opencode-trace-review/scripts/run_review.py init-task ./my-task --template invoice
python3 skills/opencode-trace-review/scripts/run_review.py import ./my-task/trace.json --profile ./my-task/profile.json --data-dir ./review-data
python3 skills/opencode-trace-review/scripts/run_review.py report RUN_ID --data-dir ./review-data --output ./report.md
```

RUN_ID 替换为导入返回的值。复杂验收依次使用 evaluator-request、自己的评估程序、evaluate --results。每次 evaluate 使用完整 Profile，不自动合并之前版本的规则；需要保留的字段、预算和专项验收放在同一份 Profile 中。

## 如何理解结果

pass 仅代表声明的必需验收条件全部通过。行为或成本检查通过不能单独证明任务完成；文章结构正确不证明事实正确。未知指标和缺少的 external 结果保持 unknown，必要证据不全时整体为 inconclusive。

外部结果绑定当前输入和规则哈希，并必须引用真实证据路径；绑定不能认证评估器作者或证明声明正确。内置 LLM Judge 仍针对代码任务，默认关闭；通用任务通过 external 接口使用自己的专项评估器。

所有示例均为合成数据，不构成真实模型性能排名。评估引擎在本机处理材料；承载 skill 的 agent 服务仍按其自身数据设置处理输入和报告。不要把真实凭据、私有会话或本地评估数据库提交到仓库。

## 文件结构

```text
skills/opencode-trace-review/
├── SKILL.md
├── agents/openai.yaml
├── scripts/run_review.py
├── references/
│   ├── advanced.md
│   └── custom-tasks.md
└── assets/
    ├── agent_trace_review-0.2.0-py3-none-any.whl
    ├── runtime.json
    ├── requirements.txt
    └── requirements-scout.txt
examples/
├── invoice/
└── research/
```

wheel 内包含引擎、网页与任务模板。run_review.py 校验运行包、准备环境并转交 CLI 参数，不依赖原始源码目录。版本与 SHA-256 见 runtime.json。

验证记录见 [VALIDATION.md](VALIDATION.md)；高级代码验收、JUnit 与 Judge 用法见 [advanced.md](skills/opencode-trace-review/references/advanced.md)。
