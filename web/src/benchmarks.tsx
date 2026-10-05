import { useState } from "react";
import {
  ArrowRight,
  Boxes,
  CheckCircle2,
  Clock,
  Code2,
  FileText,
  FileX,
  FlaskConical,
  History,
  Layers,
  Loader2,
  Terminal,
  XCircle,
} from "lucide-react";
import { api } from "./api-client";
import type { Metric, Run } from "./generated/models";

type Summary = Omit<
  Run,
  "events" | "evidence" | "usage" | "diff" | "verifications"
> & {
  metrics: Metric[];
  outcome: string;
  finding_count: number;
  revision_id: string;
};

interface BenchmarksProps {
  runs: Summary[];
  onRefresh: () => void;
  onError: (msg: string) => void;
}

export function Benchmarks({ runs, onRefresh, onError }: BenchmarksProps) {
  const [busyCandidate, setBusyCandidate] = useState<string | null>(null);
  const [useFixture, setUseFixture] = useState(false);

  async function runCodeRepair(candidate: string) {
    setBusyCandidate(`repair-${candidate}`);
    try {
      const res = await api<{ run_ids: string[]; mode?: string }>(
        `/code-repair-demo?candidate=${candidate}&use_fixture=${useFixture}`,
        { method: "POST" },
      );
      onRefresh();
      if (res.run_ids && res.run_ids.length > 0) {
        location.hash = `#/runs/${res.run_ids[0]}`;
      }
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusyCandidate(null);
    }
  }

  async function runDocument(candidate: string) {
    setBusyCandidate(`doc-${candidate}`);
    try {
      const res = await api<{ run_ids: string[] }>(
        `/document-demo?candidate=${candidate}`,
        { method: "POST" },
      );
      onRefresh();
      if (res.run_ids && res.run_ids.length > 0) {
        location.hash = `#/runs/${res.run_ids[0]}`;
      }
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusyCandidate(null);
    }
  }

  // 筛选出属于基准评测的运行记录
  const benchmarkRuns = runs.filter(
    (r) =>
      r.framework === "code-repair-fixture" ||
      r.title.includes("文档转换") ||
      r.title.includes("代码修复"),
  );

  return (
    <div className="benchmarks-page">
      <div className="page-heading">
        <div>
          <h1>专项基准评估</h1>
        </div>
      </div>

      <div className="benchmarks-grid">
        {/* 代码修复评测 */}
        <section className="standalone-panel benchmark-card">
          <div className="benchmark-card-header">
            <span className="benchmark-badge code">
              <Code2 size={16} /> 代码修复
            </span>
            <h2>代码修复基准 · Docker 隔离测试</h2>
          </div>

          <div className="benchmark-scenarios">
            <h3>选择候选场景一键评测</h3>
            <div className="scenario-actions">
              <button
                disabled={busyCandidate !== null}
                onClick={() => runCodeRepair("correct")}
                className="benchmark-btn pass"
                title="预期通过：边界缺陷已正确修复，通过 3 项测试"
              >
                {busyCandidate === "repair-correct" && (
                  <Loader2 className="spin" size={14} />
                )}
                <CheckCircle2 size={14} />
                正确修复 (correct) · pass
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runCodeRepair("incorrect")}
                className="benchmark-btn fail"
                title="预期未通过：边界缺陷仍然存在，空 diff 或未修好"
              >
                {busyCandidate === "repair-incorrect" && (
                  <Loader2 className="spin" size={14} />
                )}
                <XCircle size={14} />
                缺陷未修复 (incorrect) · fail
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runCodeRepair("regression")}
                className="benchmark-btn fail"
                title="预期未通过：修复了边界却破坏原本正常行为，发现回归"
              >
                {busyCandidate === "repair-regression" && (
                  <Loader2 className="spin" size={14} />
                )}
                <XCircle size={14} />
                引入回归 (regression) · fail
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runCodeRepair("timeout")}
                className="benchmark-btn warn"
                title="预期证据不足：超过 10 秒执行限制，验证未完成"
              >
                {busyCandidate === "repair-timeout" && (
                  <Loader2 className="spin" size={14} />
                )}
                <Clock size={14} />
                执行超时 (timeout) · inconclusive
              </button>
            </div>
          </div>

          <div className="benchmark-options">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={useFixture}
                onChange={(e) => setUseFixture(e.target.checked)}
              />
              <span>优先离线预置包（无需本地运行 Docker daemon）</span>
            </label>
          </div>
        </section>

        {/* PDF 文档转换评测 */}
        <section className="standalone-panel benchmark-card">
          <div className="benchmark-card-header">
            <span className="benchmark-badge doc">
              <FileText size={16} /> 文档转换
            </span>
            <h2>文档转换基准 · PDF 提取与结构化验收</h2>
          </div>

          <div className="benchmark-scenarios">
            <h3>选择候选场景一键评测</h3>
            <div className="scenario-actions">
              <button
                disabled={busyCandidate !== null}
                onClick={() => runDocument("correct")}
                className="benchmark-btn pass"
                title="预期通过：七项必需检查全通过"
              >
                {busyCandidate === "doc-correct" && (
                  <Loader2 className="spin" size={14} />
                )}
                <CheckCircle2 size={14} />
                完整正确 · pass
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runDocument("omitted")}
                className="benchmark-btn fail"
                title="预期未通过：正文内容有遗漏"
              >
                {busyCandidate === "doc-omitted" && (
                  <Loader2 className="spin" size={14} />
                )}
                <FileX size={14} />
                内容遗漏 · fail
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runDocument("table_error")}
                className="benchmark-btn fail"
                title="预期未通过：表格行列单元格数值错误，可准确定位行列"
              >
                {busyCandidate === "doc-table_error" && (
                  <Loader2 className="spin" size={14} />
                )}
                <XCircle size={14} />
                表格错误 · fail
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runDocument("order_error")}
                className="benchmark-btn fail"
                title="预期未通过：阅读顺序或正文排序颠倒"
              >
                {busyCandidate === "doc-order_error" && (
                  <Loader2 className="spin" size={14} />
                )}
                <XCircle size={14} />
                顺序错误 · fail
              </button>

              <button
                disabled={busyCandidate !== null}
                onClick={() => runDocument("missing_reference")}
                className="benchmark-btn warn"
                title="预期证据不足：缺少独立参考快照，无法完成验收"
              >
                {busyCandidate === "doc-missing_reference" && (
                  <Loader2 className="spin" size={14} />
                )}
                <Clock size={14} />
                缺少参考 · inconclusive
              </button>
            </div>
          </div>
        </section>
      </div>

      {/* 历史基准评测运行 */}
      <section className="standalone-panel benchmark-history-section">
        <div className="section-toolbar">
          <div>
            <h2>基准评测历史记录</h2>
            <span className="count">{benchmarkRuns.length}</span>
          </div>
        </div>

        {benchmarkRuns.length === 0 ? (
          <div className="empty-runs-hint">
            <History size={24} />
            <p>暂无基准评估记录</p>
          </div>
        ) : (
          <div className="assessment-table">
            <table>
              <thead>
                <tr>
                  <th>评测任务 / 候选</th>
                  <th>基准类别</th>
                  <th>验收结论</th>
                  <th>运行时长</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {benchmarkRuns.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <strong>{run.title}</strong>
                      <br />
                      <small className="mono">{run.id}</small>
                    </td>
                    <td>
                      {run.framework === "code-repair-fixture" ? (
                        <span className="badge-tag code">代码修复 (Docker)</span>
                      ) : (
                        <span className="badge-tag doc">文档转换 (PDF)</span>
                      )}
                    </td>
                    <td>
                      <span className={`status ${run.outcome}`}>
                        <span />
                        {run.outcome === "pass"
                          ? "验收通过"
                          : run.outcome === "fail"
                            ? "未通过"
                            : "待补证据"}
                      </span>
                    </td>
                    <td>
                      {run.metrics.find((m) => m.key === "duration_ms")?.value !=
                      null
                        ? `${(
                            (run.metrics.find((m) => m.key === "duration_ms")!
                              .value as number) / 1000
                          ).toFixed(2)} 秒`
                        : "—"}
                    </td>
                    <td>
                      <a href={`#/runs/${run.id}`} className="button-link">
                        查看完整报告 <ArrowRight size={13} />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
