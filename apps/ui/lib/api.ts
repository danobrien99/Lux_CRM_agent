const SERVER_API_BASE = process.env.API_BASE_INTERNAL ?? "http://api:8000/v1";
const CLIENT_API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/proxy";

function getApiBase(): string {
  return typeof window === "undefined" ? SERVER_API_BASE : CLIENT_API_BASE;
}

async function safeFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to fetch";
    throw new Error(`${message} (${input})`);
  }
}

async function readErrorMessage(res: Response): Promise<string | null> {
  const contentType = res.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) {
      const payload = (await res.json()) as unknown;
      if (payload && typeof payload === "object") {
        const detail = (payload as { detail?: unknown }).detail;
        if (typeof detail === "string" && detail.trim()) {
          return detail;
        }
        if (detail && typeof detail === "object") {
          const message = (detail as { message?: unknown }).message;
          if (typeof message === "string" && message.trim()) {
            return message;
          }
        }
      }
      return null;
    }
    const text = (await res.text()).trim();
    return text || null;
  } catch {
    return null;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const res = await safeFetch(url, { cache: "no-store" });
  if (!res.ok) {
    const detail = await readErrorMessage(res);
    throw new Error(detail ? `GET ${path} failed: ${res.status} (${detail})` : `GET ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const res = await safeFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await readErrorMessage(res);
    throw new Error(detail ? `POST ${path} failed: ${res.status} (${detail})` : `POST ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const res = await safeFetch(url, {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await readErrorMessage(res);
    throw new Error(
      detail ? `DELETE ${path} failed: ${res.status} (${detail})` : `DELETE ${path} failed: ${res.status}`,
    );
  }
  return res.json() as Promise<T>;
}
