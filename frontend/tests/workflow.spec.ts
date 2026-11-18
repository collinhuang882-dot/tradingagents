import { test, expect } from "@playwright/test";

test("AKShare 正式目录可选择且设置不再需要 Tushare", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByText(/行情来源：AKShare/)).toBeVisible();
  await expect(page.getByText("Tushare Token")).toHaveCount(0);
  await page.goto("/#/new");
  await page.getByLabel("搜索股票").fill("600519");
  await page.getByRole("button", { name: /贵州茅台.*600519/ }).click();
  await page.getByLabel("搜索行业").fill("白酒");
  await expect(
    page.getByRole("button", { name: /白酒Ⅱ.*ak:sw:801125/ }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/akshare-live-picker.png",
    fullPage: true,
  });
});

test("正式目录空状态、独立同步与无匹配提示", async ({ page }) => {
  let synced = false;
  await page.route("**/api/instruments?**", async (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q") || "";
    const rows =
      synced &&
      url.searchParams.get("kind") === "stock" &&
      (!q || q === "600519")
        ? [
            {
              id: "600519.SH",
              name: "贵州茅台",
              source: "Tushare",
              kind: "stock",
            },
          ]
        : [];
    await route.fulfill({ json: rows });
  });
  await page.route("**/api/catalog/sync", async (route) => {
    synced = true;
    await route.fulfill({
      json: {
        count: 1,
        counts: { stock: 1, sector: 0 },
        warnings: ["行业同步失败：index_classify 接口无访问权限"],
      },
    });
  });
  await page.goto("/#/new");
  await page.getByLabel("搜索股票").fill("600519");
  await expect(page.getByText(/正式股票目录尚未同步成功/)).toBeVisible();
  await page.getByRole("button", { name: "同步正式目录" }).click();
  await expect(
    page.getByRole("button", { name: /贵州茅台.*600519/ }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("index_classify");
  await page.getByLabel("搜索股票").fill("不存在");
  await expect(page.getByText(/没有匹配结果/)).toBeVisible();
});

test("创建样例复盘、筛选、证据、两种导出与历史", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#/new");
  await expect(
    page.getByRole("heading", { name: "把今天看清楚" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "样例演示" }).click();
  await page.getByLabel("交易日", { exact: true }).fill("2026-09-22");
  await page.getByLabel("信息截止时间").fill("18:00");
  await page.getByLabel("搜索股票").fill("600519");
  await page.getByRole("button", { name: /贵州茅台.*600519/ }).click();
  await page.getByLabel("搜索股票").fill("300750");
  await page.getByRole("button", { name: /宁德时代.*300750/ }).click();
  await page.getByLabel("搜索行业").fill("白酒");
  await page.getByRole("button", { name: /白酒.*demo:白酒/ }).click();
  await page.screenshot({
    path: "test-results/new-review.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "开始复盘", exact: true }).click();
  await expect(page).toHaveURL(/#\/report\//, { timeout: 45000 });
  await expect(
    page.getByRole("heading", { name: "原报告核心结论" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/report-overview.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "M-600519.SH", exact: true })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "证据详情" })).toBeVisible();
  await expect(page.locator("dialog pre")).toContainText("close");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("tab", { name: "个股", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "贵州茅台日K与成交量图" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "120 日" }).click();
  await page.screenshot({
    path: "test-results/report-chart.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await page.getByLabel("股票", { exact: true }).selectOption(["600519.SH"]);
  await expect(page.locator("table tbody tr")).toHaveCount(1);
  await page.reload();
  await expect(page.locator("table tbody tr")).toHaveCount(1);
  for (const format of ["md", "docx"]) {
    await page.getByRole("button", { name: "导出报告" }).click();
    await page.getByLabel("文件格式").selectOption(format);
    await page.getByLabel("导出范围").selectOption("filtered");
    await page.getByRole("button", { name: "生成文件" }).click();
    const link = page.getByRole("link", { name: /文件已生成/ });
    await expect(link).toBeVisible({ timeout: 40000 });
    const downloadPromise = page.waitForEvent("download");
    await link.click();
    const file = await downloadPromise;
    await file.saveAs("test-results/" + file.suggestedFilename());
    await page
      .getByRole("button", { name: "关闭", exact: true })
      .last()
      .click();
  }
  await page.getByRole("tab", { name: "政策与外围" }).click();
  await expect(
    page.getByRole("heading", { name: "政策与公司公告" }),
  ).toBeVisible();
  await page.goto("/#/history");
  await page.getByRole("button", { name: "清空", exact: true }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.screenshot({ path: "test-results/history.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("正式模式说明、设置和窄屏", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(
    page.getByRole("heading", { name: "数据与模型", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("尚未完成采集")).not.toBeVisible();
  await page.screenshot({ path: "test-results/settings.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/new");
  await expect(page.getByRole("button", { name: "开始复盘" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
});


test("仅股票复盘说明未选择行业并保留参数新建", async ({ page, request }) => {
  const created = await request.post('/api/reviews', {
    headers: {'Idempotency-Key': 'sector-empty-' + Date.now()},
    data: {trade_date:'2026-09-22',as_of:'2026-09-22T18:00:00+08:00',stocks:['600519.SH'],sectors:[],mode:'demo',lookback:7},
  });
  expect(created.ok()).toBeTruthy();
  const run = await created.json();
  await expect.poll(async () => (await request.get('/api/reports/' + run.id)).status(), {timeout:45000}).toBe(200);
  await page.goto('/#/report/' + run.id);
  await page.getByRole('tab', {name:'板块',exact:true}).click();
  await expect(page.getByRole('heading', {name:'未选择行业',exact:true})).toBeVisible();
  await page.getByRole('button', {name:'添加行业并新建复盘'}).click();
  await expect(page).toHaveURL(new RegExp('/new\\?copy=' + run.id));
  await expect(page.getByLabel('交易日', {exact:true})).toHaveValue('2026-09-22');
  await expect(page.getByRole('button', {name:'移除贵州茅台',exact:true})).toBeVisible();
  await page.getByLabel('搜索行业').fill('白酒');
  await expect(page.getByRole('button', {name:/白酒.*demo:白酒/})).toBeVisible();
  const report = await (await request.get('/api/reports/' + run.id)).json();
  report.request.sectors = ['demo:白酒'];
  await page.route('**/api/reports/' + run.id + '**', route => route.fulfill({json:report}));
  await page.goto('/#/report/' + run.id + '?tab=sector');
  await expect(page.getByRole('heading', {name:'所选行业数据暂不可用'})).toBeVisible();
});
