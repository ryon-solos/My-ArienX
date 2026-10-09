import type { Config } from "@netlify/functions"
import { getStore } from "@netlify/blobs"
import { bad, json } from "./_shared/http.js"
import { requireUser } from "./_shared/auth.js"

type SafeMemory = { revision: number; facts: Record<string, { category: string; key: string; value: string; updated_at: string }> }
const empty = (): SafeMemory => ({ revision: 0, facts: {} })
const allowed = new Set(["identity", "preferences", "projects", "relationships", "wishes", "notes", "assistant", "settings", "summaries"])
const secret = /api[_ -]?key|oauth|token|password|secret|private[_ -]?key|ssh[_ -]?key|cookie|camera|screen[_ -]?(?:capture|frame)|file(?:path|name)?/i
export default async (req: Request) => {
  try {
    const user = await requireUser(req); const store = getStore({ name: "arienx-memory", consistency: "strong" }); const key = `memory/${user.id}`
    const existing = await store.getWithMetadata(key, {type: "json"})
    const current = (existing?.data as SafeMemory | null) || empty()
    if (req.method === "GET") return json(current)
    if (req.method !== "PUT") return bad("method not allowed", 405)
    const body = await req.json()
    if (Number(body.base_revision) !== current.revision) return json({ error: "sync_conflict", current }, 409)
    if (!Array.isArray(body.facts) || body.facts.length > 100 || JSON.stringify(body).length > 60000) return bad("invalid memory batch")
    const facts = body.facts
    for (const fact of facts) if (!allowed.has(fact.category) || typeof fact.key !== "string" || typeof fact.value !== "string" || !fact.key || !fact.value || fact.key.length > 80 || fact.value.length > 380 || secret.test(`${fact.key} ${fact.value}`)) return bad("unsafe memory payload")
    for (const fact of facts) current.facts[`${fact.category}/${fact.key}`] = { category: fact.category, key: String(fact.key).slice(0, 80), value: String(fact.value).slice(0, 380), updated_at: new Date().toISOString() }
    current.revision += 1
    const saved = await store.setJSON(key, current, existing ? {onlyIfMatch: existing.etag} : {onlyIfNew: true})
    if (!saved.modified) return json({error: "sync_conflict", current: await store.get(key, {type: "json"})}, 409)
    return json(current)
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/memory", method: ["GET", "PUT"] }
