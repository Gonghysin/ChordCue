const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {ClockCalibration,transportFresh}=require('../../../Resources/score/DeviceClient.js');

const probe=(clock,offset=300000000,start=100,end=120)=>{
 const request=clock.begin(start,100000+start);
 assert.equal(clock.accept({received:start+offset+8,sent:start+offset+10,session:'host'},request,end,100000+end),true);
 return request;
};
test('independent 3e8 millisecond origins map transport without changing RTT or position',()=>{
 for(const origin of [0,300000000,-300000000]){
  const clock=new ClockCalibration();probe(clock,origin);
  assert.equal(clock.offset,origin-1);assert.equal(clock.best.rtt,18);
  assert.equal(clock.diagnostics(125,100125).status,'valid');
  const sample={revision:3,valid:true,sampleTime:124+origin};
  assert.equal(125+clock.offset-sample.sampleTime,0);
  assert.equal(transportFresh(sample,{revision:3},125,clock.offset,125),true);
 }
});
test('negative RTT, reversed, nonfinite and overdue probes cannot become the best sample',()=>{
 for(const [received,sent,end] of [[200,230,220],[205,204,220],[NaN,205,220],[205,206,199],[205,206,2801]]){
  const clock=new ClockCalibration();probe(clock);const before=clock.offset;
  const request=clock.begin(200,100200);
  assert.equal(clock.accept({received,sent,session:'host'},request,end,100000+end),false);
  assert.equal(clock.offset,end<200?null:before);
 }
});
test('future and old transport ages use the same signed validity boundaries',()=>{
 for(const [age,valid] of [[-31,false],[-30,true],[-1,true],[0,true],[350,true],[351,false]]){
  assert.equal(transportFresh({valid:true,revision:2,sampleTime:100+3e8-age},{revision:2},100,3e8,100),valid);
 }
 assert.equal(transportFresh({valid:true,revision:1,sampleTime:100},{revision:2},100,0,100),false);
 assert.equal(transportFresh({valid:true,revision:2,sampleTime:100},{revision:2},100,0,101),false);
});
test('calibration expires and rejected probes do not refresh effective probe age',()=>{
 const clock=new ClockCalibration();probe(clock);
 clock.observe(4000,104000);clock.observe(8000,108000);
 assert.equal(clock.diagnostics(10119,110119).status,'valid');
 assert.equal(clock.diagnostics(10120,110120).status,'stale');
 const request=clock.begin(10120,110120);
 assert.equal(clock.accept({received:10,sent:40,session:'host'},request,10140,110140),false);
 assert.equal(clock.diagnostics(10140,110140).probeAgeMs,10020);
});
test('reconnection, host session changes and sleep quarantine in-flight calibration',()=>{
 const clock=new ClockCalibration(),request=probe(clock);
 clock.reset('new-host');
 assert.equal(clock.accept({received:3e8,sent:3e8,session:'host'},request,121,100121),false);
 let next=clock.begin(130,100130);
 assert.equal(clock.accept({received:3e8,sent:3e8,session:'host'},next,140,100140),false);
 assert.equal(clock.accept({received:3e8,sent:3e8,session:'new-host'},next,140,100140),true);
 next=clock.begin(150,100150);
 assert.equal(clock.diagnostics(151,160151).status,'calibrating'); // performance clock paused in sleep
 assert.equal(clock.accept({received:3e8,sent:3e8,session:'new-host'},next,160,160160),false);
 const restarted=new ClockCalibration();
 assert.equal(restarted.accept({received:3e8,sent:3e8,session:'new-host'},next,170,160170),false);
 const original=new ClockCalibration(),old=original.begin(100,100100),reload=new ClockCalibration();
 assert.equal(reload.accept({received:3e8,sent:3e8,session:'host'},old,120,100120),false);
});
test('measurement jitter describes consistency rather than monotonic origin mapping',()=>{
 const clock=new ClockCalibration();probe(clock,3e8);probe(clock,3e8+2,200,220);
 assert.ok(clock.diagnostics(220,100220).jitterMs>0);
 assert.ok(clock.diagnostics(220,100220).jitterMs<3);
});
test('asymmetric 256 ms bootstrap waits for a bounded 6 ms mapping instead of slewing for 25 seconds',()=>{
 const clock=new ClockCalibration(),origin=3e8;
 const first=clock.begin(0,100000);
 assert.equal(clock.accept({received:origin,sent:origin,session:'host'},first,256,100256),true);
 assert.equal(clock.offset,null);assert.equal(clock.diagnostics(256,100256).status,'calibrating');
 assert.equal(transportFresh({valid:true,revision:1,sampleTime:origin+251},{revision:1},256,clock.offset,256),false);
 const reliable=clock.begin(800,100800);
 assert.equal(clock.accept({received:origin+803,sent:origin+803,session:'host'},reliable,806,100806),true);
 assert.equal(clock.offset,origin);assert.equal(clock.best.rtt,6);
 assert.equal(clock.diagnostics(806,100806).status,'valid');
 assert.equal(transportFresh({valid:true,revision:1,sampleTime:origin+801},{revision:1},806,clock.offset,806),true);
});
test('probe uncertainty is bounded by the existing 30 ms transport future tolerance',()=>{
 for(const [rtt,status] of [[60,'valid'],[60.001,'calibrating']]){
  const clock=new ClockCalibration(),request=clock.begin(0,100000);
  assert.equal(clock.accept({received:3e8,sent:3e8,session:'host'},request,rtt,100000+rtt),true);
  assert.equal(clock.diagnostics(rtt,100000+rtt).status,status);
  if(status==='valid')assert.equal(transportFresh({valid:true,revision:1,sampleTime:3e8+rtt},{revision:1},rtt,clock.offset,rtt),true);
 }
});
test('congested probes neither retarget a healthy mapping nor renew expired reliable calibration',()=>{
 const clock=new ClockCalibration();probe(clock,3e8,0,20);const offset=clock.offset,generation=clock.generation;
 for(let start=1000;start<=12000;start+=1000){
  const request=clock.begin(start,100000+start);
  // A return-side delay is compatible with the good map, but cannot refine it.
  assert.equal(clock.accept({received:3e8+start,sent:3e8+start,session:'host'},request,start+256,100000+start+256),true);
  assert.equal(clock.offset,offset);assert.equal(clock.generation,generation);
  assert.equal(clock.diagnostics(start+256,100000+start+256).status,start+256<10020?'valid':'stale');
 }
 assert.equal(clock.latest,20);assert.equal(clock.best.rtt,18);
 // First reliable probe after staleness acquires the map rather than carrying
 // a stale offset through a new valid epoch.
 probe(clock,3e8+10,13000,13010);
 assert.equal(clock.diagnostics(13010,113010).status,'valid');
 assert.equal(clock.offset,clock.best.offset);assert.equal(clock.generation,generation+1);
});
test('latest accepted probe keeps calibration valid when the minimum RTT estimate expires',()=>{
 const clock=new ClockCalibration();probe(clock,3e8,0,20);
 for(let start=1000;start<=12000;start+=1000){
  probe(clock,3e8+8,start,start+30);
  assert.equal(clock.diagnostics(start+30,100000+start+30).status,'valid');
  assert.equal(clock.diagnostics(start+31,100000+start+31).probeAgeMs,1);
 }
 assert.ok(clock.offset>3e8-1); // The lower RTT initial estimate has expired.
});
test('small calibration changes slew at 5 ms per second without changing generation',()=>{
 const clock=new ClockCalibration();probe(clock,3e8,0,20);const generation=clock.generation,initial=clock.offset;
 // This better RTT estimate replaces the target immediately, not the applied map.
 probe(clock,3e8+15,100,110);assert.equal(clock.offset,initial);
 for(let now=210;now<=4210;now+=100){
  const before=clock.offset;assert.equal(clock.diagnostics(now,100000+now).status,'valid');
  assert.ok(clock.offset-before<=0.500001);assert.ok(clock.offset>=before);
 }
 assert.equal(clock.generation,generation);assert.ok(Math.abs(clock.offset-clock.best.offset)<4);
});
test('incompatible clock mapping replaces an earlier lower RTT sample',()=>{
 const clock=new ClockCalibration();probe(clock,3e8,100,120);const generation=clock.generation;
 probe(clock,3e8+2000,200,225);
 assert.equal(clock.samples.length,1);assert.equal(clock.best.time,225);
 assert.equal(clock.offset,3e8+1996.5);
 assert.equal(clock.generation,generation+1);
});

