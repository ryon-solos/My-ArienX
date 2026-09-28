import type { Config } from "@netlify/functions"
import { bad, json } from "./_shared/http.js"
import { readDevice, requireUser } from "./_shared/auth.js"

export default async (req: Request) => {
  if (req.method !== "GET") return bad("method not allowed", 405)
  try {
    const user = await requireUser(); const id = new URL(req.url).searchParams.get("device_id") || ""; const device = await readDevice(id)
    if (!device || device.owner !== user.id) return bad("not found", 404)
    const online = !!device.lastSeen && Date.now() - Date.parse(device.lastSeen) < 90_000
    return json({ device_id: device.id, label: device.label, paired: device.active, online: device.active && online, capabilities: device.active && online ? device.capabilities : [], last_seen: device.lastSeen || null })
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/status", method: ["GET"] }
