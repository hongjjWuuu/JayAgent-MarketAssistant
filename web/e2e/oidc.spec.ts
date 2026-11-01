import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_OIDC !== "true", "Requires the Compose Keycloak environment");

const cases = [
  ["customer_001", "客户服务中心"],
  ["agent_001", "人工客服工作台"],
  ["admin_001", "运营与治理台"],
  ["platform_admin_001", "平台运维台"],
] as const;

for (const [username, heading] of cases) {
  test(`${username} reaches only the assigned workspace`, async ({ page }) => {
    await page.goto("/");
    await page.locator("#username").fill(username);
    await page.locator("#password").fill("demo1234");
    await page.locator("#kc-login").click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".tenant")).toContainText(username);
  });
}
