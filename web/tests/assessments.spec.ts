import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("active HTTP assessment, fixed suite, evidence and export", async ({ page, request }) => {
  const headers = process.env.REVIEW_SERVICE_TOKEN ? { Authorization: `Bearer ${process.env.REVIEW_SERVICE_TOKEN}` } : {};
  const targets = await request.get("/api/targets", { headers });
  test.skip(targets.status() !== 200 || !(await targets.json()).some((t: { id: string }) => t.id === "control"), "Start scripts/serve_assessment_test.py first");
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await expect(page.getByRole("heading", { name: "连接评审服务" })).toBeVisible();
    await page.getByLabel("服务访问令牌").fill("wrong-token");
    await page.getByRole("button", { name: "连接服务" }).click();
    await expect(page.getByRole("alert")).toContainText("需要服务访问令牌");
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  await expect(page.getByRole("heading", { name: "新建评测", exact: true })).toBeVisible();
  await page.getByLabel("被测 Agent").selectOption("control");
  await page.getByLabel("固定题集 JSON").setInputFiles(resolve("../examples/assessment/suite.json"));
  await page.getByRole("button", { name: "开始评测", exact: false }).click();
  await expect(page.getByRole("heading", { name: "评测详情 · control" })).toBeVisible();
  const details = page.locator("section").filter({ has: page.getByRole("heading", { name: "评测详情 · control" }) });
  await expect(details).toContainText("已完成 · 16/16", { timeout: 30000 });
  await expect(details).toContainText("100%");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出评测报告", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/assessment_.*\.md$/);
  await page.getByRole("link", { name: "查看对话和验收", exact: true }).first().click();
  await expect(page.locator(".outcome-bar.pass")).toBeVisible();
  await page.getByRole("button", { name: "任务验收", exact: true }).click();
  await expect(page.locator(".verification-list")).toContainText("answer-correct");
  await page.getByRole("link", { name: "主动评测", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "http-controls / control · 示例", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "评测详情 · control" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: "/private/tmp/agent-review-assessment-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
