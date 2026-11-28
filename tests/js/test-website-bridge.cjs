const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const root = path.resolve(__dirname, '../..');
// Strip only the known TypeScript annotations from this small bridge; fail if source changes unexpectedly.
let src = fs.readFileSync(path.join(root, 'marketing-site/src/platformBridge.ts'), 'utf8');
for (const [a,b] of [['(data: unknown, status = 200)', '(data, status = 200)'], ['(input: RequestInfo | URL, init?: RequestInit)', '(input, init)'], ['querySelector<HTMLInputElement>', 'querySelector']]) {
  assert(src.includes(a)); src=src.replace(a,b);
}
const valid = {fullName:'Synthetic',phone:'+12025550192',email:'case@example.invalid',consent:true,projectName:'Test',message:'Test'};
async function harness({query='?funnel=test-funnel',funnelStatus=200,submitStatus=200,submitOk=true,existing=null}={}) {
  const calls=[]; let saved=existing;
  const native=async (input, init={})=>{ const u=String(input); calls.push({url:u,init});
    if(u.startsWith('/f/')) return new Response('<html/>',{status:funnelStatus});
    if(u.startsWith('/api/v1/public/funnel/')) return new Response(JSON.stringify(submitOk?{ok:true,contact_id:'synthetic'}:{ok:false,error:{message:'Rejected'}}),{status:submitStatus,headers:{'content-type':'application/json'}});
    return new Response('external',{status:200});};
  const location={href:'https://preview.test/website/'+query,origin:'https://preview.test',search:query};
  const sessionStorage={getItem:()=>saved,setItem:(_k,v)=>{saved=v;}};
  const ctx={window:{fetch:native},location,sessionStorage,URL,URLSearchParams,Request,Response,DOMParser:class{parseFromString(){return {querySelector:()=>({value:'synthetic-token'})}}}};
  vm.runInNewContext(src,ctx);return {fetch:ctx.window.fetch,calls};
}
const cases=[
 ['missing-funnel',async()=>{const h=await harness({query:''});const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify(valid)});assert.equal(r.status,409);assert.equal(h.calls.length,0)}],
 ['invalid-funnel',async()=>{const h=await harness({query:'?funnel=INVALID'});const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify(valid)});assert.equal(r.status,409)}],
 ['consent-required',async()=>{const h=await harness();const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify({...valid,consent:false})});assert.equal(r.status,422);assert.equal(h.calls.length,0)}],
 ['malformed-json',async()=>{const h=await harness();const r=await h.fetch('/api/leads',{method:'POST',body:'{'});assert.equal(r.status,400)}],
 ['funnel-unavailable',async()=>{const h=await harness({funnelStatus:404});const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify(valid)});assert.equal(r.status,404);assert.equal(h.calls.length,1)}],
 ['server-rejection',async()=>{const h=await harness({submitStatus:422,submitOk:false});const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify(valid)});assert.equal(r.status,422);assert.equal((await r.json()).ok,false)}],
 ['success-mapping',async()=>{const h=await harness();const r=await h.fetch('/api/leads',{method:'POST',body:JSON.stringify(valid)});assert.equal(r.status,200);assert.equal((await r.json()).ok,true);const body=JSON.parse(h.calls[1].init.body);assert.equal(body.name,valid.fullName);assert.equal(body.consent,true);assert.equal(body._ftok,'synthetic-token');assert.equal(h.calls[0].init.credentials,'same-origin');assert.equal(h.calls[1].init.credentials,'same-origin')}],
 ['analytics-not-recorded',async()=>{const h=await harness();const r=await h.fetch('/api/analytics/event',{method:'POST'});assert.equal((await r.json()).recorded,false);assert.equal(h.calls.length,0)}],
 ['private-lead-read-denied',async()=>{const h=await harness();const r=await h.fetch('/api/leads');assert.equal(r.status,403);assert.equal(h.calls.length,0)}],
 // A protected Preview admits only requests carrying its login cookie, so the shipped bundle must
 // send both same-origin requests with credentials (never omit). Public routes ignore CRM cookies.
 ['shipped-bundle-sends-login-cookie',async()=>{const html=fs.readFileSync(path.join(root,'web/website/index.html'),'utf8');const m=html.match(/src="\/website\/assets\/(index-[\w-]+\.js)"/);assert(m);const js=fs.readFileSync(path.join(root,'web/website/assets',m[1]),'utf8');assert(js.includes('re_website_funnel'));assert.equal(js.split('credentials:`omit`').length-1,0);assert.equal(js.split('credentials:`same-origin`').length-1,2)}],
 ['external-fetch-passthrough',async()=>{const h=await harness();await h.fetch('https://example.invalid/resource');assert.equal(h.calls.length,1)}]
];
(async()=>{let failures=0;for(const [name,fn] of cases){try{await fn();console.log('PASS '+name)}catch{failures++;console.log('FAIL '+name)}}console.log('SUMMARY '+(cases.length-failures)+'/'+cases.length);if(failures)process.exitCode=1})()
