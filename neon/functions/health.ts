import { attachDatabasePool } from "@neon/functions";
import { parseEnv } from "@neon/env";
import { Pool } from "pg";

import config from "../../neon";

const { postgres } = parseEnv(config, ["DATABASE_URL"]);
const pool = new Pool({ connectionString: postgres.databaseUrl, max: 5 });
attachDatabasePool(pool);

export default async function health(): Promise<Response> {
  try {
    await pool.query("SELECT 1");
    return Response.json({ status: "ok", service: "agentflow-neon" });
  } catch {
    return Response.json({ status: "unavailable" }, { status: 503 });
  }
}
