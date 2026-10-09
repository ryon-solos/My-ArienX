import type { Config } from '@netlify/functions'
import { getStore } from '@netlify/blobs'
import { requireUser } from './_shared/auth.js'
import { bad } from './_shared/http.js'
import { conversations, validId } from './conversations.js'
import { streamReply } from './_shared/stream.js'
export default async (req: Request) => {
  if (req.method !== 'POST') return bad('method not allowed', 405)
  try {
    const user = await requireUser(req), b = await req.json()
    if (!validId(b.id) || typeof b.message !== 'string' || !b.message.trim() || b.message.length > 12000) return bad('invalid message')
    const store = conversations(), key = `user/${user.id}/${b.id}`, current = await store.getWithMetadata(key, {type:'json'})
    if (!current || current.data.deleted) return bad('not found', 404)
    if (b.revision !== current.data.revision) return bad('conversation changed; refresh before sending', 409)
    const lockStore = getStore({name:'arienx-chat-locks', consistency:'strong'})
    const lockKey = `${user.id}/${b.id}`, previous = await lockStore.getWithMetadata(lockKey, {type:'json'})
    if (previous && previous.data.expires > Date.now()) return bad('conversation is responding on another device', 409)
    const lock = await lockStore.setJSON(lockKey, {expires: Date.now() + 65000}, previous ? {onlyIfMatch:previous.etag} : {onlyIfNew:true})
    if (!lock.modified) return bad('conversation is busy', 409)
    const memory = await getStore({name:'arienx-memory', consistency:'strong'}).get(`memory/${user.id}`, {type:'json'})
    const encoder = new TextEncoder()
    const stream = new ReadableStream({async start(controller) {
      const emit = (value: unknown) => {try {controller.enqueue(encoder.encode(`data: ${JSON.stringify(value)}\n\n`))} catch { /* Client can disconnect while the cloud saves its reply. */ }}
      try {
        let answer = ''
        for await (const delta of streamReply(b.message, b.provider, current.data.messages, JSON.stringify(memory?.facts || {}))) {answer += delta; if (answer.length > 60000) throw new Error('Response exceeded limit'); emit({delta})}
        if (!answer) throw new Error('Provider returned an empty response')
        const chat = {...current.data, revision: current.data.revision + 1, updated: new Date().toISOString(), messages:[...current.data.messages, {role:'user',text:b.message}, {role:'assistant',text:answer}].slice(-160)}
        if (chat.title === 'New conversation') chat.title = b.message.slice(0,80)
        const saved = await store.setJSON(key, chat, {onlyIfMatch:current.etag})
        if (!saved.modified) throw new Error('Conversation changed; response was not saved. Keep your draft and refresh.')
        emit({done:true, conversation:chat})
      } catch (e) { emit({error: e instanceof Error ? e.message : 'Cloud chat failed'}) }
      finally { await lockStore.delete(lockKey); try {controller.close()} catch {} }
    }})
    return new Response(stream, {headers:{'Content-Type':'text/event-stream','Cache-Control':'no-store'}})
  } catch { return bad('chat unavailable or session expired', 401) }
}
export const config: Config = {path:'/api/mobile/chat', method:['POST']}
