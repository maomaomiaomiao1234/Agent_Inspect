import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, apiFetch, DownloadLink, setAccessToken } from "./api-client";
import { Assessments } from "./assessments";
import { Benchmarks } from "./benchmarks";
import { SkillTools } from "./skills";
import { ModelCallUsage } from "./usage";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Boxes,
  Braces,
  Check,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  Columns2,
  FileDiff,
  FileJson,
  Files,
  FlaskConical,
  FolderOpen,
  GitBranch,
  LayoutList,
  Loader2,
  MessageSquare,
  Plus,
  Search,
  ShieldCheck,
  Terminal,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import type {
  Evaluation,
  Event,
  Evidence,
  Finding,
  Metric,
  Run,
} from "./generated/models";
import "./style.css";
import "./assessment-style.css";

type Summary = Omit<
  Run,
  "events" | "evidence" | "usage" | "diff" | "verifications"
> & {
  metrics: Metric[];
  outcome: string;
  finding_count: number;
  revision_id: string;
};
type Detail = {
  run: Omit<Run, "events">;
  evaluation: Evaluation;
  event_count: number;
};
type EventPage = { items: Event[]; total: number; next_offset: number | null };
type ProfileCheck = {
  id: string; description: string; dimension: string; required: boolean;
  status: string; explanation: string; evidence_ids: string[]; origin: string;
};
type Comparison = {
  comparable: boolean;
  conclusion: string;
  issues: { field: string; reason: string }[];
  differences: {
    key: string;
    label: string;
    left: number | null;
    right: number | null;
    delta: number | null;
    unit: string;
    left_status: string;
    right_status: string;
  }[];
  behavior_differences: {
    category: string;
    title: string;
    left_count: number;
    right_count: number;
    left_finding_ids: string[];
    right_finding_ids: string[];
  }[];
};

function go(path: string) {
  location.hash = "#" + path;
}
function useRoute() {
  const [route, setRoute] = useState(location.hash.slice(1) || "/runs");
  useEffect(() => {
    const change = () => setRoute(location.hash.slice(1) || "/runs");
    addEventListener("hashchange", change);
    return () => removeEventListener("hashchange", change);
  }, []);
  return route;
}
const metric = (items: Metric[], key: string) =>
  items.find((item) => item.key === key);
