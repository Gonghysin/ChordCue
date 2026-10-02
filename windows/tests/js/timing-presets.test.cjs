// Real browser display and WebAudio scheduling for manual per-measure presets.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const Plan=require('../../../Resources/score/PlaybackPlan.js');
const Device=require('../../../Resources/score/DeviceClient.js');
const resource=name=>fs.readFileSync(path.join(__dirname,'../../../Resources',name),'utf8');

function manualChart(){
  const meters=[[4,4],[6,8],[7,8]],lengths=[4,3,3.5],bpms=[120,90,150];
  let quarter=0,seconds=0;
  const measures=meters.map(([numerator,denominator],i)=>({id:'manual-measure-'+(i+1),sourceNumber:String(i+1),duration:{numerator:lengths[i]*2,denominator:2},meter:{numerator,denominator}}));
  const occurrences=[],segments=[];
  for(let i=0;i<3;i++){
    const measure=measures[i],id=measure.id+'@1',endQuarter=quarter+lengths[i],endSeconds=seconds+lengths[i]*60/bpms[i];
    occurrences.push({id,sourceMeasureId:measure.id,sourceIndex:i+1,sourceNumber:measure.sourceNumber,startQuarter:quarter,endQuarter,meter:measure.meter});
    segments.push({startQuarter:quarter,endQuarter,startSeconds:seconds,endSeconds,bpm:bpms[i],occurrenceId:id,sourceMeasureId:measure.id,sourceOffsetQuarter:0});
    quarter=endQuarter;seconds=endSeconds;
  }
  const route={version:1,endQuarter:quarter,durationSeconds:seconds,occurrences,segments,warnings:[]};
  Plan.validateRoute(route);
  const events=[[1,0,'C'],[1,2,'G'],[2,0,'Dm'],[2,1.5,'Am'],[3,0,'E'],[3,.5,'F']].map(([bar,q,symbol],i)=>({id:'chord-'+i,bar,beat:Math.floor(q)+1,division:q%1===.5?3:1,tick:0,symbol,number:String(i+1)}));
  // Deliberately wrong fallback metadata: only the map and route are authoritative.
  return {revision:7,name:'Manual timing presets',score:null,meter:'9/4',bpm:13,measures,route,events,sections:[]};
}

class Element{
  constructor(){this.children=[];this.value='';this.checked=true;this.hidden=false;this.textContent='';this.style={};this.dataset={};this.listeners=new Map();this.clientWidth=180;this.offsetTop=0;this.tokens=new Set();
    this.classList={add:token=>this.tokens.add(token),remove:token=>this.tokens.delete(token),toggle:(token,on)=>{if(on??!this.tokens.has(token))this.tokens.add(token);else this.tokens.delete(token);},contains:token=>this.tokens.has(token)};}
  append(...children){for(const child of children){if(this.chartRoot)child.offsetTop=20+Math.floor(this.children.length/2)*200;this.children.push(child);}}
  replaceChildren(...children){this.children=[];this.append(...children);}
  addEventListener(kind,listener){this.listeners.set(kind,listener);}
  setAttribute(){}
  getBoundingClientRect(){return {top:this.offsetTop-(this.scrollY?.()||0),height:64};}
}

