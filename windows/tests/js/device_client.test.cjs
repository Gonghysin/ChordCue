const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {DeviceClient,partChart,numberChord}=require('../../../Resources/score/DeviceClient.js');
const score=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original.score.json'),'utf8'));
const baseURL='http://127.0.0.1:9100/join/example/';
const settle=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function environment(){
 const registrations=[],telemetry=[],streams=[],records=new Map(),timeouts=[],intervals=[];
 class EventSource{
  constructor(url){this.url=url;this.listeners=new Map();this.closed=false;streams.push(this);}
  addEventListener(kind,callback){this.listeners.set(kind,callback);}
  emit(kind,value){this.listeners.get(kind)?.({data:JSON.stringify(value)});}
  open(){this.onopen();}
  close(){this.closed=true;}
 }
 const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),values};};
 const fetch=async(url,options)=>{
  const route=new URL(url,baseURL).pathname.split('/').at(-1),body=options?.body?JSON.parse(options.body):null;
  if(route==='clock')return {ok:true,json:async()=>({received:100,sent:100,session:'server-one'})};
  if(route==='register'){
   registrations.push(body);let record=records.get(body.clientId);
   if(record&&record.resumeKey!==body.resumeKey)return {ok:false,status:403};
   if(!record){record={protocolVersion:2,clientId:body.clientId,label:body.label,partId:'p1',view:'tab',assignmentRevision:1,scoreRevision:4,resumeKey:'a'.repeat(64),sessionId:'server-one'};records.set(body.clientId,record);}
   return {ok:true,json:async()=>({...record})};
  }
  if(route==='telemetry'){telemetry.push(body);return {ok:true,json:async()=>({...records.get(body.clientId)})};}
  throw Error('Unexpected route '+url);
 };
 const options={baseURL,fetch,EventSource,setTimeout:(fn,ms)=>{const value={fn,ms,cleared:false};timeouts.push(value);return value;},clearTimeout:value=>{if(value)value.cleared=true;},setInterval:(fn,ms)=>{const value={fn,ms};intervals.push(value);return value;},clearInterval:()=>{},crypto:{randomUUID:()=>String(records.size+registrations.length).padStart(6,'0')+'-unique'},storage:storage()};
 const assign=(client,partId,view,revision=2)=>{const record=records.get(client.snapshot().clientId);Object.assign(record,{partId,view,assignmentRevision:revision});return {...record};};
 return {options,records,registrations,telemetry,streams,timeouts,intervals,storage,assign};
}
const chart=(revision=4)=>({revision,name:score.title,bars:score.measures.length,meter:'4/4',events:[],sections:[],score});

test('registration uses authenticated SSE and only ACKs a rendered matching score',async()=>{
 const env=environment(),applied=[];let finish;
 const client=new DeviceClient({...env.options,applyAssignment:(assignment,value)=>{applied.push([assignment,value]);return new Promise(resolve=>finish=resolve);}});
 client.start();await settle();const stream=env.streams[0];stream.open();await settle();
 const url=new URL(stream.url);assert.equal(url.searchParams.get('clientId'),client.snapshot().clientId);assert.equal(url.searchParams.get('resumeKey'),'a'.repeat(64));
 assert.equal(env.telemetry[0].appliedAssignmentRevision,0);assert.equal(applied.length,0);
 stream.emit('chart',chart(3));await settle();assert.equal(applied.length,0);
 stream.emit('chart',chart());await settle();assert.equal(applied.length,1);assert.equal(client.snapshot().applied,false);
 assert.equal(env.telemetry.some(value=>value.appliedAssignmentRevision===1),false);
 finish(true);await settle();assert.equal(client.snapshot().applied,true);
 assert.equal(env.telemetry.at(-1).appliedAssignmentRevision,1);assert.equal(env.telemetry.at(-1).scoreRevision,4);
 client.stop();assert.equal(stream.closed,true);
});