const fmt = (value: number | string | null | undefined, unit = "") => {
  if (value == null) return "—";
  if (typeof value !== "number") return value;
  if (unit === "ms") {
    const n = Math.abs(value);
    return (
      (value < 0 ? "−" : "") +
      (n >= 60000
        ? `${Math.floor(n / 60000)}分 ${Math.floor((n % 60000) / 1000)}秒`
        : `${(n / 1000).toFixed(1)}秒`)
    );
  }
  if (unit === "USD")
    return `$${value.toFixed(value < 0.01 && value > 0 ? 4 : 3)}`;
  return value.toLocaleString("zh-CN") + (unit === "%" ? "%" : "");
};
function Status({ value }: { value: string }) {
  return (
    <span className={`status ${value}`}>
      <span />
      {(
        {
          pass: "验收通过",
          fail: "未通过",
          inconclusive: "待补证据",
          completed: "执行结束",
          interrupted: "执行中断",
          unknown: "状态未知",
        } as Record<string, string>
      )[value] || value}
    </span>
  );
}
function MetricCard({ item }: { item?: Metric }) {
  return (
    <div className="metric-card" title={item?.note}>
      <span>
        {item?.label || "—"} <CircleHelp size={12} />
      </span>
      <strong>{item?.status === "not_applicable" ? "不适用" :
        `${item?.status === "partial" && (item.unit === "tokens" || item.unit === "USD") && item.value != null ? "≥" : ""}${fmt(item?.value, item?.unit)}`}</strong>
      <small>
        {item?.status === "not_applicable" ? "离线校准" : item?.status === "partial"
          ? "部分可见"
          : item?.status === "unknown"
            ? "日志未提供"
            : item?.unit === "USD"
              ? "轨迹报告值"
              : item?.unit === "tokens"
                ? "上报用量记录"
                : "来自执行证据"}
      </small>
    </div>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <FolderOpen size={30} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function Loading() {
  return (
    <div className="loading">
      <Loader2 className="spin" size={20} />
      正在读取运行记录
    </div>
  );
}

function App() {
  const route = useRoute();
  const [runs, setRuns] = useState<Summary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [documentBusy, setDocumentBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [health, setHealth] = useState<{ data_dir: string } | null>(null);
  useEffect(() => {
    api<{ data_dir: string }>("/health")
      .then(setHealth)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    setLoading(true);
    api<Summary[]>("/runs")
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [refresh, route]);
  const path = route.split("?")[0];
  const params = new URLSearchParams(route.split("?")[1] || "");
  const runId = path.startsWith("/runs/") ? path.split("/")[2] : null;
  async function demo(scenario?: "focused" | "iterative" | "failed") {
    setDemoBusy(true);
    try {
      const result = await api<{ run_ids: string[] }>(
        scenario ? `/demo?scenario=${scenario}` : "/demo", { method: "POST" },
      );
      setRefresh((x) => x + 1);
      if (scenario) go(`/runs/${result.run_ids[0]}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDemoBusy(false);
    }
  }
  async function documentDemo(candidate: string) {
    setDocumentBusy(true);
    try {
      const result = await api<{ run_ids: string[] }>(`/document-demo?candidate=${candidate}`, { method: "POST" });
      setRefresh((x) => x + 1);
      go(`/runs/${result.run_ids[0]}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDocumentBusy(false);
    }
  }
  const filtered = runs.filter((run) =>
    (run.title + run.models.join(" ") + run.session_id)
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className="app">
      <aside className="sidebar">
        <a href="#/runs" className="brand">
          <span className="brand-symbol">
            <Activity size={22} />
          </span>
          <span>
            Trace Review<small>AGENT TASK ANALYSIS</small>
          </span>
        </a>
        <div className="workspace-label">
          本地工作区 <span className="live-dot" />
        </div>
        <nav>
          <div className="nav-group-label">评估体系</div>
          <a className={path.startsWith("/runs") ? "active" : ""} href="#/runs">
            <LayoutList size={18} />
            交互轨迹评估 <span>{runs.length}</span>
          </a>
          <a className={path === "/benchmarks" ? "active" : ""} href="#/benchmarks">
            <CheckSquare size={18} />
            专项基准评估
          </a>
          <a className={path === "/assessments" ? "active" : ""} href="#/assessments">
            <FlaskConical size={18} />
            主动评测
          </a>

          <div className="nav-group-label">工具与分析</div>
          <a className={path === "/skills" ? "active" : ""} href="#/skills">
            <Wrench size={18} />
            Skill 评测工具
          </a>
          <a className={path === "/compare" ? "active" : ""} href="#/compare">
            <Columns2 size={18} />
            运行比较
          </a>
          <a className={path === "/guide" ? "active" : ""} href="#/guide">
            <Braces size={18} />
            接入指南
          </a>
        </nav>
        <div className="sidebar-note">
          <ShieldCheck size={20} />
          <strong>让结论有据可查</strong>
          <p>结果、过程和资源分别呈现。数据不足时，保留未知。</p>
        </div>
        <div className="sidebar-bottom">
          <span className="opencode-mark">OC</span>
          <span>
            通用 Agent / OpenCode<small>本地分析 · v0.3.0</small>
          </span>
        </div>
      </aside>
      <div className="shell">
        <header className="topbar">
          <div>
            <span>工作区</span>
            <ChevronRight size={14} />
            <b>
              {runId
                ? "运行详情"
                : path === "/compare"
                  ? "运行比较"
                  : path === "/guide"
                    ? "接入指南"
                    : path === "/assessments"
                      ? "主动评测"
                      : path === "/benchmarks"
                        ? "专项基准评估"
                        : path === "/skills"
                          ? "Skill 评测工具"
                        : "交互轨迹评估"}
            </b>
          </div>
          <div className="local-chip">
            <span className="live-dot" />
            数据存储在评审服务
          </div>
        </header>
        <main>
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button aria-label="关闭错误" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {runId ? (
            <RunDetail
              key={runId}
              runId={runId}
              initialFinding={params.get("finding")}
              onError={setError}
            />
          ) : path === "/assessments" ? (
            <Assessments />
          ) : path === "/benchmarks" ? (
            <Benchmarks
              runs={runs}
              onRefresh={() => setRefresh((x) => x + 1)}
              onError={setError}
            />
          ) : path === "/skills" ? (
            <SkillTools
              dataDir={health?.data_dir}
              onRunDemo={(scenario) => demo(scenario)}
            />
          ) : path === "/compare" ? (
            <CompareView
              runs={runs}
              left={params.get("left") || ""}
              right={params.get("right") || ""}
              onError={setError}
            />
          ) : path === "/guide" ? (
            <Guide dataDir={health?.data_dir} />
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">OPENCODE / RUNS · 交互轨迹评估</div>
                  <h1>交互轨迹评估</h1>
                  <p>从工具调用到代码变更，全面解析 Agent 的运行轨迹、资源开销与独立验收依据。</p>
                </div>
                <button className="primary" onClick={() => setImporting(true)}>
                  <Plus size={17} />
                  导入运行
                </button>
              </div>
              <div className="overview">
                <div>
                  <span>已导入运行</span>
                  <strong>
                    {runs.length}
                    <small>次</small>
                  </strong>
                </div>
                <div>
                  <span>独立验收通过</span>
                  <strong>
                    {runs.filter((r) => r.outcome === "pass").length}
                    <small>次</small>
                  </strong>
                </div>
                <div>
                  <span>待补验收证据</span>
                  <strong>
                    {runs.filter((r) => r.outcome === "inconclusive").length}
                    <small>次</small>
                  </strong>
                </div>
                <div className="overview-description">
                  <GitBranch size={22} />
                  <p>
                    相同任务，两条轨迹。
                    <br />
                    <b>比较行为差异，追溯每个结论。</b>
                  </p>
                </div>
              </div>
              <section className="standalone-panel assessment-guide" aria-labelledby="scenario-heading">
                <h2 id="scenario-heading">OpenCode 轨迹评估</h2>
                <p>先用三种证据场景理解验收：过程正常不等于结果正确，证据不足也不等于失败。</p>
                <div className="scenario-actions">
                  <button disabled={demoBusy} onClick={() => demo("focused")}>通过示例 · 聚焦修复</button>
                  <button disabled={demoBusy} onClick={() => demo("iterative")}>待补证据示例 · 反复定位</button>
                  <button disabled={demoBusy} onClick={() => demo("failed")}>失败示例 · 修复失败</button>
                </div>
                <p className="scenario-note" role="status">
                  {demoBusy ? "正在加载示例…" : "日志和验证报告均为合成材料，没有真实运行 Agent 或验证器，不用于能力排名。"}
                </p>
                <a href="#/guide">如何接入真实数据与后续任务 <ArrowRight size={14} /></a>
              </section>
              <section className="standalone-panel assessment-guide" aria-labelledby="document-heading">
                <h2 id="document-heading">PDF 文档转换评估</h2>
                <p>同一份两页 PDF，对照独立标注检查正文、标题、阅读顺序、表格、完整性及 Markdown/JSON 一致性。</p>
                <div className="scenario-actions">
                  {[["correct", "文档示例 · 正确"], ["omitted", "文档示例 · 内容遗漏"], ["table_error", "文档示例 · 表格错误"], ["order_error", "文档示例 · 顺序错误"], ["missing_reference", "文档示例 · 缺少参考"]].map(([candidate, label]) =>
                    <button key={candidate} disabled={documentBusy} onClick={() => documentDemo(candidate)}>{label}</button>
                  )}
                </div>
                <p className="scenario-note" role="status">{documentBusy ? "正在执行文档检查…" : "转换输出由内置模拟器提供，独立检查实际执行。未调用转换 Agent、OCR 或模型；不代表官方基准成绩。"}</p>
              </section>
              <div className="section-toolbar">
                <div>
                  <h2>运行记录</h2>
                  <span className="count">{runs.length}</span>
                </div>
                <div>
                  <label className="search">
                    <Search size={16} />
                    <input
                      aria-label="搜索运行"
                      placeholder="搜索任务或模型…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={selected.length !== 2}
                    onClick={() =>
                      go(`/compare?left=${selected[0]}&right=${selected[1]}`)
                    }
                  >
                    <Columns2 size={16} />
                    比较{selected.length > 0 ? ` (${selected.length}/2)` : ""}
                  </button>
                </div>
              </div>
              <section className="runs-table">
                {loading ? (
                  <Loading />
                ) : filtered.length ? (
                  <table>
                    <thead>
                      <tr>
                        <th className="checkbox-cell" />
                        <th>任务 / 运行</th>
                        <th>验收结果</th>
                        <th>运行跨度</th>
                        <th>调用</th>
                        <th>报告成本</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((run) => (
                        <tr key={run.id}>
                          <td className="checkbox-cell">
                            <input
                              type="checkbox"
                              aria-label={`选择 ${run.title}`}
                              checked={selected.includes(run.id)}
                              disabled={
                                selected.length === 2 &&
                                !selected.includes(run.id)
                              }
                              onChange={(e) =>
                                setSelected(
                                  e.target.checked
                                    ? [...selected, run.id]
                                    : selected.filter((id) => id !== run.id),
                                )
                              }
                            />
                          </td>
                          <td>
                            <a className="run-title" href={`#/runs/${run.id}`}>
                              {run.title}
                              <ArrowRight size={15} />
                            </a>
                            <div className="run-meta">
                              {run.demo && (
                                <span className="demo-tag">示例</span>
                              )}
                              <span>{run.models.join(", ") || "模型未知"}</span>
                              <span className="mono">{run.id.slice(-8)}</span>
                            </div>
                          </td>
                          <td>
                            <Status value={run.outcome} />
                          </td>
                          <td className="mono">
                            {fmt(
                              metric(run.metrics, "duration_ms")?.value,
                              "ms",
                            )}
                          </td>
                          <td className="mono">
                            {fmt(metric(run.metrics, "tool_calls")?.value)}
                          </td>
                          <td className="mono">
                            {fmt(metric(run.metrics, "cost_usd")?.value, "USD")}
                          </td>
                          <td>
                            <button
                              className="icon-button"
                              aria-label={`打开 ${run.title}`}
                              onClick={() => go("/runs/" + run.id)}
                            >
                              <ChevronRight size={17} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : runs.length ? (
                  <Empty
                    title="没有匹配的运行"
                    text="试试其他任务名或模型名。"
                  />
                ) : (
                  <div className="first-run">
                    <div className="first-run-icon">
                      <FileJson size={30} />
                    </div>
                    <h2>从一条真实轨迹开始</h2>
                    <p>
                      导入通用轨迹或 OpenCode JSON，查看调用、用量和任务验收证据。
                      <br />
                      也可以先用两条示例轨迹体验比较流程。
                    </p>
                    <code>
                      opencode export &lt;sessionID&gt; --pure &gt; session.json
                    </code>
                    <div>
                      <button
                        className="primary"
                        onClick={() => setImporting(true)}
                      >
                        <Upload size={16} />
                        选择导出文件
                      </button>
                      <button onClick={() => demo()} disabled={demoBusy}>
                        {demoBusy ? (
                          <Loader2 className="spin" size={16} />
                        ) : (
                          <FlaskConical size={16} />
                        )}
                        加载示例
                      </button>
                    </div>
                  </div>
                )}
              </section>
              {runs.length > 0 && (
                <div className="table-footnote">
                  <span>
                    “待补证据”表示尚不能确认任务结果，不等于运行失败。
                  </span>
                  <button
                    className="text-button"
                    disabled={demoBusy}
                    onClick={() => demo()}
                  >
                    加载示例数据 <ArrowRight size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </main>
      </div>
      {importing && (
        <ImportDialog
          onClose={() => setImporting(false)}
          onDone={(id) => {
            setRefresh((x) => x + 1);
            setImporting(false);
            go("/runs/" + id);
          }}
        />
      )}
    </div>
  );
}

function ImportDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: (id: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState<File | null>(null);
  const [diff, setDiff] = useState<File | null>(null);
  const [junit, setJunit] = useState<File | null>(null);
  const [profile, setProfile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    formRef.current?.querySelector<HTMLElement>("button")?.focus();
    return () => previous?.focus();
  }, []);
  function trapFocus(e: React.KeyboardEvent) {
    if (e.key !== "Tab") return;
    const nodes = Array.from(
      formRef.current?.querySelectorAll<HTMLElement>(
        "button:not(:disabled),input:not(:disabled),a[href]",
      ) || [],
    );
    const first = nodes[0],
      last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      setError("文件超过 100 MB，请先按会话拆分。");
      return;
    }
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    if (task) body.append("task_file", task);
    if (diff) body.append("diff_file", diff);
    if (junit) body.append("junit_file", junit);
    if (profile) body.append("profile_file", profile);
    try {
      const result = await api<{ run_id: string }>("/imports", {
        method: "POST",
        body,
      });
      onDone(result.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, [busy, onClose]);
  return (
    <div className="modal-backdrop">
      <form
        ref={formRef}
        onKeyDown={trapFocus}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        onSubmit={submit}
      >
        <div className="modal-heading">
          <div>
            <div className="eyebrow">IMPORT RUN</div>
            <h2 id="import-title">导入 Agent 运行</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            disabled={busy}
            aria-label="关闭导入"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        <p>支持通用轨迹 JSON、OpenCode session export 和本工具导出的运行包。</p>
        <label
          className={`dropzone ${file ? "has-file" : ""}`}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            setFile(e.dataTransfer.files[0] || null);
          }}
        >
          <FileJson size={32} />
          <strong>{file?.name || "选择文件，或拖放到这里"}</strong>
          <span>
            {file
              ? `${(file.size / 1024).toFixed(1)} KB · 点击更换`
              : "JSON · 最大 100 MB"}
          </span>
          <input
            type="file"
            accept=".json,application/json"
            aria-label="轨迹 JSON 文件"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </label>
        <code className="command">
          opencode export &lt;sessionID&gt; --pure &gt; session.json
        </code>
        <button
          className="advanced-toggle"
          type="button"
          onClick={() => setAdvanced(!advanced)}
        >
          <ChevronDown size={16} className={advanced ? "rotated" : ""} />
          添加任务与验证材料 <span>可选</span>
        </button>
        {advanced && (
          <div className="advanced-files">
            {[
              ["任务评估 Profile (.json)", setProfile, ".json"],
              ["Task Manifest (.json)", setTask, ".json"],
              ["最终 Git diff (.patch)", setDiff, ".patch,.diff,.txt"],
              ["JUnit 报告 (.xml)", setJunit, ".xml"],
            ].map(([title, setter, accept]) => (
              <label key={title as string}>
                {title as string}
                <input
                  type="file"
                  accept={accept as string}
                  onChange={(e) =>
                    (setter as (f: File | null) => void)(
                      e.target.files?.[0] || null,
                    )
                  }
                />
              </label>
            ))}
            <p>
              通用任务使用 Profile 定义结果、行为和成本要求。代码任务可补充 Manifest、diff 和 JUnit。缺少必要证据时，结果保持“待补证据”。
            </p>
          </div>
        )}
        {error && (
          <div className="inline-error" role="alert">
            {error}
          </div>
        )}
        <div className="modal-footer">
          <span>
            <ShieldCheck size={14} />
            本地解析，不执行轨迹命令
          </span>
          <button className="primary" disabled={!file || busy}>
            {busy ? (
              <Loader2 className="spin" size={16} />
            ) : (
              <Upload size={16} />
            )}
            {busy ? "正在分析…" : "导入并分析"}
          </button>
        </div>
      </form>
    </div>
  );
}

function EventIcon({ event }: { event: Event }) {
  if (event.stage.includes("verification")) return <FlaskConical size={15} />;
  if (event.tool === "bash") return <Terminal size={15} />;
  if (event.stage.includes("implementation")) return <FileDiff size={15} />;
  if (event.tool === "read") return <Files size={15} />;
  if (event.tool === "grep" || event.tool === "glob")
    return <Search size={15} />;
  if (event.kind === "message") return <MessageSquare size={15} />;
  return <Activity size={15} />;
}
function DiffView({ value }: { value: string }) {
  const [limit, setLimit] = useState(1000);
  const lines = value.split("\n");
  return (
    <div className="diff-view">
      {lines.slice(0, limit).map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+++") ||
            line.startsWith("---") ||
            line.startsWith("@@")
              ? "diff-header"
              : line.startsWith("+")
                ? "diff-add"
                : line.startsWith("-")
                  ? "diff-remove"
                  : ""
          }
        >
          <span>{i + 1}</span>
          <code>{line || " "}</code>
        </div>
      ))}
      {lines.length > limit && (
        <button onClick={() => setLimit((x) => x + 1000)}>
          继续显示 ({lines.length - limit} 行)
        </button>
      )}
    </div>
  );
}

function DocumentResult({ run }: { run: Detail["run"] }) {
  const output = run.output && typeof run.output === "object" && !Array.isArray(run.output)
    ? run.output as Record<string, unknown> : {};
  return <div className="document-result">
    <div className="document-result-heading">
      <div><h3>转换产物</h3><p>固定数字 PDF 样例；不含 OCR、公式、合并单元格或版式保真验收。</p></div>
      <DownloadLink path={`/runs/${run.id}/document.pdf`}><ArrowDownToLine size={16} />下载源 PDF</DownloadLink>
    </div>
    <h3>Markdown</h3>
    {typeof output.markdown === "string" ? <pre className="output document-markdown">{output.markdown}</pre> : <p>未提供 Markdown 输出。</p>}
    <details><summary>查看结构化 JSON</summary><pre className="output">{JSON.stringify(output.document ?? null, null, 2)}</pre></details>
  </div>;
}

function RunDetail({
  runId,
  initialFinding,
  onError,
}: {
  runId: string;
  initialFinding: string | null;
  onError: (s: string) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [page, setPage] = useState<EventPage | null>(null);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [event, setEvent] = useState<Event | null>(null);
  const [finding, setFinding] = useState<Finding | null>(null);
  const [artifact, setArtifact] = useState<{
    title: string;
    text: string;
  } | null>(null);
  const [tab, setTab] = useState("timeline");
  const [eventTab, setEventTab] = useState("output");
  const [moreBusy, setMoreBusy] = useState(false);
  const [documentEvaluating, setDocumentEvaluating] = useState(false);
  const [revision, setRevision] = useState("");
  const [revisions, setRevisions] = useState<
    { id: string; created_at: string; version: string }[]
  >([]);
  useEffect(() => {
    api<typeof revisions>(`/runs/${runId}/revisions`)
      .then(setRevisions)
      .catch((e) => onError(e.message));
  }, [runId, detail?.evaluation.id]);
  useEffect(() => {
    let active = true;
    api<Detail>(`/runs/${runId}${revision ? `?revision=${revision}` : ""}`)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => onError(e.message));
    return () => {
      active = false;
    };
  }, [runId, revision]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<EventPage>(
        `/runs/${runId}/events?kind=${filter}&search=${encodeURIComponent(search)}`,
        { signal: controller.signal },
      )
        .then((p) => {
          setPage(p);
          setEvent(
            (current) =>
              current ||
              p.items.find((e) => e.kind === "tool") ||
              p.items[0] ||
              null,
          );
        })
        .catch((e) => {
          if (e.name !== "AbortError") onError(e.message);
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [runId, filter, search]);
  useEffect(() => {
    if (detail && initialFinding) {
      const f = detail.evaluation.findings.find((f) => f.id === initialFinding);
      if (f) chooseFinding(f);
    }
  }, [detail, initialFinding]);
  async function showEvidence(ref: Evidence) {
    setTab("timeline");
    setArtifact(null);
    try {
      if (ref.event_id) {
        const e = await api<Event>(`/runs/${runId}/events/${ref.event_id}`);
        setEvent(e);
        setEventTab(e.data.patch ? "diff" : "output");
      } else if (ref.kind === "absence") {
        setArtifact({
          title: ref.description,
          text: JSON.stringify(ref.query, null, 2),
        });
      } else if (ref.artifact_id) {
        const res = await apiFetch("/artifacts/" + ref.artifact_id);
        if (!res.ok) throw new Error("证据文件读取失败");
        let text = await res.text();
        if (ref.pointer) {
          try {
            let val = JSON.parse(text);
            for (const key of ref.pointer.split("/").slice(1))
              val = val[key.replace(/~1/g, "/").replace(/~0/g, "~")];
            text = JSON.stringify(val, null, 2);
          } catch {
            /* raw text */
          }
        }
        setArtifact({ title: ref.description, text });
      }
    } catch (e) {
      onError((e as Error).message);
    }
  }
  function chooseFinding(f: Finding) {
    setFinding(f);
    const ref = detail?.evaluation.evidence.find(
      (e) => e.id === f.evidence_ids[0],
    );
    if (ref) void showEvidence(ref);
  }
  async function more() {
    if (page?.next_offset == null) return;
    setMoreBusy(true);
    try {
      const p = await api<EventPage>(
        `/runs/${runId}/events?offset=${page.next_offset}&kind=${filter}&search=${encodeURIComponent(search)}`,
      );
      setPage({ ...p, items: [...page.items, ...p.items] });
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setMoreBusy(false);
    }
  }
  if (!detail) return <Loading />;
  const { run, evaluation } = detail;
  const profileChecks = (evaluation.custom?.checks as ProfileCheck[] | undefined) || [];
  const timing = (e: Event) =>
    e.start_ms != null && run.start_ms != null
      ? `${Math.floor((e.start_ms - run.start_ms) / 60000)
          .toString()
          .padStart(2, "0")}:${Math.floor(
          ((e.start_ms - run.start_ms) % 60000) / 1000,
        )
          .toString()
          .padStart(2, "0")}`
      : "—";
  return (
    <>
      <a className="back-link" href="#/runs">
        <ArrowLeft size={14} />
        全部运行
      </a>
      <div className="page-heading detail-heading">
        <div>
          <div className="eyebrow">
            RUN / {runId.slice(-8)}{" "}
            {run.demo && <span className="demo-tag">示例，非 Agent 能力评测</span>}
          </div>
          <h1>{run.title}</h1>
          <p>
            {run.models.join(" · ") || "模型未知"}{" "}
            <span className="separator">/</span> {detail.run.framework}{" "}
            {run.agent_version || "版本未知"}{" "}
            <span className="separator">/</span>{" "}
            {run.execution_status === "completed"
              ? "执行已结束"
              : run.execution_status}
          </p>
        </div>
        <DownloadLink
          className="button"
          path={`/runs/${runId}/export?format=markdown&revision=${evaluation.id}`}
        >
          <ArrowDownToLine size={16} />
          导出报告
        </DownloadLink>
      </div>
      <div className={`outcome-bar ${evaluation.outcome}`}>
        <Status value={evaluation.outcome} />
        <p>{evaluation.outcome_reason}</p>
      </div>
      <section className="standalone-panel assessment-guide" aria-labelledby="assessment-heading">
        <h2 id="assessment-heading">如何理解这次评估</h2>
        <ul>
          <li><strong>结果：</strong>依据任务规则和匹配的验收材料；命令退出码 0 或 Agent 自报成功不代表任务完成。</li>
          <li><strong>过程：</strong>诊断仅针对可见记录；未触发规则不代表没有问题，也不是能力总分。</li>
          <li><strong>资源：</strong>区分观测、推导、部分和未知；缺失不填零，详见“指标与范围”。</li>
          <li><strong>范围：</strong>单次运行不代表总体能力；待补证据不等于失败。</li>
        </ul>
        {run.demo && <p>示例不代表真实 Agent 能力；具体验证材料来源见记录。</p>}
        {typeof run.artifacts.source_description === "string" && <p>材料来源声明：{run.artifacts.source_description}</p>}
        {run.framework === "code-repair-fixture" && <p>耗时覆盖模拟实验及容器验证；未调用模型，Token 和费用保持未知。</p>}
      </section>
      <div className="metrics-grid">
        {["duration_ms", "tool_calls", "tokens_total", "cost_usd"].map(
          (key) => (
            <MetricCard key={key} item={metric(evaluation.metrics, key)} />
          ),
        )}
      </div>
      <div className="detail-tabs">
        {[
          ["timeline", "执行轨迹", Activity],
          ["diff", run.source_format === "generic" ? "最终结果" : "最终变更", FileDiff],
          ["verification", run.source_format === "generic" ? "任务验收" : "验证记录", FlaskConical],
          ["metrics", "指标与范围", ShieldCheck],
          ["judge", "LLM 评审", MessageSquare],
        ].map(([key, label, Icon]) => (
          <button
            key={key as string}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key as string)}
          >
            {React.createElement(Icon as typeof Activity, { size: 16 })}
            {label as string}
          </button>
        ))}
        <span>{detail.event_count} 个可见事件</span>
      </div>
      {tab === "timeline" && (
        <div className="trace-layout">
          <section className="timeline">
            <div className="panel-heading">
              <h2>时间线</h2>
              <span>相对时间</span>
            </div>
            <label className="timeline-search">
              <Search size={14} />
              <input
                aria-label="搜索事件"
                placeholder="搜索命令、输出…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <div className="filter-chips">
              {[
                ["", "全部"],
                ["tool", "工具"],
                ["verification", "验证"],
                ["implementation", "修改"],
              ].map(([v, t]) => (
                <button
                  className={filter === v ? "active" : ""}
                  onClick={() => setFilter(v)}
                  key={t}
                >
                  {t}
                </button>
              ))}
            </div>
            <div className="event-list">
              {!page ? (
                <Loading />
              ) : page.items.length ? (
                page.items.map((e) => (
                  <button
                    key={e.id}
                    className={`event-row ${e.id === event?.id && !artifact ? "selected" : ""}`}
                    onClick={() => {
                      setEvent(e);
                      setArtifact(null);
                      setEventTab(e.data.patch ? "diff" : "output");
                    }}
                  >
                    <time>{timing(e)}</time>
                    <span
                      className={`event-glyph ${e.stage[0] || ""} ${e.status === "error" || (e.data.exit_code && e.data.exit_code !== 0) ? "error" : ""}`}
                    >
                      <EventIcon event={e} />
                    </span>
                    <span className="event-description">
                      <b>{e.tool || e.title}</b>
                      <small>
                        {e.kind === "tool"
                          ? e.title
                          : e.output.slice(0, 70) || e.title}
                      </small>
                    </span>
                    {e.data.exit_code === 0 && (
                      <Check size={13} className="success-text" />
                    )}
                  </button>
                ))
              ) : (
                <Empty title="没有匹配事件" text="更换搜索词或事件类型。" />
              )}
              {page?.next_offset != null && (
                <button
                  className="load-more"
                  disabled={moreBusy}
                  onClick={more}
                >
                  {moreBusy
                    ? "加载中…"
                    : `继续加载 (${page.items.length}/${page.total})`}
                </button>
              )}
            </div>
          </section>
          <section className="event-detail">
            <div className="panel-heading">
              <h2>{artifact ? "证据详情" : "调用详情"}</h2>
              {event && !artifact && (
                <span className="mono">{event.native_id.slice(-10)}</span>
              )}
            </div>
            {artifact ? (
              <>
                <h3 className="artifact-title">{artifact.title}</h3>
                <pre className="output">{artifact.text}</pre>
              </>
            ) : event ? (
              <>
                <div className="event-header">
                  <div className="event-title">
                    <EventIcon event={event} />
                    <h3>{event.tool || event.title}</h3>
                    <span className={`tiny-status ${event.status}`}>
                      {event.status}
                    </span>
                  </div>
                  <p>{event.title}</p>
                  <div className="event-facts">
                    <span>
                      <Clock3 size={12} />
                      {typeof event.data.duration_ms === "number"
                        ? fmt(event.data.duration_ms, "ms")
                        : event.time_basis === "native" &&
                      event.start_ms != null &&
                      event.end_ms != null
                        ? fmt(event.end_ms - event.start_ms, "ms")
                        : "精确耗时未知"}
                    </span>
                    {event.data.provenance === "target_reported" && <span>目标自报</span>}
                    {typeof event.data.model === "string" && <span>模型 {event.data.model}</span>}
                    {event.kind === "llm" && <ModelCallUsage data={event.data} />}
                    {event.data.exit_code != null && (
                      <span>退出码 {String(event.data.exit_code)}</span>
                    )}
                    {Boolean(event.data.truncated) && (
                      <span className="warning-text">输出截断 / 脱敏</span>
                    )}
                  </div>
                </div>
                <div className="event-tabs">
                  {[
                    ["output", "输出"],
                    ["input", "输入"],
                    ...(event.data.patch ? [["diff", "修改片段"]] : []),
                    ["source", "来源"],
                  ].map(([key, title]) => (
                    <button
                      className={eventTab === key ? "active" : ""}
                      onClick={() => setEventTab(key)}
                      key={key}
                    >
                      {title}
                    </button>
                  ))}
                </div>
                {eventTab === "diff" ? (
                  <DiffView value={String(event.data.patch || "")} />
                ) : (
                  <pre className="output">
                    {eventTab === "input"
                      ? JSON.stringify(event.input, null, 2)
                      : eventTab === "source"
                        ? JSON.stringify(
                            {
                              source_pointer: event.source_pointer,
                              message_id: event.message_id,
                              parent_message_id: event.parent_message_id,
                              call_id: event.call_id,
                              time_basis: event.time_basis,
                              data: event.data,
                            },
                            null,
                            2,
                          )
                        : event.output || "此事件没有可见输出。"}
                  </pre>
                )}
              </>
            ) : (
              <Empty
                title="选择一条事件"
                text="查看调用输入、输出与关联证据。"
              />
            )}
          </section>
          <aside className="diagnosis-panel">
            <div className="panel-heading">
              <h2>轨迹诊断</h2>
              <span className="count">{evaluation.findings.length}</span>
            </div>
            <p className="panel-description">
              点击结论，检查它所依据的执行记录。
            </p>
            {evaluation.findings.length ? (
              evaluation.findings.map((f) => (
                <button
                  key={f.id}
                  className={`finding-card ${f.severity} ${finding?.id === f.id ? "selected" : ""}`}
                  onClick={() => chooseFinding(f)}
                >
                  <span className="finding-label">
                    {f.origin === "llm_judge" ? "模型判断" : f.origin === "custom" ? "任务检查" : f.category === "verified_recovery"
                      ? "恢复证据"
                      : f.verdict === "hypothesis"
                        ? "待确认"
                        : f.severity === "info"
                          ? "行为观察"
                          : "需要关注"}
                  </span>
                  <strong>{f.title}</strong>
                  <p>{f.explanation}</p>
                  <span className="evidence-count">
                    {f.evidence_ids.length} 条证据 <ArrowRight size={13} />
                  </span>
                </button>
              ))
            ) : (
              <div className="no-findings">
                <ShieldCheck size={25} />
                <strong>未触发当前规则</strong>
                <p>这不等于没有问题，仍需结合验收与观察范围判断。</p>
              </div>
            )}
            {finding && (
              <div className="finding-evidence">
                <h3>证据链</h3>
                {finding.evidence_ids.map((id, i) => {
                  const ref = evaluation.evidence.find((e) => e.id === id);
                  return (
                    ref && (
                      <button key={id} onClick={() => showEvidence(ref)}>
                        <span>{i + 1}</span>
                        {ref.description}
                        <ChevronRight size={12} />
                      </button>
                    )
                  );
                })}
                {finding.counter_evidence_ids.length > 0 && (
                  <details>
                    <summary>反证 / 其他检查</summary>
                    {finding.counter_evidence_ids.map((id) => {
                      const ref = evaluation.evidence.find((e) => e.id === id);
                      return (
                        ref && (
                          <button key={id} onClick={() => showEvidence(ref)}>
                            {ref.description}
                          </button>
                        )
                      );
                    })}
                  </details>
                )}
                <p>{finding.limitations.join(" ")}</p>
                {finding.recommendation && (
                  <div className="recommendation">{finding.recommendation}</div>
                )}
              </div>
            )}
          </aside>
        </div>
      )}
      {tab === "diff" && (
        <section className="standalone-panel">
          <div className="panel-heading">
            <h2>{run.source_format === "generic" ? "最终任务结果" : "最终 Git diff"}</h2>
            <span>导入的最终工件</span>
          </div>
          {run.source_format === "generic" ? (
            <>
              {run.framework === "document-conversion-fixture" ? <DocumentResult run={run} /> : run.output_present ? <pre className="output">{JSON.stringify(run.output, null, 2)}</pre>
                : <Empty title="尚未提供最终结果" text="在通用轨迹的 output 字段提供最终产物。" />}
              {run.diff_provided && <>
                <h3>最终代码 diff</h3>
                {run.diff ? <DiffView value={run.diff} /> : <p>最终代码与基线相同（已提供空 diff）。</p>}
              </>}
            </>
          ) : run.diff ? (
            <DiffView value={run.diff} />
          ) : (
            <Empty
              title="尚未提供最终 diff"
              text="原生工具记录只说明发生过的修改。导入时附加 Git diff，才能查看最终变更。"
            />
          )}
        </section>
      )}
      {tab === "verification" && (
        <section className="standalone-panel">
          <div className="panel-heading">
            <h2>检查与验收记录</h2>
            <span>{evaluation.custom ? "按用户定义的任务规则逐项验收" : "外部报告按代码状态和测试集合匹配"}</span>
          </div>
          {run.framework === "document-conversion-fixture" && <div className="assessment-guide">
            <p>通过仅覆盖本例的七项必需检查。OCR 与公式保持 unknown，不计入本例验收；这些结果不代表真实转换 Agent 能力。</p>
            <button disabled={documentEvaluating} onClick={async () => {
              setDocumentEvaluating(true);
              try {
                const result = await api<Evaluation>(`/runs/${runId}/document-evaluation`, { method: "POST" });
                setDetail({ ...detail, evaluation: result });
              } catch (e) { onError((e as Error).message); }
              finally { setDocumentEvaluating(false); }
            }}>{documentEvaluating ? "正在检查…" : "运行固定文档检查"}</button>
          </div>}
          {profileChecks.length > 0 && <div className="verification-list">
            {profileChecks.map((check) => <article key={check.id}>
              <div><strong>{check.description || check.id}</strong><span className="tiny-status">{check.status}</span></div>
              <p>{({ outcome: "任务结果", behavior: "执行行为", resource: "资源使用" } as Record<string, string>)[check.dimension]} · {check.required ? "必需" : "可选"} · {check.origin === "llm_judge" ? "大模型评审" : check.origin === "external" ? "用户评估器" : "规则检查"}</p>
              <p>{check.explanation}</p>
              {check.evidence_ids.map((id) => <button key={id} className="text-button" onClick={() => {
                const ref = evaluation.evidence.find((e) => e.id === id);
                if (ref) showEvidence(ref);
              }}>查看验收证据 <ArrowRight size={13} /></button>)}
            </article>)}
          </div>}
          {evaluation.verifications.length ? (
            <div className="verification-list">
              {evaluation.verifications.map((v, i) => (
                <article key={i}>
                  <div>
                    <strong>{v.check_id || v.command || v.id}</strong>
                    <span className="tiny-status">{v.result}</span>
                  </div>
                  <p>
                    {v.provenance === "external_verifier"
                      ? "外部验证报告"
                      : "Agent 工具检查"}{" "}
                    · {v.kind} · {v.phase}
                  </p>
                  <p>
                    执行 {v.executed ?? "未知"} · 通过 {v.passed ?? "未知"} ·
                    失败 {v.failed ?? "未知"} · 跳过 {v.skipped ?? "未知"}
                  </p>
                  {v.state_hash && (
                    <p className="mono">
                      状态 {v.state_hash} / 测试集 {v.suite_hash || "未知"}
                    </p>
                  )}
                  {v.evidence_ids.map((id) => (
                    <button
                      key={id}
                      className="text-button"
                      onClick={() => {
                        const ref = evaluation.evidence.find(
                          (e) => e.id === id,
                        );
                        if (ref) showEvidence(ref);
                      }}
                    >
                      {id.endsWith("_execution") ? "查看执行记录" : "查看报告证据"} <ArrowRight size={13} />
                    </button>
                  ))}
                </article>
              ))}
            </div>
          ) : profileChecks.length ? null : (
            <Empty
              title="尚无可识别的检查记录"
              text="添加任务 Profile 或独立验收报告，定义本任务需要满足的条件。"
            />
          )}
        </section>
      )}
      {tab === "judge" && run.source_format === "generic" && (
        <LLMReviewPanel key={runId} runId={runId} evaluation={evaluation}
          onResult={(value) => setDetail({ ...detail, evaluation: value })} onError={onError} />
      )}
      {tab === "judge" && run.source_format === "opencode" && (
        <JudgePanel
          runId={runId}
          evaluation={evaluation}
          onResult={(value) => setDetail({ ...detail, evaluation: value })}
          onEvidence={() => {
            const ref = evaluation.evidence.find((e) => e.id === "judge-input");
            if (ref) showEvidence(ref);
          }}
          onError={onError}
        />
      )}
      {tab === "metrics" && (
        <div className="metadata-layout">
          <section className="standalone-panel">
            <div className="panel-heading">
              <h2>全部指标</h2>
              <span>不合并为总分</span>
            </div>
            <table className="metrics-table">
              <tbody>
                {evaluation.metrics.map((m) => (
                  <tr key={m.key}>
                    <td>
                      <strong>{m.label}</strong>
                      <small>{m.note}</small>
                    </td>
                    <td className="mono">{fmt(m.value, m.unit)}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => {
                          const ref = evaluation.evidence.find(
                            (e) => e.id === m.evidence_ids[0],
                          );
                          if (ref) showEvidence(ref);
                        }}
                      >
                        {m.status} <ArrowRight size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="standalone-panel coverage-panel">
            <div className="panel-heading">
              <h2>观察范围</h2>
            </div>
            {Object.entries(run.coverage).map(([key, val]) => (
              <div className="coverage-item" key={key}>
                <b>{key}</b>
                <span>
                  {
                    (
                      {
                        observed: "已观察",
                        partial: "部分可见",
                        unavailable: "不可用",
                        not_applicable: "不适用（离线校准）",
                        unknown: "未知",
                      } as Record<string, string>
                    )[val.level]
                  }
                </span>
                <p>{val.reason}</p>
              </div>
            ))}
            <label className="revision-select">
              评估版本
              <select
                aria-label="评估版本"
                value={revision || evaluation.id}
                onChange={(e) => setRevision(e.target.value)}
              >
                {revisions.map((r) => (
                  <option value={r.id} key={r.id}>
                    {(r.id.startsWith("judge_") || r.id.startsWith("llm_")) ? "LLM + 规则" : "确定性规则"} ·{" "}
                    {r.version} · {r.id.slice(-6)}
                  </option>
                ))}
              </select>
            </label>
            <details>
              <summary>导入说明与任务</summary>
              {run.warnings.map((w, i) => (
                <p key={i}>{w}</p>
              ))}
              <pre>{JSON.stringify(run.task, null, 2)}</pre>
            </details>
            <DownloadLink
              className="button"
              path={`/runs/${runId}/export?format=bundle`}
            >
              <ArrowDownToLine size={14} />
              导出可移植运行包
            </DownloadLink>
          </section>
        </div>
      )}
    </>
  );
}

function CompareView({
  runs,
  left,
  right,
  onError,
}: {
  runs: Summary[];
  left: string;
  right: string;
  onError: (s: string) => void;
}) {
  const [a, setA] = useState(left),
    [b, setB] = useState(right);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setA(left);
    setB(right);
  }, [left, right]);
  useEffect(() => {
    setComparison(null);
    if (!a || !b || a === b) return;
    const controller = new AbortController();
    setBusy(true);
    api<Comparison>("/comparisons", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ left_id: a, right_id: b }),
      signal: controller.signal,
    })
      .then(setComparison)
      .catch((e) => {
        if (e.name !== "AbortError") onError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [a, b]);
  const l = runs.find((r) => r.id === a),
    r = runs.find((r) => r.id === b);
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">COMPARE / TWO RUNS</div>
          <h1>结果之外，比较过程。</h1>
          <p>固定任务与验收条件，查看两次运行的实际差异。</p>
        </div>
      </div>
      <div className="compare-selectors">
        {[
          ["A", a, setA],
          ["B", b, setB],
        ].map(([label, id, setter]) => (
          <label key={label as string}>
            <span className="compare-letter">{label as string}</span>
            <div>
              <span>选择运行 {label as string}</span>
              <select
                aria-label={`运行 ${label}`}
                value={id as string}
                onChange={(e) =>
                  (setter as (v: string) => void)(e.target.value)
                }
              >
                <option value="">选择一个运行…</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.title}
                  </option>
                ))}
              </select>
            </div>
          </label>
        ))}
      </div>
      {a === b && a && <div className="notice">请选择两条不同的运行。</div>}
      {busy && <Loading />}
      {comparison && l && r ? (
        <>
          <div
            className={`comparison-notice ${comparison.comparable ? "matched" : ""}`}
          >
            <ShieldCheck size={20} />
            <div>
              <strong>
                {comparison.comparable
                  ? "任务与实验条件匹配"
                  : "仅作描述性比较"}
              </strong>
              <p>{comparison.conclusion}</p>
              {comparison.issues.length > 0 && (
                <details>
                  <summary>查看 {comparison.issues.length} 项限制</summary>
                  {comparison.issues.map((issue, i) => (
                    <p key={i}>
                      {issue.field}：{issue.reason}
                    </p>
                  ))}
                </details>
              )}
            </div>
          </div>
          <section className="standalone-panel">
            <table className="compare-table">
              <thead>
                <tr>
                  <th>评估维度</th>
                  <th>
                    <span className="compare-letter small">A</span>
                    {l.title}
                  </th>
                  <th>
                    <span className="compare-letter small">B</span>
                    {r.title}
                  </th>
                  <th>B − A</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>独立验收</td>
                  <td>
                    <Status value={l.outcome} />
                  </td>
                  <td>
                    <Status value={r.outcome} />
                  </td>
                  <td>—</td>
                </tr>
                {[
                  "duration_ms",
                  "tool_calls",
                  "tokens_total",
                  "cost_usd",
                  "files_read",
                  "files_changed",
                  "duplicate_calls",
                  "observed_checks",
                  "observed_checks_passed",
                ].map((key) => {
                  const d = comparison.differences.find((d) => d.key === key);
                  return (
                    d && (
                      <tr key={key}>
                        <td>{d.label}</td>
                        <td className="mono">
                          {fmt(d.left, d.unit)}
                          {d.left_status === "partial" && <small>部分</small>}
                        </td>
                        <td className="mono">
                          {fmt(d.right, d.unit)}
                          {d.right_status === "partial" && <small>部分</small>}
                        </td>
                        <td className="mono delta">
                          {d.delta == null
                            ? "—"
                            : (d.delta > 0 ? "+" : "") + fmt(d.delta, d.unit)}
                        </td>
                      </tr>
                    )
                  );
                })}
              </tbody>
            </table>
          </section>
          <div className="section-toolbar">
            <h2>值得查看的行为差异</h2>
            <span>由可引用的诊断生成</span>
          </div>
          <div className="behavior-grid">
            {comparison.behavior_differences.map((d) => (
              <article className="behavior-card" key={d.category}>
                <span className="eyebrow">
                  {d.category.replaceAll("_", " ")}
                </span>
                <h3>{d.title}</h3>
                <div>
                  <span>
                    A <b>{d.left_count}</b>
                  </span>
                  <ArrowRight size={18} />
                  <span>
                    B <b>{d.right_count}</b>
                  </span>
                </div>
                <footer>
                  {d.left_finding_ids.length > 0 && (
                    <a href={`#/runs/${a}?finding=${d.left_finding_ids[0]}`}>
                      查看 A 证据 <ArrowRight size={13} />
                    </a>
                  )}
                  {d.right_finding_ids.length > 0 && (
                    <a href={`#/runs/${b}?finding=${d.right_finding_ids[0]}`}>
                      查看 B 证据 <ArrowRight size={13} />
                    </a>
                  )}
                </footer>
              </article>
            ))}
          </div>
          {!comparison.behavior_differences.length && (
            <Empty
              title="当前规则未发现数量差异"
              text="可以进入运行详情，继续比较具体代码与验证证据。"
            />
          )}
        </>
      ) : (
        !busy && (
          <Empty
            title="选择两条运行开始比较"
            text="同一任务、相同环境与验收条件下的运行更适合比较。"
          />
        )
      )}
    </>
  );
}

function JudgePanel({
  runId,
  evaluation,
  onResult,
  onEvidence,
  onError,
}: {
  runId: string;
  evaluation: Evaluation;
  onResult: (value: Evaluation) => void;
  onEvidence: () => void;
  onError: (message: string) => void;
}) {
  const [config, setConfig] = useState<{
    enabled: boolean;
    model: string | null;
    provider_url: string | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<NonNullable<typeof config>>("/judge/config")
      .then(setConfig)
      .catch((e) => onError(e.message));
  }, []);
  async function start() {
    setBusy(true);
    try {
      onResult(
        await api<Evaluation>(`/runs/${runId}/judge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ send_trace: true }),
        }),
      );
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="standalone-panel guide-card">
      <h2>可选的轨迹评审</h2>
      <p>
        补充意图、改动范围和恢复过程的解释。每项判断需要证据，任务验收结果由确定性规则保留。
      </p>
      {config?.enabled ? (
        <>
          <p>
            模型：<strong>{config.model}</strong> ·{" "}
            {config.provider_url || "模型提供商默认 API"}
          </p>
          <p>
            点击后会向此模型发送任务、最终 diff 和可见事件摘录，最多约 4
            万字符；输出上限 1,600 tokens，可能产生 API
            费用。隐藏推理不发送。相同输入与配置复用已保存结果。
          </p>
          <button className="primary" disabled={busy} onClick={start}>
            {busy ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <MessageSquare size={16} />
            )}
            {busy ? "正在评审…" : "发送摘录并评审"}
          </button>
        </>
      ) : (
        <>
          <p>LLM 评审尚未启用。可在本机配置模型后重启服务：</p>
          <pre>
            uv sync --extra scout{"\n"}export
            AGENT_REVIEW_JUDGE_MODEL=provider/model{"\n"}uv run agent-review
            serve
          </pre>
          <p>
            使用对应 provider
            的环境变量配置凭据；配置前，导入和规则分析仍可直接使用。
          </p>
        </>
      )}
      {evaluation.judge && (
        <div className="judge-result">
          <h3>已保存的评审</h3>
          <button className="text-button" onClick={onEvidence}>
            查看实际输入与引用 <ArrowRight size={14} />
          </button>
          <pre>{JSON.stringify(evaluation.judge, null, 2)}</pre>
        </div>
      )}
    </section>
  );
}

type ReviewRule = { id: string; op: string; description?: string; [key: string]: unknown };
type ReviewProfile = { profile_version: string; id: string; rules: ReviewRule[]; [key: string]: unknown };
const defaultCriterion = "根据任务要求和提供的参考材料评审答案：完整回应要求且与材料一致为通过；明确矛盾或遗漏必要内容为失败；事实无法从材料核实则为未知。引用输入、输出或参考材料作为证据。";

function LLMReviewPanel({ runId, evaluation, onResult, onError }: {
  runId: string; evaluation: Evaluation;
  onResult: (value: Evaluation) => void; onError: (message: string) => void;
}) {
  const [apiUrl, setApiUrl] = useState("");
  const [model, setModel] = useState("");
  const [token, setToken] = useState("");
  const [configured, setConfigured] = useState(false);
  const [parameter, setParameter] = useState("max_tokens");
  const [jsonMode, setJsonMode] = useState(true);
  const [busy, setBusy] = useState(false);
  const [profile, setProfile] = useState<ReviewProfile>(() => {
    const existing = evaluation.custom?.profile as ReviewProfile | undefined;
    const base = existing || { profile_version: "1", id: "answer-review", rules: [
      { id: "output_exists", op: "exists", path: "/output", description: "提供了最终答案" },
    ] };
    if (base.rules.some((rule) => rule.op === "external")) return base;
    let id = "llm_quality";
    while (base.rules.some((rule) => rule.id === id)) id += "_new";
    return { ...base, rules: [...base.rules, { id, op: "external", description: defaultCriterion }] };
  });
  useEffect(() => {
    api<{ enabled: boolean; api_url?: string; model?: string; token_parameter?: string }>("/llm-review/config")
      .then((value) => { setConfigured(value.enabled); setApiUrl((current) => current || value.api_url || "");
        setModel((current) => current || value.model || ""); setParameter(value.token_parameter || "max_tokens"); })
      .catch((error) => onError(error.message));
  }, []);
  async function start() {
    setBusy(true);
    try {
      const result = await api<Evaluation>(`/runs/${runId}/llm-review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ send_data: true, profile, api_url: apiUrl, model,
          ...(token ? { token } : {}), token_parameter: parameter, json_mode: jsonMode }),
      });
      onResult(result);
    } catch (error) { onError((error as Error).message); }
    finally { setBusy(false); setToken(""); }
  }
  return <section className="standalone-panel guide-card llm-review-form">
    <h2>大模型任务评审</h2>
    <p>根据任务输入、最终答案和参考材料逐项评审，支持没有过程轨迹的任务。现有规则会一并保留。</p>
    <label>API 地址<input value={apiUrl} disabled={busy} onChange={(e) => setApiUrl(e.target.value)}
      placeholder="https://api.example.com/v1" type="url" /></label>
    <label>模型名称<input value={model} disabled={busy} onChange={(e) => setModel(e.target.value)}
      placeholder="填写接口支持的模型 ID" /></label>
    <label>访问 Token<input value={token} disabled={busy} onChange={(e) => setToken(e.target.value)}
      placeholder={configured ? "当前接口已在服务端配置；更换地址需重新填写" : "填写 API Token"}
      type="password" autoComplete="off" /></label>
    <p>Token 不写入报告或浏览器存储，本次请求结束后清空输入框。</p>
    {profile.rules.map((rule, index) => rule.op === "external" && <label key={rule.id}>
      评审标准 · {rule.id}<textarea rows={4} value={rule.description || ""} disabled={busy}
        onChange={(e) => setProfile({ ...profile, rules: profile.rules.map((item, i) =>
          i === index ? { ...item, description: e.target.value } : item) })} />
    </label>)}
    <details><summary>接口兼容选项</summary>
      <label>输出长度参数<select value={parameter} onChange={(e) => setParameter(e.target.value)} disabled={busy}>
        <option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option>
      </select></label>
      <label className="inline-check"><input type="checkbox" checked={jsonMode} disabled={busy}
        onChange={(e) => setJsonMode(e.target.checked)} />接口支持 JSON 模式</label>
    </details>
    <p>点击后会把任务材料发送到上述接口，可能产生费用。模型结论会参与本次验收，并标明来源；缺少依据的项目保持未知。</p>
    <button className="primary" onClick={start} disabled={busy || !apiUrl.trim() || !model.trim() || (!token && !configured)
      || profile.rules.some((rule) => rule.op === "external" && !rule.description?.trim())}>
      {busy ? <Loader2 size={16} className="spin" /> : <MessageSquare size={16} />}
      {busy ? "正在评审…" : "发送材料并评审"}
    </button>
    {evaluation.judge?.backend === "chat-completions" && <div className="judge-result">
      <h3>已保存的模型评审</h3><p>模型判断可能出错，请结合引用核实；评审用量与被评 Agent 的用量分开记录。</p>
      <p>评审模型：{String(evaluation.judge.model)} · 评审用量：{
        String((evaluation.judge.usage as { total_tokens?: number } | null)?.total_tokens ?? "未知")
      } tokens</p>
      <div className="verification-list">{(evaluation.judge.results as {
        id: string; status: string; explanation: string; evidence_paths: string[];
      }[]).map((item) => <article key={item.id}>
        <div><strong>{item.id}</strong><span className="tiny-status">{
          ({ pass: "通过", fail: "未通过", unknown: "依据不足" } as Record<string, string>)[item.status]
        }</span></div><p>{item.explanation}</p><p>引用材料：{item.evidence_paths.join("、") || "无"}</p>
      </article>)}</div>
      <details><summary>查看完整评审数据</summary><pre>{JSON.stringify(evaluation.judge, null, 2)}</pre></details>
    </div>}
  </section>;
}

