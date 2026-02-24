import { NextRequest } from "next/server";

const SERVER_API_BASE = process.env.API_BASE_INTERNAL ?? "http://api:8000/v1";

function buildTargetUrl(request: NextRequest, pathParts: string[]): string {
  const base = SERVER_API_BASE.endsWith("/") ? SERVER_API_BASE.slice(0, -1) : SERVER_API_BASE;
  const joinedPath = pathParts.map(encodeURIComponent).join("/");
  const search = request.nextUrl.search || "";
  return `${base}/${joinedPath}${search}`;
}

async function proxyRequest(request: NextRequest, pathParts: string[]): Promise<Response> {
  const targetUrl = buildTargetUrl(request, pathParts);
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, init);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Proxy fetch failed";
    return Response.json({ detail: `API proxy request failed: ${message}` }, { status: 502 });
  }

  const responseHeaders = new Headers();
  const upstreamContentType = upstream.headers.get("content-type");
  if (upstreamContentType) {
    responseHeaders.set("content-type", upstreamContentType);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

type RouteContext = {
  params: {
    path?: string[];
  };
};

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxyRequest(request, context.params.path ?? []);
}

export async function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxyRequest(request, context.params.path ?? []);
}

export async function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxyRequest(request, context.params.path ?? []);
}
