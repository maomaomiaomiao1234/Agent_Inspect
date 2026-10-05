import { useEffect, useState } from "react";
import { api, DownloadLink } from "./api-client";
import { RepositoryAssessments } from "./repository-assessments";
import { UsageValue, type Usage } from "./usage";

type Target = { id: string; repository_configured: boolean; managed: boolean; demo: boolean };
type GeneratedSuite = { id: string; description: string; cases: { id: string; turns: unknown[] }[] };
type Source = { path: string; line: number; url?: string };
type Claim = Source & { id: string; capability: string; snippet: string; assessment_status?: string };
type Repository = { id: string; commit: string | null; snapshot_kind: string; files: unknown[]; claims: Claim[]; limitations: string[] };
type Plan = { id: string; suite: GeneratedSuite; estimated_requests: number; max_serial_deadline_seconds: number;
  dimensions: { id: string; label: string; cases: number }[]; gaps: { kind: string; name: string; reason: string }[];
  tool_candidates: { name: string; path: string; line: number; parameters: string[] }[]; limitations: string[] };
type Quality = { dimensions: { category: string; budget_id: string; observed: number; planned: number;
  pass: number; fail: number; unknown: number; confirmed_pass_rate: number; possible_pass_rate: number;
  p50_duration_ms: number | null; p95_duration_ms: number | null }[];
  stability: { case_id: string; budget_id: string; status: string; observed: number; planned: number }[];
  source_coverage: { kind: string; name: string; path: string; line: number; status: string }[];
  evidence: { token_known_runs: number; token_partial_runs?: number; token_not_applicable_runs?: number;
    planned_runs: number; unknown_required_checks: number; required_checks: number };
  limitations: string[] };
type Telemetry = { coverage: "complete" | "partial" | "unavailable"; llm_calls: number | null; tool_calls: number | null;
  llm_errors: number | null; tool_errors: number | null; llm_duration_ms: number | null; tool_duration_ms: number | null;
  models: { model: string; calls: number; tokens: { input: number | null; output: number | null; total: number | null };
    usage?: Usage; duration_ms: number | null }[] };
type Job = {
  id: string; state: string; target_id: string; suite_id: string; planned: number; completed: number;
  commit: string | null; repository_id: string | null; error: string | null; demo: boolean;
  curves: { budget: { id: string }; pass: number; fail: number; unknown: number; pass_rate: number;
    mean_duration_ms: number | null; reported_total_tokens: number | null; reported_cost_usd: number | null; usage?: Usage }[];
  results: { case_id: string; budget_id: string; attempt: number; outcome: string; run_id: string;
    execution_state: string; error: string | null; source_evidence: Source[]; telemetry?: Telemetry;
    usage?: Usage }[];
  claims: Claim[]; limitations: string[];
  quality?: Quality; concurrency?: number;
  purpose?: string;
};

const stateLabel: Record<string, string> = { queued: "排队中", running: "执行中", completed: "已完成",
  failed: "执行异常", cancelled: "已取消", interrupted: "服务重启中断" };
const outcomeLabel: Record<string, string> = { pass: "通过", fail: "失败", inconclusive: "证据不足" };
const claimLabel: Record<string, string> = { untested: "未测试", supported_for_cases: "这些案例支持声明",
  contradicted_by_cases: "有案例未满足声明", inconclusive: "证据不足" };
const qualityLabel: Record<string, string> = { consistent_pass: "重复通过", consistent_fail: "重复失败",
  variable: "结果波动", incomplete: "证据不全", insufficient_repeats: "重复次数不足",
  observed_for_cases: "已观察到调用", not_observed: "尚未观察到调用", untested: "未测试" };

function callCount(value: number | null | undefined, telemetry?: Telemetry) {
  return value == null ? "未知" : `${telemetry?.coverage === "complete" ? "" : "≥"}${value}`;
}
function callTime(value: number | null | undefined, telemetry?: Telemetry) {
  return value == null ? "未知" : `${telemetry?.coverage === "complete" ? "" : "≥"}${value.toFixed(2)} ms`;
}