function harness(chart=manualChart()){
  let now=0,frame,clientOptions;const origin=300000000,elements=new Map(),intervals=[],starts=[],cancels=[],scrolls=[];
  const el=id=>{if(!elements.has(id)){const element=new Element();element.chartRoot=id==='chart';elements.set(id,element);}return elements.get(id);};
  const gainParam=()=>({setValueAtTime(){},exponentialRampToValueAtTime(){},setTargetAtTime(){},cancelScheduledValues(at){cancels.push(at);}});
  class AudioContext{
    constructor(){this.state='suspended';this.sampleRate=48000;this.baseLatency=0;this.outputLatency=.02;this.destination={};}
    get currentTime(){return now/1000+1;}
    getOutputTimestamp(){return {contextTime:this.currentTime,performanceTime:now};}
    resume(){this.state='running';return Promise.resolve();}
    suspend(){this.state='suspended';return Promise.resolve();}
    close(){this.state='closed';return Promise.resolve();}
    createGain(){return {gain:gainParam(),connect(){},disconnect(){}};}
    createBuffer(_channels,size){return {getChannelData(){return new Float32Array(size);}};}
    createOscillator(){const source={frequency:{setValueAtTime(value){source.pitch=value;},exponentialRampToValueAtTime(){}},connect(){},stops:[],start(at){starts.push({at,queuedAt:now,source});},stop(at){source.stops.push(at);}};return source;}
  }
  const sandbox={console,AudioContext,ChordCueNative:false,ChordCueDeviceClient:{...Device,DeviceClient:class{
    constructor(options){clientOptions=options;}
    start(){clientOptions.onSession('manual-host');clientOptions.onConnection(true);}
    snapshot(){return {applied:true};}stop(){}
  }},ChordCuePlaybackPlan:Plan,performance:{now:()=>now},Date:{now:()=>100000+now},
    document:{hidden:false,body:new Element(),getElementById:el,querySelector:el,createElement:()=>{const e=new Element();e.scrollY=()=>sandbox.scrollY;return e;},addEventListener(){}},
    localStorage:{getItem:key=>key.endsWith('subdivision')?'quarter':null,setItem(){}},location:{hash:''},AbortController,
    fetch:async()=>({ok:true,json:async()=>({received:now+origin,sent:now+origin,session:'manual-host'})}),
    setInterval(fn,ms){intervals.push({fn,ms});return intervals.length;},clearInterval(){},setTimeout(){return 1;},clearTimeout(){},
    requestAnimationFrame(fn){frame=fn;},addEventListener(){},scrollY:0,innerHeight:800,
    scrollTo(value){scrolls.push(value);sandbox.scrollY=value.top;}};
  sandbox.window=sandbox;vm.createContext(sandbox);
  vm.runInContext(resource('Broadcast.html').match(/<script>([\s\S]*?)<\/script>/)[1],sandbox);
  vm.runInContext(resource('Metronome.js'),sandbox);
  const initial={revision:chart.revision,sampleTime:origin,playQuarter:0,playing:true,valid:true,precise:true,readMs:0,routeId:'manual-route',discontinuity:1,
    playback:{endBeat:chart.route.endQuarter,startTime:origin+400,loop:null}};
  const makeSample=(at,{loop=null,start=400,quarter=0,epoch=1,playing=true}={})=>{
    const anchor={...initial,playQuarter:quarter,playing,discontinuity:epoch,playback:{...initial.playback,startTime:origin+start,loop:loop?{startBeat:loop[0],endBeat:loop[1],iteration:0}:null}};
    const p=Plan.position(chart.route,anchor,origin+at);
    return {...anchor,...p,sampleTime:origin+at,division:1,tick:0,playback:{...anchor.playback,loop:anchor.playback.loop?{...anchor.playback.loop,iteration:p.loopIteration}:null}};
  };
  return {window:sandbox,el,starts,cancels,scrolls,makeSample,
    async flush(){for(let i=0;i<8;i++)await Promise.resolve();},
    async load(){await this.flush();clientOptions.onChart(chart);},
    async enable(){sandbox.ChordCueMetronome.setEnabled(true);await this.flush();},
    async clock(){sandbox.ChordCueSync.calibrate();await this.flush();},
    time(value){now=value;},
    receive(value){clientOptions.onTransport(value);},
    tick(at,options={}){now=at;if(options!==false)this.receive(makeSample(at,options));intervals.find(v=>v.ms===25).fn();},
    frame(){frame();},
    replaceChart(value){clientOptions.onChart(value);},
    disconnect(){clientOptions.onConnection(false);},
  };
}

test('manual map preserves quarter-based chord offsets and displays denominator beats',async()=>{
  const chart=manualChart();chart.measures[2].sourceNumber='III';const h=harness(chart);await h.load();
  const bars=h.el('chart').children;assert.equal(bars.length,3);assert.equal(bars[2].children[0].textContent,'III');
  const onsets=bars.map(bar=>bar.children[1].children.map(slot=>slot.children[1]?.textContent));
  assert.deepEqual(onsets,[['第 1 拍','第 3 拍'],['第 1 拍','第 4 拍'],['第 1 拍','第 2 拍']]);
  h.time(5400);await h.clock();h.tick(5400);h.frame();assert.equal(h.el('metadata').textContent,'7/8 · 150 BPM');assert.match(h.el('position').textContent,/第 III 小节 · 第 6 拍/);
  assert.ok(Math.abs(parseFloat(bars[2].children[2].style.left)-(8+164*2.5/3.5))<1e-6);
});

