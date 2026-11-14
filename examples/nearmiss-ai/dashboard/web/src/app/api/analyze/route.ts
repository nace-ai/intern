import { NextRequest, NextResponse } from "next/server";

const ANALYSIS_API = process.env.ANALYSIS_API_URL ?? "http://127.0.0.1:8000";

export async function POST(request: NextRequest) {
  let context: Record<string, unknown>;
  try {
    context = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON" }, { status: 400 });
  }
  if (context.contract_version !== "1.0" || typeof context.case_id !== "string" || typeof context.narrative !== "string") {
    return NextResponse.json({ detail: "Invalid CaseContext" }, { status: 422 });
  }
  try {
    const response = await fetch(`${ANALYSIS_API}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(context),
      cache: "no-store",
      signal: AbortSignal.timeout(125000),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ detail: "Analysis service unavailable or request timed out" }, { status: 503 });
  }
}
