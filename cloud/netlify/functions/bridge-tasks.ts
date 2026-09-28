import type { Config } from "@netlify/functions"
import { getStore } from "@netlify/blobs"
import { randomUUID } from "node:crypto"
import { bad, json } from "./_shared/http.js"
import { readDevice, requireDevice, requireUser } from "./_shared/auth.js"

type Task = { id: string; action: string; args: Record<string, unknown>; status: "queued" | "claimed" | "complete"; result?: string }
const store = () => getStore({ name: "arienx-tasks", consistency: "strong" })
const allowed = new Set(["system_status", "screen_process", "close_camera", "open_app", "browser_control", "file_controller", "computer_control"])
const key = (id: string) => `tasks/${id}`

export default async (req: Request) => {
  const raw = req.method === "GET" ? "" : await req.text()
  try {
    if (req.method === "POST" && !req.headers.get("x-arienx-device")) {
      const user = await requireUser(); const body = JSON.parse(raw); const device = await readDevice(String(body.device_id || ""))
      if (!device || device.owner !== user.id || !allowed.has(body.action)) return bad("invalid task", 400)
      const tasks = (await store().get(key(device.id), { type: "json" }) as Task[] | null) || []
      const task: Task = { id: randomUUID(), action: body.action, args: typeof body.args === "object" && body.args ? body.args : {}, status: "queued" }
      tasks.push(task); await store().setJSON(key(device.id), tasks.slice(-50)); return json({ task_id: task.id, status: task.status })
    }
    const device = await requireDevice(req, raw); const tasks = (await store().get(key(device.id), { type: "json" }) as Task[] | null) || []
    if (req.method === "GET") { const pending = tasks.filter(task => task.status === "queued"); pending.forEach(task => task.status = "claimed"); await store().setJSON(key(device.id), tasks); return json({ tasks: pending }) }
    if (req.method === "PUT") { const body = JSON.parse(raw); const task = tasks.find(item => item.id === body.task_id && item.status === "claimed"); if (!task) return bad("task not found", 404); task.status = "complete"; task.result = String(body.result || "").slice(0, 1000); await store().setJSON(key(device.id), tasks); return json({ complete: true }) }
    return bad("method not allowed", 405)
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/tasks", method: ["GET", "POST", "PUT"] }
