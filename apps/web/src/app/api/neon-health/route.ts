import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const functionUrl = process.env.NEON_FUNCTION_HEALTH_BASE_URL;
  if (!functionUrl) {
    return NextResponse.json(
      { status: "unconfigured", service: "agentflow-neon" },
      { status: 503 },
    );
  }

  try {
    const response = await fetch(functionUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    const body = await response.json();
    return NextResponse.json(body, { status: response.status });
  } catch {
    return NextResponse.json(
      { status: "unavailable", service: "agentflow-neon" },
      { status: 503 },
    );
  }
}
