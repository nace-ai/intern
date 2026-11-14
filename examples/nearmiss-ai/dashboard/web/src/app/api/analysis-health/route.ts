import { NextResponse } from "next/server";

const ANALYSIS_API = process.env.ANALYSIS_API_URL ?? "http://127.0.0.1:8000";

export async function GET() {
  try {
    const response = await fetch(`${ANALYSIS_API}/health`, { cache: "no-store", signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error("Service health check failed");
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ status: "offline", nace_configured: false }, { status: 503 });
  }
}
