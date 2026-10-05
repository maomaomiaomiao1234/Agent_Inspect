import { test, expect } from "@playwright/test";

const capabilities = { enabled: true, allowed_environment: [],
  adaptation: { enabled: true, model: "fixture-generator", language: "python", max_repairs: 2, default_repairs: 1 },
  model: { status: "configured", source: "target", model: "fixture-model", api_url: "https://provider.example/v1", missing: [], reason: "模型配置就绪" },
  defaults: { backend: "openai", cases: 12, seed: 42, deadline_seconds: 60, max_output_tokens: 2048, attempts: 1, concurrency: 1 } };

test("repository form submits configuration, shows stages and exports evidence", async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  let started = false;
  const job = {
    id: "repository_job_11111111111111111111111111111111", state: "completed", stage: "finished",
    request: { repository_url: "https://github.com/huggingface/smolagents", ref: "v1.26.0" },
    commit: "12c1bc820eca50ace6f80a21d90426d41d74f845", assessment_id: null, image_id: "sha256:fixture",
    cleanup: "completed", demo: true, error: null, log_artifacts: [],
  };
  await page.route("**/api/repository-builds", route => route.fulfill({ json: capabilities }));
  await page.route("**/api/repository-jobs", async route => {
    if (route.request().method() === "POST") {
      submissions.push(route.request().postDataJSON()); started = true;
      await route.fulfill({ status: 202, json: { ...job, state: "queued", stage: "queued" } });
    } else await route.fulfill({ json: started ? [job] : [] });
  });
  await page.route(`**/api/repository-jobs/${job.id}/export`, route => route.fulfill({
    headers: { "Content-Disposition": `attachment; filename="${job.id}.json"` }, json: { job },
  }));
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  const section = page.getByRole("region", { name: "从仓库评测" });
  await section.getByLabel("GitHub 仓库地址").fill(job.request.repository_url);
  await section.getByLabel("分支、标签或提交").fill("v1.26.0");
  await section.getByLabel("仓库案例数").fill("2");
  await section.getByLabel("每案例期限", { exact: true }).fill("20");
  await section.getByLabel("每案例输出 Token 上限").fill("1234");
  await section.getByLabel("仓库重复次数").selectOption("2");
  await section.getByLabel("仓库案例并发").selectOption("2");
  await section.getByRole("button", { name: "拉取、部署并评测", exact: true }).click();
  await expect(section).toContainText("已完成 · 结束");
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ repository_url: job.request.repository_url, ref: "v1.26.0", recipe: "auto", environment: {}, backend: "auto", suite: null,
    settings: { deadline_seconds: 20, max_output_tokens: 1234, attempts: 2, concurrency: 2 } });
  const downloading = page.waitForEvent("download");
  await section.getByRole("link", { name: "下载完整记录" }).click();
  expect((await downloading).suggestedFilename()).toBe(`${job.id}.json`);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(section.getByLabel("GitHub 仓库地址")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: "/private/tmp/agent-review-repository-mobile.png", fullPage: true });
});

test("repository failure identifies unsupported repository and keeps an export", async ({ page }) => {
  await page.route("**/api/repository-builds", route => route.fulfill({ json: capabilities }));
  await page.route("**/api/repository-jobs", route => route.fulfill({ json: [{
    id: "repository_job_22222222222222222222222222222222", state: "failed", stage: "finished", failed_stage: "inspecting",
    request: { repository_url: "https://github.com/example/unsupported", ref: "HEAD" }, commit: null,
    assessment_id: null, image_id: null, cleanup: "completed", error: "unsupported_repository_requires_manifest", log_artifacts: [],
  }] }));
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  const section = page.getByRole("region", { name: "从仓库评测" });
  await expect(section.getByRole("alert")).toContainText("检查配置：仓库尚未适配");
  await expect(section.getByRole("link", { name: "下载完整记录" })).toBeVisible();
});

