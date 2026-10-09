import type {Config} from '@netlify/functions'
import {getStore} from '@netlify/blobs'
import {randomUUID} from 'node:crypto'
import {requireUser,requireDevice} from './_shared/auth.js'
import {bad,json} from './_shared/http.js'
export default async(req:Request)=>{
 try {
  const raw=req.method==='POST'?await req.text():''
  if(raw.length>4400000)return bad('maximum file size is 3 MB',413)
  const owner=req.headers.has('x-arienx-device')?(await requireDevice(req,raw)).owner:(await requireUser(req)).id
  const store=getStore({name:'arienx-transfers',consistency:'strong'}),prefix=`user/${owner}/`
  if(req.method==='POST'){
   const b=JSON.parse(raw)
   if(typeof b.name!=='string'||typeof b.data!=='string'||!b.data.length||!/^[A-Za-z0-9+/]*={0,2}$/.test(b.data))return bad('invalid file')
   const data=Buffer.from(b.data,'base64');if(data.length>3*1024*1024)return bad('maximum file size is 3 MB',413)
   const id=randomUUID(),meta={id,name:b.name.replace(/[^a-zA-Z0-9._ -]/g,'_').slice(0,120)||'download',size:data.length,created:new Date().toISOString()}
   await store.set(prefix+id,new Uint8Array(data).buffer);await store.setJSON(prefix+id+'.json',meta);return json(meta,201)
  }
  if(req.method!=='GET')return bad('method not allowed',405)
  const id=new URL(req.url).searchParams.get('id')
  if(id){if(!/^[a-f0-9-]{36}$/.test(id))return bad('invalid file id');const meta=await store.get(prefix+id+'.json',{type:'json'});if(!meta)return bad('not found',404);const data=await store.get(prefix+id,{type:'arrayBuffer'});if(!data)return bad('not found',404);return json({...meta,data:Buffer.from(data).toString('base64')})}
  const {blobs}=await store.list({prefix});const files=await Promise.all(blobs.filter(b=>b.key.endsWith('.json')).map(b=>store.get(b.key,{type:'json'})));return json({files:files.filter(Boolean)})
 }catch{return bad('file request failed or unauthorized',401)}
}
export const config:Config={path:'/api/mobile/files',method:['GET','POST']}
