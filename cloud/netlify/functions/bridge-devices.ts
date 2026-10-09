import type { Config } from "@netlify/functions"
import { getStore } from "@netlify/blobs"
import { bad, json } from "./_shared/http.js"
import { type Device, requireUser } from "./_shared/auth.js"

export default async (req: Request) => {
  if (req.method !== "GET") return bad("method not allowed", 405)
  try {
    const user = await requireUser(req); const store = getStore({ name: "arienx-devices", consistency: "strong" })
    const { blobs } = await store.list({ prefix: "device/" })
    const devices = await Promise.all(blobs.map(async ({ key }) => await store.get(key, { type: "json" }) as Device | null))
    return json({ devices: devices.filter((device): device is Device => !!device && device.owner === user.id).map(device => ({
      device_id: device.id, label: device.label, paired: device.active,
      online: device.active && !!device.lastSeen && Date.now() - Date.parse(device.lastSeen) < 90_000,
      capabilities: device.active ? device.capabilities : [], last_seen: device.lastSeen || null,
    })) })
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/devices", method: ["GET"] }
