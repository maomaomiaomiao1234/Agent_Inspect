import { useEffect, useState } from "react";
import { api, DownloadLink } from "./api-client";
import { UsageValue, type Usage } from "./usage";

type Capabilities = { enabled: boolean; allowed_environment: string[];
  gateway?: { available: boolean; recipes: string[]; default: boolean };
  model?: { status: string; source: string; model: string | null; api_url: string | null; missing: string[]; reason: string };
  defaults?: { backend: string; cases: number; seed: number; deadline_seconds: number; max_output_tokens: number; attempts: number; concurrency: number };
  adaptation?: { enabled: boolean; model: string | null; language: string; max_repairs: number; default_repairs: number;
    max_output_tokens?: number | null; thinking?: "enabled" | "disabled" | null; timeout_seconds?: number };
  runtime?: { git_available: boolean; docker_available: boolean } };
type RepositoryJob = {
  id: string; state: string; stage: string; commit: string | null; image_id: string | null;
  assessment_id: string | null; error: string | null; failed_stage?: string; cleanup: string;
  recipe?: string; adaptation_rounds?: number; validation_ids?: string[];
  adaptation_usage?: { generation: Usage; validation: Usage };
  request: { repository_url: string; ref: string }; demo?: boolean;
  log_artifacts: { stage: string; artifact_id: string }[];
};
export type RepositoryReportIdentity = Pick<RepositoryJob, "assessment_id" | "validation_ids" | "request">;
const stages: Record<string, string> = { queued: "排队", fetching: "拉取源码", inspecting: "检查配置",
  adapting: "生成适配器", repairing: "修复适配器", validating: "验证原 Agent 接入",
  building: "构建镜像", assessing: "部署并评测", cleaning: "清理资源", finished: "结束" };
const states: Record<string, string> = { queued: "排队中", running: "执行中", completed: "已完成",
  failed: "失败", cancelled: "已取消", interrupted: "服务重启中断" };
const errors: Record<string, string> = {
  unsupported_repository_requires_manifest: "仓库尚未适配。请添加 agent-review.json 部署清单。",
  independent_suite_required: "请上传独立题集，或在清单中声明支持的测试模板。",
  required_environment_mapping_missing: "缺少清单要求的环境变量映射。",
  required_environment_unavailable: "服务端未设置所需环境变量。",
  invalid_repository_configuration: "仓库配置无效，请检查部署清单、Dockerfile 与文件路径。",
  command_failed: "该阶段执行失败，请下载日志查看原因。", command_timeout: "该阶段超时。",
  command_unavailable: "服务端缺少 Git 或 Docker。", service_restarted: "服务重启，任务已中断。",
  repository_symlink_not_supported: "构建输入包含符号链接；首版需要普通文件。",
  repository_size_limit: "仓库超过 200 MiB 或 20,000 文件限制。",
  adaptation_model_not_configured: "自动适配模型配置未就绪，请检查服务端 .env。",
  adaptation_requires_real_model: "未知仓库的自动适配需要真实模型模式。",
  adaptation_python_source_required: "未找到可供自动适配的 Python 源码。",
  adaptation_unsupported: "模型未能确认可靠的原 Agent 接入方式，具体原因见完整记录。",
  adaptation_invalid_entry: "生成方案未引用有效的原仓库入口。",
  adaptation_entry_not_in_context: "生成方案的入口没有对应的已提供源码证据。",
  adaptation_independent_caller_rejected: "生成代码试图独立调用模型，未通过原 Agent 接入检查。",
  adaptation_provider_http_error: "自动适配模型 API 返回错误，请检查配置。",
  adaptation_output_incomplete: "自动适配模型输出被截断或无效，可调整服务端生成 Token 上限。",
  native_entry_not_observed: "未观察到原仓库入口被调用。",
  gateway_requires_generated_python_adapter: "网关试点需要选择 Python 自动适配入口。",
  gateway_configuration_or_start_failed: "网关无法启动，请检查监听地址、容器访问地址及目标模型配置。",
  agent_execution_failed: "生成的适配器调用失败，诊断与已采集用量见完整记录。",
};

