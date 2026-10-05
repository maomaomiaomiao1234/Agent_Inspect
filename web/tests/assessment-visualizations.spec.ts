import { test, expect, type Page } from "@playwright/test";

function fixture() {
  const dimension = (category: string, budget_id: string, planned: number, observed: number, pass: number, fail: number) =>
    ({ category, budget_id, planned, observed, pass, fail, unknown: planned - pass - fail,
      confirmed_pass_rate: pass / planned * 100, possible_pass_rate: (planned - fail) / planned * 100,
      p50_duration_ms: 100, p95_duration_ms: 200 });
  const dimensions = [dimension("structured_output", "low", 1, 1, 1, 0), dimension("capability", "low", 2, 2, 1, 1),
    dimension("grounding", "low", 3, 2, 0, 1), dimension("structured_output", "high", 1, 1, 1, 0),
    dimension("capability", "high", 2, 2, 2, 0), dimension("grounding", "high", 3, 2, 2, 0)];
  const results = dimensions.flatMap(d => Array.from({ length: d.observed }, (_, i) => ({
    case_id: `${d.category}-${i}`, category: d.category, budget_id: d.budget_id, attempt: 1,
    run_id: `${d.category}-${d.budget_id}-${i}`, description: `${d.category} 案例 ${i + 1}`,
    outcome: i < d.pass ? "pass" : i < d.pass + d.fail ? "fail" : "inconclusive",
    execution_state: "completed", error: null, source_evidence: [], checks: [],
  })));
  const curve = (id: string, pass: number, fail: number, observed: number) => ({ budget: { id }, planned: 6, observed,
    pass, fail, unknown: 6 - pass - fail, pass_rate: pass / 6 * 100,
    mean_duration_ms: null, reported_total_tokens: null, reported_cost_usd: null,
    usage: { fields: { total_tokens: { value: 30, status: "partial", source: "fixture", reason: "部分可见" } } } });
  return {
    id: "assessment_visual", target_id: "test-agent", suite_id: "fixed-suite", state: "running", demo: false,
    planned: 12, completed: 10, commit: null, error: null, claims: [], limitations: [],
    curves: [curve("low", 2, 2, 5), curve("high", 5, 0, 5)], results,
    quality: { dimensions, summary: { planned: 12, observed: 10, pass: 7, fail: 2, inconclusive: 1, pending: 2,
      execution_issues: 0, headline: "发现未通过的必需检查", conclusion: "fail", scope_note: "结论只覆盖本题集。",
      covered_categories: [{ id: "capability", label: "计算与推理" }], uncovered_categories: [{ id: "memory", label: "跨轮记忆" }], next_steps: ["补齐评测结果。"] },
      stability: ["consistent_pass", "consistent_fail", "variable", "incomplete"].map((status, i) => ({
        case_id: `repeat-${i}`, budget_id: "low", planned: 2, observed: status === "incomplete" ? 1 : 2, status })),
      source_coverage: [], evidence: { token_known_runs: 0, planned_runs: 12, required_checks: 10, unknown_required_checks: 1 }, limitations: [] },
  };
}

async function mockReports(page: Page, job = fixture(), history?: ReturnType<typeof fixture>[]) {
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/health" ? { authentication_required: false, data_dir: "test" }
      : path === "/api/assessments" ? history || [job]
      : path.startsWith("/api/assessments/") ? job
      : path === "/api/repository-builds" ? { enabled: false, allowed_environment: [] } : [];
    await route.fulfill({ json: body });
  });
}

