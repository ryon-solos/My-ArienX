import type { Config } from "@netlify/functions"
import { createPublicKey } from "node:crypto"
import { bad, json } from "./_shared/http.js"
import { readDevice, requireUser, saveDevice } from "./_shared/auth.js"

export default async (req: Request) => {
  try {
    const user = await requireUser()
    if (req.method === "DELETE") {
      const id = new URL(req.url).searchParams.get("device_id") || ""; const device = await readDevice(id)
      if (!device || device.owner !== user.id) return bad("not found", 404)
      await saveDevice({ ...device, active: false, capabilities: [] })
      return json({ disconnected: true })
    }
    if (req.method !== "POST") return bad("method not allowed", 405)
    const body = await req.json(); const id = String(body.device_id || "")
    if (!/^[a-f0-9]{32}$/.test(id) || typeof body.public_key !== "string" || body.public_key.length > 2000) return bad("invalid device")
    try { createPublicKey(body.public_key) } catch { return bad("invalid device key") }
    const existing = await readDevice(id)
    if (existing && existing.owner !== user.id) return bad("device belongs to another account", 403)
    await saveDevice({ id, owner: user.id, publicKey: body.public_key, label: String(body.label || "ArienX device").slice(0, 80), capabilities: existing?.capabilities || [], lastSeen: existing?.lastSeen, active: true })
    return json({ registered: true, re_paired: !!existing })
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/register", method: ["POST", "DELETE"] }
