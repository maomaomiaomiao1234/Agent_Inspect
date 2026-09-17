# OpenCode Trace Review

用于评估 OpenCode 编程会话的 Codex skill。导入运行记录后，生成带证据的行为诊断、资源统计和运行比较报告。

## 功能

- 导入 OpenCode 原生 JSON、会话 ID 或可移植的 `.bundle.json` 运行包。
- 检查重复只读操作、重复失败、修改后未观察到验证、明确的路径约束违反，以及失败后的恢复行为。
- 汇总可观测的 token、工具调用与 OpenCode 报告成本，保留缺失数据说明。
- 比较两次运行，先检查任务、环境和验收条件是否一致。
- 输出 Markdown、JSON 报告和可追溯证据；提供本地交互界面。
- 可选 Inspect Scout / LLM Judge，按用户明确要求启用。

任务结果、执行行为与资源使用分别报告。没有独立验收材料时，任务结果保持 `inconclusive`（证据不足）；会话结束或工具退出码为 0 不代表任务通过验收。

## 安装

在 Codex 中发送：

```text
使用 $skill-installer 安装这个 skill：
https://github.com/maomaomiaomiao1234/Agent_Inspect/tree/main/skills/opencode-trace-review
```

安装的是 `skills/opencode-trace-review` 整个目录。请保留其中的 `assets/`，它包含评估引擎和固定版本依赖清单。

运行环境需要 `python3` 和 `uv`。启动器通过 uv 管理 Python 3.12 和依赖；首次运行或缓存缺失时可能需要联网下载。分析已有导出文件不需要安装 OpenCode；按会话 ID 导入需要本机可用的 `opencode` 命令。使用内置界面不需要 Node.js。

## 使用

### 分析会话文件

把 JSON 文件提供给 Codex，或提供其绝对路径，然后发送：

```text
使用 $opencode-trace-review 分析这份 OpenCode 会话，重点检查重复操作、失败重试、修改后的验证情况，以及 token 和成本。生成中文 Markdown 和 JSON 报告，并列出对应证据。
```

### 分析本机会话

将 `ses_xxx` 替换为实际会话 ID：

```text
使用 $opencode-trace-review 分析 OpenCode 会话 ses_xxx，找出最值得改进的三个问题，并列出证据。
```

这会导出并分析已有会话，不会重新运行原来的编程任务。

### 比较两次运行

提供两份完成同一任务的运行记录，然后发送：

```text
使用 $opencode-trace-review 比较这两次运行，从任务结果、执行行为、token 和成本解释差异。先检查比较条件是否一致，再给出结论和证据。
```

如果比较条件缺失或不同，报告仅描述观察到的差异，不据此给模型排名。

## 目录结构

```text
skills/opencode-trace-review/
├── SKILL.md
├── agents/openai.yaml
├── scripts/run_review.py
├── references/advanced.md
└── assets/
    ├── agent_trace_review-0.1.0-py3-none-any.whl
    ├── runtime.json
    ├── requirements.txt
    └── requirements-scout.txt
```

`SKILL.md` 指导 Codex 选择输入、调用引擎并解释结果。`run_review.py` 校验内置 wheel 的 SHA-256，准备运行环境，然后把命令传给 `agent-review`。实际指标和检测规则由 wheel 中的评估引擎执行。

当前内置引擎版本为 **0.1.0**。启动器校验、独立目录导入、报告生成和两次运行比较已经验证；合成样例不构成真实模型性能结论。

## 数据与可选评审

评估引擎默认在本机处理轨迹。Codex 会读取输入与报告来解释结果，因此它仍受你所使用的 Codex 服务与数据设置约束。额外的 LLM Judge 默认关闭；启用前需要配置模型和凭据，并明确授权把可见片段发送给相应提供方。

导入轨迹不会执行其中记录的命令。缺少修改后验证只表示“在当前导出中未观察到”。报告中的成本来自 OpenCode 记录，不等同于实际账单，也不包含额外 Judge 成本。

详细流程见 [SKILL.md](skills/opencode-trace-review/SKILL.md)。Task Manifest、JUnit、交互界面和可选 Judge 的使用见 [高级说明](skills/opencode-trace-review/references/advanced.md)。
