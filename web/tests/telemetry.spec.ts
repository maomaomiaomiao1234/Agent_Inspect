import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile } from "node:fs/promises";

test("actual smolagents calls appear in assessment, evidence export and timeline", async ({ page, request }) => {
  const headers = process.env.REVIEW_SERVICE_TOKEN ? { Authorization: `Bearer ${process.env.REVIEW_SERVICE_TOKEN}` } : {};
  const targets = await request.get("/api/targets", { headers });
  test.skip(targets.status() !== 200 || !(await targets.json()).some((t: { id: string }) => t.id === "smolagents-offline"),
    "Start scripts/serve_assessment_test.py with --telemetry-target first");
  await page.goto("/#/assessments");
  if (process.env.REVIEW_SERVICE_TOKEN) {
    await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
    await page.getByRole("button", { name: "连接服务" }).click();
  }
  await page.getByLabel("被测 Agent").selectOption("smolagents-offline");
  await page.getByLabel("固定题集 JSON").setInputFiles(resolve("../examples/smolagents/suite.smoke.json"));
  await page.getByRole("button", { name: "开始评测", exact: false }).click();
  const details = page.locator("section").filter({ has: page.getByRole("heading", { name: "评测详情 · smolagents-offline" }) });
  await expect(details).toContainText("已完成 · 1/1", { timeout: 30000 });
  await page.getByText("模型与工具调用 · 资源明细", { exact: true }).click();
  const stats = page.getByRole("table", { name: "模型与工具调用统计" });
  await expect(stats).toContainText("目标声明完整");
  const cells = stats.locator("tbody tr").first().locator("td");
  await expect(cells.nth(1)).toHaveText("不适用（离线校准）");
  await expect(cells.nth(4)).toHaveText("2");
  await expect(cells.nth(5)).toHaveText("2");
  await expect(cells.nth(6)).toHaveText("0 / 0");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出证据包", exact: true }).click();
  const file = await download;
  const bundle = JSON.parse(await readFile((await file.path())!, "utf-8"));
  expect(bundle.job.results[0].telemetry.llm_calls).toBe(2);
  expect(bundle.runs[0].bundle.trace.events.filter((e: { kind: string }) => e.kind === "tool")).toHaveLength(2);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: "/private/tmp/agent-inspect-telemetry-mobile.png", fullPage: true });
  await page.getByRole("link", { name: "查看对话和验收", exact: true }).first().click();
  await page.locator(".event-row").filter({ has: page.locator("b", { hasText: /^llm$/ }) }).first().click();
  await expect(page.locator(".event-detail")).toContainText("目标自报");
  await expect(page.locator(".event-detail")).toContainText("模型 offline-scripted-tool-planner");
  await expect(page.locator(".event-detail")).toContainText("输入 Token 未知");
  await page.locator(".event-row").filter({ has: page.locator("b", { hasText: /^calculator$/ }) }).first().click();
  await expect(page.locator(".event-detail")).toContainText('"answer":19');
  await page.getByRole("button", { name: "输入", exact: true }).click();
  await expect(page.locator(".event-detail")).toContainText('"operation": "add"');
});
