import type { Config } from '@netlify/functions'
import { getStore } from '@netlify/blobs'
import { randomUUID } from 'node:crypto'
import { bad, json } from './_shared/http.js'
import { readDevice, requireDevice, requireUser } from './_shared/auth.js'
type Task = {id:string; action:string; args:Record<string,unknown>; status:string; result?:string; claimedAt?:string; attempts:number; expiresAt:string; createdAt?:string; requestId?:string}
const allowed = new Set(['mobile_receive_file','system_status','computer_settings','open_app','browser_control','file_controller','computer_control','extension_request'])
export default async (req:Request) => {
  const raw = ['GET'].includes(req.method) ? '' : await req.text()
  if (raw.length > 12000) return bad('task too large',413)
  try {
    const signed = req.headers.has('x-arienx-device')
    const body = raw ? JSON.parse(raw) : {}
    const device = signed ? await requireDevice(req,raw) : await readDevice(String(body.device_id || new URL(req.url).searchParams.get('device_id') || ''))
    if (!device?.active) return bad('device not found',404)
    if (!signed && device.owner !== (await requireUser(req)).id) return bad('not found',404)
    const store = getStore({name:'arienx-tasks',consistency:'strong'}), key = `tasks/${device.id}`
    const existing = await store.getWithMetadata(key,{type:'json'})
    const tasks:Task[] = (existing?.data || []).filter((t:Task) => Date.parse(t.expiresAt) + 86400000 > Date.now())
    for (const task of tasks) if (task.status === 'queued' && Date.parse(task.expiresAt) <= Date.now()) task.status = 'expired'
    let result:unknown
    if (req.method === 'GET' && !signed) return json({tasks:tasks.map(({args, ...t}) => t)})
    if (req.method === 'POST' && !signed) {
      if (!allowed.has(body.action) || !body.args || typeof body.args !== 'object' || Array.isArray(body.args) || JSON.stringify(body.args).length > 8000) return bad('invalid task')
      if (body.action === 'extension_request' && !['list','open','enable','disable','remove','rollback','create','update'].includes(body.args.operation)) return bad('invalid extension operation')
      if (typeof body.request_id !== 'undefined' && !/^[a-f0-9-]{32,36}$/.test(body.request_id)) return bad('invalid request id')
      const previous = body.request_id && tasks.find(t => t.requestId === body.request_id)
      if (previous) return json({task_id:previous.id,status:previous.status})
      if (tasks.filter(t => ['queued','claimed'].includes(t.status)).length >= 50) return bad('queue full',429)
      const task:Task = {id:randomUUID(), action:body.action,args:body.args,status:'queued',attempts:0,createdAt:new Date().toISOString(),expiresAt:new Date(Date.now()+86400000).toISOString(),requestId:body.request_id}
      tasks.push(task); result = {task_id:task.id,status:task.status}
    } else if (req.method === 'DELETE' && !signed) {
      const task = tasks.find(t => t.id === body.task_id && t.status === 'queued')
      if (!task) return bad('only queued tasks can be cancelled',409)
      task.status = 'cancelled'; result = {cancelled:true}
    } else if (req.method === 'GET' && signed) {
      // Never replay a claimed command: a disconnect may happen after a destructive action.
      const pending = tasks.filter(t => t.status === 'queued').slice(0,10)
      pending.forEach(t => {t.status='claimed';t.claimedAt=new Date().toISOString();t.attempts++})
      result = {tasks:pending}
    } else if (req.method === 'PUT' && signed) {
      const task=tasks.find(t => t.id===body.task_id && t.status==='claimed')
      if (!task) return bad('task not found',404)
      task.status='complete'; task.result=String(body.result || '').slice(0,1000); result={complete:true}
    } else return bad('method not allowed',405)
    const saved=await store.setJSON(key,tasks,existing ? {onlyIfMatch:existing.etag}:{onlyIfNew:true})
    return saved.modified ? json(result) : bad('queue changed; retry',409)
  } catch { return bad('unauthorized or invalid request',401) }
}
export const config:Config={path:'/api/bridge/tasks',method:['GET','POST','PUT','DELETE']}
