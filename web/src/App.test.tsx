import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("shows the governed customer workspace", () => {
    render(<App />);
    expect(screen.getByText("客户服务中心")).toBeInTheDocument();
    expect(screen.getByText("租户隔离 · 幂等写入")).toBeInTheDocument();
  });

  it("exposes the governed task runtime center to administrators", async () => {
    globalThis.fetch = ((input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/auth/me")
        ? { subject:"admin_001", tenant_id:"tenant_demo", role:"tenant_admin", username:"admin_001", capabilities:[] }
        : url.includes("runtime-summary")
        ? { tasks: {}, workers: { online: 0, stale: 0 } }
        : [];
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    }) as typeof fetch;
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "运营与治理" }));
    expect(await screen.findByText("任务运行中心")).toBeInTheDocument();
    expect(screen.getByText("原死信不可变", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("待对账执行")).toBeInTheDocument();
  });
});
