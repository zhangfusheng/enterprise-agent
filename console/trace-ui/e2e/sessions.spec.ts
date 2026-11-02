import { test, expect } from "@playwright/test";

const run = {
  run_id: "run_session_1",
  tenant_id: "local-tenant",
  status: "SUCCEEDED",
  input: "分析本周销售数据并给出结论",
  route: "research",
  summary: "已生成初版销售分析结论。",
  missing_information: [],
  created_at: "2026-10-28T08:00:00Z",
  updated_at: "2026-10-28T08:01:00Z",
  budget: {},
};

test("new runs persist a session and can be reopened as conversation history", async ({
  page,
}) => {
  await page.route("**/api/v1/runs**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/events")) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
      return;
    }
    if (request.method() === "POST" && url.pathname === "/api/v1/runs") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ run_id: run.run_id, session_id: "session_browser_1", status: "PENDING" }),
      });
      return;
    }
    if (url.pathname.endsWith(`/${run.run_id}`)) {
      await route.fulfill({ json: run });
      return;
    }
    await route.fulfill({ json: { items: [run] } });
  });
  await page.route("**/api/v1/sessions**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/session_browser_1")) {
      await route.fulfill({
        json: {
          session_id: "session_browser_1",
          title: run.input,
          created_at: run.created_at,
          updated_at: run.updated_at,
          latest_run_id: run.run_id,
          messages: [
            {
              message_id: "message_user_1",
              role: "user",
              content: run.input,
              created_at: run.created_at,
              run_id: run.run_id,
            },
            {
              message_id: "message_assistant_1",
              role: "assistant",
              content: run.summary,
              created_at: run.updated_at,
              run_id: run.run_id,
            },
          ],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        items: [
          {
            session_id: "session_browser_1",
            title: run.input,
            created_at: run.created_at,
            updated_at: run.updated_at,
            latest_run_id: run.run_id,
          },
        ],
      },
    });
  });

  await page.goto("/?view=new");
  await page.getByLabel("任务目标").fill(run.input);
  await page.getByRole("button", { name: "开始执行" }).click();
  await expect(page).toHaveURL(/view=run&run_id=run_session_1&session_id=/);

  await page.goto("/?view=sessions");
  await expect(page.getByRole("heading", { name: "历史对话" })).toBeVisible();
  await expect(page.locator(".session-row").getByText(run.input, { exact: true })).toBeVisible();
  await page.locator(".session-row").click();
  await page.getByRole("tab", { name: /对话/ }).click();
  await expect(page.getByText("已生成初版销售分析结论。", { exact: true })).toBeVisible();
});
