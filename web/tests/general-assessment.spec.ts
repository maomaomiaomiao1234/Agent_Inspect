import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("general tasks expose scope, independent mismatches, filtering and a readable export", async ({ page, request }) => {
  const headers = process.env.REVIEW_SERVICE_TOKEN ? { Authorization: `Bearer ${process.env.REVIEW_SERVICE_TOKEN}` } : {};
  const targets = await request.get("/api/targets", { headers });
  test.skip(targets.status() !== 200 || !(await targets.json()).some((t: { id: string }) => t.id === "noop"),
    "Start scripts/serve_assessment_test.py first");
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  await page.getByLabel("被测 Agent").selectOption("noop");
  await page.getByLabel("测试模板").selectOption("general");
  await page.getByLabel("案例数量", { exact: true }).fill("12");
  await page.getByLabel("重复次数", { exact: true }).selectOption("2");
  await page.getByLabel("案例并发", { exact: true }).selectOption("3");
  await page.getByRole("button", { name: "生成测试文件", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("共 24 次案例执行、28 轮请求");
  await page.getByText("任务清单与评测范围", { exact: true }).click();
  await expect(page.getByText("识别冲突材料并引用双方依据", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始评测", exact: false }).click();
  await expect(page.getByText("已完成 · 24/24", { exact: false })).toBeVisible({ timeout: 30000 });
  const summary = page.getByRole("region", { name: "评测结论", exact: true });
  await expect(summary).toContainText("发现未通过的必需检查");
  await expect(summary).toContainText("控制示例");
  await expect(summary).toContainText("尚未覆盖（按需补测）：跨轮记忆、会话隔离、工具调用");
  await summary.screenshot({ path: "/private/tmp/agent-review-general-summary-desktop.png" });
  const review = page.getByRole("region", { name: "案例诊断", exact: true });
  await expect(review.getByRole("status")).toHaveText("显示 24/24 次结果");
  await review.getByLabel("搜索案例").fill("空结果");
  await expect(review.getByRole("status")).toHaveText("显示 2/24 次结果");
  const card = review.locator(".assessment-case").first();
  await card.locator("summary").first().click();
  await expect(card).toContainText("空结果边界");
  await expect(card.locator(".assessment-comparison").first()).toContainText("字段缺失");
  await expect(card.locator(".assessment-check:visible")).toHaveCount(2);
  await expect(card.locator(".assessment-comparison").first()).toContainText("[]");
  await expect(card.getByRole("link", { name: "打开完整对话与验收证据" })).toHaveAttribute("href", /^#\/runs\/run_/);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await summary.screenshot({ path: "/private/tmp/agent-review-general-summary-mobile.png" });
  await card.screenshot({ path: "/private/tmp/agent-review-general-case-mobile.png" });
  await review.getByLabel("筛选结果").selectOption("pass");
  await expect(review).toContainText("没有符合筛选条件的案例");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出评测报告", exact: true }).click();
  const download = await downloadPromise;
  const report = await readFile((await download.path())!, "utf-8");
  expect(report).toContain("## 结论与下一步");
  expect(report).toContain("未通过 24 次");
  expect(report).toContain("### 优先处理");
  expect(report).toContain("实际返回：");
  expect(report).toContain("字段缺失");
  expect(errors).toEqual([]);
});
