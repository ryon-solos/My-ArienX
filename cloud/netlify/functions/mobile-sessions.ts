import type { Config } from '@netlify/functions'
import { requireUser } from './_shared/auth.js'
import { mobileStore, type Session, validToken } from './_shared/mobile.js'
import { bad, json } from './_shared/http.js'
export default async (req: Request) => {
  try {
    const user = await requireUser(req), store = mobileStore()
    if (req.method === 'GET') {
      const {blobs} = await store.list({prefix: `owner/${user.id}/`})
      const sessions = await Promise.all(blobs.map(async ({key}) => await store.get(`session/${key.split('/').pop()}`, {type: 'json'}) as Session | null))
      return json({sessions: sessions.filter((s): s is Session => !!s && s.owner === user.id).map(({id, label, active, expires, created}) => ({id, label, active, expires, created}))})
    }
    if (!['DELETE', 'PATCH'].includes(req.method)) return bad('method not allowed', 405)
    const body = await req.json()
    if (!validToken(body.id)) return bad('invalid session')
    const key = `session/${body.id}`, session = await store.get(key, {type: 'json'}) as Session | null
    if (!session || session.owner !== user.id) return bad('not found', 404)
    if (req.method === 'DELETE') session.active = false
    else { if (typeof body.label !== 'string' || !body.label.trim() || body.label.length > 80) return bad('invalid name'); session.label = body.label.trim() }
    await store.setJSON(key, session)
    return json({saved: true})
  } catch { return bad('unauthorized', 401) }
}
export const config: Config = {path: '/api/mobile/sessions', method: ['GET', 'PATCH', 'DELETE']}