test("fixed-suite score retains pending results, weights dimensions, and drills into budget cases", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockReports(page);
  await page.goto("/#/assessments?id=assessment_visual");
  const visuals = page.getByRole("region", { name: "Agent 能力可视化", exact: true });
  await expect(visuals.getByTestId("assessment-score")).toHaveText("58.3");
  await expect(visuals).toContainText("确认通过 7 / 计划 12 次");
  await expect(visuals).toContainText("评测尚未完成");
  await expect(visuals).toContainText("最高 83.3 分");
  await expect(visuals.getByRole("button", { name: "查看计算与推理案例", exact: true })).toContainText("75 分");
  await expect(visuals.getByRole("img", { name: "能力雷达图", exact: true })).toBeVisible();
  await expect(visuals).toContainText("66.7%");
  await expect(visuals).toContainText("一致包含重复失败；1 组待确认");
  await expect(visuals.getByRole("region", { name: "预算表现对比" })).toContainText("≥30");
  await expect(visuals.getByRole("region", { name: "预算表现对比" })).toContainText("平均耗时 未知");
  await page.getByLabel("可视化预算").selectOption("low");
  await expect(visuals.getByTestId("assessment-score")).toHaveText("33.3");
  await expect(visuals.getByRole("button", { name: "查看计算与推理案例", exact: true })).toContainText("50 分");
  await visuals.getByRole("button", { name: "查看计算与推理案例", exact: true }).click();
  const review = page.getByRole("region", { name: "案例诊断", exact: true });
  await expect(review.getByRole("status")).toHaveText("显示 2/10 次结果");
  await expect(review).toContainText("计算与推理 · low");
  await expect(review.locator(".assessment-case")).toHaveCount(2);
  await review.getByRole("button", { name: "清除维度筛选" }).click();
  await expect(review.getByRole("status")).toHaveText("显示 10/10 次结果");
  await page.reload();
  await expect(page.getByRole("heading", { name: "评测详情 · test-agent", exact: true })).toBeVisible();
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 960 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  }
  expect(errors).toEqual([]);
});

test("unobserved, historical, demo and adapter reports preserve their scope", async ({ page }) => {
  const job = fixture();
  job.completed = 0; job.results = []; job.state = "queued"; job.demo = true;
  job.curves = job.curves.map(c => ({ ...c, observed: 0, pass: 0, fail: 0, unknown: 6, pass_rate: 0 }));
  job.quality.dimensions = job.quality.dimensions.map(d => ({ ...d, observed: 0, pass: 0, fail: 0, unknown: d.planned }));
  await mockReports(page, job);
  await page.goto("/#/assessments?id=assessment_visual");
  const visuals = page.getByRole("region", { name: "Agent 能力可视化" });
  await expect(visuals.getByTestId("assessment-score")).toHaveText("—");
  await expect(visuals).toContainText("等待评测结果");
  await expect(visuals).toContainText("控制示例 · 分数仅用于校准评测流程");
  await expect(visuals.locator(".radar-point")).toHaveCount(0);
  await expect(visuals.getByRole("button", { name: "查看计算与推理案例", exact: true })).toContainText("待评测");
  const legacy = { ...fixture(), quality: undefined, purpose: "adapter_validation" };
  await page.unroute("**/api/**");
  await page.route("**/api/assessments**", route => route.fulfill({ json: new URL(route.request().url()).pathname === "/api/assessments" ? [legacy] : legacy }));
  await page.route("**/api/health", route => route.fulfill({ json: { authentication_required: false } }));
  await page.route("**/api/runs", route => route.fulfill({ json: [] }));
  await page.route("**/api/targets", route => route.fulfill({ json: [] }));
  await page.route("**/api/repository-builds", route => route.fulfill({ json: { enabled: false } }));
  await page.route("**/api/repository-jobs", route => route.fulfill({ json: [] }));
  await page.reload();
  await expect(visuals).toContainText("接入检查 · 不计入正式能力评测");
  await expect(visuals).toContainText("暂无维度数据");
  await expect(page.getByRole("region", { name: "评测任务", exact: true }).locator(".history-score")).toHaveText("接入检查");
});

test("report history filters, paginates, and opens a persistent report link", async ({ page }) => {
  const job = fixture();
  job.state = "completed";
  const jobs = Array.from({ length: 10 }, (_, i) => ({ ...job, id: `assessment_${i}`, target_id: `agent-${i}`, demo: i === 0 }));
  await mockReports(page, job, jobs);
  await page.goto("/#/assessments");
  const history = page.getByRole("region", { name: "评测任务", exact: true });
  await expect(history.locator("tbody tr")).toHaveCount(8);
  await history.getByRole("button", { name: "下一页评测" }).click();
  await expect(history.locator("tbody tr")).toHaveCount(2);
  await page.getByLabel("搜索评测").fill("agent-0");
  await expect(history.locator("tbody tr")).toHaveCount(1);
  await page.getByLabel("评测记录筛选").selectOption("formal");
  await expect(history).toContainText("没有匹配的评测记录");
  await page.getByLabel("搜索评测").fill("");
  await expect(history).toContainText("/ 9 条");
  await history.getByRole("button", { name: "查看评测 assessment_1", exact: true }).click();
  await expect(page).toHaveURL(/id=assessment_1/);
  await expect(page.getByRole("heading", { name: "评测详情 · test-agent", exact: true })).toBeVisible();
});

