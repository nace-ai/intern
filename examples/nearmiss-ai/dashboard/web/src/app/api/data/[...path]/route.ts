import { NextRequest, NextResponse } from "next/server";

const DATA_API = process.env.DATA_API_URL ?? "http://127.0.0.1:8001";

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const pathname = path.join("/");
  if (!/^(health|stats|cases|cases\/\d{5,10}\/context)$/.test(pathname)) {
    return NextResponse.json({ detail: "Unknown data route" }, { status: 404 });
  }
  const target = new URL(pathname, `${DATA_API}/`);
  if (pathname === "cases") target.search = request.nextUrl.search;
  try {
    const response = await fetch(target, { cache: "no-store", signal: AbortSignal.timeout(15000) });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ detail: "Local ASRS data service is unavailable" }, { status: 503 });
  }
}
