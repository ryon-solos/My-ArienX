import type { Config } from "@netlify/functions"
import { bad, json } from "./_shared/http.js"
import { requireUser, saveDevice } from "./_shared/auth.js"

export default async (req: Request) => {
  if (req.method !== "POST") return bad("method not allowed", 405)
  try {
    const user = await requireUser(); const body = await req.json()
    if (!/^[a-f0-9]{32}$/.test(body.device_id || "") || typeof body.public_key !== "string" || body.public_key.length > 2000) return bad("invalid device")
    await saveDevice({ id: body.device_id, owner: user.id, publicKey: body.public_key, label: String(body.label || "ArienX device").slice(0, 80), capabilities: [] })
    return json({ registered: true })
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/register", method: ["POST"] }