test('three browser identities keep independent parts and view changes',async()=>{
 const env=environment(),clients=[],views=[[],[],[]];
 for(let i=0;i<3;i++){
  const client=new DeviceClient({...env.options,storage:env.storage(),applyAssignment:value=>{views[i].push([value.partId,value.view]);return true;}});
  clients.push(client);client.start();await settle();env.streams[i].open();env.streams[i].emit('chart',chart());await settle();
 }
 assert.equal(new Set(clients.map(client=>client.snapshot().clientId)).size,3);
 env.streams[0].emit('assignment',env.assign(clients[0],'p2','staff'));await settle();
 env.streams[1].emit('assignment',env.assign(clients[1],'p1','chords'));await settle();
 env.streams[2].emit('assignment',env.assign(clients[2],'p3','metronome'));await settle();
 assert.deepEqual(clients.map(client=>[client.snapshot().assignment.partId,client.snapshot().assignment.view]),[['p2','staff'],['p1','chords'],['p3','metronome']]);
 assert.deepEqual(views.map(value=>value.length),[2,2,2]);
 env.streams[0].emit('assignment',env.assign(clients[0],'p1','numbers',3));await settle();
 assert.deepEqual(views.map(value=>value.length),[3,2,2]);for(const client of clients)client.stop();
});

test('late render completion and stale assignment responses cannot ACK a newer assignment',async()=>{
 const env=environment(),finishes=[];
 const client=new DeviceClient({...env.options,applyAssignment:()=>new Promise(resolve=>finishes.push(resolve))});
 client.start();await settle();const stream=env.streams[0];stream.open();stream.emit('chart',chart());await settle();
 stream.emit('assignment',env.assign(client,'p2','staff'));await settle();assert.equal(finishes.length,2);
 finishes[0](true);await settle();assert.equal(client.snapshot().applied,false);
 finishes[1](false);await settle();assert.equal(client.snapshot().applied,false);
 assert.equal(env.telemetry.some(value=>value.appliedAssignmentRevision===2),false);
 stream.emit('assignment',{...env.records.get(client.snapshot().clientId),partId:'p1',view:'tab',assignmentRevision:1});await settle();
 assert.equal(client.snapshot().assignment.partId,'p2');client.stop();
});

test('reconnect and tab reload recover the original identity and assigned view',async()=>{
 const env=environment(),connections=[];
 const client=new DeviceClient({...env.options,applyAssignment:()=>true,onConnection:value=>connections.push(value)});
 client.start();await settle();const stream=env.streams[0];stream.open();stream.emit('chart',chart());await settle();
 stream.emit('assignment',env.assign(client,'p2','staff'));await settle();
 stream.onerror();assert.equal(stream.closed,true);assert.deepEqual(connections,[true,false]);
 const retry=env.timeouts.find(value=>value.ms===1000&&!value.cleared);assert.ok(retry);retry.fn();await settle();
 assert.equal(env.registrations[1].clientId,env.registrations[0].clientId);assert.equal(env.registrations[1].resumeKey,'a'.repeat(64));
 env.streams[1].open();env.streams[1].emit('chart',chart());await settle();assert.equal(client.snapshot().assignment.view,'staff');client.stop();
 const returning=new DeviceClient({...env.options,applyAssignment:()=>true});returning.start();await settle();
 assert.equal(returning.snapshot().clientId,client.snapshot().clientId);assert.equal(returning.snapshot().assignment.partId,'p2');returning.stop();
});

test('score revision changes stay unacknowledged until their complete chart arrives',async()=>{
 const env=environment(),client=new DeviceClient({...env.options,applyAssignment:()=>true});
 client.start();await settle();const stream=env.streams[0];stream.open();stream.emit('chart',chart());await settle();assert.equal(client.snapshot().applied,true);
 const next=env.assign(client,'p2','staff');next.scoreRevision=5;env.records.set(client.snapshot().clientId,next);stream.emit('assignment',next);await settle();
 assert.equal(client.snapshot().applied,false);await client.sendTelemetry();assert.equal(env.telemetry.at(-1).appliedAssignmentRevision,0);
 stream.emit('chart',chart(5));await settle();assert.equal(client.snapshot().applied,true);assert.equal(env.telemetry.at(-1).scoreRevision,5);client.stop();
});

