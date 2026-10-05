import { useState } from "react";

export const categoryLabels: Record<string, string> = {
  structured_output: "结构化输出", instruction_following: "指令遵循", retrieval: "上下文检索与引用",
  grounding: "信息不足与拒绝编造", robustness: "不可信输入干扰", capability: "计算与推理",
  multi_turn: "多轮澄清与纠错", memory: "跨轮记忆", session_isolation: "会话隔离", tool_use: "工具调用",
};
const verdicts: Record<string, string> = { pass: "通过", fail: "未通过", inconclusive: "证据不足", unknown: "证据不足" };
const executions: Record<string, string> = { completed: "执行完成", error: "调用异常", timeout: "超时",
  cancelled: "已取消", budget_exhausted: "预算耗尽" };
const operators: Record<string, string> = { equals: "等于", exists: "存在", contains: "包含", min: "不小于",
  max: "不大于", length_min: "长度至少", length_max: "长度至多" };

export type AssessmentCheck = { id: string; description?: string; status: string; required: boolean; explanation: string;
  comparison?: { path: string; operator: string; expected: string; actual: string } | null };
export type AssessmentResult = { case_id: string; description?: string; category?: string; budget_id: string; attempt: number;
  outcome: string; execution_state: string; run_id: string; error: string | null; checks?: AssessmentCheck[];
  guidance?: { kind: string; reason: string; next_step: string } };
export type AssessmentSummary = { headline: string; conclusion: string; planned: number; observed: number;
  pass: number; fail: number; inconclusive: number; pending: number; execution_issues: number;
  covered_categories: { id: string; label: string }[]; uncovered_categories: { id: string; label: string }[];
  next_steps: string[]; scope_note: string };

export function AssessmentOverview({ summary, demo }: { summary: AssessmentSummary; demo: boolean }) {
  return <section className="assessment-overview" aria-label="评测结论">
    <div className="eyebrow">{demo ? "控制示例 · 用于校准评测流程" : "独立验收结论"}</div>
    <h3>{summary.headline}</h3>
    <p>{summary.scope_note}</p>
    <dl className="assessment-counts">
      {[["通过", summary.pass], ["未通过", summary.fail], ["证据不足", summary.inconclusive], ["未取得结果", summary.pending]].map(([label, value]) =>
        <div key={label}><dt>{label}</dt><dd>{value}<small> 次</small></dd></div>)}
    </dl>
    <p>已取得 {summary.observed}/{summary.planned} 次结果；其中执行异常或取消 {summary.execution_issues} 次。</p>
    <h4>下一步建议</h4>
    <ul>{summary.next_steps.map(step => <li key={step}>{step}</li>)}</ul>
    <p><strong>题集覆盖（按计划）：</strong>{summary.covered_categories.map(c => c.label).join("、")}</p>
    {!!summary.uncovered_categories.length && <p><strong>尚未覆盖（按需补测）：</strong>{summary.uncovered_categories.map(c => c.label).join("、")}</p>}
  </section>;
}

export function ResultReview({ results }: { results: AssessmentResult[] }) {
  const [filter, setFilter] = useState("problems");
  const [search, setSearch] = useState("");
  const filtered = results.filter(r => {
    const matches = filter === "all" || (filter === "problems" ? r.outcome !== "pass"
      : filter === "execution" ? r.execution_state !== "completed" : r.outcome === filter);
    const text = `${r.case_id} ${r.description || ""} ${categoryLabels[r.category || ""] || ""} ${r.budget_id}`.toLowerCase();
    return matches && text.includes(search.trim().toLowerCase());
  });
  return <section className="assessment-review" aria-label="案例诊断">
    <h3>案例诊断</h3>
    <p>先处理未通过与证据不足的案例。每个结论都可以追溯到对话和验收记录。</p>
    <div className="assessment-controls">
      <label>筛选结果<select value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="problems">需要关注</option><option value="all">全部结果</option>
        <option value="fail">未通过</option><option value="inconclusive">证据不足</option>
        <option value="execution">执行异常或取消</option><option value="pass">通过</option>
      </select></label>
      <label>搜索案例<input type="search" placeholder="案例名称、编号或维度" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <span role="status">显示 {filtered.length}/{results.length} 次结果</span>
    </div>
    {!filtered.length && <p>{!results.length ? "尚无案例结果，执行完成后会显示在这里。"
      : filter === "problems" && !search ? "当前已取得的结果没有待处理问题。可选择“全部结果”查看通过依据。" : "没有符合筛选条件的案例。"}</p>}
    {filtered.map(r => {
      const important = (r.checks || []).filter(c => c.required && c.status !== "pass");
      const missing = new Set(important.filter(c => c.status === "fail" && c.comparison?.operator === "exists")
        .map(c => c.comparison!.path));
      const diagnostics = important.filter(c => !(c.status === "unknown" && c.comparison?.operator === "equals"
        && missing.has(c.comparison.path)));
      return <details className="assessment-case" key={r.run_id}>
        <summary><span className={`assessment-verdict ${r.outcome}`}>{verdicts[r.outcome] || r.outcome}</span>
          <strong>{r.description || r.case_id}</strong>
          <span>{r.budget_id} · 第 {r.attempt} 次</span></summary>
        <p>{categoryLabels[r.category || ""] || r.category} · {r.case_id} · {executions[r.execution_state] || r.execution_state}</p>
        {r.guidance && <p><strong>{r.guidance.reason}</strong>。{r.guidance.next_step}</p>}
        {r.error && <p>执行错误：<code>{r.error}</code></p>}
        {diagnostics.map(c => <CheckDetail key={c.id} check={c} related={c.comparison?.operator === "exists"
          ? important.find(other => other.comparison?.operator === "equals" && other.comparison.path === c.comparison?.path)
          : undefined} />)}
        {!!r.checks?.length && <details><summary>全部验收检查（{r.checks.length}）</summary>
          {r.checks.map(c => <CheckDetail key={c.id} check={c} />)}</details>}
        {!r.checks && <p>历史报告未保存逐项对照，请打开运行记录查看。</p>}
        <a href={`#/runs/${r.run_id}`}>打开完整对话与验收证据</a>
      </details>;
    })}
  </section>;
}

function CheckDetail({ check, related }: { check: AssessmentCheck; related?: AssessmentCheck }) {
  return <div className="assessment-check">
    <p><strong>{check.description || check.id}</strong> · {verdicts[check.status] || check.status} · {check.required ? "必需" : "可选"}</p>
    <p>{check.explanation}</p>
    {check.comparison && <>
      <p><code>{check.comparison.path}</code> · 条件：{related ? "存在且等于" : operators[check.comparison.operator] || check.comparison.operator}</p>
      <dl className="assessment-comparison"><div><dt>期望</dt><dd><pre>{related?.comparison?.expected ?? check.comparison.expected}</pre></dd></div>
        <div><dt>实际返回</dt><dd><pre>{check.comparison.actual}</pre></dd></div></dl>
    </>}
  </div>;
}
