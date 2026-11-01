import Keycloak from "keycloak-js";

const baseUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
const authMode = import.meta.env.VITE_AUTH_MODE || "dev";
export const isOidcMode = authMode === "oidc";
export let activeRole = "customer";
let keycloak: Keycloak | null = null;

export async function initializeAuth() {
  if (authMode !== "oidc") return;
  keycloak = new Keycloak({
    url: import.meta.env.VITE_KEYCLOAK_URL || "http://localhost:8080",
    realm: "market-assistant",
    clientId: "market-assistant-web",
  });
  await keycloak.init({ onLoad: "login-required", pkceMethod: "S256" });
  setInterval(async () => {
    try { await keycloak?.updateToken(30); }
    catch { keycloak?.clearToken(); await keycloak?.login(); }
  }, 20_000);
}

export function setDevRole(role: string) { activeRole = role; }

export class ApiClientError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function authHeaders(init?: HeadersInit) {
  const headers = new Headers(init);
  headers.set("Content-Type", "application/json");
  if (keycloak?.token) headers.set("Authorization", `Bearer ${keycloak.token}`);
  else {
    headers.set("X-Tenant-ID", "tenant_demo");
    headers.set("X-User-ID", activeRole === "customer" ? "customer_001" : activeRole === "agent" ? "agent_001" : "admin_001");
    headers.set("X-Role", activeRole);
  }
  return headers;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = authHeaders(init.headers);
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: response.statusText }));
    if (response.status === 401 && keycloak) {
      keycloak.clearToken();
      await keycloak.login();
    }
    throw new ApiClientError(response.status, response.status === 403 ? "当前账号没有此功能权限" : (error.message || "请求失败"));
  }
  return response.json();
}

export async function streamConversationEvents(
  conversationId: string,
  onEvent: (type: string, data: Record<string, unknown>, eventId?: string) => void,
  signal: AbortSignal,
) {
  const cursorKey = `jayagent.lastEvent.${conversationId}`;
  const headers = authHeaders({ Accept: "text/event-stream" });
  const lastEventId = localStorage.getItem(cursorKey);
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  const response = await fetch(`${baseUrl}/api/v1/conversations/${conversationId}/events`, {
    headers,
    signal,
  });
  if (!response.ok || !response.body) throw new ApiClientError(response.status, "实时事件连接失败");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      if (!frame.trim() || frame.startsWith(":")) continue;
      const type = frame.match(/^event:\s*(.+)$/m)?.[1] || "message";
      const eventId = frame.match(/^id:\s*(.+)$/m)?.[1];
      const raw = frame.match(/^data:\s*(.+)$/m)?.[1];
      if (eventId) localStorage.setItem(cursorKey, eventId);
      if (raw) onEvent(type, JSON.parse(raw), eventId);
    }
  }
}
