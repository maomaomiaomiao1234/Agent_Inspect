import { useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, FlaskConical, RefreshCw, Search } from "lucide-react";
import { formatScore, jobScore, type BudgetCurve } from "./assessment-visualizations";

type HistoryJob = {
  id: string; state: string; target_id: string; suite_id: string; planned: number; completed: number;
  demo: boolean; purpose?: string; curves: BudgetCurve[];
  target_label?: string;
};
export const assessmentStateLabels: Record<string, string> = { queued: "排队中", running: "执行中", completed: "已完成",
  failed: "执行异常", cancelled: "已取消", interrupted: "服务重启中断" };

export function AssessmentHistory({ jobs, selected, loading, onOpen, onRefresh }: {
  jobs: HistoryJob[]; selected: string; loading: boolean; onOpen: (id: string) => void; onRefresh: () => void;
}) {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(0);
  const filtered = jobs.filter(j => `${j.target_label || ""} ${j.target_id} ${j.suite_id} ${j.id}`.toLowerCase().includes(search.trim().toLowerCase())
    && (kind === "all" || (kind === "formal" ? !j.demo && j.purpose !== "adapter_validation"
      : kind === "demo" ? j.demo : kind === "active" ? ["queued", "running"].includes(j.state) : j.state === "completed")));
  const pageSize = 8, pages = Math.max(1, Math.ceil(filtered.length / pageSize)), current = Math.min(page, pages - 1);
  const visible = filtered.slice(current * pageSize, (current + 1) * pageSize);
  return <section className="standalone-panel assessment-history" aria-label="评测任务">
    <div className="history-heading"><div><h2>评测任务 <span className="count">{jobs.length}</span></h2></div>
      <button onClick={onRefresh}><RefreshCw size={14} />刷新评测</button></div>
    <div className="history-controls"><label className="search"><Search size={16} /><input type="search" aria-label="搜索评测" placeholder="搜索 Agent、题集或任务…" value={search}
      onChange={e => { setSearch(e.target.value); setPage(0); }} /></label>
      <select aria-label="评测记录筛选" value={kind} onChange={e => { setKind(e.target.value); setPage(0); }}>
        <option value="all">全部记录</option><option value="formal">正式能力评测</option><option value="completed">已完成</option>
        <option value="active">执行中 / 排队中</option><option value="demo">控制示例</option>
      </select></div>
    {loading && !jobs.length ? <p className="history-empty" role="status">正在读取评测记录…</p>
      : !visible.length ? <div className="history-empty"><FlaskConical size={26} /><strong>{!jobs.length ? "还没有评测结果" : "没有匹配的评测记录"}</strong>
        <p>{!jobs.length ? "从下方新建评测，完成后即可查看 Agent 的能力与评分。" : "试试其他关键词或筛选条件。"}</p></div>
        : <div className="assessment-table"><table><thead><tr><th>题集 / Agent</th><th>状态</th><th>题集得分</th><th>案例进度</th><th>查看</th></tr></thead>
          <tbody>{visible.map(j => { const score = jobScore(j); return <tr key={j.id} className={selected === j.id ? "selected-assessment" : ""}>
            <td><button className="history-job text-button" aria-pressed={selected === j.id} onClick={() => onOpen(j.id)}>
              {j.suite_id} / {j.target_label || j.target_id}{j.demo ? " · 示例" : ""}{j.purpose === "adapter_validation" ? " · 接入检查" : ""}</button>
              <span className="history-job-id mono">{j.id.slice(-12)}</span></td>
            <td><span className={`job-state ${j.state}`}><i />{assessmentStateLabels[j.state] || j.state}</span></td>
            <td><span className="history-score">{j.purpose === "adapter_validation" ? "接入检查" : score === null ? "—" : formatScore(score)}
              {score !== null && j.purpose !== "adapter_validation" && <small> / 100</small>}</span>
              {j.demo && <span className="history-score-note">校准题集</span>}
              {score !== null && j.completed < j.planned && <span className="history-score-note">当前结果</span>}</td>
            <td><span className="history-progress-text">{j.completed}/{j.planned}</span><progress value={j.completed} max={j.planned || 1} aria-label={`${j.target_id} 案例进度`} /></td>
            <td><button className="icon-button" aria-label={`查看评测 ${j.id}`} onClick={() => onOpen(j.id)}><ArrowRight size={17} /></button></td>
          </tr>; })}</tbody></table></div>}
    {!!filtered.length && <div className="history-footer"><span>{current * pageSize + 1}–{Math.min((current + 1) * pageSize, filtered.length)} / {filtered.length} 条</span>
      {pages > 1 && <div><button aria-label="上一页评测" disabled={current === 0} onClick={() => setPage(current - 1)}><ChevronLeft size={14} /></button>
        <span>{current + 1} / {pages}</span><button aria-label="下一页评测" disabled={current === pages - 1} onClick={() => setPage(current + 1)}><ChevronRight size={14} /></button></div>}</div>}
  </section>;
}
