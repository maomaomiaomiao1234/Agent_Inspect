import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("gateway usage survives target crash and a review service restart", async ({ page, request }) => {
  const list = await request.get("/api/assessments?limit=100");
  const jobs = await list.json();
  const job = jobs.find((j: { suite_id: string }) => j.suite_id === "native-addition");
  test.skip(!job, "Start the test server against the --gateway --target-crash Docker validation data directory");
  const detail = await (await request.get(`/api/assessments/${job.id}`)).json();
  expect(detail.results[0].execution_state).toBe("error");
  expect(detail.gateway.usage.fields.total_tokens.value).toBe(15);
  await page.goto("/#/assessments");
  await page.getByRole("button", { name: `${job.suite_id} / ${job.target_id}`, exact: true }).click();
  const gateway = page.getByRole("region", { name: "模型网关记录" });
  await expect(gateway).toContainText("已持久化 1 次请求");
  await expect(gateway).toContainText("已保存 Token 15");
  await page.getByText("模型与工具调用 · 资源明细", { exact: true }).click();
  await expect(page.getByRole("table", { name: "模型与工具调用统计" })).toContainText("≥15");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出证据包", exact: true }).click();
  const bundle = JSON.parse(await readFile((await (await downloaded).path())!, "utf-8"));
  expect(bundle.gateway.request_count).toBe(1);
  expect(bundle.gateway.events[0].usage.tokens.total).toBe(15);
  expect(bundle.runs[0].bundle.trace.usage_summary.provenance).toBe("gateway_reported");
  expect(JSON.stringify(bundle)).not.toContain("synthetic-target-key");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
});
