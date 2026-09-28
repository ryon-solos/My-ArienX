import type { Config } from "@netlify/functions"
import { getStore } from "@netlify/blobs"
import { bad, json } from "./_shared/http.js"
import { requireUser } from "./_shared/auth.js"

type SafeMemory = { revision: number; facts: Record<string, { category: string; key: string; value: string; updated_at: string }> }
const empty = (): SafeMemory => ({ revision: 0, facts: {} })
const allowed = new Set(["preferences", "projects", "notes"])
const secret = /api[_ -]?key|token|password|secret|private[_ -]?key/i
export default async (req: Request) => {
  try {
    const user = await requireUser(); const store = getStore({ name: "arienx-memory", consistency: "strong" }); const key = `memory/${user.id}`
    const current = (await store.get(key, { type: "json" }) as SafeMemory | null) || empty()
    if (req.method === "GET") return json(current)
    if (req.method !== "PUT") return bad("method not allowed", 405)
    const body = await req.json()
    if (Number(body.base_revision) !== current.revision) return json({ error: "sync_conflict", current }, 409)
    const facts = Array.isArray(body.facts) ? body.facts : []
    for (const fact of facts) if (!allowed.has(fact.category) || !fact.key || !fact.value || secret.test(`${fact.key} ${fact.value}`)) return bad("unsafe memory payload")
    for (const fact of facts) current.facts[`${fact.category}/${fact.key}`] = { category: fact.category, key: String(fact.key).slice(0, 80), value: String(fact.value).slice(0, 380), updated_at: new Date().toISOString() }
    current.revision += 1; await store.setJSON(key, current); return json(current)
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/memory", method: ["GET", "PUT"] }
