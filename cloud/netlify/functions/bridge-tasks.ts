import type { Config } from "@netlify/functions"
import { getStore } from "@netlify/blobs"
import { randomUUID } from "node:crypto"
import { bad, json } from "./_shared/http.js"
import { readDevice, requireDevice, requireUser } from "./_shared/auth.js"

type Task = { id: string; action: string; args: Record<string, unknown>; status: "queued" | "claimed" | "complete"; result?: string; claimedAt?: string; attempts: number; expiresAt: string }
const store = () => getStore({ name: "arienx-tasks", consistency: "strong" })
const allowed = new Set(["system_status", "screen_process", "close_camera", "open_app", "browser_control", "file_controller", "computer_control"])
const key = (id: string) => `tasks/${id}`
const now = () => Date.now()
const taskAlive = (task: Task) => Date.parse(task.expiresAt) > now()
const requeue = (task: Task) => task.status === "claimed" && (!task.claimedAt || now() - Date.parse(task.claimedAt) > 60_000)

export default async (req: Request) => {
  const raw = req.method === "GET" ? "" : await req.text()
  try {
    if (req.method === "POST" && !req.headers.get("x-arienx-device")) {
      const user = await requireUser(); const body = JSON.parse(raw); const device = await readDevice(String(body.device_id || ""))
      const args = typeof body.args === "object" && body.args && !Array.isArray(body.args) ? body.args as Record<string, unknown> : {}
      if (!device || !device.active || device.owner !== user.id || !allowed.has(body.action) || JSON.stringify(args).length > 8_000) return bad("invalid task", 400)
      const tasks = (await store().get(key(device.id), { type: "json" }) as Task[] | null) || []
      const task: Task = { id: randomUUID(), action: body.action, args, status: "queued", attempts: 0, expiresAt: new Date(now() + 10 * 60_000).toISOString() }
      tasks.push(task); await store().setJSON(key(device.id), tasks.filter(taskAlive).slice(-50)); return json({ task_id: task.id, status: task.status })
    }
    if (req.method === "GET" && !req.headers.get("x-arienx-device")) {
      const user = await requireUser(); const id = new URL(req.url).searchParams.get("device_id") || ""; const device = await readDevice(id)
      if (!device || device.owner !== user.id) return bad("not found", 404)
      const tasks = ((await store().get(key(device.id), { type: "json" }) as Task[] | null) || []).filter(taskAlive)
      return json({ tasks: tasks.slice(-20).map(task => ({ id: task.id, action: task.action, status: task.status, result: task.result || null })) })
    }
    const device = await requireDevice(req, raw); const tasks = ((await store().get(key(device.id), { type: "json" }) as Task[] | null) || []).filter(taskAlive)
    if (req.method === "GET") {
      tasks.filter(requeue).forEach(task => { task.status = "queued"; delete task.claimedAt })
      const pending = tasks.filter(task => task.status === "queued").slice(0, 10)
      pending.forEach(task => { task.status = "claimed"; task.claimedAt = new Date().toISOString(); task.attempts += 1 })
      await store().setJSON(key(device.id), tasks); return json({ tasks: pending })
    }
    if (req.method === "PUT") { const body = JSON.parse(raw); const task = tasks.find(item => item.id === body.task_id && item.status === "claimed"); if (!task) return bad("task not found", 404); task.status = "complete"; task.result = String(body.result || "").slice(0, 1000); await store().setJSON(key(device.id), tasks); return json({ complete: true }) }
    return bad("method not allowed", 405)
  } catch { return bad("unauthorized", 401) }
}
export const config: Config = { path: "/api/bridge/tasks", method: ["GET", "POST", "PUT"] }