class Element{
 constructor(){this.value='';this.checked=true;this.hidden=false;this.textContent='';this.style={};this.classList={add(){},remove(){},toggle(){}};}
 addEventListener(){}replaceChildren(){}append(){}setAttribute(){}
}
test('native Broadcast rejects late calibration after hide/reload and stops future transport display',()=>{
 let now=100,wall=100100,frame;const elements=new Map(),events=new Map(),lifecycle=new Map(),requests=[],cancels=[];
 const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const sandbox={console,performance:{now:()=>now},Date:{now:()=>wall},localStorage:{getItem:()=>null},location:{hash:''},
  document:{hidden:false,getElementById:el,querySelector:el,body:new Element(),addEventListener:(name,fn)=>events.set(name,fn)},
  setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame:fn=>frame=fn,
  addEventListener:(name,fn)=>lifecycle.set(name,fn),ChordCueNative:true,ChordCueBridge:{clock:t0=>requests.push(t0)},
  ChordCueDeviceClient:{ClockCalibration,transportFresh},ChordCueMetronome:{cancel:()=>cancels.push(now),visual(){},transportChanged(){}},
  ChordCueTimeline:{position:()=>({bar:1,offset:0})}};
 sandbox.window=sandbox;vm.createContext(sandbox);
 const inline=fs.readFileSync(path.join(__dirname,'../../../Resources/Broadcast.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
 vm.runInContext(inline,sandbox);
 now=120;wall=100120;sandbox.acceptNativeClock({received:300000108,sent:300000110,session:'host'},requests.at(-1));
 assert.equal(sandbox.ChordCueSync.snapshot().clockOffset,299999999);
 sandbox.acceptNativeState({name:'Test',transport:{revision:1,sampleTime:now+299999999+31,valid:true,precise:true,bpm:120,meter:'4/4',playing:true,rate:2,readMs:0}});
 frame();assert.match(el('status').textContent,/过期/);assert.equal(el('current').textContent,'—');
 now=130;wall=100130;sandbox.ChordCueSync.calibrate();const old=requests.at(-1);
 sandbox.document.hidden=true;events.get('visibilitychange')();
 sandbox.document.hidden=false;events.get('visibilitychange')(); // Same performance timestamp, different probe token.
 sandbox.acceptNativeClock({received:3e8,sent:3e8,session:'host'},old);
 assert.equal(sandbox.ChordCueSync.snapshot().clockOffset,null);
 sandbox.document.hidden=true;events.get('visibilitychange')();sandbox.document.hidden=false;now=140;wall=100140;events.get('visibilitychange')();
 const active=requests.at(-1);sandbox.acceptNativeClock({received:300000140,sent:300000140,session:'host'},active);
 assert.equal(sandbox.ChordCueSync.snapshot().clockDiagnostics.status,'valid');
 lifecycle.get('pagehide')();assert.equal(sandbox.ChordCueSync.snapshot().clockOffset,null);assert.ok(cancels.length);
});