test("version score comparison is shown only after comparability checks", async ({ page }) => {
  const baseline = fixture(); baseline.id = "baseline"; baseline.state = "completed";
  const revised = fixture(); revised.id = "revised"; revised.state = "completed";
  await mockReports(page, baseline, [baseline, revised]);
  let comparable = false;
  await page.route("**/api/assessment-comparisons", route => route.fulfill({ json: {
    comparable, reasons: comparable ? [] : ["题集不同"],
    changes: comparable ? [{ case_id: "case-1", from: "pass", to: "fail", regression: true }] : [],
  } }));
  await page.goto("/#/assessments");
  await page.getByLabel("基线评测").selectOption("baseline");
  await page.getByLabel("新版评测").selectOption("baseline");
  await expect(page.getByRole("button", { name: "比较版本" })).toBeDisabled();
  await page.getByLabel("新版评测").selectOption("revised");
  await page.getByRole("button", { name: "比较版本" }).click();
  await expect(page.getByRole("status")).toContainText("无法比较：题集不同");
  await expect(page.locator(".comparison-score-grid")).toHaveCount(0);
  comparable = true;
  await page.getByRole("button", { name: "比较版本" }).click();
  await expect(page.locator(".comparison-score-grid")).toContainText("基线题集得分58.3 / 100");
  await expect(page.getByRole("status")).toContainText("发现回归1 次结果");
  await page.getByLabel("基线评测").selectOption("");
  await expect(page.locator(".comparison-score-grid")).toHaveCount(0);
});

test("repository names and persisted validation links identify reports on older services", async ({ page }) => {
  const formal = fixture(); formal.id = "formal-repo";
  const validation = fixture(); validation.id = "validation-repo";
  await mockReports(page, formal, [formal, validation]);
  let publishMetadata!: () => void;
  const metadataReady = new Promise<void>(resolve => { publishMetadata = resolve; });
  await page.route("**/api/repository-jobs", async route => { await metadataReady; await route.fulfill({ json: [{
    id: "repository-job", state: "completed", stage: "finished", commit: "abc", cleanup: "removed", error: null,
    assessment_id: formal.id, validation_ids: [validation.id], log_artifacts: [],
    request: { repository_url: "https://github.com/sample/agent", ref: "HEAD" },
  }] }); });
  await page.goto("/#/assessments?id=formal-repo");
  await expect(page.getByRole("heading", { name: "评测详情 · test-agent", exact: true })).toBeVisible();
  await expect(page.locator(".assessment-visuals")).toHaveCount(1);
  publishMetadata();
  await expect(page.getByRole("heading", { name: "评测详情 · sample/agent", exact: true })).toBeVisible();
  await expect(page.locator(".assessment-visuals")).toHaveCount(1);
  await expect(page.locator(".assessment-review")).toHaveCount(1);
  const history = page.getByRole("region", { name: "评测任务", exact: true });
  await expect(history.getByRole("button", { name: "fixed-suite / sample/agent", exact: true })).toBeVisible();
  await expect(history.locator("tr").filter({ hasText: "接入检查" }).locator(".history-score")).toHaveText("接入检查");
  await page.getByLabel("评测记录筛选").selectOption("formal");
  await expect(history.locator("tbody tr")).toHaveCount(1);
  await page.getByLabel("搜索评测").fill("sample/agent");
  await expect(history.locator("tbody tr")).toHaveCount(1);
  await history.getByRole("button", { name: "fixed-suite / sample/agent", exact: true }).click();
  await expect(page.getByRole("heading", { name: "评测详情 · sample/agent", exact: true })).toBeVisible();
  await expect(page.locator(".assessment-detail")).toContainText("目标 ID：test-agent");
});