export function RepositoryAssessments({ onOpen, onReports }: { onOpen: (id: string) => void; onReports?: (reports: RepositoryReportIdentity[]) => void }) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("HEAD");
  const [recipe, setRecipe] = useState("auto");
  const [manifest, setManifest] = useState("agent-review.json");
  const [backend, setBackend] = useState("auto");
  const [environment, setEnvironment] = useState("");
  const [cases, setCases] = useState(12);
  const [seed, setSeed] = useState(42);
  const [deadline, setDeadline] = useState(60);
  const [maxOutputTokens, setMaxOutputTokens] = useState(2048);
  const [attempts, setAttempts] = useState(1);
  const [concurrency, setConcurrency] = useState(1);
  const [sourcePlanning, setSourcePlanning] = useState(false);
  const [autoAdapt, setAutoAdapt] = useState(true);
  const [modelGateway, setModelGateway] = useState(false);
  const [adaptationRepairs, setAdaptationRepairs] = useState(1);
  const [suite, setSuite] = useState<unknown>(null);
  const [suiteName, setSuiteName] = useState("");
  const [jobs, setJobs] = useState<RepositoryJob[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api<Capabilities>("/repository-builds").then(c => {
    setCapabilities(c);
    if (c.adaptation) setAdaptationRepairs(c.adaptation.default_repairs);
    if (c.defaults) {
      setCases(c.defaults.cases); setSeed(c.defaults.seed); setDeadline(c.defaults.deadline_seconds);
      setMaxOutputTokens(c.defaults.max_output_tokens); setAttempts(c.defaults.attempts); setConcurrency(c.defaults.concurrency);
    }
  }).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await api<RepositoryJob[]>("/repository-jobs");
        if (stopped) return;
        setJobs(data);
        onReports?.(data);
        if (data.some(j => ["queued", "running"].includes(j.state))) timer = setTimeout(load, 1500);
      } catch (e) { if (!stopped) setError((e as Error).message); }
    }
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh, onReports]);

  async function submit() {
    setBusy(true); setError("");
    try {
      const mappings: Record<string, string> = {};
      for (const line of environment.split(/\r?\n/).map(v => v.trim()).filter(Boolean)) {
        const match = line.match(/^([A-Z][A-Z0-9_]{0,99})=([A-Z][A-Z0-9_]{0,99})$/);
        if (!match || mappings[match[1]]) throw new Error("环境映射每行填写 NAME=HOST_ENV，只填写名称且不能重复。");
        if (!capabilities?.allowed_environment.includes(match[2])) throw new Error("宿主变量未获管理员允许。");
        mappings[match[1]] = match[2];
      }
      await api<RepositoryJob>("/repository-jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repository_url: url.trim(), ref, recipe, manifest_path: manifest, backend,
          environment: mappings, suite, planning: sourcePlanning && !suite ? { cases, seed, attempts, concurrency } : null,
          adaptation: { enabled: autoAdapt, max_repairs: adaptationRepairs },
          ...(modelGateway ? { model_gateway: true } : {}),
          settings: suite ? null : { deadline_seconds: deadline, max_output_tokens: maxOutputTokens, attempts, concurrency },
          generation: { template: "smolagents", cases, seed } }) });
      setRefresh(v => v + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const effectiveBackend = backend === "auto" ? capabilities?.defaults?.backend || "openai" : backend;
  useEffect(() => { if (effectiveBackend !== "openai") setModelGateway(false); }, [effectiveBackend]);
  const modelMissing = effectiveBackend === "openai" && capabilities?.model?.status !== "configured" && !environment.trim();
  const runtimeMissing = capabilities?.runtime && (!capabilities.runtime.git_available || !capabilities.runtime.docker_available);
  const settingsInvalid = !suite && (!Number.isFinite(deadline) || deadline < 0.05 || deadline > 60
    || !Number.isInteger(maxOutputTokens) || maxOutputTokens < 1 || maxOutputTokens > 100000 || cases * attempts * deadline > 900);

  return <section className="standalone-panel assessment-guide" aria-label="从仓库评测">
    <h2>从仓库自动部署并评测</h2>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {capabilities && !capabilities.enabled ? <p>仓库构建已关闭。可在服务端 .env 设置
      <code> AGENT_REVIEW_ENABLE_REPOSITORY_BUILDS=true</code> 并重启服务。</p> : capabilities?.enabled && <>
      <p>当前评测模式：{effectiveBackend === "offline" ? "离线校准，未调用模型服务，Token 统计不适用。" : "真实大模型"}
        {effectiveBackend === "openai" && capabilities.model?.status === "configured" && <>
          {` · ${capabilities.model.model} · ${capabilities.model.api_url}；配置来自服务端 .env，凭据已自动接入。`}
          {capabilities.model.source === "review_shared" && "被测 Agent 使用与评审模型相同的配置。"}
        </>}
      </p>
      {modelMissing && <p role="alert">{capabilities.model?.reason || "尚未配置被测模型，请检查服务端 .env 并重启服务。"}
        {!!capabilities.model?.missing.length && <>缺少：{capabilities.model.missing.join("、")}。</>}</p>}
      {runtimeMissing && <p role="alert">服务端需要安装 Git 和 Docker，并启动 Docker。</p>}
      <div className="assessment-controls">
        <label className="repository-url">GitHub 仓库地址<input aria-label="GitHub 仓库地址" type="url" value={url}
          placeholder="https://github.com/huggingface/smolagents" onChange={e => setUrl(e.target.value)} /></label>
        <label>分支、标签或提交<input aria-label="分支、标签或提交" value={ref} onChange={e => setRef(e.target.value)} /></label>
        <button className="primary" disabled={busy || !!modelMissing || !!runtimeMissing || settingsInvalid || !url.trim() || !ref || !Number.isInteger(cases) || cases < 1 || cases > 30
          || !Number.isInteger(seed) || seed < 0 || seed > 2147483647} onClick={submit}>{busy ? "正在提交…" : "拉取、部署并评测"}</button>
      </div>
      {!suite && <p className="scenario-note">预计 {cases * attempts} 个案例运行；累计期限 {cases * attempts * deadline} 秒（上限 900 秒）。
        多轮、记忆和会话隔离案例串行运行。</p>}
      <div className="assessment-controls">
        <label>评测模式<select aria-label="评测模式" value={backend} onChange={e => setBackend(e.target.value)}>
          <option value="auto">使用 .env 默认模式</option><option value="openai">真实大模型</option><option value="offline">离线校准</option>
        </select></label>
        <label>生成案例数<input aria-label="仓库案例数" type="number" min="1" max="30" value={cases} onChange={e => setCases(Number(e.target.value))} /></label>
        <label>生成种子<input aria-label="仓库生成种子" type="number" min="0" max="2147483647" value={seed} onChange={e => setSeed(Number(e.target.value))} /></label>
        <label>每案例期限（秒）<input aria-label="每案例期限" disabled={!!suite} type="number" min="0.05" max="60" step="0.05"
          value={deadline} onChange={e => setDeadline(Number(e.target.value))} /></label>
        <label>每案例输出 Token 上限<input aria-label="每案例输出 Token 上限" disabled={!!suite} type="number" min="1" max="100000"
          value={maxOutputTokens} onChange={e => setMaxOutputTokens(Number(e.target.value))} /></label>
        <label>重复次数<select aria-label="仓库重复次数" disabled={!!suite} value={attempts} onChange={e => setAttempts(Number(e.target.value))}>
          {[1, 2, 3].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>案例并发<select aria-label="仓库案例并发" disabled={!!suite} value={concurrency} onChange={e => setConcurrency(Number(e.target.value))}>
          {[1, 2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
      </div>
      <details><summary>配置构建与题集</summary>
        {capabilities.gateway?.available && <label><input type="checkbox" checked={modelGateway}
          disabled={effectiveBackend !== "openai"} onChange={e => { setModelGateway(e.target.checked); if (e.target.checked) setRecipe("llm"); }} />
          通过模型网关保留用量（Python 自动适配试点）</label>}
        {modelGateway && <p>模型请求在评审端保存用量，目标中断后仍可查看。仅覆盖经过网关的调用；启用后使用 Python 自动适配。</p>}
        <div className="assessment-controls">
          <label>接入方式<select aria-label="接入方式" value={recipe} onChange={e => setRecipe(e.target.value)}>
            <option value="auto">自动识别</option><option value="manifest">仓库部署清单</option><option value="smolagents">smolagents</option>
            <option value="llm">LLM 自动适配 Python 仓库</option>
          </select></label>
          <label>部署清单路径<input value={manifest} onChange={e => setManifest(e.target.value)} /></label>
          <label>题集策略<select aria-label="题集策略" value={sourcePlanning ? "source" : "template"}
            onChange={e => setSourcePlanning(e.target.value === "source")}>
            <option value="template">使用仓库声明的模板</option><option value="source">根据源码规划受控测试</option>
          </select></label>
          <label>独立题集（可选）<input aria-label="仓库独立题集" type="file" accept=".json,application/json" onChange={async e => {
            const file = e.target.files?.[0]; setSuite(null); setSuiteName(""); setError("");
            if (!file) return;
            try {
              if (file.size > 1024 * 1024) throw new Error("题集超过 1 MB。");
              setSuite(JSON.parse(await file.text())); setSuiteName(file.name);
            } catch (e) { setError((e as Error).message); }
          }} /></label>
        </div>
        <div className="assessment-controls">
          <label><input aria-label="未知仓库自动适配" type="checkbox" checked={autoAdapt} onChange={e => setAutoAdapt(e.target.checked)} />未知 Python 仓库自动适配</label>
          <label>适配修复次数<select aria-label="适配修复次数" value={adaptationRepairs} onChange={e => setAdaptationRepairs(Number(e.target.value))}>
            {[0, 1, 2].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        </div>
        {(autoAdapt || recipe === "llm") && <p className="scenario-note">自动适配会将相关源码片段发送到服务端配置的模型，生成文件仅在容器中运行。
          {capabilities.adaptation?.enabled ? `适配模型：${capabilities.adaptation.model}。生成输出 Token 上限：${capabilities.adaptation.max_output_tokens ?? "供应商默认"}；思考模式：${capabilities.adaptation.thinking === "enabled" ? "已开启" : capabilities.adaptation.thinking === "disabled" ? "已关闭" : "供应商默认"}。` : "未配置可用适配模型，未知仓库需要补齐 .env 或提供部署清单。"}
          先独立验证原 Agent 入口，再运行题集；生成和接入验证 Token 单独记录。未上传题集时使用源码规划的受控探测。</p>}
        <p>{suiteName ? `将使用：${suiteName}，保留文件中的预算、重复次数与并发配置。` : sourcePlanning
          ? "将根据固定提交生成题集和漏测清单。目标须支持题目的 JSON 输出；离线 smolagents 校准模型不支持通用题目。"
          : "未上传题集时，smolagents 或清单声明的 smolagents 模板由评审端独立生成答案。"}</p>
        <details><summary>其他框架的环境变量接入（可选）</summary>
          <label className="repository-environment">运行期环境变量映射<textarea aria-label="运行期环境变量映射" rows={3} value={environment}
            placeholder="CUSTOM_MODEL_KEY=MY_MODEL_KEY" onChange={e => setEnvironment(e.target.value)} /></label>
          <p>模型变量自动从服务端配置接入，无需填写。其他变量仅填写名称；允许的宿主变量：{capabilities.allowed_environment.join("、") || "未配置"}。</p>
        </details>
      </details>
      <p className="scenario-note">首次构建会下载依赖。离线模式用于校准，不代表模型能力；真实模型模式会使用已配置的模型额度。</p>
    </>}
    {!!jobs.length && <div className="assessment-table"><table>
      <thead><tr><th>仓库 / 版本</th><th>进度</th><th>结果和证据</th></tr></thead>
      <tbody>{jobs.map(job => <tr key={job.id}>
        <td>{job.request.repository_url.replace("https://github.com/", "")}<br /><code>{job.commit?.slice(0, 12) || job.request.ref}</code>
          {job.demo && <span> · 校准示例</span>}</td>
        <td>{states[job.state] || job.state} · {stages[job.stage] || job.stage}
          {job.adaptation_rounds != null && <p>自动适配 {job.adaptation_rounds} 轮 · 接入验证 {job.validation_ids?.length || 0} 次</p>}
          {job.adaptation_usage && <p>生成 Token：<UsageValue usage={job.adaptation_usage.generation} field="total_tokens" />
            {" · 接入验证 Token："}<UsageValue usage={job.adaptation_usage.validation} field="total_tokens" /></p>}
          {job.error && <p role="alert">{job.failed_stage ? `${stages[job.failed_stage] || job.failed_stage}：` : ""}{errors[job.error] || job.error}</p>}
          {job.cleanup === "failed" && <p role="alert">资源清理失败，需管理员重试清理。</p>}</td>
        <td><div className="scenario-actions">
          {job.assessment_id && <button onClick={() => onOpen(job.assessment_id!)}>查看评测结果</button>}
          {job.validation_ids?.map((id, index) => <button key={id} onClick={() => onOpen(id)}>查看接入检查 {index + 1}</button>)}
          <DownloadLink path={`/repository-jobs/${job.id}/export`}>下载完整记录</DownloadLink>
          {job.recipe === "llm" && <DownloadLink path={`/repository-jobs/${job.id}/adapter-files`}>下载适配文件</DownloadLink>}
          {["queued", "running"].includes(job.state) && <button disabled={busy} onClick={async () => {
            setBusy(true); setError("");
            try { await api(`/repository-jobs/${job.id}/cancel`, { method: "POST" }); setRefresh(v => v + 1); }
            catch (e) { setError((e as Error).message); }
            finally { setBusy(false); }
          }}>取消仓库任务</button>}
        </div><details><summary>阶段日志（{job.log_artifacts.length}）</summary>
          {job.log_artifacts.map((log, i) => <div key={i}><DownloadLink path={`/artifacts/${log.artifact_id}`}>{stages[log.stage] || log.stage} · 日志 {i + 1}</DownloadLink></div>)}
        </details></td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
