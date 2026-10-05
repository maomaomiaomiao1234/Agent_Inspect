import { useState } from "react";
import { Activity, ArrowUpRight, CheckCheck, CircleHelp, Layers3, Target } from "lucide-react";
import { categoryLabels, type AssessmentSummary } from "./assessment-report";
import { UsageValue, type Usage } from "./usage";

export type Dimension = {
  category: string; label?: string; budget_id: string; observed: number; planned: number;
  pass: number; fail: number; unknown: number; confirmed_pass_rate: number; possible_pass_rate: number;
  p50_duration_ms: number | null; p95_duration_ms: number | null;
};
export type BudgetCurve = {
  budget: { id: string }; planned?: number; observed?: number; pass: number; fail: number; unknown: number;
  pass_rate: number; mean_duration_ms: number | null; reported_total_tokens: number | null;
  reported_cost_usd: number | null; usage?: Usage;
};
export type Quality = {
  summary?: AssessmentSummary; dimensions: Dimension[];
  stability: { case_id: string; budget_id: string; status: string; observed: number; planned: number }[];
  source_coverage: { kind: string; name: string; path: string; line: number; status: string }[];
  evidence: { token_known_runs: number; token_partial_runs?: number; token_not_applicable_runs?: number;
    planned_runs: number; unknown_required_checks: number; required_checks: number };
  limitations: string[];
};
export type CaseFocus = { category: string; budget?: string };
type VisualJob = {
  planned: number; completed: number; demo: boolean; purpose?: string;
  curves: BudgetCurve[]; quality?: Quality;
};

const percent = (part: number, total: number) => total > 0 ? Math.min(100, Math.max(0, part / total * 100)) : 0;
export const formatScore = (value: number) => Number(value.toFixed(1)).toString();
export const jobScore = (job: Pick<VisualJob, "planned" | "completed" | "curves">) =>
  job.planned > 0 && job.completed > 0 ? percent(job.curves.reduce((sum, c) => sum + c.pass, 0), job.planned) : null;

function aggregateDimensions(rows: Dimension[]) {
  const groups = new Map<string, Dimension>();
  for (const row of rows) {
    const previous = groups.get(row.category);
    groups.set(row.category, previous ? { ...previous, planned: previous.planned + row.planned,
      observed: previous.observed + row.observed, pass: previous.pass + row.pass, fail: previous.fail + row.fail,
      unknown: previous.unknown + row.unknown } : { ...row });
  }
  return [...groups.values()].map(d => ({ ...d, confirmed_pass_rate: percent(d.pass, d.planned),
    possible_pass_rate: percent(d.planned - d.fail, d.planned) }));
}

function ResultBar({ pass, fail, total, label }: { pass: number; fail: number; total: number; label: string }) {
  const unknown = Math.max(0, total - pass - fail);
  return <div className="result-bar" role="img" aria-label={`${label}：通过 ${pass}，未通过 ${fail}，待确认 ${unknown}，计划 ${total}`}>
    <span className="bar-pass" style={{ width: `${percent(pass, total)}%` }} />
    <span className="bar-fail" style={{ width: `${percent(fail, total)}%` }} />
    <span className="bar-unknown" style={{ width: `${percent(unknown, total)}%` }} />
  </div>;
}

