import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("import, evidence, tests, compare and exports", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "导入运行", exact: true }).click();
  await page
    .getByLabel("轨迹 JSON 文件")
    .setInputFiles(resolve("../examples/focused.bundle.json"));
  await page.getByRole("button", { name: "导入并分析", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "会话过期边界 · 聚焦修复", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("验收通过", { exact: true })).toBeVisible();
  await page
    .getByRole("button")
    .filter({ hasText: "观察到检查从失败恢复" })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "证据链" })).toBeVisible();
  await expect(page.locator(".event-detail")).toContainText(
    "FAILED tests/test_session.py",
  );
  await page.getByRole("button", { name: "验证记录", exact: true }).click();
  await expect(
    page.getByText("外部验证报告 · test · final", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "最终变更", exact: true }).click();
  await expect(page.locator(".diff-view")).toContainText(
    "return now < session.expires_at",
  );
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出报告", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.md$/);
  await page.getByRole("button", { name: "LLM 评审", exact: true }).click();
  await expect(
    page.getByText("LLM 评审尚未启用。", { exact: false }),
  ).toBeVisible();
  await page.getByRole("link", { name: "全部运行", exact: true }).click();
  await page
    .getByRole("button", { name: "加载示例数据", exact: false })
    .click();
  await expect(
    page.getByRole("link", { name: "会话过期边界 · 反复定位", exact: false }),
  ).toBeVisible();
  await page
    .getByLabel("选择 会话过期边界 · 聚焦修复", { exact: true })
    .check();
  await page
    .getByLabel("选择 会话过期边界 · 反复定位", { exact: true })
    .check();
  await page.getByRole("button", { name: "比较 (2/2)", exact: true }).click();
  await expect(
    page.getByText("任务与实验条件匹配", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "值得查看的行为差异", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "/private/tmp/agent-review-compare.png",
    fullPage: true,
  });
  await page
    .getByRole("link", { name: "查看 B 证据", exact: true })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "证据链" })).toBeVisible();
  await expect(page.locator(".event-row").first()).toBeVisible();
  await page.screenshot({
    path: "/private/tmp/agent-review-detail.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("mobile layout and import dialog keyboard behavior", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "导入运行", exact: true }).click();
  await expect(page.getByLabel("关闭导入")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page
    .getByRole("link", { name: "会话过期边界 · 聚焦修复", exact: false })
    .click();
  await expect(
    page.getByRole("heading", { name: "会话过期边界 · 聚焦修复", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".event-row").first()).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/private/tmp/agent-review-mobile.png",
    fullPage: true,
  });
});

