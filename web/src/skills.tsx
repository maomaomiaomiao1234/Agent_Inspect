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
          <h1>Skill 评测工具 · opencode-trace-review</h1>
        </div>
      </div>

      {/* 状态统计条 */}
      <div className="assessment-workspace-stats skill-stats-bar">
        <div>
          <Bot size={18} />
          <span>Skill 标识</span>
          <strong className="mono">opencode-trace-review</strong>
          <small>v0.3.0</small>
        </div>
        <div>
          <Cpu size={18} />
          <span>运行时环境</span>
          <strong>Python 3.12 / uv</strong>
        </div>
        <div>
          <Layers size={18} />
          <span>支持数据格式</span>
          <strong>OpenCode / Generic Trace</strong>
        </div>
        <div>
          <ShieldCheck size={18} />
          <span>判定机制</span>
          <strong>确定性独立验收</strong>
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
        </section>

        {/* 3. 自定义任务脚手架 */}
        <section className="standalone-panel skill-card">
          <div className="skill-card-header">
            <span className="skill-step-chip">03</span>
            <h2>业务任务脚手架生成</h2>
          </div>

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
        </section>
      </div>

      {/* 与当前 Web 界面联动快速体验 */}
      {onRunDemo && (
        <section className="standalone-panel skill-quick-demo">
          <div className="section-toolbar">
            <div>
              <h2>快速体验</h2>
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
    </div>
  );
}
