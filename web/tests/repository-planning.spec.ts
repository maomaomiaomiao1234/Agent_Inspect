import { test, expect } from "@playwright/test";

test("source plan generates, runs, reports quality and preserves frozen suite", async ({ page, request }) => {
  const headers = process.env.REVIEW_SERVICE_TOKEN ? { Authorization: `Bearer ${process.env.REVIEW_SERVICE_TOKEN}` } : {};
  const response = await request.get("/api/targets", { headers });
  test.skip(response.status() !== 200 || !(await response.json()).some((t: { id: string }) => t.id === "source-planning-control"),
    "Start scripts/serve_assessment_test.py first");
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  await page.getByLabel("被测 Agent").selectOption("source-planning-control");
  await page.getByLabel("测试模板").selectOption("repository");
  await page.getByLabel("案例数量", { exact: true }).fill("8");
  await page.getByLabel("重复次数").selectOption("2");
  await page.getByLabel("案例并发").selectOption("3");
  await page.getByRole("button", { name: "生成测试文件", exact: true }).click();
  const plan = page.getByRole("region", { name: "源码评测计划" });
  await expect(plan).toContainText("18 轮请求");
  await expect(plan).toContainText("跨轮记忆");
  await plan.getByText("待补测项", { exact: false }).click();
  await expect(plan).toContainText("search");
  await page.getByRole("button", { name: "开始评测", exact: false }).click();
  await expect(page.getByRole("heading", { name: "评测详情 · source-planning-control" })).toBeVisible();
  await expect(page.getByText("已完成 · 16/16", { exact: false })).toBeVisible({ timeout: 30000 });
  const quality = page.getByRole("region", { name: "维度覆盖与质量" });
  await expect(quality).toContainText("案例并发上限 3");
  await expect(quality).toContainText("Token 已知 0/16");
  await quality.getByText("重复稳定性", { exact: true }).click();
  await expect(quality).toContainText("重复失败");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  // Changing targets must not silently reuse a source-bound suite.
  await page.getByLabel("被测 Agent").selectOption("control");
  await expect(page.getByRole("button", { name: "开始评测", exact: true })).toBeDisabled();
});
