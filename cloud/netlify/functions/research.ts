import type { Config } from "@netlify/functions"
import { bad, json } from "./_shared/http.js"
import { requireUser } from "./_shared/auth.js"
import { research } from "./_shared/provider.js"

export default async (req: Request) => {
  if (req.method !== "POST") return bad("method not allowed", 405)
  try { await requireUser(req); const body = await req.json(); return json({ answer: await research(body.query) }) }
  catch (error) { return bad(error instanceof Error ? error.message : "request failed", 401) }
}
export const config: Config = { path: "/api/research", method: ["POST"] }
