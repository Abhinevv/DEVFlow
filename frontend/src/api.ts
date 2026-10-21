import type { WorkflowEvent } from "./types";

const TOKEN_KEY = "devflow.token";

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable: session lasts for this page only */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function headers(json = false): HeadersInit {
  const h: Record<string, string> = {};
  const token = getToken();
  if (token) h.Authorization = `Bearer ${token}`;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function check(res: Response): Promise<Response> {
  if (res.ok) return res;
  if (res.status === 401) {
    setToken(null);
    window.dispatchEvent(new Event("devflow:logout"));
  }
  let message = res.statusText;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) message = body.detail.map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  } catch { /* not JSON */ }
  throw new ApiError(res.status, message);
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
    headers: headers(options.body !== undefined),
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  await check(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function apiText(path: string): Promise<string> {
  const res = await check(await fetch(path, { headers: headers() }));
  return res.text();
}

export async function download(path: string, filename: string): Promise<void> {
  const res = await check(await fetch(path, { headers: headers() }));
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadText(text: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Subscribe to a project's Server-Sent Events. Uses fetch (not EventSource) so the bearer token
 * travels in a header rather than the URL. Reconnects automatically from the last event id.
 */
export function streamEvents(
  projectId: number,
  handlers: { onEvent: (e: WorkflowEvent) => void; onStatus: (status: string) => void; onConnection?: (live: boolean) => void },
): () => void {
  const controller = new AbortController();
  let lastId = 0;
  let stopped = false;

  const connect = async () => {
    while (!stopped) {
      try {
        const res = await fetch(`/api/projects/${projectId}/events/stream?after=${lastId}`, { headers: headers(), signal: controller.signal });
        if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
        handlers.onConnection?.(true);
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          let idx;
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const chunk = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            let event = "message";
            const data: string[] = [];
            for (const line of chunk.split("\n")) {
              if (line.startsWith("event:")) event = line.slice(6).trim();
              else if (line.startsWith("data:")) data.push(line.slice(5).trim());
            }
            if (!data.length) continue;
            const payload = JSON.parse(data.join("\n"));
            if (event === "workflow") {
              lastId = Math.max(lastId, payload.id);
              handlers.onEvent(payload as WorkflowEvent);
            } else if (event === "status") {
              handlers.onStatus(payload.status);
            }
          }
        }
      } catch (err) {
        if (stopped || (err instanceof DOMException && err.name === "AbortError")) return;
      }
      handlers.onConnection?.(false);
      await new Promise((r) => setTimeout(r, 2000));
    }
  };
  void connect();
  return () => { stopped = true; controller.abort(); };
}
