import { NextResponse } from "next/server";

const ANALYSIS_API = process.env.ANALYSIS_API_URL ?? "http://127.0.0.1:8000";

export async function GET() {
  try {
    const response = await fetch(`${ANALYSIS_API}/evidence/status`, { cache: "no-store", signal: AbortSignal.timeout(4000) });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ status: "not_ready", document_count: 0, detail: "Evidence service is offline" }, { status: 503 });
  }
}
