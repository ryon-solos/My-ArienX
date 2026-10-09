import type { Config } from '@netlify/functions'
import { randomBytes } from 'node:crypto'
import { requireUser } from './_shared/auth.js'
import { mobileStore, digest, validToken, type Session } from './_shared/mobile.js'
import { bad, json } from './_shared/http.js'
export default async (req: Request) => {
  if (req.method !== 'POST') return bad('method not allowed', 405)
  try {
    const store = mobileStore()
    const body = await req.json()
    if (new URL(req.url).pathname.endsWith('/redeem')) {
      if (!validToken(body.code)) return bad('invalid or expired pairing code', 400)
      const key = `pair/${digest(body.code)}`
      const pair = await store.getWithMetadata(key, {type: 'json'})
      if (!pair || pair.data.used || pair.data.expires < Date.now()) return bad('invalid or expired pairing code', 410)
      const claimed = await store.setJSON(key, {...pair.data, used: true}, {onlyIfMatch: pair.etag})
      if (!claimed.modified) return bad('pairing code already used', 409)
      const token = randomBytes(32).toString('hex'), id = digest(token)
      const session: Session = {id, owner: pair.data.owner, email: pair.data.email, label: String(body.label || 'ArienX phone').slice(0, 80), expires: Date.now() + 30 * 86400000, active: true, created: new Date().toISOString()}
      await store.setJSON(`session/${id}`, session)
      await store.setJSON(`owner/${session.owner}/${id}`, {id})
      return json({token, expires: session.expires, email: session.email})
    }
    // Only the existing Identity account can delegate a new mobile session.
    if (req.headers.has('x-arienx-session')) return bad('sign in on desktop to pair another phone', 403)
    const user = await requireUser(req)
    const code = randomBytes(32).toString('hex'), expires = Date.now() + 120000
    await store.setJSON(`pair/${digest(code)}`, {owner: user.id, email: user.email, expires, used: false})
    const url = new URL('arienx://pair')
    url.searchParams.set('cloud', new URL(req.url).origin); url.searchParams.set('code', code)
    return json({qr: url.toString(), expires})
  } catch { return bad('pairing unavailable', 401) }
}
export const config: Config = {path: ['/api/mobile/pair', '/api/mobile/redeem'], method: ['POST']}
