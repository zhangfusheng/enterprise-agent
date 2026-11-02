import { test, expect } from "@playwright/test";

test("real API: list, tree, error paths, inspector, deep links and export", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Trace Explorer" }),
  ).toBeVisible();
  await expect(page.locator(".trace-link")).toHaveCount(8);
  await page.screenshot({
    path: testInfo.outputPath("explorer.png"),
    fullPage: true,
  });
  await page.locator(".trace-link").first().click();
  await expect(page.getByTestId("span-row")).toHaveCount(10);
  await page.getByRole("button", { name: "折叠全部", exact: true }).click();
  await expect(page.getByTestId("span-row")).toHaveCount(1);
  await page.getByRole("button", { name: "展开全部", exact: true }).click();
  await expect(page.getByTestId("span-row")).toHaveCount(10);
  await page.getByLabel("错误路径").check();
  await expect(page.getByTestId("span-row")).toHaveCount(4);
  await page.getByLabel("错误路径").uncheck();
  await page.locator('[data-span-id="tool-0"] .span-button').click();
  await expect(
    page
      .getByRole("complementary", { name: "节点详情" })
      .getByRole("heading", { name: "web_search" }),
  ).toBeVisible();
  await expect(page.getByText("耗时未采集", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "错误", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText(
    "Search provider timed out",
  );
  await page.reload();
  await expect(
    page
      .getByRole("complementary", { name: "节点详情" })
      .getByRole("heading", { name: "web_search" }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("detail.png"),
    fullPage: true,
  });
  const download = page.waitForEvent("download");
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制 Span ID", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("tool-0");
  await page.getByRole("button", { name: "导出 Run JSON" }).click();
  expect((await download).suggestedFilename()).toMatch(/^trace-.*\.json$/);
  await page.getByRole("button", { name: "返回 Traces", exact: true }).click();
  await page.getByLabel("搜索 Trace", { exact: true }).fill("nonexistent-root");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page.getByText("还没有匹配的 Trace")).toBeVisible();
  expect(errors).toEqual([]);
});

test("tenant isolation is preserved across list and direct navigation", async ({
  page,
}) => {
  await page.goto("/");
  await page.locator(".trace-link").first().click();
  const url = new URL(page.url());
  url.searchParams.set("tenant", "other-tenant");
  await page.goto(url.toString());
  await expect(page.getByRole("alert")).toContainText("未找到记录");
  await page.goto("/?tenant=other-tenant");
  await expect(page.getByText("还没有匹配的 Trace")).toBeVisible();
});

test("narrow viewport keeps the inspector and controls accessible", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.locator(".trace-link").first().click();
  await expect(
    page.getByRole("complementary", { name: "节点详情" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("mobile.png"),
    fullPage: true,
  });
});

test("pending traces poll until completion, then stop", async ({
  page,
  request,
}) => {
  const listing = await (await request.get("/api/v1/traces")).json();
  const runId = listing.items[0].run_id;
  const finished = await (
    await request.get(`/api/v1/runs/${runId}/trace`)
  ).json();
  let requests = 0;
  await page.route(`**/api/v1/runs/${runId}/trace`, (route) => {
    requests += 1;
    return route.fulfill({
      json:
        requests < 3
          ? { ...finished, run_status: "PENDING", runs: [], roots: [] }
          : finished,
    });
  });
  await page.goto(`/?run_id=${runId}`);
  await expect(page.getByTestId("span-row")).toHaveCount(10);
  await expect(page.getByRole("button", { name: "执行已停止" })).toBeVisible();
  const completedRequests = requests;
  await page.waitForTimeout(2500);
  expect(requests).toBe(completedRequests);
});

test("pagination history survives details and refresh", async ({
  page,
  request,
}) => {
  const listing = await (await request.get("/api/v1/traces")).json();
  await page.route("**/api/v1/traces?*", (route) => {
    const cursor = Number(
      new URL(route.request().url()).searchParams.get("cursor") || 0,
    );
    return route.fulfill({
      json: {
        items: listing.items.slice(cursor, cursor + 2),
        next_cursor: cursor < 6 ? String(cursor + 2) : null,
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "下一页" }).click();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page).toHaveURL(/cursor=4/);
  await page.locator(".trace-link").first().click();
  await page.getByRole("button", { name: "返回 Traces", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(page).toHaveURL(/cursor=2/);
  await expect(page.locator(".trace-link").first()).toHaveAttribute(
    "href",
    new RegExp(listing.items[2].run_id),
  );
});
