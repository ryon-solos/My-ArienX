import type {Config} from '@netlify/functions'
import {requireUser} from './_shared/auth.js'
import {mobileStore,digest,validToken,type Session} from './_shared/mobile.js'
import {bad,json} from './_shared/http.js'
export default async(req:Request)=>{
  if(req.method!=='POST')return bad('method not allowed',405)
  try{await requireUser(req);const token=req.headers.get('x-arienx-session');if(!validToken(token))return bad('invalid session');const key=`session/${digest(token)}`,store=mobileStore();const s=await store.get(key,{type:'json'}) as Session;await store.setJSON(key,{...s,active:false});return json({revoked:true})}catch{return bad('unauthorized',401)}
}
export const config:Config={path:'/api/mobile/logout',method:['POST']}