test('route prediction crosses presets with correct metadata and follows source rows only once',async()=>{
  const h=harness(),options={loop:[0,10.5]};await h.load();h.tick(2390,options);h.frame();assert.equal(h.el('metadata').textContent,'4/4 · 120 BPM');
  h.time(2405);h.frame();assert.equal(h.el('metadata').textContent,'6/8 · 90 BPM');assert.match(h.el('position').textContent,/第 2 小节 · 第 1 拍/);
  h.tick(2500,options);h.frame();assert.equal(h.scrolls.length,1); // Bars 1 and 2 share a row.
  h.tick(4405,options);h.frame();assert.equal(h.el('metadata').textContent,'7/8 · 150 BPM');assert.equal(h.scrolls.length,2);assert.equal(h.scrolls.at(-1).top,156);
  h.tick(4500,options);h.frame();assert.equal(h.scrolls.length,2);
  assert.equal(h.el('chart').children[2].tokens.has('active'),true);
  h.tick(5800,options);h.frame();assert.equal(h.scrolls.length,3);assert.equal(h.scrolls.at(-1).top,0);assert.equal(h.el('chart').children[0].tokens.has('active'),true);
  h.el('follow').checked=false;h.tick(9805,options);h.frame();assert.equal(h.scrolls.length,3);assert.equal(h.el('chart').children[2].tokens.has('active'),true);
});

test('preset routes schedule all quarter and eighth denominator beats at exact integrated times',async()=>{
  const h=harness();await h.load();await h.enable();
  for(let now=0;now<5700;now+=25){if(now&&now%3000===0){h.time(now);await h.clock();}h.tick(now);}
  const expected=[0,.5,1,1.5,2,7/3,8/3,3,10/3,11/3,4,4.2,4.4,4.6,4.8,5,5.2];
  assert.equal(h.starts.length,17);h.starts.forEach((s,i)=>assert.ok(Math.abs(s.at-(1.4+expected[i]))<1e-6));
  assert.deepEqual(h.starts.filter(s=>s.source.pitch===1800).map(s=>Number(s.at.toFixed(6))),[1.4,3.4,5.4]);
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
  assert.equal(h.window.ChordCueMetronome.diagnostics().audioOutputDelayMs,20);
});

test('100 manual mixed-preset loops retain 1701 unique onsets with no drift or calibration interruption',async()=>{
  const h=harness(),cycle=[0,.5,1,1.5,2,7/3,8/3,3,10/3,11/3,4,4.2,4.4,4.6,4.8,5,5.2];await h.load();await h.enable();
  for(let now=0;now<=540300;now+=25){
    if(now&&now%3000===0){h.time(now);await h.clock();}
    h.tick(now,{loop:[0,10.5]});
    if(now%5400===400){h.frame();assert.match(h.el('position').textContent,/第 1 小节/);}
  }
  assert.equal(h.starts.length,1701);assert.equal(new Set(h.starts.map(s=>s.at.toFixed(7))).size,1701);
  h.starts.forEach((s,i)=>assert.ok(Math.abs(s.at-(1.4+Math.floor(i/17)*5.4+cycle[i%17]))<1e-6));
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
  assert.equal(h.window.ChordCueSync.snapshot().clockDiagnostics.status,'valid');
});

test('late joining a six-eight preset joins its next grid and keeps seven-eight transition exact',async()=>{
  const h=harness();await h.load();h.time(3100);h.receive(h.makeSample(3100));await h.enable();
  for(let now=3100;now<=5500;now+=25)h.tick(now);
  const expected=[3.4,3.733333333,4.066666667,4.4,4.6,4.8,5,5.2,5.4,5.6];
  assert.equal(h.starts.length,expected.length);h.starts.forEach((s,i)=>assert.ok(Math.abs(s.at-(1+expected[i]))<1e-6));
  assert.equal(h.cancels.length,0);
});