test("generic trace and custom profile show evidence-backed acceptance", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "导入运行", exact: true }).click();
  await page.getByLabel("轨迹 JSON 文件").setInputFiles(
    resolve("../src/agent_trace_review/templates/invoice/trace.json"),
  );
  await page.getByRole("button", { name: "添加任务与验证材料", exact: false }).click();
  await page.getByLabel("任务评估 Profile (.json)").setInputFiles(
    resolve("../src/agent_trace_review/templates/invoice/profile.json"),
  );
  await page.getByRole("button", { name: "导入并分析", exact: true }).click();
  await expect(page.getByRole("heading", { name: "发票提取 · 合成示例", exact: true })).toBeVisible();
  await expect(page.getByText("验收通过", { exact: true })).toBeVisible();
  await expect(page.locator(".event-row").first()).toBeVisible();
  await page.getByRole("button").filter({ hasText: "发票号码正确" }).click();
  await expect(page.getByRole("heading", { name: "证据链" })).toBeVisible();
  await expect(page.getByText("自定义规则输入 /output/invoice_no", { exact: false })).toBeVisible();
  await page.screenshot({ path: "/private/tmp/agent-review-generic-profile.png", fullPage: true });
  await page.getByRole("button", { name: "最终结果", exact: true }).click();
  await expect(page.locator("pre.output")).toContainText('"amount": 128.5');
  await page.getByRole("button", { name: "任务验收", exact: true }).click();
  await expect(page.locator(".verification-list")).toContainText("发票号码正确");
  await page.screenshot({ path: "/private/tmp/agent-review-task-checks.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("OpenCode scenarios distinguish pass, fail and missing evidence", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const scenarios = [
    { button: "通过示例 · 聚焦修复", title: "会话过期边界 · 聚焦修复", outcome: "pass" },
    { button: "待补证据示例 · 反复定位", title: "会话过期边界 · 反复定位", outcome: "inconclusive" },
    { button: "失败示例 · 修复失败", title: "会话过期边界 · 修复失败", outcome: "fail" },
  ];
  for (const scenario of scenarios) {
    await page.goto("/");
    await page.getByRole("button", { name: scenario.button, exact: true }).click();
    await expect(page.getByRole("heading", { name: scenario.title, exact: true })).toBeVisible();
    await expect(page.locator(`.outcome-bar.${scenario.outcome}`)).toBeVisible();
    await expect(page.getByRole("heading", { name: "如何理解这次评估" })).toBeVisible();
    await expect(page.getByText("示例不代表真实 Agent 能力；具体验证材料来源见记录。", { exact: true })).toBeVisible();
    if (scenario.outcome === "fail") {
      await expect(page.locator(".event-row").first()).toBeVisible();
      await page.screenshot({ path: "/private/tmp/agent-review-task1-failed.png", fullPage: true });
      await page.getByRole("button", { name: "验证记录", exact: true }).click();
      await expect(page.locator(".verification-list")).toContainText("外部验证报告");
      await page.getByRole("button", { name: "最终变更", exact: true }).click();
      await expect(page.locator(".diff-view")).toContainText("session.expires_at + 1");
    }
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "导出报告", exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(/\.md$/);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "失败示例 · 修复失败", exact: true })).toBeVisible();
  await page.screenshot({ path: "/private/tmp/agent-review-task1-mobile.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "失败示例 · 修复失败", exact: true }).click();
  await expect(page.locator(".outcome-bar.fail")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto("/#/guide");
  await expect(page.getByRole("heading", { name: "评估场景，逐步接入" })).toBeVisible();
  await expect(page.getByText("代码修复评估：", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("input-output review through API and Token", async ({ page }) => {
  test.skip(!process.env.REVIEW_MOCK_API_URL, "Requires scripts/serve_llm_test.py");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "导入运行", exact: true }).click();
  await page.getByLabel("轨迹 JSON 文件").setInputFiles(
    resolve("../src/agent_trace_review/templates/llm-review/trace.json"),
  );
  await page.getByRole("button", { name: "添加任务与验证材料", exact: false }).click();
  await page.getByLabel("任务评估 Profile (.json)").setInputFiles(
    resolve("../src/agent_trace_review/templates/llm-review/profile.json"),
  );
  await page.getByRole("button", { name: "导入并分析", exact: true }).click();
  await expect(page.getByRole("heading", { name: "客服答复 · 输入输出合成示例", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "LLM 评审", exact: true }).click();
  await page.getByLabel("API 地址", { exact: true }).fill(process.env.REVIEW_MOCK_API_URL!);
  await page.getByLabel("模型名称", { exact: true }).fill("local-mock-model");
  await page.getByLabel("访问 Token", { exact: true }).fill("local-test-token");
  await expect(page.getByLabel("评审标准 · policy_alignment")).toHaveValue(/reference_policy/);
  await page.getByRole("button", { name: "发送材料并评审", exact: true }).click();
  await expect(page.getByRole("heading", { name: "已保存的模型评审" })).toBeVisible();
  await expect(page.getByText("验收通过", { exact: true })).toBeVisible();
  await expect(page.getByLabel("访问 Token", { exact: true })).toHaveValue("");
  await expect(page.locator(".judge-result")).toContainText("本地模拟响应");
  await page.screenshot({ path: "/private/tmp/agent-review-llm-result.png", fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "导出报告", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.md$/);
  await page.getByRole("button", { name: "任务验收", exact: true }).click();
  await expect(page.locator(".verification-list")).toContainText("大模型评审");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "LLM 评审", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain("local-test-token");
  expect(errors).toEqual([]);
});

test("actual code repair fixtures expose diff and verification evidence", async ({ page, request }) => {
  test.skip(!process.env.REVIEW_CODE_REPAIR_FIXTURES, "Seed with scripts/check_code_repair.py first");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const runs = await (await request.get("/api/runs")).json();
  for (const [candidate, outcome] of [["correct", "pass"], ["incorrect", "fail"], ["regression", "fail"], ["timeout", "inconclusive"]]) {
    const run = runs.find((r: { framework: string; title: string }) => r.framework === "code-repair-fixture" && r.title === `代码修复 · ${candidate}`);
    expect(run).toBeTruthy();
    await page.goto(`/#/runs/${run.id}`);
    await expect(page.locator(`.outcome-bar.${outcome}`)).toBeVisible();
    await expect(page.getByText("材料来源声明：候选由内置模拟器生成；baseline/final 验证记录来自实际 Docker 执行。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "最终结果", exact: true }).click();
    await expect(page.getByRole("heading", { name: "最终代码 diff" })).toBeVisible();
    if (candidate === "incorrect") {
      await expect(page.getByText("最终代码与基线相同（已提供空 diff）。", { exact: true })).toBeVisible();
    } else {
      await expect(page.locator(".diff-view")).toContainText("session.py");
    }
    await page.getByRole("button", { name: "任务验收", exact: true }).click();
    await expect(page.locator(".verification-list")).toContainText("baseline");
    await expect(page.locator(".verification-list")).toContainText("final");
    await page.getByRole("button", { name: "查看执行记录", exact: false }).last().click();
    await expect(page.locator(".event-detail")).toContainText('"exit_code"');
    await expect(page.locator(".event-detail")).toContainText('"cleanup": "removed"');
    if (candidate === "regression") {
      await expect(page.getByRole("button").filter({ hasText: "发现测试回归" }).first()).toBeVisible();
    }
    const report = await (await request.get(`/api/runs/${run.id}/export`)).text();
    expect(report).toContain("实际 Docker 执行");
    expect(report).not.toContain("没有真实运行被测 Agent 或独立验证器");
    if (candidate === "correct") {
      await page.getByRole("button", { name: "最终结果", exact: true }).click();
      await page.screenshot({ path: "/private/tmp/agent-review-task2-desktop.png", fullPage: true });
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "任务验收", exact: true }).click();
  await page.screenshot({ path: "/private/tmp/agent-review-task2-mobile.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("document conversion scenarios show source, outputs and independent checks", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const [label, candidate, outcome, clue] of [
    ["正确", "correct", "pass", "paragraph 匹配 5/5"],
    ["内容遗漏", "omitted", "fail", "limitation"],
    ["表格错误", "table_error", "fail", "行3列3"],
    ["顺序错误", "order_error", "fail", "核对内容块 ID、页码与顺序"],
    ["缺少参考", "missing_reference", "inconclusive", "缺少固定独立参考快照"],
  ]) {
    await page.goto("/");
    await page.getByRole("button", { name: `文档示例 · ${label}`, exact: true }).click();
    await expect(page.getByRole("heading", { name: `文档转换 · ${candidate}`, exact: true })).toBeVisible();
    await expect(page.locator(`.outcome-bar.${outcome}`)).toBeVisible();
    await page.getByRole("button", { name: "最终结果", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Markdown", exact: true })).toBeVisible();
    await expect(page.locator(".document-markdown")).toContainText("Field study: garden water use");
    const pdfHref = await page.getByRole("link", { name: "下载源 PDF", exact: true }).getAttribute("href");
    const pdf = await request.get(pdfHref!);
    expect(pdf.headers()["content-type"]).toBe("application/pdf");
    expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
    await page.getByText("查看结构化 JSON", { exact: true }).click();
    await expect(page.locator(".document-result details .output")).toContainText('"page_count": 2');
    if (candidate === "correct") {
      await page.screenshot({ path: "/private/tmp/agent-review-task3-output.png", fullPage: true });
    }
    await page.getByRole("button", { name: "任务验收", exact: true }).click();
    await expect(page.locator(".verification-list")).toContainText(clue);
    const ocr = page.locator(".verification-list article").filter({ hasText: "扫描文档 OCR（未覆盖）" });
    await expect(ocr).toContainText("unknown");
    await page.getByRole("button", { name: "运行固定文档检查", exact: true }).click();
    await expect(page.getByRole("button", { name: "运行固定文档检查", exact: true })).toBeEnabled();
    await expect(page.locator(`.outcome-bar.${outcome}`)).toBeVisible();
    if (candidate === "table_error") {
      const table = page.locator(".verification-list article").filter({ hasText: "表格结构与单元格准确" });
      await table.getByRole("button", { name: "查看验收证据", exact: false }).nth(1).click();
      await expect(page.locator(".event-detail")).toContainText("71");
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "文档示例 · 正确", exact: true }).click();
  await page.getByRole("button", { name: "最终结果", exact: true }).click();
  await page.screenshot({ path: "/private/tmp/agent-review-task3-mobile.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto("/#/guide");
  await expect(page.getByText("PDF 文档转换评估：", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
