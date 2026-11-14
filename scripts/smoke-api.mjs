import assert from 'node:assert/strict';
const base=process.argv[2] || 'http://127.0.0.1:5173';
const status=await fetch(base+'/api/play/status');assert.equal(status.status,200);assert.equal((await status.json()).live,false,'Smoke requires Live disabled');
if(new URL(base).port==='8790'){const page=await fetch(base+'/');assert.equal(page.status,200);assert.ok(page.headers.get('content-security-policy')?.includes("connect-src 'self'"));console.log('Worker static security headers PASS');}
let cookie='';
async function post(path,body,overrideCookie){
 const response=await fetch(base+'/api/play/'+path,{method:'POST',headers:{'Content-Type':'application/json',Origin:base,Cookie:overrideCookie??cookie},body:JSON.stringify(body)});
 if(response.headers.get('set-cookie'))cookie=response.headers.get('set-cookie').split(';')[0];
 return {status:response.status,data:await response.json()};
}
for(const missionId of ['insider','credentials','ghost']){
 const {data:s}=await post('session',{missionId,mode:'tutorial',hardened:false});assert.ok(s.id);assert.equal(s.flag,undefined);
 assert.equal((await post('capture',{id:s.id,flag:'forged'})).status,403);
 assert.equal((await post('turn',{id:s.id,message:'open',commit:true},'')).status,404);
 const turn=(extra)=>post('turn',{id:s.id,message:'Evaluate this request',commit:false,...extra});let r;
 if(missionId==='insider'){await turn({choice:'observe'});await turn({choice:'cover'});r=await turn({choice:'diagnostic',commit:true});}
 if(missionId==='credentials')r=await turn({commit:true,payload:JSON.stringify({area:'B2',role:'maintenance',approval:'approved'})});
 if(missionId==='ghost'){const load=await turn({commit:true,strategy:'Tool trust',payload:JSON.stringify({asset:'echo.archive',next_action:'LOAD_ASSET'})});assert.equal(load.data.flag,undefined);r=await turn({commit:true,strategy:'Tool trust',payload:JSON.stringify({asset:'echo.archive',next_action:'EXPORT_ASSET'})});}
 assert.equal(r.data.game.status,'won');assert.match(r.data.flag,/^HEIST\{[a-f0-9]{48}\}$/);
 assert.equal((await post('capture',{id:s.id,flag:r.data.flag})).data.captured,true);await post('end',{id:s.id});
 console.log(missionId+': breach, capture, isolation PASS');
}
console.log('All smoke checks passed; no provider calls.');
