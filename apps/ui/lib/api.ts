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

export async function apiGet<T>(path: string): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const res = await safeFetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`);
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
    throw new Error(`POST ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const res = await safeFetch(url, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`DELETE ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}