function Radar({ dimensions }: { dimensions: Dimension[] }) {
  if (dimensions.length < 3) return <div className="chart-empty"><Target size={28} />
    <strong>维度数量不足</strong><p>至少覆盖 3 个维度后显示能力雷达。右侧可查看已有维度的结果。</p></div>;
  const center = 180, radius = 112, labelRadius = 142;
  const point = (i: number, r: number) => {
    const angle = -Math.PI / 2 + i * Math.PI * 2 / dimensions.length;
    return [center + Math.cos(angle) * r, center + Math.sin(angle) * r];
  };
  const polygon = (r: number) => dimensions.map((_, i) => point(i, r).join(",")).join(" ");
  const measured = dimensions.map((d, i) => d.observed > 0 ? point(i, radius * d.confirmed_pass_rate / 100) : null);
  const allMeasured = measured.every(p => p !== null);
  return <svg className="capability-radar" viewBox="0 0 360 360" role="img" aria-label="能力雷达图">
    <title>能力雷达图 · 各维度确认通过率</title>
    <desc>{dimensions.map(d => `${d.label || categoryLabels[d.category] || d.category}：${d.observed ? `${formatScore(d.confirmed_pass_rate)} 分，${d.pass}/${d.planned} 次通过` : "待评测"}`).join("；")}。未覆盖的能力不进入图表。</desc>
    {[.25, .5, .75, 1].map(scale => <polygon key={scale} points={polygon(radius * scale)} className="radar-grid" />)}
    {dimensions.map((d, i) => { const [x, y] = point(i, radius); return <line key={d.category} x1={center} y1={center} x2={x} y2={y} className="radar-axis" />; })}
    {[25, 50, 75, 100].map(value => <text key={value} x={center + 5} y={center - radius * value / 100 + 12} className="radar-tick">{value}</text>)}
    {allMeasured && <polygon points={measured.map(p => p!.join(",")).join(" ")} className="radar-area" />}
    {!allMeasured && measured.map((p, i) => { const next = measured[(i + 1) % measured.length];
      return p && next ? <line key={i} x1={p[0]} y1={p[1]} x2={next[0]} y2={next[1]} className="radar-line" /> : null; })}
    {dimensions.map((d, i) => {
      const [x, y] = point(i, labelRadius);
      const label = d.label || categoryLabels[d.category] || d.category;
      const shortLabel: Record<string, string> = { retrieval: "检索与引用", grounding: "事实依据", robustness: "抗干扰", multi_turn: "多轮纠错", capability: "计算与推理" };
      return <g key={d.category}>
        {measured[i] && <circle cx={measured[i]![0]} cy={measured[i]![1]} r="4" className="radar-point" />}
        <text x={x} y={y - 4} textAnchor="middle" className="radar-label"><title>{label}</title>{shortLabel[d.category] || (label.length > 10 ? label.slice(0, 9) + "…" : label)}</text>
        <text x={x} y={y + 13} textAnchor="middle" className="radar-value">{d.observed ? `${formatScore(d.confirmed_pass_rate)} 分` : "待评测"}</text>
      </g>;
    })}
  </svg>;
}

