import type { Config } from "@netlify/functions"
import { bad, json } from "./_shared/http.js"
import { requireDevice, saveDevice } from "./_shared/auth.js"

export default async (req: Request) => {
  if (req.method !== "POST") return bad("method not allowed", 405)
  const raw = await req.text()
  try {
    const device = await requireDevice(req, raw); const body = JSON.parse(raw)
    const capabilities = Array.isArray(body.capabilities) ? body.capabilities.filter((v: unknown) => ["computer", "files", "screen", "camera"].includes(String(v))).slice(0, 4) : []
    await saveDevice({ ...device, capabilities, lastSeen: new Date().toISOString() })
    return json({ online: true })
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/heartbeat", method: ["POST"] }
