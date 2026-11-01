import { expect, test } from "@playwright/test";

test.skip(process.env.E2E_OIDC === "true", "Development-header workspace switching test");

test("customer, agent and governance workspaces are reachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "客户服务中心" })).toBeVisible();
  await page.getByRole("button", { name: "人工工作台" }).click();
  await expect(page.getByRole("heading", { name: "人工客服工作台" })).toBeVisible();
  await page.getByRole("button", { name: "运营与治理" }).click();
  await expect(page.getByRole("heading", { name: "运营与治理台" })).toBeVisible();
  await expect(page.getByText("任务运行中心")).toBeVisible();
  await expect(page.getByText("待对账执行")).toBeVisible();
});
