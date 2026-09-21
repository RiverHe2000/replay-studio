let csrfToken = "";
export function setCsrf(token: string) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    if (typeof options.body === "string")
      headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let message = `Request failed (${response.status}). Please try again.`;
    try {
      const data = (await response.json()) as { detail?: unknown };
      if (typeof data.detail === "string") message = data.detail;
      else if (data.detail) message = JSON.stringify(data.detail);
    } catch {
      /* The HTTP status remains useful for non-JSON failures. */
    }
    if (
      response.status === 401 &&
      path !== "/auth/me" &&
      path !== "/auth/login"
    )
      window.dispatchEvent(new Event("replay:session-expired"));
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
export const errorText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
export const mediaUrl = (assetId: string) =>
  `/api/assets/${encodeURIComponent(assetId)}/media`;
export const frameUrl = (assetId: string, frame: string) =>
  `/api/assets/${encodeURIComponent(assetId)}/frames/${encodeURIComponent(frame.split(/[\\/]/).pop() ?? frame)}`;
