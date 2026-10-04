import { useEffect, useState } from "react";
import { api, DownloadLink } from "./api-client";
import { RepositoryAssessments } from "./repository-assessments";

type Target = { id: string; repository_configured: boolean; managed: boolean; demo: boolean };
type GeneratedSuite = { id: string; description: string; cases: { id: string; turns: unknown[] }[] };
type Source = { path: string; line: number; url?: string };
type Claim = Source & { id: string; capability: string; snippet: string; assessment_status?: string };
type Repository = { id: string; commit: string | null; snapshot_kind: string; files: unknown[]; claims: Claim[]; limitations: string[] };
type Job = {
  id: string; state: string; target_id: string; suite_id: string; planned: number; completed: number;
  commit: string | null; repository_id: string | null; error: string | null; demo: boolean;
  curves: { budget: { id: string }; pass: number; fail: number; unknown: number; pass_rate: number;
    mean_duration_ms: number | null; reported_total_tokens: number | null; reported_cost_usd: number | null }[];
  results: { case_id: string; budget_id: string; attempt: number; outcome: string; run_id: string;
    execution_state: string; error: string | null; source_evidence: Source[] }[];
  claims: Claim[]; limitations: string[];
};

const stateLabel: Record<string, string> = { queued: "排队中", running: "执行中", completed: "已完成",
  failed: "执行异常", cancelled: "已取消", interrupted: "服务重启中断" };
const outcomeLabel: Record<string, string> = { pass: "通过", fail: "失败", inconclusive: "证据不足" };
const claimLabel: Record<string, string> = { untested: "未测试", supported_for_cases: "这些案例支持声明",
  contradicted_by_cases: "有案例未满足声明", inconclusive: "证据不足" };

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
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [repository, setRepository] = useState<Repository | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
          <label>被测 Agent<select aria-label="被测 Agent" value={target} onChange={e => { setTarget(e.target.value); setRepository(null); }}>
            {targets.map(t => <option key={t.id} value={t.id}>{t.id}{t.demo ? "（控制示例）" : ""}{t.managed ? " · Docker" : ""}</option>)}
          </select></label>
          <label>固定题集 JSON<input aria-label="固定题集 JSON" type="file" accept=".json,application/json" onChange={async e => {
            const file = e.target.files?.[0]; setSuite(null); setSuiteName(""); setGenerated(null); setError("");
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
        </div>
        <h3>自动生成测试文件</h3>
        <div className="assessment-controls">
          <label>测试模板<select aria-label="测试模板" disabled={busy}>
            <option value="smolagents">smolagents 基础能力</option>
          </select></label>
          <label>案例数量<input aria-label="案例数量" type="number" min="1" max="30" step="1"
            value={caseCount} onChange={e => setCaseCount(Number(e.target.value))} /></label>
          <label>随机种子<input aria-label="随机种子" type="number" min="0" max="2147483647" step="1"
            value={seed} onChange={e => setSeed(Number(e.target.value))} /></label>
          <button disabled={busy || !Number.isInteger(caseCount) || caseCount < 1 || caseCount > 30
            || !Number.isInteger(seed) || seed < 0 || seed > 2147483647} onClick={() => action(async () => {
            const created = await api<GeneratedSuite>("/assessment-suites/generate", { method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ template: "smolagents", cases: caseCount, seed }) });
            setGenerated(created); setSuite(created); setSuiteName(`${created.id}.json`);
          })}>生成测试文件</button>
          {generated && <button onClick={() => {
            const url = URL.createObjectURL(new Blob([JSON.stringify(generated, null, 2) + "\n"], { type: "application/json" }));
            const link = document.createElement("a"); link.href = url; link.download = `${generated.id}.json`;
            link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}>下载测试 JSON</button>}
        </div>
        <p className="scenario-note">生成无需模型额度。同一种子和案例数量可复现题目；标准答案由程序计算。
          模板要求算术、保存代码、读取代码工具及示例的 JSON 输出约定。</p>
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
        <tbody>{jobs.map(j => <tr key={j.id}><td><button className="text-button" onClick={() => setSelected(j.id)}>{j.suite_id} / {j.target_id}{j.demo ? " · 示例" : ""}</button></td>
          <td>{stateLabel[j.state] || j.state}</td><td>{j.completed}/{j.planned}</td><td className="mono">{j.id.slice(-12)}</td></tr>)}</tbody>
      </table></div>}
    </section>
    {job && <section className="standalone-panel assessment-guide">
      <div className="document-result-heading"><h2>评测详情 · {job.target_id}</h2><div className="scenario-actions">
        {["queued", "running"].includes(job.state) && <button disabled={busy} onClick={() => action(async () => {
          await api(`/assessments/${job.id}/cancel`, { method: "POST" }); setRefresh(x => x + 1);
        })}>取消评测</button>}
        <DownloadLink path={`/assessments/${job.id}/export`}>导出评测报告</DownloadLink>
        <DownloadLink path={`/assessments/${job.id}/export?format=bundle`}>导出证据包</DownloadLink>
      </div></div>
      <p>{stateLabel[job.state]} · {job.completed}/{job.planned} · 源码提交：<code>{job.commit || "未知"}</code></p>
      {job.error && <p role="alert">执行错误：{job.error}</p>}
      <div className="assessment-table"><table><thead><tr><th>预算</th><th>通过</th><th>失败</th><th>未知</th><th>通过率</th><th>平均观测耗时</th><th>自报 Token</th></tr></thead>
        <tbody>{job.curves.map(c => <tr key={c.budget.id}><td>{c.budget.id}</td><td>{c.pass}</td><td>{c.fail}</td><td>{c.unknown}</td>
          <td>{c.pass_rate}%</td><td>{c.mean_duration_ms == null ? "未知" : `${(c.mean_duration_ms / 1000).toFixed(2)} 秒`}</td><td>{c.reported_total_tokens ?? "未知"}</td></tr>)}</tbody></table></div>
      <p className="scenario-note">未知和未完成的案例保留在通过率分母中。耗时包含评审端开销；Token 与费用没有目标自报时保持未知。</p>
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
