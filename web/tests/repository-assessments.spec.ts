import { test, expect } from "@playwright/test";

test("repository form submits configuration, shows stages and exports evidence", async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  let started = false;
  const job = {
    id: "repository_job_11111111111111111111111111111111", state: "completed", stage: "finished",
    request: { repository_url: "https://github.com/huggingface/smolagents", ref: "v1.26.0" },
    commit: "12c1bc820eca50ace6f80a21d90426d41d74f845", assessment_id: null, image_id: "sha256:fixture",
    cleanup: "completed", demo: true, error: null, log_artifacts: [],
  };
  await page.route("**/api/repository-builds", route => route.fulfill({ json: { enabled: true, allowed_environment: [] } }));
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
  await section.getByRole("button", { name: "拉取、部署并评测", exact: true }).click();
  await expect(section).toContainText("已完成 · 结束");
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ repository_url: job.request.repository_url, ref: "v1.26.0", recipe: "auto", environment: {}, backend: "offline", suite: null });
  const downloading = page.waitForEvent("download");
  await section.getByRole("link", { name: "下载完整记录" }).click();
  expect((await downloading).suggestedFilename()).toBe(`${job.id}.json`);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(section.getByLabel("GitHub 仓库地址")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: "/private/tmp/agent-review-repository-mobile.png", fullPage: true });
});

test("repository failure identifies unsupported repository and keeps an export", async ({ page }) => {
  await page.route("**/api/repository-builds", route => route.fulfill({ json: { enabled: true, allowed_environment: [] } }));
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
