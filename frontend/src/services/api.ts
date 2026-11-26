/** Thin fetch wrapper. Session is an httpOnly cookie; errors follow the API's {code, message, details}. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

const BASE = "/api/v1";

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(BASE + path, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event("testlab:unauthorized"));
    }
    throw new ApiError(res.status, body?.code ?? "error", body?.message ?? res.statusText, body?.details);
  }
  return body as T;
}

/** Human-readable message for any thrown error, including field-level validation details. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "validation_error" && Array.isArray(e.details) && e.details.length) {
      return e.details.map((d: { msg?: string }) => d.msg?.replace(/^Value error, /, "")).join("; ");
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
