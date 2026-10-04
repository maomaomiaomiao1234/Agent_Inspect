import { useEffect, useState } from "react";
import { api, DownloadLink } from "./api-client";

type Capabilities = { enabled: boolean; allowed_environment: string[] };
type RepositoryJob = {
  id: string; state: string; stage: string; commit: string | null; image_id: string | null;
  assessment_id: string | null; error: string | null; failed_stage?: string; cleanup: string;
  request: { repository_url: string; ref: string }; demo?: boolean;
  log_artifacts: { stage: string; artifact_id: string }[];
};
const stages: Record<string, string> = { queued: "排队", fetching: "拉取源码", inspecting: "检查配置",
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
};

export function RepositoryAssessments({ onOpen }: { onOpen: (id: string) => void }) {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("HEAD");
  const [recipe, setRecipe] = useState("auto");
  const [manifest, setManifest] = useState("agent-review.json");
  const [backend, setBackend] = useState("offline");
  const [environment, setEnvironment] = useState("");
  const [cases, setCases] = useState(12);
  const [seed, setSeed] = useState(42);
  const [suite, setSuite] = useState<unknown>(null);
  const [suiteName, setSuiteName] = useState("");
  const [jobs, setJobs] = useState<RepositoryJob[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api<Capabilities>("/repository-builds").then(setCapabilities).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await api<RepositoryJob[]>("/repository-jobs");
        if (stopped) return;
        setJobs(data);
        if (data.some(j => ["queued", "running"].includes(j.state))) timer = setTimeout(load, 1500);
      } catch (e) { if (!stopped) setError((e as Error).message); }
    }
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [refresh]);

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
          environment: mappings, suite, generation: { template: "smolagents", cases, seed } }) });
      setRefresh(v => v + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <section className="standalone-panel assessment-guide" aria-label="从仓库评测">
    <h2>从仓库自动部署并评测</h2>
    <p>提交公开 GitHub 仓库地址，自动拉取固定版本、构建、启动、评测并回收容器。
      支持带部署清单的仓库，或自动识别 Hugging Face smolagents。</p>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {capabilities && !capabilities.enabled ? <p>此服务尚未启用仓库构建。管理员可在装有 Docker 的宿主机上以
      <code> --enable-repository-builds</code> 启动评审服务。</p> : capabilities?.enabled && <>
      <div className="assessment-controls">
        <label className="repository-url">GitHub 仓库地址<input aria-label="GitHub 仓库地址" type="url" value={url}
          placeholder="https://github.com/huggingface/smolagents" onChange={e => setUrl(e.target.value)} /></label>
        <label>分支、标签或提交<input aria-label="分支、标签或提交" value={ref} onChange={e => setRef(e.target.value)} /></label>
        <button className="primary" disabled={busy || !url.trim() || !ref || !Number.isInteger(cases) || cases < 1 || cases > 30
          || !Number.isInteger(seed) || seed < 0 || seed > 2147483647} onClick={submit}>{busy ? "正在提交…" : "拉取、部署并评测"}</button>
      </div>
      <details><summary>配置构建与题集</summary>
        <div className="assessment-controls">
          <label>接入方式<select aria-label="接入方式" value={recipe} onChange={e => setRecipe(e.target.value)}>
            <option value="auto">自动识别</option><option value="manifest">仓库部署清单</option><option value="smolagents">smolagents</option>
          </select></label>
          <label>部署清单路径<input value={manifest} onChange={e => setManifest(e.target.value)} /></label>
          <label>smolagents 模式<select aria-label="smolagents 模式" value={backend} onChange={e => setBackend(e.target.value)}>
            <option value="offline">离线校准（不调用模型）</option><option value="openai">真实模型（需配置环境变量）</option>
          </select></label>
          <label>生成案例数<input type="number" min="1" max="30" value={cases} onChange={e => setCases(Number(e.target.value))} /></label>
          <label>生成种子<input type="number" min="0" max="2147483647" value={seed} onChange={e => setSeed(Number(e.target.value))} /></label>
          <label>独立题集（可选）<input aria-label="仓库独立题集" type="file" accept=".json,application/json" onChange={async e => {
            const file = e.target.files?.[0]; setSuite(null); setSuiteName(""); setError("");
            if (!file) return;
            try {
              if (file.size > 1024 * 1024) throw new Error("题集超过 1 MB。");
              setSuite(JSON.parse(await file.text())); setSuiteName(file.name);
            } catch (e) { setError((e as Error).message); }
          }} /></label>
        </div>
        <p>{suiteName ? `将使用：${suiteName}` : "未上传题集时，smolagents 或清单声明的 smolagents 模板由评审端独立生成答案。"}</p>
        <label className="repository-environment">运行期环境变量映射<textarea aria-label="运行期环境变量映射" rows={3} value={environment}
          placeholder="SMOL_MODEL_API_KEY=MY_MODEL_KEY" onChange={e => setEnvironment(e.target.value)} /></label>
        <p>仅填写名称，密钥保留在服务端。允许的宿主变量：{capabilities.allowed_environment.join("、") || "未配置"}。</p>
      </details>
      <p className="scenario-note">首次构建会下载依赖。离线模式用于校准，不代表模型能力；真实模型模式会使用已配置的模型额度。</p>
    </>}
    {!!jobs.length && <div className="assessment-table"><table>
      <thead><tr><th>仓库 / 版本</th><th>进度</th><th>结果和证据</th></tr></thead>
      <tbody>{jobs.map(job => <tr key={job.id}>
        <td>{job.request.repository_url.replace("https://github.com/", "")}<br /><code>{job.commit?.slice(0, 12) || job.request.ref}</code>
          {job.demo && <span> · 校准示例</span>}</td>
        <td>{states[job.state] || job.state} · {stages[job.stage] || job.stage}
          {job.error && <p role="alert">{job.failed_stage ? `${stages[job.failed_stage] || job.failed_stage}：` : ""}{errors[job.error] || job.error}</p>}
          {job.cleanup === "failed" && <p role="alert">资源清理失败，需管理员重试清理。</p>}</td>
        <td><div className="scenario-actions">
          {job.assessment_id && <button onClick={() => onOpen(job.assessment_id!)}>查看评测结果</button>}
          <DownloadLink path={`/repository-jobs/${job.id}/export`}>下载完整记录</DownloadLink>
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