test("repository URL alone uses env model configuration and no manual mappings", async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  await page.route("**/api/repository-builds", route => route.fulfill({ json: capabilities }));
  await page.route("**/api/repository-jobs", route => {
    if (route.request().method() === "POST") {
      submissions.push(route.request().postDataJSON());
      return route.fulfill({ status: 202, json: { id: "fixture-submitted" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/#/assessments");
  const section = page.getByRole("region", { name: "从仓库评测" });
  await expect(section).toContainText("真实大模型 · fixture-model");
  await expect(section).toContainText("凭据已自动接入");
  await section.getByLabel("GitHub 仓库地址").fill("https://github.com/huggingface/smolagents");
  await section.getByRole("button", { name: "拉取、部署并评测", exact: true }).click();
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ backend: "auto", environment: {}, generation: { cases: 12, seed: 42 },
    settings: { deadline_seconds: 60, max_output_tokens: 2048, attempts: 1, concurrency: 1 } });
});

test("missing env configuration blocks real model assessment and cumulative limits are visible", async ({ page }) => {
  await page.route("**/api/repository-builds", route => route.fulfill({ json: { ...capabilities,
    model: { status: "missing", source: "target", missing: ["AGENT_REVIEW_TARGET_TOKEN"], reason: "请配置 .env 并重启服务。" } } }));
  await page.route("**/api/repository-jobs", route => route.fulfill({ json: [] }));
  await page.goto("/#/assessments");
  const section = page.getByRole("region", { name: "从仓库评测" });
  await section.getByLabel("GitHub 仓库地址").fill("https://github.com/huggingface/smolagents");
  await expect(section.getByRole("alert")).toContainText("AGENT_REVIEW_TARGET_TOKEN");
  const button = section.getByRole("button", { name: "拉取、部署并评测", exact: true });
  await expect(button).toBeDisabled();
  await section.getByLabel("评测模式", { exact: true }).selectOption("offline");
  await expect(button).toBeEnabled();
  await section.getByLabel("仓库案例数").fill("30");
  await expect(section).toContainText("累计期限 1800 秒（上限 900 秒）");
  await expect(button).toBeDisabled();
});

test("unknown Python repository enables generation, repair settings and separate usage evidence", async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  let submitted = false;
  const job = {
    id: "repository_job_33333333333333333333333333333333", state: "completed", stage: "finished", recipe: "llm",
    request: { repository_url: "https://github.com/example/native-agent", ref: "HEAD" },
    commit: "a".repeat(40), assessment_id: "formal-assessment", image_id: "sha256:fixture",
    cleanup: "completed", error: null, log_artifacts: [], adaptation_rounds: 2, validation_ids: ["adapter-check"],
    adaptation_usage: { generation: { fields: { total_tokens: { value: 500, status: "complete", source: "aggregate", reason: "生成用量" } } },
      validation: { fields: { total_tokens: { value: 15, status: "partial", source: "calls", reason: "已知下界" } } } },
  };
  await page.route("**/api/repository-builds", route => route.fulfill({ json: capabilities }));
  await page.route("**/api/repository-jobs", route => {
    if (route.request().method() === "POST") {
      submissions.push(route.request().postDataJSON()); submitted = true;
      return route.fulfill({ status: 202, json: job });
    }
    return route.fulfill({ json: submitted ? [job] : [] });
  });
  await page.route(`**/api/repository-jobs/${job.id}/adapter-files`, route => route.fulfill({
    contentType: "application/zip", headers: { "Content-Disposition": 'attachment; filename="generated-adapter.zip"' }, body: "fixture archive",
  }));
  await page.goto("/#/assessments");
  const section = page.getByRole("region", { name: "从仓库评测" });
  await section.getByLabel("GitHub 仓库地址").fill(job.request.repository_url);
  await section.getByText("配置构建与题集", { exact: true }).click();
  await expect(section.getByLabel("未知仓库自动适配")).toBeChecked();
  await section.getByLabel("适配修复次数").selectOption("2");
  await expect(section).toContainText("适配模型：fixture-generator");
  await section.getByRole("button", { name: "拉取、部署并评测", exact: true }).click();
  expect(submissions[0]).toMatchObject({ recipe: "auto", backend: "auto", environment: {}, adaptation: { enabled: true, max_repairs: 2 } });
  await expect(section).toContainText("自动适配 2 轮 · 接入验证 1 次");
  await expect(section).toContainText("生成 Token：500 · 接入验证 Token：≥15");
  await expect(section.getByRole("button", { name: "查看接入检查 1" })).toBeVisible();
  const downloading = page.waitForEvent("download");
  await section.getByRole("link", { name: "下载适配文件" }).click();
  expect((await downloading).suggestedFilename()).toBe("generated-adapter.zip");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
});