test('display adapter keeps original explicit harmonies, source labels, and empty parts',()=>{
 const source=chart(),before=JSON.stringify(source),guitar=partChart(source,'p1'),other=partChart(source,'p2');
 assert.equal(guitar.events[0].symbol,'Cmaj7/E');assert.equal(guitar.events[1].offsetQuarter,1.5);assert.equal(other.events.length,0);
 assert.equal(guitar.measures[2].meter.denominator,8);assert.equal(JSON.stringify(source),before);
 const unknown=chart();unknown.score={...score,keyChanges:[]};const events=partChart(unknown,'p1').events;
 assert.ok(events.every(value=>!value.keyKnown&&value.number===value.symbol));
 assert.equal(numberChord('N.C.',{fifths:2,mode:'major'}),'N.C.');assert.equal(numberChord('Cmaj7/E',{fifths:0,mode:'major'}),'1maj₇/3');
 assert.equal(numberChord('unrecognized',{fifths:0,mode:'major'}),'unrecognized');
});

test('clock diagnostics are sent only when the registered host advertises support',async()=>{
 for(const supported of [false,true]){
  const env=environment(),diagnostics={version:1,status:'valid',jitterMs:0.5,probeAgeMs:5};
  const client=new DeviceClient({...env.options,metrics:()=>({rttMs:18,clockOffsetMs:3e8,clockDiagnostics:diagnostics,syncStatus:'synchronized'})});
  client.start();await settle();const record=env.records.get(client.snapshot().clientId);
  if(supported){client.stop();record.serverCapabilities=['clockDiagnosticsV1'];client.start();await settle();}
  env.streams.at(-1).open();await settle();
  assert.equal(Object.hasOwn(env.telemetry.at(-1),'clockDiagnostics'),supported);
  if(supported)assert.deepEqual(env.telemetry.at(-1).clockDiagnostics,diagnostics);
  assert.equal(env.telemetry.at(-1).clockOffsetMs,3e8);client.stop();
 }
});