function Guide({ dataDir }: { dataDir?: string }) {
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">GET STARTED</div>
          <h1>导入轨迹，定义任务标准。</h1>
          <p>分析已有运行，并按任务要求检查结果、行为和资源。</p>
        </div>
      </div>
      <section className="standalone-panel assessment-guide">
        <h2>评估场景，逐步接入</h2>
        <p><strong>OpenCode 轨迹评估：</strong>OpenCode 轨迹导入、过程诊断和已有材料验收。先加载通过、失败、待补证据三个合成场景。</p>
        <pre>agent-review demo --scenario all{"\n"}agent-review list{"\n"}agent-review report RUN_ID --output report.md</pre>
        <p>scenario 可选 focused、iterative、failed、all；不指定时仍加载原有两例。所有命令使用同一个 --data-dir。</p>
        <p><strong>代码修复评估：</strong>内置正确、错误、回归和超时候选，在 Docker 中实际执行固定独立测试。候选由模拟器生成，不代表真实 Agent 能力。</p>
        <pre>agent-review code-repair --candidate all --data-dir /absolute/review-data{"\n"}agent-review serve --data-dir /absolute/review-data</pre>
        <p>先启动 Docker 并准备 python:3.12-slim 镜像。命令完成后在运行列表查看结果、最终代码 diff 和 baseline/final 验证记录。支持导出含报告的 generic/2 包。</p>
        <p><strong>PDF 文档转换评估：</strong>自制两页数字 PDF、独立标注、五种模拟候选及实际本地检查。验收正文、标题、阅读顺序、矩形表格、完整性和 Markdown/JSON 一致性。</p>
        <pre>agent-review document-conversion --candidate all --data-dir /absolute/review-data{"\n"}agent-review document-evaluate RUN_ID --data-dir /absolute/review-data</pre>
        <p>网页可直接运行文档示例，查看转换产物、下载源 PDF 并追溯各项验收证据。单独导入 bundle 不执行评估器，重导入后可点击“运行固定文档检查”。</p>
        <p>OCR、公式、合并单元格和版式保真尚未覆盖；没有接入真实转换 Agent 或 OmniDocBench，简化检查不是 TEDS/CDM。</p>
        <p>OpenCode 是轨迹来源，可与代码修复或文档转换验收组合，不是互斥的任务类型。</p>
      </section>
      <div className="guide-grid">
        <section className="standalone-panel guide-card">
          <h2>通用任务与自定义评估</h2>
          <p>从发票提取或调研模板开始，替换轨迹和验收标准。复杂业务可通过 external 规则接入自己的评估程序。</p>
          <pre>agent-review init-task ./my-task --template invoice{"\n"}agent-review import ./my-task/trace.json --profile ./my-task/profile.json</pre>
          <p>导入时附加“任务评估 Profile”。结构检查不证明内容真实；未实现的专项检查保留为待补证据。</p>
        </section>
        <section className="standalone-panel guide-card">
          <span className="step-number">01</span>
          <h2>导出指定会话</h2>
          <p>在 OpenCode 中找到 session ID，然后导出原生 JSON。</p>
          <pre>
            opencode session list{"\n"}opencode export &lt;sessionID&gt; --pure
            &gt; session.json
          </pre>
          <p>也支持直接从 CLI 导入指定会话：</p>
          <pre>agent-review import-session &lt;sessionID&gt;</pre>
        </section>
        <section className="standalone-panel guide-card">
          <span className="step-number">02</span>
          <h2>代码任务的验收材料</h2>
          <p>
            日志可用于分析行为。确认任务结果还需要任务定义、最终代码状态与外部测试报告。
          </p>
          <pre>
            git diff &gt; final.patch{"\n"}pytest --junitxml=results.xml
          </pre>
          <p>
            导入时展开“任务与验证材料”，附加 Task Manifest、diff 和
            JUnit。运行包支持多个检查和 baseline/final 报告。
          </p>
        </section>
        <section className="standalone-panel guide-card">
          <span className="step-number">03</span>
          <h2>理解观察边界</h2>
          <p>
            运行跨度可能包含用户等待；报告成本不等同实际账单；文件读取只统计可见原生工具。
          </p>
          <p>
            压缩、脱敏和子会话会影响可观察范围。未知数据会显示“—”，不会填为零。
          </p>
          <a
            href="https://opencode.ai/docs/cli/#export"
            target="_blank"
            rel="noreferrer"
          >
            OpenCode 导出文档 <ArrowRight size={14} />
          </a>
        </section>
      </div>
      <section className="standalone-panel guide-card">
        <h2>Task Manifest 示例</h2>
        <pre>
          {JSON.stringify(
            {
              id: "my-bugfix",
              version: "1",
              prompt: "任务说明",
              base_commit: "git-base-sha",
              environment_hash: "image-or-lockfiles-hash",
              suite_hash: "fixed-test-suite-hash",
              budget_policy: "900s-2usd",
              final_state_hash: "final-tree-hash",
              checks: [{ id: "tests", kind: "test", command: "pytest -q" }],
              protected_paths: ["config/secrets.*"],
            },
            null,
            2,
          )}
        </pre>
        <p>
          这些标识由实验定义者提供，导入器不会自动证明它们对应真实环境。多次正式实验需在执行前固定条件。
        </p>
      </section>
      <div className="data-location">
        <ShieldCheck size={18} />
        <div>
          <b>当前数据目录</b>
          <code>{dataDir || "正在读取…"}</code>
          <p>
            会话与报告保存在本地。LLM
            评审默认关闭；导入过程不会执行日志中的命令。
          </p>
        </div>
      </div>
    </>
  );
}

function ServiceAccess() {
  const [required, setRequired] = useState<boolean | null>(null);
  const [authorized, setAuthorized] = useState(false);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ authentication_required?: boolean }>("/health")
      .then(health => setRequired(!!health.authentication_required))
      .catch(e => setError(e.message));
  }, []);
  if (required === false || authorized) return <App />;
  return <main className="service-access standalone-panel">
    <h1>连接评审服务</h1>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {required === null ? <p>正在连接服务…</p> : <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError(""); setAccessToken(token);
      try { await api("/targets"); setToken(""); setAuthorized(true); }
      catch (e) { setAccessToken(""); setError((e as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>输入管理员提供的服务访问令牌。令牌只保留在当前页面内存中。</p>
      <label>服务访问令牌<input aria-label="服务访问令牌" type="password" autoComplete="off" required
        value={token} onChange={e => setToken(e.target.value)} /></label>
      <button className="primary" disabled={busy}>连接服务</button>
    </form>}
  </main>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ServiceAccess />
  </React.StrictMode>,
);
