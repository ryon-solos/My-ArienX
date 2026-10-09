import {test,mock} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,generateKeyPairSync,sign} from 'node:crypto';
const values=new Map();let user={id:'owner-a',email:'a@example.test'},version=0;
const store=(name)=>({
 async get(key){return structuredClone(values.get(name+'/'+key)?.data??null)},
 async getWithMetadata(key){return structuredClone(values.get(name+'/'+key)??null)},
 async setJSON(key,data,options={}){return this.set(key,data,options)},
 async set(key,data,options={}){const k=name+'/'+key,current=values.get(k);if(options.onlyIfNew&&current || options.onlyIfMatch&&current?.etag!==options.onlyIfMatch)return {modified:false};const etag=String(++version);values.set(k,{data:structuredClone(data),etag});return {modified:true,etag}},
 async delete(key){values.delete(name+'/'+key)},
 async list({prefix='' }={}){return {blobs:[...values.keys()].filter(k=>k.startsWith(name+'/'+prefix)).map(k=>({key:k.slice(name.length+1)}))}}
});
mock.module('@netlify/blobs',{namedExports:{getStore:({name})=>store(name)}});
let signupInput=null,originChecks=0;
mock.module('@netlify/identity',{namedExports:{
 getUser:async()=>user,
 signup:async(email,password)=>{signupInput={email,password};return {id:'new-user',email,confirmedAt:undefined}},
 login:async()=>{},logout:async()=>{},acceptInvite:async()=>{},confirmEmail:async()=>{},verifyRequestOrigin:()=>{originChecks++}
}});
const base='../.test-build/';
const pair=(await import(base+'mobile-pair.js')).default;
const sessions=(await import(base+'mobile-sessions.js')).default;
const memory=(await import(base+'memory.js')).default;
const tasks=(await import(base+'bridge-tasks.js')).default;
const telemetry=(await import(base+'bridge-telemetry.js')).default;
const files=(await import(base+'mobile-files.js')).default;
const conversation=(await import(base+'conversations.js')).default;
const chat=(await import(base+'mobile-chat.js')).default;
const auth=(await import(base+'auth.js')).default;
const {requireUser,requireDevice}=await import(base+'_shared/auth.js');
const {events}=await import(base+'_shared/stream.js');
function req(path,method='GET',body,headers={}){return new Request('https://cloud.example'+path,{method,headers:{'Content-Type':'application/json',...headers},body:body===undefined?undefined:JSON.stringify(body)})}
const {privateKey,publicKey}=generateKeyPairSync('ed25519');
const device={id:'a'.repeat(32),owner:'owner-a',label:'desktop',active:true,capabilities:[],publicKey:publicKey.export({format:'pem',type:'spki'})};
function signed(path,method='GET',body){const raw=body?JSON.stringify(body):'',stamp=String(Math.floor(Date.now()/1000));return req(path,method,body,{'x-arienx-device':device.id,'x-arienx-time':stamp,'x-arienx-signature':sign(null,Buffer.from(`${stamp}\n${method}\n${path}\n${createHash('sha256').update(raw).digest('hex')}`),privateKey).toString('base64')})}
async function seed(){values.clear();user={id:'owner-a',email:'a@example.test'};await store('arienx-devices').setJSON('device/'+device.id,device)}
test('pairing is single use, account scoped, and revocable',async()=>{
 await seed();const issued=await (await pair(req('/api/mobile/pair','POST',{}))).json();const code=new URL(issued.qr).searchParams.get('code');
 const results=await Promise.all([pair(req('/api/mobile/redeem','POST',{code})),pair(req('/api/mobile/redeem','POST',{code}))]);assert.equal(results.filter(r=>r.status===200).length,1);
 const session=await results.find(r=>r.status===200).json();user=null;assert.equal((await requireUser(req('/api/memory','GET',undefined,{'x-arienx-session':session.token}))).id,'owner-a');
 user={id:'other',email:'other@example.test'};const id=createHash('sha256').update(session.token).digest('hex');assert.equal((await sessions(req('/api/mobile/sessions','DELETE',{id}))).status,404);
 user={id:'owner-a'};assert.equal((await sessions(req('/api/mobile/sessions','DELETE',{id}))).status,200);await assert.rejects(requireUser(req('/api/memory','GET',undefined,{'x-arienx-session':session.token})));
 const expiredCode='b'.repeat(64);await store('arienx-mobile').setJSON('pair/'+createHash('sha256').update(expiredCode).digest('hex'),{owner:'owner-a',expires:Date.now()-1});assert.equal((await pair(req('/api/mobile/redeem','POST',{code:expiredCode}))).status,410);
});
test('memory accepts cloud-safe identity and rejects secrets and stale writes',async()=>{await seed();const body={base_revision:0,facts:[{category:'identity',key:'name',value:'Ryon'}]};const result=await Promise.all([memory(req('/api/memory','PUT',body)),memory(req('/api/memory','PUT',body))]);assert.deepEqual(result.map(r=>r.status).sort(),[200,409]);assert.equal((await memory(req('/api/memory','PUT',{base_revision:1,facts:[{category:'notes',key:'api_key',value:'private'}]}))).status,400);assert.equal((await memory(req('/api/memory','PUT',{base_revision:1,facts:[{category:'notes',key:'browser_cookie',value:'private'}]}))).status,400)});
test('queue is idempotent, cancellable, owner scoped and never replays claimed actions',async()=>{await seed();const b={device_id:device.id,action:'open_app',args:{app_name:'Calculator'},request_id:'b'.repeat(32)};const queued=await (await tasks(req('/api/bridge/tasks','POST',b))).json();const again=await (await tasks(req('/api/bridge/tasks','POST',b))).json();assert.equal(queued.task_id,again.task_id);const first=await (await tasks(signed('/api/bridge/tasks'))).json();assert.equal(first.tasks.length,1);const second=await (await tasks(signed('/api/bridge/tasks'))).json();assert.equal(second.tasks.length,0);assert.equal((await tasks(req('/api/bridge/tasks','DELETE',{device_id:device.id,task_id:queued.task_id}))).status,409);user={id:'other'};assert.equal((await tasks(req('/api/bridge/tasks?device_id='+device.id))).status,404)});
test('signatures reject nonfinite timestamps and telemetry drops unapproved fields',async()=>{await seed();await assert.rejects(requireDevice(req('/api/bridge/tasks','GET',undefined,{'x-arienx-device':device.id,'x-arienx-time':'NaN','x-arienx-signature':'bad'}),''));const response=await telemetry(signed('/api/bridge/telemetry','POST',{cpu:500,private_key:'secret',workers:[{id:'one',heading:'Worker',diagnostic:'private result'}],extensions:[]}));assert.equal(response.status,200);const saved=await (await telemetry(req('/api/bridge/telemetry?device_id='+device.id))).json();assert.equal(saved.cpu,100);assert.equal(JSON.stringify(saved).includes('private'),false)});
test('explicit files are sanitized and isolated by account',async()=>{await seed();const file=await (await files(req('/api/mobile/files','POST',{name:'../../note.txt',data:Buffer.from('hello').toString('base64')}))).json();assert.ok(!file.name.includes('/'));const downloaded=await (await files(req('/api/mobile/files?id='+file.id))).json();assert.equal(Buffer.from(downloaded.data,'base64').toString(),'hello');user={id:'other'};assert.equal((await files(req('/api/mobile/files?id='+file.id))).status,404)});
test('SSE parser handles split UTF-8 and frames',async()=>{const bytes=new TextEncoder().encode('data: {"value":"हेलो"}\n\ndata: [DONE]\n\n');const stream=new ReadableStream({start(c){for(const b of bytes)c.enqueue(Uint8Array.of(b));c.close()}});const output=[];for await(const event of events(stream))output.push(event);assert.deepEqual(output,[{value:'हेलो'}])});
test('streamed conversations persist and reject stale revisions',async()=>{await seed();const c=await (await conversation(req('/api/conversations','POST',{}))).json();const savedFetch=globalThis.fetch;process.env.GEMINI_API_KEY='offline-test';globalThis.fetch=async()=>new Response('data: {"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}\n\n');try{const response=await chat(req('/api/mobile/chat','POST',{id:c.id,revision:0,message:'hello'}));assert.equal(response.status,200);const output=await response.text();assert.ok(output.includes('"done":true'));const read=await (await conversation(req('/api/conversations?id='+c.id))).json();assert.equal(read.messages.length,2);assert.equal(read.revision,1);assert.equal((await chat(req('/api/mobile/chat','POST',{id:c.id,revision:0,message:'stale'}))).status,409)}finally{globalThis.fetch=savedFetch;delete process.env.GEMINI_API_KEY}});
test('desktop conversation IDs are preserved for cross-device sync',async()=>{await seed();const id='b'.repeat(32), messages=[{role:'user',text:'Desktop message'}];const created=await conversation(req('/api/conversations','POST',{id,title:'Desktop chat',messages}));assert.equal(created.status,201);const first=await created.json();assert.equal(first.id,id);const updated=await conversation(req('/api/conversations','PATCH',{id,revision:0,messages:[...messages,{role:'assistant',text:'Desktop reply'}]}));assert.equal(updated.status,200);assert.equal((await updated.json()).messages.length,2);assert.equal((await conversation(req('/api/conversations','POST',{id,messages}))).status,409)});

