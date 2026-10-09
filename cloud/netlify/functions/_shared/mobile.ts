import { getStore } from '@netlify/blobs'
import { createHash } from 'node:crypto'
export const mobileStore = () => getStore({name: 'arienx-mobile', consistency: 'strong'})
export const digest = (value: string) => createHash('sha256').update(value).digest('hex')
export const validToken = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
export type Session = {id: string; owner: string; email: string; label: string; expires: number; active: boolean; created: string}
export async function mobileUser(req?: Request) {
  const token = req?.headers.get('x-arienx-session')
  if (!token) return null
  if (!validToken(token)) throw new Error('unauthorized')
  const session = await mobileStore().get(`session/${digest(token)}`, {type: 'json'}) as Session | null
  if (!session?.active || session.expires < Date.now()) throw new Error('unauthorized')
  return {id: session.owner, email: session.email}
}
