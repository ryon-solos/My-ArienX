import { mobileUser } from "./mobile.js"
import { getUser } from "@netlify/identity"
import { getStore } from "@netlify/blobs"
import { createHash, createPublicKey, verify } from "node:crypto"

export type Device = { id: string; owner: string; publicKey: string; label: string; capabilities: string[]; lastSeen?: string; active: boolean }
const devices = () => getStore({ name: "arienx-devices", consistency: "strong" })

export async function requireUser(req?: Request) {
  const mobile = await mobileUser(req)
  if (mobile) return mobile
  const user = await getUser()
  if (!user) throw new Error("unauthorized")
  return user
}

export async function readDevice(id: string): Promise<Device | null> {
  return await devices().get(`device/${id}`, { type: "json" }) as Device | null
}

export async function requireDevice(req: Request, body: string): Promise<Device> {
  const id = req.headers.get("x-arienx-device") || ""
  const stamp = req.headers.get("x-arienx-time") || ""
  const signature = req.headers.get("x-arienx-signature") || ""
  if (!/^[a-f0-9]{32}$/.test(id) || !/^\d{10}$/.test(stamp) || !signature || Math.abs(Date.now() / 1000 - Number(stamp)) > 120) throw new Error("unauthorized")
  const device = await readDevice(id)
  if (!device || !device.active) throw new Error("unauthorized")
  const digest = createHash("sha256").update(body).digest("hex")
  const signed = `${stamp}\n${req.method}\n${new URL(req.url).pathname}\n${digest}`
  if (!verify(null, Buffer.from(signed), createPublicKey(device.publicKey), Buffer.from(signature, "base64"))) throw new Error("unauthorized")
  return device
}

export async function saveDevice(device: Device) { await devices().setJSON(`device/${device.id}`, device) }
