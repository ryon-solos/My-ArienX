import type { Config } from '@netlify/functions'
import { getStore } from '@netlify/blobs'
import { readDevice, requireDevice, requireUser } from './_shared/auth.js'
import { bad, json } from './_shared/http.js'
export default async (req: Request) => {
  try {
    const store = getStore({name: 'arienx-telemetry', consistency: 'strong'})
    if (req.method === 'GET') {
      const user = await requireUser(req), id = new URL(req.url).searchParams.get('device_id') || ''
      const device = await readDevice(id)
      if (!device || device.owner !== user.id || !device.active) return bad('not found', 404)
      return json(await store.get(id, {type: 'json'}) || {at: null, workers: [], extensions: []})
    }
    if (req.method !== 'POST') return bad('method not allowed', 405)
    const raw = await req.text()
    if (raw.length > 40000) return bad('snapshot too large', 413)
    const device = await requireDevice(req, raw), b = JSON.parse(raw)
    const percent = (v: unknown) => typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : null
    const text = (v: unknown, max = 80) => String(v || '').slice(0, max)
    const workers = Array.isArray(b.workers) ? b.workers.slice(0, 30).map((w: any) => ({id: text(w.id), heading: text(w.heading), provider: text(w.provider), model: text(w.model), status: text(w.status), progress: percent(w.progress), runtime: typeof w.runtime === 'number' ? Math.max(0, w.runtime) : null})) : []
    const extensions = Array.isArray(b.extensions) ? b.extensions.slice(0, 100).map((a: any) => ({id: text(a.id), name: text(a.name), version: text(a.version), enabled: a.enabled === true, status: text(a.status), permissions: Array.isArray(a.permissions) ? a.permissions.map((p: unknown) => text(p, 30)).slice(0, 20) : []})) : []
    await store.setJSON(device.id, {at: new Date().toISOString(), cpu: percent(b.cpu), ram: percent(b.ram), battery: percent(b.battery), workers, extensions})
    return json({saved: true})
  } catch { return bad('unauthorized', 401) }
}
export const config: Config = {path: '/api/bridge/telemetry', method: ['GET', 'POST']}