test('revoked desktop cannot reactivate through heartbeat registration',async()=>{await seed();const register=(await import('../.test-build/bridge-register.js')).default;await store('arienx-devices').setJSON('device/'+device.id,{...device,active:false});const response=await register(req('/api/bridge/register','POST',{device_id:device.id,public_key:device.publicKey,label:'revoked'}));assert.equal(response.status,403)});
test('mobile session cannot delegate another phone',async()=>{await seed();const response=await pair(req('/api/mobile/pair','POST',{}, {'x-arienx-session':'a'.repeat(64)}));assert.equal(response.status,403)});
test('public signup delegates to Identity, accepts native no-Origin clients, and requires email confirmation by default',async()=>{originChecks=0;const response=await auth(req('/api/auth/signup','POST',{email:'new@example.test',password:'long-enough-password'}));assert.equal(response.status,200);assert.deepEqual(await response.json(),{email:'new@example.test',confirmation_required:true});assert.deepEqual(signupInput,{email:'new@example.test',password:'long-enough-password'});assert.equal(originChecks,0)});
test('SSE accepts CRLF split between network packets',async()=>{const b=new TextEncoder().encode('data: {"ok":true}\r\n\r\n');const stream=new ReadableStream({start(c){for(const v of b)c.enqueue(Uint8Array.of(v));c.close()}});const result=[];for await(const e of events(stream))result.push(e);assert.deepEqual(result,[{ok:true}])});

