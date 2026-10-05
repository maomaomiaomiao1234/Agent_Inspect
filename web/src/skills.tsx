import { useState } from "react";
import {
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  Code,
  Copy,
  Cpu,
  FileCode2,
  GitCompare,
  Layers,
  Play,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react";

interface SkillToolsProps {
  dataDir?: string;
  onRunDemo?: (scenario: "focused" | "iterative" | "failed") => void;
}

export function SkillTools({ dataDir, onRunDemo }: SkillToolsProps) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const effectiveDir = dataDir || "./.agent-review";

  function copy(text: string, key: string) {
    navigator.clipboard?.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  }

  const commands = {
    help: `python3 "$REVIEW_SKILL/scripts/run_review.py" --help`,
    import: `python3 "$REVIEW_SKILL/scripts/run_review.py" import "/path/to/session.json" --data-dir "${effectiveDir}"`,
    reportMd: `python3 "$REVIEW_SKILL/scripts/run_review.py" report RUN_ID --data-dir "${effectiveDir}" --output "/path/to/report.md"`,
    reportJson: `python3 "$REVIEW_SKILL/scripts/run_review.py" report RUN_ID --data-dir "${effectiveDir}" --format json --output "/path/to/report.json"`,
    compare: `python3 "$REVIEW_SKILL/scripts/run_review.py" compare RUN_A RUN_B --data-dir "${effectiveDir}"`,
    initInvoice: `python3 "$REVIEW_SKILL/scripts/run_review.py" init-task ./my-invoice-task --template invoice`,
    initResearch: `python3 "$REVIEW_SKILL/scripts/run_review.py" init-task ./my-research-task --template research`,
    llmReview: `python3 "$REVIEW_SKILL/scripts/run_review.py" llm-review RUN_ID --profile "/path/to/profile.json" --data-dir "${effectiveDir}"`,
  };

  return (
    <div className="skills-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            <Wrench size={14} /> AGENT EVALUATION SKILL
          </div>
          <h1>Skill 评测工具 · opencode-trace-review</h1>
          <p>
            专为 Agent 与自动化流程设计的可复用评测引擎。可作为独立 Skill
            由其他 Agent、CI 或命令行直接调用，无需启动 Web 界面即可输出带证据的指标与结论。
          </p>
        </div>
      </div>

      {/* 状态统计条 */}
      <div className="assessment-workspace-stats skill-stats-bar">
        <div>
          <Bot size={18} />
          <span>Skill 标识</span>
          <strong className="mono">opencode-trace-review</strong>
          <small>v0.3.0 · 内置引擎已构建</small>
        </div>
        <div>
          <Cpu size={18} />
          <span>运行时环境</span>
          <strong>Python 3.12 / uv</strong>
          <small>自动管理锁定依赖包</small>
        </div>
        <div>
          <Layers size={18} />
          <span>支持数据格式</span>
          <strong>OpenCode / Generic Trace</strong>
          <small>trace_version: 1 · generic/2</small>
        </div>
        <div>
          <ShieldCheck size={18} />
          <span>判定机制</span>
          <strong>确定性独立验收</strong>
          <small>结果、过程与用量三维分离</small>
        </div>
      </div>

      {/* 核心功能卡片网格 */}
      <div className="skill-cards-grid">
        {/* 1. 轨迹导入与生成报告 */}
        <section className="standalone-panel skill-card">
          <div className="skill-card-header">
            <span className="skill-step-chip">01</span>
            <h2>轨迹导入与报告生成</h2>
          </div>
          <p>
            解析 OpenCode 会话导出或通用 Agent 轨迹，结合 Profile
            验收规则，计算工具调用、耗时、Token 与行为检测，生成 Markdown 或结构化 JSON 报告。
          </p>

          <div className="code-box">
            <div className="code-box-header">
              <span>导入原生会话或轨迹文件</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.import, "import")}
                title="复制指令"
              >
                {copiedKey === "import" ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre className="mono">{commands.import}</pre>
          </div>

          <div className="code-box">
            <div className="code-box-header">
              <span>导出供人类阅读的 Markdown 评估报告</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.reportMd, "reportMd")}
                title="复制指令"
              >
                {copiedKey === "reportMd" ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre className="mono">{commands.reportMd}</pre>
          </div>
        </section>

        {/* 2. 成对运行回归比较 */}
        <section className="standalone-panel skill-card">
          <div className="skill-card-header">
            <span className="skill-step-chip">02</span>
            <h2>双运行回归与行为比较</h2>
          </div>
          <p>
            当 Agent 修改了提示词、模型、工具或配置后，针对同一任务对比两次运行，严格检查实验条件可比性，检测重复调用、循环卡死及测试回归。
          </p>

          <div className="code-box">
            <div className="code-box-header">
              <span>比较两个 Run ID 并输出行为差异</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.compare, "compare")}
                title="复制指令"
              >
                {copiedKey === "compare" ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre className="mono">{commands.compare}</pre>
          </div>
          <p className="scenario-note">
            命令将 JSON 输出至标准输出；捕获后可提取两者指标差异（时间、工具调用、Token）与具体行为检测差异。
          </p>
        </section>

        {/* 3. 自定义任务脚手架 */}
        <section className="standalone-panel skill-card">
          <div className="skill-card-header">
            <span className="skill-step-chip">03</span>
            <h2>业务任务脚手架生成</h2>
          </div>
          <p>
            如果你的任务是数据提取（如发票/票据）、深度调研或业务流程，可通过
            <code>init-task</code> 快速生成包含 <code>trace.json</code> 和
            <code>profile.json</code> 的可运行合成模板。
          </p>

          <div className="code-box">
            <div className="code-box-header">
              <span>生成发票提取与结构化校验模板</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.initInvoice, "initInvoice")}
                title="复制指令"
              >
                {copiedKey === "initInvoice" ? (
                  <Check size={14} />
                ) : (
                  <Copy size={14} />
                )}
              </button>
            </div>
            <pre className="mono">{commands.initInvoice}</pre>
          </div>

          <div className="code-box">
            <div className="code-box-header">
              <span>生成深度调研与交付验收模板</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.initResearch, "initResearch")}
                title="复制指令"
              >
                {copiedKey === "initResearch" ? (
                  <Check size={14} />
                ) : (
                  <Copy size={14} />
                )}
              </button>
            </div>
            <pre className="mono">{commands.initResearch}</pre>
          </div>
        </section>

        {/* 4. LLM 辅助评审与大模型接入 */}
        <section className="standalone-panel skill-card">
          <div className="skill-card-header">
            <span className="skill-step-chip">04</span>
            <h2>LLM-as-a-Judge 外部模型评审</h2>
          </div>
          <p>
            对纯输入输出任务，通过标准 API 和 Token 调用外部模型评审
            Profile 中的 <code>external</code> 规则。结论明确标记为大模型判定，保持未通过规则的独立性。
          </p>

          <div className="code-box">
            <div className="code-box-header">
              <span>使用配置的模型评审指定运行</span>
              <button
                className="icon-button"
                onClick={() => copy(commands.llmReview, "llmReview")}
                title="复制指令"
              >
                {copiedKey === "llmReview" ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre className="mono">{commands.llmReview}</pre>
          </div>
          <p className="scenario-note">
            凭据从服务端环境变量或 trusted 配置中读取，不写入报告文件或持久化存储。
          </p>
        </section>
      </div>

      {/* 与当前 Web 界面联动快速体验 */}
      {onRunDemo && (
        <section className="standalone-panel skill-quick-demo">
          <div className="section-toolbar">
            <div>
              <h2>在 Web 界面快速检验 Skill 判定效果</h2>
              <span className="scenario-note">
                点击一键加载示例轨迹，直接在交互轨迹评估中查看 Skill
                引擎生成的各项指标与证据
              </span>
            </div>
          </div>
          <div className="scenario-actions">
            <button
              onClick={() => {
                onRunDemo("focused");
              }}
            >
              <CheckCircle2 size={15} /> 加载通过示例 (focused)
            </button>
            <button
              onClick={() => {
                onRunDemo("iterative");
              }}
            >
              <Terminal size={15} /> 加载待补证据示例 (iterative)
            </button>
            <button
              onClick={() => {
                onRunDemo("failed");
              }}
            >
              <FileCode2 size={15} /> 加载失败示例 (failed)
            </button>
          </div>
        </section>
      )}

      {/* 规范与使用边界 */}
      <section className="standalone-panel assessment-guide">
        <h2>Skill 工具规范与边界</h2>
        <ul>
          <li>
            <strong>确定性优先：</strong>{" "}
            以工具退出码或自报完成不构成独立验收依据；必需检查必须有明确的测试输出、外部报告或参考依据。
          </li>
          <li>
            <strong>Token 与成本准确：</strong>{" "}
            缺失的用量保持未知，已采集下界标注 <code>≥</code>
            ，不凭空插值或累加重试开销。
          </li>
          <li>
            <strong>不执行轨迹中的恶意命令：</strong>{" "}
            Skill 导入轨迹仅作为只读数据分析，绝对不会在宿主机上重新执行轨迹中出现的任何 shell 命令。
          </li>
        </ul>
      </section>
    </div>
  );
}
