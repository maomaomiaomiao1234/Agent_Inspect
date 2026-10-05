import { test, expect } from "@playwright/test";

test("navigation displays three assessment areas and skill tools in sidebar", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/");

  // 验证侧边栏包含评估体系与工具与分析分组
  const sidebar = page.locator("aside.sidebar");
  await expect(sidebar.getByText("评估体系")).toBeVisible();
  await expect(sidebar.getByText("工具与分析")).toBeVisible();

  // 验证三个可以评估的项目作为一级菜单
  await expect(
    sidebar.getByRole("link", { name: "交互轨迹评估", exact: false }),
  ).toBeVisible();
  await expect(
    sidebar.getByRole("link", { name: "专项基准评估", exact: true }),
  ).toBeVisible();
  await expect(
    sidebar.getByRole("link", { name: "主动评测", exact: true }),
  ).toBeVisible();

  // 验证 Skill 工具一级菜单
  await expect(
    sidebar.getByRole("link", { name: "Skill 评测工具", exact: true }),
  ).toBeVisible();

  expect(errors).toEqual([]);
});

test("benchmarks page provides code repair and document conversion suites", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/#/benchmarks");
  await expect(
    page.getByRole("heading", { name: "专项基准评估", exact: true }),
  ).toBeVisible();

  // 验证两大核心基准模块
  await expect(
    page.getByRole("heading", {
      name: "代码修复基准 · Docker 隔离测试",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "文档转换基准 · PDF 提取与结构化验收",
      exact: true,
    }),
  ).toBeVisible();

  // 测试一键发起代码修复评测
  const correctRepairBtn = page.getByRole("button", {
    name: "正确修复 (correct) · pass",
    exact: false,
  });
  await expect(correctRepairBtn).toBeVisible();
  await correctRepairBtn.click();

  // 验证跳转到运行详情
  await expect(
    page.getByRole("heading", { name: "代码修复 · correct", exact: false }),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".outcome-bar.pass")).toBeVisible();

  // 返回专项基准评估，测试文档转换候选
  await page.goto("/#/benchmarks");
  const docCorrectBtn = page.getByRole("button", {
    name: "完整正确 · pass",
    exact: false,
  });
  await expect(docCorrectBtn).toBeVisible();
  await docCorrectBtn.click();

  // 验证跳转到文档转换运行详情
  await expect(
    page.getByRole("heading", { name: "文档转换 · correct", exact: false }),
  ).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".outcome-bar.pass")).toBeVisible();

  // 检查移动端适配
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/benchmarks");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();

  expect(errors).toEqual([]);
});

test("skills page displays opencode-trace-review instructions and workflows", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/#/skills");
  await expect(
    page.getByRole("heading", {
      name: "Skill 评测工具 · opencode-trace-review",
      exact: true,
    }),
  ).toBeVisible();

  // 验证 Skill 元数据条
  await expect(
    page.getByText("opencode-trace-review", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Python 3.12 / uv")).toBeVisible();

  // 验证四大核心能力卡片
  await expect(
    page.getByRole("heading", { name: "轨迹导入与报告生成", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "双运行回归与行为比较", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "业务任务脚手架生成", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "LLM-as-a-Judge 外部模型评审",
      exact: true,
    }),
  ).toBeVisible();

  // 验证命令行可读性
  await expect(page.locator("pre").first()).toContainText(
    'python3 "$REVIEW_SKILL/scripts/run_review.py"',
  );

  // 检查移动端适配
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();

  expect(errors).toEqual([]);
});