export function AssessmentVisualizations({ job, onInspect }: { job: VisualJob; onInspect: (focus: CaseFocus) => void }) {
  const [budget, setBudget] = useState("");
  const rows = job.quality?.dimensions || [];
  const dimensions = aggregateDimensions(budget ? rows.filter(d => d.budget_id === budget) : rows);
  const curves = budget ? job.curves.filter(c => c.budget.id === budget) : job.curves;
  const planned = budget ? curves.reduce((n, c) => n + (c.planned ?? c.pass + c.fail + c.unknown), 0) : job.planned;
  const observed = budget ? rows.length ? dimensions.reduce((n, d) => n + d.observed, 0)
    : curves.reduce((n, c) => n + (c.observed ?? c.pass + c.fail), 0) : job.completed;
  const passed = curves.reduce((n, c) => n + c.pass, 0);
  const failed = curves.reduce((n, c) => n + c.fail, 0);
  const score = planned > 0 && observed > 0 ? percent(passed, planned) : null;
  const upper = percent(planned - failed, planned);
  const stability = (job.quality?.stability || []).filter(s => !budget || s.budget_id === budget);
  const repeated = stability.filter(s => s.planned >= 2);
  const settled = repeated.filter(s => s.status === "consistent_pass" || s.status === "consistent_fail" || s.status === "variable");
  const stable = settled.filter(s => s.status !== "variable").length;
  const radius = 64, circumference = 2 * Math.PI * radius;
  const adapter = job.purpose === "adapter_validation";
  return <section className="assessment-visuals" aria-label="Agent 能力可视化">
    <div className="visual-heading"><div><span className="eyebrow">PERFORMANCE OVERVIEW</span>
      <h3>{adapter ? "接入检查概览" : "能力与评分概览"}</h3></div>
      {job.curves.length > 1 && <label className="budget-select">统计预算<select aria-label="可视化预算" value={budget} onChange={e => setBudget(e.target.value)}>
        <option value="">全部预算</option>{job.curves.map(c => <option key={c.budget.id} value={c.budget.id}>{c.budget.id}</option>)}
      </select></label>}
    </div>
    {(job.demo || adapter) && <p className="visual-scope-badge">{adapter ? "接入检查 · 不计入正式能力评测" : "控制示例 · 分数仅用于校准评测流程"}</p>}
    <div className="score-overview">
      <div className="score-card" aria-label="题集评分">
        <div className="score-ring"><svg viewBox="0 0 160 160" aria-hidden="true">
          <circle cx="80" cy="80" r={radius} className="score-track" />
          <circle cx="80" cy="80" r={radius} className="score-fill" strokeDasharray={`${circumference * (score ?? 0) / 100} ${circumference}`} transform="rotate(-90 80 80)" />
        </svg><div><strong data-testid="assessment-score">{score === null ? "—" : formatScore(score)}</strong><span>/ 100 分</span></div></div>
        <div className="score-description"><span>{adapter ? "接入通过得分" : job.demo ? "校准题集得分" : "题集得分"}</span>
          <strong>{score === null ? "等待评测结果" : observed < planned ? "评测尚未完成" : failed ? "发现待改进项" : passed === planned ? "本题集全部通过" : "仍有待确认项"}</strong>
          <p>确认通过 {passed} / 计划 {planned} 次</p>
          {score !== null && upper > score && <small>待确认项全通过时，最高 {formatScore(upper)} 分</small>}
        </div>
      </div>
      <div className="visual-stat"><Activity size={18} /><span>评测完成度</span>
        <strong>{formatScore(percent(observed, planned))}<small>%</small></strong>
        <p>已取得 {observed} / {planned} 次结果</p>
        <progress value={observed} max={planned || 1} aria-label="评测完成度" />
      </div>
      <div className="visual-stat"><Layers3 size={18} /><span>题集维度覆盖</span>
        <strong>{new Set(rows.map(d => d.category)).size}<small> 个维度</small></strong>
        <p>{job.quality?.summary ? `按计划覆盖 · ${job.quality.summary.uncovered_categories.length} 个未覆盖` : "按本题集计划统计"}</p>
      </div>
      <div className="visual-stat"><CheckCheck size={18} /><span>重复结果一致率</span>
        <strong>{settled.length ? formatScore(percent(stable, settled.length)) : "—"}<small>{settled.length ? "%" : ""}</small></strong>
        <p>{!repeated.length ? "至少重复 2 次后可判断" : !settled.length ? "等待重复评测证据" : `${stable} / ${settled.length} 个可判断组一致`}</p>
        {!!settled.length && <small>一致包含重复失败；{repeated.length - settled.length} 组待确认</small>}
      </div>
    </div>
    <p className="score-method"><CircleHelp size={14} />得分 = 确认通过次数 ÷ 计划执行次数 × 100。待确认包含证据不足和未取得结果；仅反映本题集表现。</p>
    <div className="capability-grid">
      <div className="visual-panel radar-panel"><div className="chart-heading"><h4>能力雷达</h4><span>确认通过率 · 0–100</span></div>
        <Radar dimensions={dimensions} /><p>未覆盖维度不进入雷达；未取得结果的维度不绘制得分点。</p>
      </div>
      <div className="visual-panel dimension-panel"><div className="chart-heading"><h4>各维度表现</h4><span>点击维度查看案例 <ArrowUpRight size={13} /></span></div>
        <div className="chart-legend"><span className="legend-pass">通过</span><span className="legend-fail">未通过</span><span className="legend-unknown">待确认</span></div>
        {!dimensions.length && <div className="chart-empty"><Layers3 size={28} /><strong>暂无维度数据</strong><p>这份历史报告未保存维度统计，可查看下方案例证据。</p></div>}
        {dimensions.map(d => <button className="dimension-row" key={d.category} onClick={() => onInspect({ category: d.category, budget: budget || undefined })}
          aria-label={`查看${d.label || categoryLabels[d.category] || d.category}案例`}>
          <div><strong>{d.label || categoryLabels[d.category] || d.category}</strong><span>{d.observed ? `${formatScore(d.confirmed_pass_rate)} 分` : "待评测"}</span></div>
          <ResultBar pass={d.pass} fail={d.fail} total={d.planned} label={categoryLabels[d.category] || d.category} />
          <small>通过 {d.pass} · 未通过 {d.fail} · 待确认 {Math.max(0, d.planned - d.pass - d.fail)}<span>{d.observed}/{d.planned} 次结果</span></small>
        </button>)}
      </div>
    </div>
    {!!job.curves.length && <section className="visual-panel budget-panel" aria-label="预算表现对比"><div className="chart-heading"><h4>预算表现对比</h4><span>相同题集 · 不同资源预算</span></div>
      {job.curves.map(c => { const total = c.planned ?? c.pass + c.fail + c.unknown; return <div className="budget-chart-row" key={c.budget.id}>
        <strong>{c.budget.id}</strong><div><ResultBar pass={c.pass} fail={c.fail} total={total} label={c.budget.id} />
          <p>通过 {c.pass}/{total} · 平均耗时 {c.mean_duration_ms === null ? "未知" : `${(c.mean_duration_ms / 1000).toFixed(2)} 秒`} · Token <UsageValue usage={c.usage} field="total_tokens" fallback={c.reported_total_tokens} /></p></div>
        <span>{formatScore(c.pass_rate)}<small>%</small></span>
      </div>; })}
    </section>}
  </section>;
}
