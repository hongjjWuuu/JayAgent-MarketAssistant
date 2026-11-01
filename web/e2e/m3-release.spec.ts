import { expect, request, test, type Browser, type Page } from "@playwright/test";

test.skip(process.env.E2E_M3 !== "true", "Requires the isolated M3 drill environment");
test.describe.configure({ mode: "serial" });

const keycloak = process.env.E2E_KEYCLOAK_URL || "http://localhost:18080";
const apiUrl = process.env.E2E_API_URL || "http://localhost:18000";
const simulatorUrl = process.env.E2E_SIMULATOR_URL || "http://localhost:18090";

async function login(page: Page, username: string, heading: string) {
  await page.goto("/");
  await page.locator("#username").fill(username);
  await page.locator("#password").fill("demo1234");
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: heading })).toBeVisible({ timeout: 20_000 });
}

async function token(username: string) {
  const client = await request.newContext();
  const response = await client.post(
    `${keycloak}/realms/market-assistant/protocol/openid-connect/token`,
    { form: { client_id: "market-assistant-web", grant_type: "password", username, password: "demo1234" } },
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}

test("customer, agent and governance complete the M3 pilot chain", async ({ browser, request }) => {
  await request.post(`${simulatorUrl}/__control/reset`, {
    headers: { "X-Simulator-Control": "m3-drill-control" },
  });

  const customerContext = await browser.newContext();
  const customer = await customerContext.newPage();
  await login(customer, "customer_001", "客户服务中心");
  await customer.getByRole("button", { name: "新建会话" }).click();
  await expect(customer.getByText("新会话已创建")).toBeVisible();
  await customer.getByPlaceholder("输入订单、物流或售后问题…").fill("请查询 order_1001 的物流状态");
  await customer.getByRole("button", { name: "发送" }).click();
  await expect(customer.getByText(/订单 order_1001/).first()).toBeVisible({ timeout: 30_000 });

  await customer.getByPlaceholder("输入订单、物流或售后问题…").fill("商品已破损，请创建售后工单");
  await customer.getByRole("button", { name: "发送" }).click();
  const confirm = customer.getByRole("button", { name: "确认并提交" });
  await expect(confirm).toBeVisible({ timeout: 30_000 });
  await confirm.click();
  await customer.reload();
  await expect(customer.locator(".panelHead span").filter({ hasText: "会话" }).first()).toBeVisible();
  await customer.getByRole("button", { name: "转人工" }).click();
  await expect(customer.getByText("已进入人工客服队列")).toBeVisible();

  const agentContext = await browser.newContext();
  const agent = await agentContext.newPage();
  await login(agent, "agent_001", "人工客服工作台");
  await agent.getByRole("button", { name: "刷新" }).click();
  await agent.locator(".queueItem").first().click();
  await agent.getByRole("button", { name: "原子领取" }).click();
  await expect(agent.getByText("会话已原子领取")).toBeVisible();
  await agent.getByRole("button", { name: "发送人工回复" }).click();
  await expect(agent.getByText("人工回复已送达")).toBeVisible();
  await agent.getByRole("button", { name: "释放" }).click();
  await agent.locator(".queueItem").first().click();
  await agent.getByRole("button", { name: "原子领取" }).click();
  await agent.getByRole("button", { name: "转派" }).click();

  const stats = await request.get(`${simulatorUrl}/__control/stats`, {
    headers: { "X-Simulator-Control": "m3-drill-control" },
  });
  expect((await stats.json()).ticket_count).toBe(1);

  const customerToken = await token("customer_001");
  const agentToken = await token("agent_001");
  const tenantAdminToken = await token("admin_001");
  const platformToken = await token("platform_admin_001");
  const checks = [
    [customerToken, "/api/v1/admin/runtime-tasks", 403],
    [agentToken, "/api/v1/admin/policies", 403],
    [tenantAdminToken, "/api/v1/admin/workers", 403],
    [platformToken, "/api/v1/admin/policies", 403],
  ] as const;
  for (const [accessToken, path, status] of checks) {
    const response = await request.get(`${apiUrl}${path}`, {
      headers: { Authorization: `Bearer ${accessToken}`, "X-Tenant-ID": "tenant_attacker" },
    });
    expect(response.status()).toBe(status);
  }
  await customerContext.close();
  await agentContext.close();
});