function SourceLink({ source }: { source: Source }) {
  return source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.path}:{source.line}</a>
    : <span className="mono">{source.path}:{source.line}</span>;
}

export function Assessments() {
  const [targets, setTargets] = useState<Target[]>([]);
  const [target, setTarget] = useState("");
  const [suite, setSuite] = useState<unknown>(null);
  const [suiteName, setSuiteName] = useState("");
  const [generated, setGenerated] = useState<GeneratedSuite | null>(null);
  const [caseCount, setCaseCount] = useState(12);
  const [seed, setSeed] = useState(42);
  const [template, setTemplate] = useState("smolagents");
  const [attempts, setAttempts] = useState(1);
  const [concurrency, setConcurrency] = useState(1);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [repository, setRepository] = useState<Repository | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [readiness, setReadiness] = useState<{ usage_mode: string; collection: string; reason: string; model: string | null } | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [comparison, setComparison] = useState<{ comparable: boolean; reasons: string[];
    changes: { case_id: string; from: string; to: string; regression: boolean }[] } | null>(null);

  useEffect(() => {
    api<Target[]>("/targets").then(items => { setTargets(items); setTarget(items[0]?.id || ""); }).catch(e => setError(e.message));
  }, []);
  useEffect(() => {
    let stopped = false;
    setJob(null);
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const items = await api<Job[]>("/assessments?limit=100");
        if (stopped) return;
        setJobs(items);
        if (selected) {
          const detail = await api<Job>(`/assessments/${encodeURIComponent(selected)}`);
          if (stopped) return;
          setJob(detail);
        }
        if (items.some(j => ["queued", "running"].includes(j.state))) timer = setTimeout(load, 1500);
      } catch (e) { if (!stopped) setError((e as Error).message); }
    }
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh, selected]);

  async function action(work: () => Promise<void>) {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <>
    <div className="page-heading"><div><div className="eyebrow">ACTIVE ASSESSMENT</div>
      <h1>向 Agent 发任务，独立检查结果。</h1>
      <p>选择已登记的服务和固定题集，观察多轮任务、会话隔离与不同预算下的表现。</p>
    </div></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <RepositoryAssessments onOpen={id => { setSelected(id); setRefresh(v => v + 1); }} />
    <section className="standalone-panel assessment-guide">
      <h2>新建评测</h2>
      {!targets.length ? <p>尚未登记目标。管理员启动服务时使用 <code>--targets targets.json</code> 配置服务地址；README 提供可直接运行的控制 Agent。</p> : <>
        <div className="assessment-controls">
          <label>被测 Agent<select aria-label="被测 Agent" value={target} onChange={e => {
            setTarget(e.target.value); setReadiness(null); setRepository(null); setPlan(null); setSuite(null); setGenerated(null); setSuiteName("");
          }}>
            {targets.map(t => <option key={t.id} value={t.id}>{t.id}{t.demo ? "（控制示例）" : ""}{t.managed ? " · Docker" : ""}</option>)}
          </select></label>
          <label>固定题集 JSON<input aria-label="固定题集 JSON" type="file" accept=".json,application/json" onChange={async e => {
            const file = e.target.files?.[0]; setSuite(null); setSuiteName(""); setGenerated(null); setPlan(null); setError("");
            if (!file) return;
            try {
              if (file.size > 1024 * 1024) throw new Error("题集超过 1 MB。");
              setSuite(JSON.parse(await file.text())); setSuiteName(file.name);
            } catch (e) { setError((e as Error).message); }
          }} /></label>
          <button className="primary" disabled={busy || !suite || !target} onClick={() => action(async () => {
            const created = await api<Job>("/assessments", { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ target_id: target, suite }) });
            setSelected(created.id); setRefresh(x => x + 1);
          })}>开始评测{suiteName ? ` · ${suiteName}` : ""}</button>
          <button disabled={busy || !targets.find(t => t.id === target)?.repository_configured} onClick={() => action(async () => {
            setRepository(await api<Repository>(`/targets/${encodeURIComponent(target)}/repository-profile`, { method: "POST" }));
          })}>扫描能力档案</button>
          <button disabled={busy || !target} onClick={() => action(async () => {
            setReadiness(await api(`/targets/${encodeURIComponent(target)}/telemetry-readiness`));
          })}>检查 Token 采集</button>
        </div>
        {readiness && <p role="status">模式：{readiness.usage_mode === "offline" ? "离线校准" : readiness.usage_mode === "model" ? "真实模型" : "未知"}
          {readiness.model ? ` · ${readiness.model}` : ""}；{readiness.reason}</p>}
        <h3>自动生成测试文件</h3>
        <div className="assessment-controls">
          <label>测试模板<select aria-label="测试模板" disabled={busy} value={template} onChange={e => setTemplate(e.target.value)}>
            <option value="smolagents">smolagents 基础能力</option>
            <option value="repository">根据源码规划评测</option>
          </select></label>
          <label>案例数量<input aria-label="案例数量" type="number" min="1" max="30" step="1"
            value={caseCount} onChange={e => setCaseCount(Number(e.target.value))} /></label>
          <label>随机种子<input aria-label="随机种子" type="number" min="0" max="2147483647" step="1"
            value={seed} onChange={e => setSeed(Number(e.target.value))} /></label>
          {template === "repository" && <>
            <label>重复次数<select aria-label="重复次数" value={attempts} onChange={e => setAttempts(Number(e.target.value))}>
              {[1, 2, 3].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
            <label>案例并发<select aria-label="案例并发" value={concurrency} onChange={e => setConcurrency(Number(e.target.value))}>
              {[1, 2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
          </>}
          <button disabled={busy || !Number.isInteger(caseCount) || caseCount < 1 || caseCount > 30
            || (template === "repository" && !targets.find(t => t.id === target)?.repository_configured)
            || !Number.isInteger(seed) || seed < 0 || seed > 2147483647} onClick={() => action(async () => {
            if (template === "repository") {
              const snapshot = await api<Repository>(`/targets/${encodeURIComponent(target)}/repository-profile`, { method: "POST" });
              setRepository(snapshot);
              const planned = await api<Plan>(`/assessment-suites/plan/${snapshot.id}`, { method: "POST",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cases: caseCount, seed, attempts, concurrency }) });
              setPlan(planned); setGenerated(planned.suite); setSuite(planned.suite); setSuiteName(`${planned.suite.id}.json`);
              return;
            }
            const created = await api<GeneratedSuite>("/assessment-suites/generate", { method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ template: "smolagents", cases: caseCount, seed }) });
            setPlan(null); setGenerated(created); setSuite(created); setSuiteName(`${created.id}.json`);
          })}>生成测试文件</button>
          {generated && <button onClick={() => {
            const url = URL.createObjectURL(new Blob([JSON.stringify(generated, null, 2) + "\n"], { type: "application/json" }));
            const link = document.createElement("a"); link.href = url; link.download = `${generated.id}.json`;
            link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}>下载测试 JSON</button>}
        </div>
        <p className="scenario-note">生成无需模型额度，标准答案由程序计算。源码规划要求登记仓库和 target-v1 服务，按源码线索选择受控任务。
          smolagents 模板要求示例工具和 JSON 输出约定；源码规划要求各题指定的 JSON 输出，跨轮与隔离测试保持串行。</p>
        {plan && <section aria-label="源码评测计划">
          <h3>源码评测计划</h3>
          <p>包含重复共 {plan.estimated_requests} 轮请求；累计任务期限最多 {plan.max_serial_deadline_seconds} 秒。</p>
          <ul>{plan.dimensions.map(d => <li key={d.id}>{d.label}：{d.cases} 个案例</li>)}</ul>
          <details><summary>静态工具候选（{plan.tool_candidates.length}）</summary><ul>{plan.tool_candidates.map((t, i) =>
            <li key={i}>{t.name}({t.parameters.join(", ")}) · <SourceLink source={t} /></li>)}</ul></details>
          <details><summary>待补测项（{plan.gaps.length}）</summary><ul>{plan.gaps.map((g, i) =>
            <li key={i}>{g.name}：{g.reason}</li>)}</ul></details>
          <details><summary>计划适用范围</summary><ul>{plan.limitations.map((v, i) => <li key={i}>{v}</li>)}</ul></details>
        </section>}
        {generated && <div role="status"><p>已生成 {generated.cases.length} 个案例、
          {generated.cases.reduce((total, item) => total + item.turns.length, 0)} 轮请求。
          可下载保存，也可直接点击“开始评测”。</p>
          <details><summary>预览题目和验收规则</summary><pre className="json-preview">{JSON.stringify(generated, null, 2)}</pre></details>
        </div>}
        <p className="scenario-note">标准答案和验收规则留在评审端。评测会向登记的 Agent 发出请求，可能使用该 Agent 配置的模型额度。</p>
      </>}
    </section>
    {repository && <section className="standalone-panel assessment-guide">
      <h2>源码能力档案</h2><p>提交：<code>{repository.commit || "无 Git 提交；内容快照"}</code> · 扫描 {repository.files.length} 个文本文件</p>
      <p>声明尚未经过任务验证。把 claim_id 写入题集中的 claim_ids，可在报告中核对声明与测试结果。</p>
      <ul>{repository.claims.slice(0, 30).map(c => <li key={c.id}><SourceLink source={c} /> · {c.capability} · <code>{c.id}</code><br />{c.snippet}</li>)}</ul>
      {repository.claims.length > 30 && <p>这里只展示前 30 条；完整档案保存在 API 中。</p>}
    </section>}
    <section className="standalone-panel assessment-guide">
      <div className="document-result-heading"><h2>评测任务</h2><button onClick={() => setRefresh(x => x + 1)}>刷新评测</button></div>
      {!jobs.length ? <p>尚无评测记录。先用 examples/assessment/suite.json 跑通流程。</p> : <div className="assessment-table"><table>
        <thead><tr><th>题集 / Agent</th><th>状态</th><th>案例进度</th><th>任务 ID</th></tr></thead>
        <tbody>{jobs.map(j => <tr key={j.id}><td><button className="text-button" onClick={() => setSelected(j.id)}>{j.suite_id} / {j.target_id}{j.demo ? " · 示例" : ""}{j.purpose === "adapter_validation" ? " · 接入检查" : ""}</button></td>
          <td>{stateLabel[j.state] || j.state}</td><td>{j.completed}/{j.planned}</td><td className="mono">{j.id.slice(-12)}</td></tr>)}</tbody>
      </table></div>}
    </section>
    {job && <section className="standalone-panel assessment-guide">
      <div className="document-result-heading"><h2>评测详情 · {job.target_id}</h2><div className="scenario-actions">
        {["queued", "running"].includes(job.state) && <button disabled={busy} onClick={() => action(async () => {
          await api(`/assessments/${job.id}/cancel`, { method: "POST" }); setRefresh(x => x + 1);
        })}>取消评测</button>}
        <DownloadLink path={`/assessments/${job.id}/export`}>导出评测报告</DownloadLink>
        <DownloadLink path={`/assessments/${job.id}/export?format=json`}>导出评测 JSON</DownloadLink>
        <DownloadLink path={`/assessments/${job.id}/export?format=bundle`}>导出证据包</DownloadLink>
      </div></div>
      <p>{stateLabel[job.state]} · {job.completed}/{job.planned} · 源码提交：<code>{job.commit || "未知"}</code></p>
      {job.purpose === "adapter_validation" && <p className="scenario-note">这是适配器接入检查，验证原入口调用和协议；本题及用量不计入正式能力评测。</p>}
      {job.error && <p role="alert">执行错误：{job.error}</p>}
      <div className="assessment-table"><table><thead><tr><th>预算</th><th>通过</th><th>失败</th><th>未知</th><th>通过率</th><th>平均观测耗时</th><th>自报 Token</th></tr></thead>
        <tbody>{job.curves.map(c => <tr key={c.budget.id}><td>{c.budget.id}</td><td>{c.pass}</td><td>{c.fail}</td><td>{c.unknown}</td>
          <td>{c.pass_rate}%</td><td>{c.mean_duration_ms == null ? "未知" : `${(c.mean_duration_ms / 1000).toFixed(2)} 秒`}</td>
          <td><UsageValue usage={c.usage} field="total_tokens" fallback={c.reported_total_tokens} /></td></tr>)}</tbody></table></div>
      <p className="scenario-note">未知和未完成的案例保留在通过率分母中。Token：数字表示完整汇总，≥ 表示已观测下界；离线校准不适用，未上报保持未知。鼠标停留可查看来源与原因。</p>
      {job.quality && <section aria-label="维度覆盖与质量">
        <h3>维度覆盖与质量</h3>
        <p>案例并发上限 {job.concurrency ?? 1}；Token 完整 {job.quality.evidence.token_known_runs}/{job.quality.evidence.planned_runs} 次，
          部分 {job.quality.evidence.token_partial_runs ?? 0} 次，离线不适用 {job.quality.evidence.token_not_applicable_runs ?? 0} 次；
          必需检查未知 {job.quality.evidence.unknown_required_checks}/{job.quality.evidence.required_checks} 项。</p>
        <div className="assessment-table"><table><thead><tr><th>维度 / 预算</th><th>完成 / 计划</th><th>通过 / 失败 / 未知</th><th>通过率范围</th><th>p50 / p95 ms</th></tr></thead>
          <tbody>{job.quality.dimensions.map(d => <tr key={`${d.category}-${d.budget_id}`}>
            <td>{d.category} / {d.budget_id}</td><td>{d.observed} / {d.planned}</td><td>{d.pass} / {d.fail} / {d.unknown}</td>
            <td>{d.confirmed_pass_rate}%–{d.possible_pass_rate}%</td><td>{d.p50_duration_ms ?? "未知"} / {d.p95_duration_ms ?? "未知"}</td>
          </tr>)}</tbody></table></div>
        <details><summary>重复稳定性</summary><ul>{job.quality.stability.map(s => <li key={`${s.case_id}-${s.budget_id}`}>
          {s.case_id} / {s.budget_id}：{qualityLabel[s.status] || s.status}（{s.observed}/{s.planned}）</li>)}</ul></details>
        <details><summary>源码覆盖与漏测</summary><ul>{job.quality.source_coverage.map((s, i) => <li key={i}>
          {s.name}：{qualityLabel[s.status] || s.status} · <SourceLink source={s} /></li>)}</ul></details>
        <p className="scenario-note">通过率范围表示未知项的最好与最坏情况，不是统计置信区间或总体能力分数。重复少于两次无法判断稳定性。</p>
      </section>}
      <h3>模型与工具调用</h3>
      <p className="scenario-note">内部记录由目标自报。≥ 表示部分记录的可见下界；未知表示未采集。
        工具计数包含 final_answer；调用耗时之和可能包含并发重叠。点击“查看对话和验收”可查看逐次调用的参数、结果和状态。</p>
      <div className="assessment-table"><table aria-label="模型与工具调用统计"><thead><tr>
        <th>案例 / 预算 / 次数</th><th>输入 Token</th><th>输出 Token</th><th>总 Token</th>
        <th>模型调用</th><th>工具调用</th><th>模型错误 / 工具错误</th><th>模型耗时 / 工具耗时</th><th>覆盖范围</th>
      </tr></thead><tbody>{job.results.map(r => <tr key={r.run_id}>
        <td>{r.case_id} / {r.budget_id} / {r.attempt}</td><td><UsageValue usage={r.usage} field="input_tokens" /></td>
        <td><UsageValue usage={r.usage} field="output_tokens" /></td><td><UsageValue usage={r.usage} field="total_tokens" /></td>
        <td>{callCount(r.telemetry?.llm_calls, r.telemetry)}</td><td>{callCount(r.telemetry?.tool_calls, r.telemetry)}</td>
        <td>{callCount(r.telemetry?.llm_errors, r.telemetry)} / {callCount(r.telemetry?.tool_errors, r.telemetry)}</td>
        <td>{callTime(r.telemetry?.llm_duration_ms, r.telemetry)} / {callTime(r.telemetry?.tool_duration_ms, r.telemetry)}</td>
        <td>{r.telemetry?.coverage === "complete" ? "目标声明完整" : r.telemetry?.coverage === "partial" ? "部分" : "未采集"}</td>
      </tr>)}</tbody></table></div>
      {job.results.some(r => !!r.telemetry?.models.length) && <details><summary>按模型查看用量</summary>
        <ul>{job.results.flatMap(r => (r.telemetry?.models || []).map(m => <li key={`${r.run_id}-${m.model}`}>
          {r.case_id} / {r.budget_id} / {r.attempt} · {m.model} · {callCount(m.calls, r.telemetry)} 次 ·
          输入 <UsageValue usage={m.usage} field="input_tokens" fallback={m.tokens.input} /> / 输出 <UsageValue usage={m.usage} field="output_tokens" fallback={m.tokens.output} /> Token · {callTime(m.duration_ms, r.telemetry)}
        </li>))}</ul>
      </details>}
      <details><summary>Token 采集状态与原因</summary><ul>{job.results.flatMap(r => Object.entries(r.usage?.fields || {})
        .filter(([key]) => ["input_tokens", "output_tokens", "total_tokens", "cost_usd"].includes(key))
        .map(([key, field]) => <li key={`${r.run_id}-${key}`}>{r.case_id} / {r.budget_id} · {key}：
          <UsageValue usage={r.usage} field={key} /> · {field.source} · {field.reason}</li>))}</ul></details>
      <div className="assessment-table"><table><thead><tr><th>案例 / 预算 / 次数</th><th>验收</th><th>执行</th><th>源码线索</th><th>证据</th></tr></thead>
        <tbody>{job.results.map(r => <tr key={r.run_id}><td>{r.case_id} / {r.budget_id} / {r.attempt}</td><td>{outcomeLabel[r.outcome]}</td>
          <td>{r.execution_state}{r.error ? ` · ${r.error}` : ""}</td><td>{r.source_evidence.map((s, i) => <div key={i}><SourceLink source={s} /></div>)}</td>
          <td><a href={`#/runs/${r.run_id}`}>查看对话和验收</a></td></tr>)}</tbody></table></div>
      {!!job.claims.length && <details><summary>能力声明与测试对应</summary><ul>{job.claims.map(c => <li key={c.id}>
        {c.capability} · {claimLabel[c.assessment_status || "untested"]} · <SourceLink source={c} /></li>)}</ul></details>}
      <details><summary>评测范围与限制</summary><ul>{job.limitations.map((v, i) => <li key={i}>{v}</li>)}</ul></details>
    </section>}
    {jobs.length >= 2 && <section className="standalone-panel assessment-guide"><h2>版本回归比较</h2>
      <div className="assessment-controls">{[[left, setLeft, "基线评测"], [right, setRight, "新版评测"]].map(([value, setter, label]) =>
        <label key={label as string}>{label as string}<select aria-label={label as string} value={value as string} onChange={e => (setter as (value: string) => void)(e.target.value)}>
          <option value="">选择评测</option>{jobs.map(j => <option key={j.id} value={j.id}>{j.target_id} · {j.id.slice(-8)} · {stateLabel[j.state]}</option>)}</select></label>)}
        <button disabled={busy || !left || !right} onClick={() => action(async () => setComparison(await api("/assessment-comparisons", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ left_id: left, right_id: right }),
        })))}>比较版本</button>
      </div>{comparison && <div role="status">{!comparison.comparable ? <p>无法比较：{comparison.reasons.join("；")}</p>
        : comparison.changes.length ? <ul>{comparison.changes.map((c, i) => <li key={i}>{c.case_id}：{outcomeLabel[c.from]} → {outcomeLabel[c.to]}{c.regression ? " · 发现回归" : ""}</li>)}</ul>
          : <p>这些案例的验收结论没有变化。</p>}</div>}
    </section>}
  </>;
}