test('Live provisioning is authenticated, owner scoped and locks Desktop voice/model without leaking the key',async()=>{
  await seed();
  const live=(await import('../.test-build/mobile-live.js')).default;
  const chat=await (await conversation(req('/api/conversations','POST',{}))).json();
  const oldFetch=globalThis.fetch;process.env.GEMINI_API_KEY='private-test-key';
  let body, calls=0;
  globalThis.fetch=async(url,options)=>{calls++;assert.equal(url,'https://generativelanguage.googleapis.com/v1alpha/auth_tokens');body=JSON.parse(options.body);return Response.json({name:'auth_tokens/test-only'})};
  try {
    const response=await live(req('/api/mobile/live','POST',{id:chat.id,revision:0}));assert.equal(response.status,200);
    const data=await response.json();assert.equal(data.voice,'Charon');assert.equal(data.setup.model,'models/gemini-3.1-flash-live-preview');
    assert.equal(body.uses,1);assert.equal(body.bidiGenerateContentSetup.generationConfig.responseModalities[0],'AUDIO');
    assert.ok(body.fieldMask.includes('systemInstruction'));assert.equal(JSON.stringify(data).includes('private-test-key'),false);
    assert.equal((await live(req('/api/mobile/live','POST',{id:chat.id,revision:0}))).status,429);
    assert.equal((await live(req('/api/mobile/live','POST',{id:chat.id,revision:1}))).status,409);
    user={id:'other'};assert.equal((await live(req('/api/mobile/live','POST',{id:chat.id,revision:0}))).status,404);
    user=null;assert.equal((await live(req('/api/mobile/live','POST',{id:chat.id,revision:0}))).status,401);assert.equal(calls,1);
  } finally {globalThis.fetch=oldFetch;delete process.env.GEMINI_API_KEY;user={id:'owner-a',email:'a@example.test'}}
});

test('cloud deletion removes content and blocks stale-device resurrection',async()=>{
 await seed();const id='d'.repeat(32),text='long evidence '.repeat(1000).trim();
 assert.equal((await conversation(req('/api/conversations','POST',{id,messages:[{role:'assistant',text}]}))).status,201);
 assert.equal((await (await conversation(req('/api/conversations?id='+id))).json()).messages[0].text,text);
 assert.equal((await conversation(req('/api/conversations?id='+id,'DELETE'))).status,200);
 assert.equal((await conversation(req('/api/conversations?id='+id))).status,404);
 const listing=await (await conversation(req('/api/conversations'))).json();
 assert.equal(listing.conversations.length,0);assert.deepEqual(listing.deleted_ids,[id]);
 assert.equal((await conversation(req('/api/conversations','POST',{id,messages:[{role:'user',text:'stale'}]}))).status,409);
 assert.equal((await conversation(req('/api/conversations','PATCH',{id,revision:1,messages:[]}))).status,404);
 assert.equal((await chat(req('/api/mobile/chat','POST',{id,revision:1,message:'hello'}))).status,404);
 assert.equal((await conversation(req('/api/conversations?id='+id,'DELETE'))).status,200);
 user={id:'other-owner',email:'other@example.test'};
 const foreign=await (await conversation(req('/api/conversations'))).json();assert.deepEqual(foreign.deleted_ids,[]);
});
