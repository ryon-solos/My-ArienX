import type { Config } from '@netlify/functions'
import { getStore } from '@netlify/blobs'
import { randomUUID } from 'node:crypto'
import { requireUser } from './_shared/auth.js'
import { bad, json } from './_shared/http.js'
export const conversations = () => getStore({name: 'arienx-conversations', consistency: 'strong'})
export const validId = (id: unknown): id is string => typeof id === 'string' && /^[a-f0-9-]{32,36}$/.test(id)
const messages = (value: unknown) => Array.isArray(value) ? value.filter((m): m is {role:string,text:string} => !!m && (m.role === 'user' || m.role === 'assistant') && typeof m.text === 'string' && !!m.text.trim()).slice(-160).map(m => ({role:m.role, text:m.text.trim().slice(0,60000)})) : []
export default async (req: Request) => {
  try {
    const user = await requireUser(req), store = conversations(), prefix = `user/${user.id}/`
    const id = new URL(req.url).searchParams.get('id')
    if (req.method === 'GET') {
      if (id) { if (!validId(id)) return bad('invalid id'); const item = await store.get(prefix + id, {type: 'json'}); return item && !item.deleted ? json(item) : bad('not found', 404) }
      const {blobs} = await store.list({prefix})
      const items = await Promise.all(blobs.map(async ({key}) => await store.get(key, {type: 'json'})))
      return json({conversations: items.filter(item => item && !item.deleted).map(({id, title, pinned, updated, revision}) => ({id, title, pinned, updated, revision})).sort((a,b) => b.updated.localeCompare(a.updated)), deleted_ids: items.filter(item => item?.deleted).map(item => item.id)})
    }
    if (req.method === 'POST') {
      const b = await req.json(), id = validId(b.id) ? b.id : randomUUID()
      const chat = {id, title: String(b.title || 'New conversation').slice(0, 80), pinned: false, revision: 0, updated: new Date().toISOString(), messages: messages(b.messages)}
      const saved = await store.setJSON(prefix + id, chat, {onlyIfNew: true})
      return saved.modified ? json(chat, 201) : bad('conversation already exists', 409)
    }
    if (req.method === 'DELETE') {
      if (!validId(id)) return bad('invalid id')
      const current = await store.getWithMetadata(prefix + id, {type: 'json'})
      if (current?.data.deleted) return json({deleted: true, id})
      // Retain only an ID tombstone, so an offline device cannot resurrect content.
      const saved = await store.setJSON(prefix + id, {id, deleted: true, messages: [], revision: (current?.data.revision || 0) + 1, updated: new Date().toISOString()}, current ? {onlyIfMatch: current.etag} : {onlyIfNew: true})
      return saved.modified ? json({deleted: true, id}) : bad('conversation changed; retry deletion', 409)
    }
    if (req.method === 'PATCH') {
      const b = await req.json(); if (!validId(b.id)) return bad('invalid id')
      const current = await store.getWithMetadata(prefix + b.id, {type: 'json'})
      if (!current || current.data.deleted) return bad('not found', 404)
      if (b.revision !== current.data.revision) return bad('conversation changed; refresh first', 409)
      const updated = {...current.data, revision: current.data.revision + 1, title: typeof b.title === 'string' ? b.title.slice(0,80) : current.data.title, pinned: typeof b.pinned === 'boolean' ? b.pinned : current.data.pinned, messages: Array.isArray(b.messages) ? messages(b.messages) : current.data.messages}
      const saved = await store.setJSON(prefix + b.id, updated, {onlyIfMatch: current.etag})
      return saved.modified ? json(updated) : bad('conversation changed; refresh first', 409)
    }
    return bad('method not allowed', 405)
  } catch { return bad('unauthorized', 401) }
}
export const config: Config = {path: '/api/conversations', method: ['GET', 'POST', 'PATCH', 'DELETE']}