test('seek into a seven-eight denominator beat cancels old epoch and respects 400 ms preparation',async()=>{
  const h=harness();await h.load();await h.enable();h.tick(300);assert.equal(h.starts.length,1);
  h.time(310);const options={quarter:7.5,start:710,epoch:2};h.receive(h.makeSample(310,options));
  assert.ok(h.starts[0].source.stops.includes(1.31));
  for(let now=325;now<=1050;now+=25)h.tick(now,options);
  assert.ok(h.starts[1].queuedAt<710);assert.equal(h.starts[1].source.pitch,1100);
  assert.deepEqual(h.starts.slice(1).map(s=>Number(s.at.toFixed(6))),[1.71,1.91,2.11]);
  h.frame();assert.equal(h.el('metadata').textContent,'7/8 · 150 BPM');assert.match(h.el('position').textContent,/第 3 小节 · 第 3 拍/);
});

test('mixed-preset loop endpoints snap only double rounding and retain both sides of epsilon',()=>{
  const route=manualChart().route;
  for(const startBeat of [0,4]){
    const sample={sampleTime:0,playQuarter:startBeat,playing:true,playback:{startTime:400,loop:{startBeat,endBeat:10.5,iteration:0}}};
    const duration=route.durationSeconds-Plan.secondsAt(route,startBeat),length=10.5-startBeat,firstBar=startBeat?2:1;
    for(let cycle=1;cycle<=100;cycle++){
      const endpoint=400+cycle*duration*1000;
      const at=Plan.position(route,sample,endpoint),before=Plan.position(route,sample,endpoint-.000001),after=Plan.position(route,sample,endpoint+.000001);
      assert.equal(at.loopIteration,cycle);assert.equal(at.playQuarter,startBeat);assert.equal(at.bar,firstBar);
      assert.equal(before.loopIteration,cycle-1);assert.equal(before.bar,3);assert.ok(before.playQuarter<10.5&&before.playQuarter>10.499999);
      assert.equal(after.loopIteration,cycle);assert.equal(after.bar,firstBar);assert.ok(after.playQuarter>startBeat&&after.playQuarter<startBeat+.000001);
      assert.ok(Math.abs(Plan.unwrappedQuarter(route,sample,endpoint)-(startBeat+cycle*length))<1e-10);
      assert.ok(Math.abs(Plan.targetTime(route,sample,startBeat+cycle*length)-endpoint)<1e-7);
    }
  }
});

test('pause and resume inside a manual preset retain position and announce a new preparation',async()=>{
  const h=harness();await h.load();await h.enable();h.tick(300);h.time(310);
  h.receive(h.makeSample(310,{playing:false,quarter:5,epoch:2}));assert.ok(h.starts[0].source.stops.includes(1.31));
  h.frame();assert.match(h.el('position').textContent,/第 2 小节 · 第 3 拍/);
  h.time(350);const resumed={quarter:5,start:750,epoch:3};h.receive(h.makeSample(350,resumed));
  for(let now=350;now<=1100;now+=25)h.tick(now,resumed);
  assert.ok(h.starts[1].queuedAt<750);assert.equal(Number(h.starts[1].at.toFixed(6)),1.75);
  assert.ok(Math.abs(h.starts[2].at-(1.75+1/3))<1e-6);h.frame();assert.equal(h.el('metadata').textContent,'6/8 · 90 BPM');
});

test('manual preset revision and disconnect clear stale display and cancel pending audio',async()=>{
  const h=harness();await h.load();await h.enable();h.tick(300);h.frame();assert.equal(h.starts.length,1);
  h.time(310);h.replaceChart({...manualChart(),revision:8});h.frame();
  assert.ok(h.starts[0].source.stops.includes(1.31));assert.equal(h.el('current').textContent,'—');
  h.disconnect();assert.equal(h.window.ChordCueSync.snapshot().sample,null);assert.equal(h.window.ChordCueSync.snapshot().clockOffset,null);
});