class Element{
 constructor(){this.children=[];this.value='';this.checked=true;this.hidden=false;this.textContent='';this.style={};this.listeners=new Map();this.classList={toggle(){},add(){},remove(){}};this.clientWidth=180;}
 append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
 addEventListener(kind,fn){this.listeners.set(kind,fn);}scrollIntoView(){}setAttribute(){}
}
test('Broadcast applies host part/view, waits for render, and cancels audio on disconnect',async()=>{
 const env=environment(),elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 let now=100,frame;const renders=[],cancels=[],lifecycle=new Map(),route={version:1,endQuarter:12,occurrences:[],segments:[]};
 const sandbox={console,URL,URLSearchParams,AbortController,Uint8Array,crypto:env.options.crypto,fetch:env.options.fetch,EventSource:env.options.EventSource,
  sessionStorage:env.options.storage,localStorage:{getItem:()=>null,setItem(){}},location:{href:baseURL,hash:''},performance:{now:()=>now},
  setTimeout:env.options.setTimeout,clearTimeout:env.options.clearTimeout,setInterval:env.options.setInterval,clearInterval:env.options.clearInterval,
  requestAnimationFrame:fn=>{frame=fn;},addEventListener:(kind,callback)=>lifecycle.set(kind,callback),document:{getElementById:el,createElement:()=>new Element(),querySelector:el,addEventListener(){},body:new Element()},
  ChordCueScoreView:{ScoreView:class{constructor(){}show(score,partId,view){renders.push([partId,view]);return Promise.resolve();}setPosition(){}setActive(){}setFollow(){} }},
  ChordCueMetronome:{cancel:value=>cancels.push(value),transportChanged(){},visual(){},diagnostics:()=>({audioOutputDelayMs:null})},ChordCueTimeline:{position:()=>({bar:1,offset:0})},
  ChordCuePlaybackPlan:{position:(value,sample,time)=>{assert.deepEqual(JSON.parse(JSON.stringify(value)),route);assert.equal(time,100);return {sourceMeasureId:'m3',sourceOffsetQuarter:1};}}};
 sandbox.window=sandbox;vm.createContext(sandbox);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../Resources/score/DeviceClient.js'),'utf8'),sandbox);
 const inline=fs.readFileSync(path.join(__dirname,'../../../Resources/Broadcast.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
 vm.runInContext(inline,sandbox);await settle();const stream=env.streams[0];stream.open();stream.emit('chart',{...chart(),route});await settle();
 assert.equal(sandbox.ChordCueSync.snapshot().page,'tab');assert.deepEqual(renders,[['p1','tab']]);assert.match(el('assignment').textContent,/源 TAB/);assert.equal(el('score').hidden,false);
 const id=sandbox.ChordCueSync.snapshot().device.clientId;const next={...env.records.get(id),partId:'p2',view:'staff',assignmentRevision:2};env.records.set(id,next);stream.emit('assignment',next);await settle();
 assert.deepEqual(renders.at(-1),['p2','staff']);assert.equal(sandbox.ChordCueSync.snapshot().device.applied,true);
 assert.deepEqual(JSON.parse(JSON.stringify(sandbox.ChordCueSync.snapshot().chart)),{...chart(),route});
 stream.emit('transport',{revision:4,routeId:'route-one',sampleTime:100,valid:true,precise:true,bpm:120,meter:'4/4',bar:1,beat:1,division:1,tick:0,playing:true,rate:2,readMs:0,sourceMeasureId:'m1',sourceOffsetQuarter:0});await settle();frame();
 assert.deepEqual(JSON.parse(JSON.stringify(sandbox.ChordCueSync.snapshot().sample.route)),route);assert.match(el('position').textContent,/第 3 小节/);
 stream.onerror();assert.equal(sandbox.ChordCueSync.snapshot().connected,false);assert.equal(sandbox.ChordCueSync.snapshot().sample,null);assert.equal(cancels.at(-1),true);
 now=200;frame();assert.match(el('status').textContent,/连接中断/);assert.equal(el('current').textContent,'—');
 lifecycle.get('pagehide')();const before=env.registrations.length;
 lifecycle.get('pageshow')({persisted:false});await settle();assert.equal(env.registrations.length,before);
 lifecycle.get('pageshow')({persisted:true});await settle();assert.equal(env.registrations.length,before+1);
 assert.equal(env.registrations.at(-1).clientId,id);assert.equal(env.registrations.at(-1).resumeKey,'a'.repeat(64));
 const restored=env.streams.at(-1);restored.open();restored.emit('chart',{...chart(),route});await settle();
 assert.equal(sandbox.ChordCueSync.snapshot().connected,true);assert.equal(sandbox.ChordCueSync.snapshot().assignment.view,'staff');assert.equal(sandbox.ChordCueSync.snapshot().sample,null);
 const native={...sandbox,ChordCueNative:true,webkit:{messageHandlers:{clock:{postMessage(){}}}}};native.window=native;vm.createContext(native);vm.runInContext(inline,native);
 const nativeSample={revision:4,routeId:'native-route',sampleTime:200,meter:'4/4',valid:true};
 native.acceptNativeState({name:'原生工程',transport:{...nativeSample,route}});native.acceptNativeState({name:'原生工程',transport:nativeSample});
 assert.deepEqual(JSON.parse(JSON.stringify(native.ChordCueSync.snapshot().sample.route)),route);
 native.acceptNativeState({name:'原生工程',transport:{...nativeSample,routeId:'new-route'}});assert.equal(native.ChordCueSync.snapshot().sample.route,undefined);
});
