// No browser dependencies: node test_dashboard.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const elements = {};
function element() {
  return {innerHTML:'',textContent:'',setAttribute(){},append(){},appendChild(){},classList:{toggle(){}},
    set id(value){this._id=value;elements[value]=this;},get id(){return this._id;}};
}
const sandbox = {console, innerWidth:1200, setTimeout(){}, window:{addEventListener(){}},
  document:{getElementById:id=>elements[id] ||= element(), createElement:element, createElementNS:element,querySelectorAll:()=>[]}};
vm.createContext(sandbox);
let source = fs.readFileSync(path.join(__dirname,'index.html'),'utf8').split('<script>')[1].split('</script>')[0];
source = source.replace(/\npoll\(\);\s*$/, '\n');
vm.runInContext(source,sandbox);
vm.runInContext(`events=[
 {id:1,type:'req',ts:Date.now()/1000-20,prompt_tokens:100,last_preview:'</title><script>alert(1)</script>'},
 {id:2,type:'req',ts:Date.now()/1000-10,prompt_tokens:200},
 {id:3,type:'prefill_sum',ts:Date.now()/1000-5,tok_s:237.5,tokens:287,ms:1209,chunks:null},
 {id:4,type:'sys',ts:Date.now()/1000-3,disk_r_mbs:null,dirty_mib:0},
 {id:5,type:'spec',ts:Date.now()/1000-2,pct:null},
 {id:6,type:'done',ts:Date.now()/1000-1,tok_s:38,tokens:100,seconds:2.63,finish:null}
]; render();`, sandbox);
assert.ok(elements.prompt.innerHTML.includes('<polyline'));
assert.ok(elements.prompt.innerHTML.includes('stroke-dasharray'));
assert.ok(elements.prefill.innerHTML.includes('<rect'));
assert.ok(elements.decode.innerHTML.includes('<rect'));
assert.ok(elements['prefill-latest'].textContent.includes('237.5'));
assert.ok(elements.prompt.innerHTML.includes('&lt;script&gt;'));
assert.ok(!elements.prompt.innerHTML.includes('<script>'));
for(const el of Object.values(elements))assert.ok(!/NaN|undefined|Infinity/.test(el.innerHTML),el.innerHTML);
vm.runInContext('events=[]; render();',sandbox);
assert.ok(elements.prefill.innerHTML.includes('No measurements'));
// Efficiency: power samples matched to the decode window only, ≥5 required.
const out=vm.runInContext(`
const T=Date.now()/1000;
const gpus=[...Array(22)].map((_,i)=>({type:'gpu',ts:T-42+i*2,p:100}));
gpus[0].p=500; gpus[0].ts=T-50; // before the 40s window
gpus.push({type:'gpu',ts:T+9,p:500}); // beyond end+2 grace
events=[{id:1,type:'done',ts:T,tok_s:50,tokens:2000,seconds:40},{id:2,type:'done',ts:T+100,tok_s:30,tokens:100,seconds:3}];
const eff=effRecords(events.filter(e=>e.type==='done'),gpus);
events=events.concat(gpus.slice(1)); render();
eff`,sandbox);
assert.equal(out[0].avg_w,100);
assert.equal(out[0].j_tok,2);
assert.equal(out[1].avg_w,null);
assert.equal(out[1].j_tok,null);
assert.ok(elements.eff.innerHTML.includes('<rect'));
assert.ok(elements.eff.innerHTML.includes('W</text>'));
assert.ok(!/NaN|undefined|Infinity/.test(elements.eff.innerHTML));
// Large retained history must not use argument-spreading min/max.
vm.runInContext("events=Array.from({length:50000},(_,i)=>({id:i,type:'sys',ts:Date.now()/1000-i,dirty_mib:i%100})); range=0; render();",sandbox);
let calls=0;
sandbox.fetch=async url=>({ok:true,json:async()=>{
 if(url==='/api/state')return {requests:2,completed:2,last_prompt:200,context_cap:65536};
 calls++;
 return {session:'test',events:[{id:calls,type:'req',ts:Date.now()/1000,prompt_tokens:100*calls}],cursor:calls,more:calls<2};
}});
(async()=>{
 vm.runInContext('events=[]; cursor=0;',sandbox);
 await vm.runInContext('poll()',sandbox);
 assert.equal(vm.runInContext('cursor',sandbox),2);
 assert.equal(vm.runInContext('events.length',sandbox),2);
 console.log('Dashboard regression tests passed: scaling, prompt line, escaping, empty/null values, efficiency windowing, bounded history rendering, paginated polling.');
})().catch(error=>{console.error(error);process.exitCode=1;});
