import { test, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile } from "node:fs/promises";

for (const mode of ["complete", "partial", "offline"]) {
  test(`Token accounting ${mode}: table, diagnostics, JSON and run metrics`, async ({ page, request }) => {
    const headers = process.env.REVIEW_SERVICE_TOKEN ? { Authorization: `Bearer ${process.env.REVIEW_SERVICE_TOKEN}` } : {};
    const targets = await request.get("/api/targets", { headers });
    test.skip(targets.status() !== 200 || !(await targets.json()).some((t: { id: string }) => t.id === `token-${mode}`),
      "Start scripts/serve_assessment_test.py first");
    await page.goto("/#/assessments");
    if (process.env.REVIEW_SERVICE_TOKEN) {
      await page.getByLabel("服务访问令牌").fill(process.env.REVIEW_SERVICE_TOKEN);
      await page.getByRole("button", { name: "连接服务" }).click();
    }
    await page.getByLabel("被测 Agent").selectOption(`token-${mode}`);
    await page.getByRole("button", { name: "检查 Token 采集", exact: true }).click();
    await expect(page.getByRole("status")).toContainText(mode === "offline" ? "离线校准" : "真实模型");
    await page.getByLabel("固定题集 JSON").setInputFiles(resolve("../examples/smolagents/suite.smoke.json"));
    await page.getByRole("button", { name: "开始评测", exact: false }).click();
    const details = page.locator("section").filter({ has: page.getByRole("heading", { name: `评测详情 · token-${mode}` }) });
    await expect(details).toContainText("已完成 · 1/1", { timeout: 15000 });
    await page.getByText("模型与工具调用 · 资源明细", { exact: true }).click();
    const cells = page.getByRole("table", { name: "模型与工具调用统计" }).locator("tbody tr").first().locator("td");
    const expected = mode === "complete" ? ["20", "10", "30"] : mode === "partial" ? ["≥10", "≥5", "≥15"] :
      ["不适用（离线校准）", "不适用（离线校准）", "不适用（离线校准）"];
    for (let i = 0; i < 3; i++) await expect(cells.nth(i + 1)).toHaveText(expected[i]);
    await page.getByText("Token 采集状态与原因", { exact: true }).click();
    await expect(details).toContainText(mode === "partial" ? "已观测下界" : mode === "offline" ? "未调用模型服务" : "完整汇总");
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "导出评测 JSON", exact: true }).click();
    const file = await downloadPromise;
    const job = JSON.parse(await readFile((await file.path())!, "utf-8"));
    expect(job.results[0].usage.fields.total_tokens.status).toBe(mode === "offline" ? "not_applicable" : mode);
    expect(job.curves[0].usage.fields.total_tokens.value).toBe(mode === "offline" ? null : mode === "partial" ? 15 : 30);
    if (mode !== "offline") {
      expect(job.results[0].usage.fields.reasoning_tokens.value).toBe(mode === "partial" ? 2 : 4);
      expect(job.results[0].usage.fields.cache_read_tokens.value).toBe(mode === "partial" ? 6 : 12);
      await expect(details).toContainText("cache_read_tokens");
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    await page.getByRole("link", { name: "查看对话和验收", exact: true }).click();
    const metric = page.locator(".metric-card").filter({ has: page.locator("span", { hasText: "已报告 Tokens" }) });
    await expect(metric.locator("strong")).toContainText(mode === "offline" ? "不适用" : expected[2]);
  });
}
