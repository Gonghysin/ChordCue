// Real metronome code, deterministic clocks and AudioContext scheduling calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const PlaybackPlan = require('../../../Resources/score/PlaybackPlan.js');
const resource = name => fs.readFileSync(path.join(__dirname, '../../..', 'Resources', name), 'utf8');

class Element {
  constructor() {
    this.children = []; this.value = ''; this.dataset = {}; this.style = {}; this.listeners = new Map();
    this.checked = true; this.hidden = false; this.textContent = '';
    this.classList = {toggle() {}, add() {}, remove() {}};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  dispatch(name) { this.listeners.get(name)?.(); }
  scrollIntoView() {}
}

function sampleAt(now, {start = 400, rate = 2, end = 32, loop = null, revision = 7,
                        epoch = 1, meter = 4, initial = 0} = {}) {
  const total = initial + Math.max(0, now - start) * rate / 1000;
  let position = Math.min(total, end), iteration = 0;
  if (loop) {
    const length = loop[1] - loop[0];
    iteration = Math.floor((total - loop[0]) / length);
    position = loop[0] + ((total - loop[0]) % length + length) % length;
  }
  const ticks = Math.floor(position * 960 + 1e-7), within = ticks % (meter * 960);
  return {revision, sampleTime: now, readMs: 0, valid: true, precise: true,
    rate, bar: Math.floor(ticks / (meter * 960)) + 1, beat: Math.floor(within / 960) + 1,
    division: Math.floor(within % 960 / 240) + 1, tick: within % 240,
    bpm: rate * 60, meter: `${meter}/4`, playing: !!loop || total < end,
    discontinuity: epoch,
    playback: {endBeat: end, startTime: start,
      loop: loop ? {startBeat: loop[0], endBeat: loop[1], iteration} : null}};
}

function harness({legacy = false, html = false, native = true, audio = {}} = {}) {
  let now = 0, sample = sampleAt(0), lastReceived = 0;
  if (legacy) delete sample.playback;
  const elements = new Map(), intervals = [], starts = [], cancels = [], clockRequests = [], documentEvents = new Map();
  let clockReply, deviceOptions, clockRequestTime, audioContext;
  const el = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const state = {chart: {revision: 7}, connected: true, clockOffset: 0, rtt: 0, page: 'metronome'};
  const gainParam = () => ({value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {},
    setTargetAtTime() {}, cancelScheduledValues(at) { cancels.push(at); }});
  class AudioContext {
    constructor() { this.state = 'suspended'; this.sampleRate = 48000; this.baseLatency = 0; this.destination = {}; Object.assign(this,audio); audioContext=this; }
    get currentTime() { return now / 1000 + 1; }
    getOutputTimestamp() { return {contextTime: this.currentTime, performanceTime: now}; }
    resume() { this.state = 'running'; return Promise.resolve(); }
    suspend() { this.state = 'suspended'; return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createGain() { return {gain: gainParam(), connect() {}, disconnect() {}}; }
    createBuffer(_channels, size) { return {getChannelData() { return new Float32Array(size); }}; }
    createOscillator() {
      const source = {type: '', frequency: gainParam(), connect() {},
        start(at) { starts.push({at, queuedAt: now, source}); },
        stop(at) { source.stops.push(at); }, stops: []};
      return source;
    }
  }
  const window = {AudioContext, ChordCuePlaybackPlan: PlaybackPlan, ChordCueNative: native, addEventListener() {},
    ChordCueSync: {snapshot: () => ({...state, sample, lastReceived}), calibrate() {}}};
  const sandbox = {window, document: {getElementById: el, createElement: () => new Element(),
    querySelector: el, addEventListener(name, listener) {documentEvents.set(name, listener);}, hidden: false, body: new Element()},
    performance: {now: () => now}, Date: {now: () => 100000 + now}, localStorage: {getItem: key => key.endsWith('subdivision') ? 'quarter' : null, setItem() {}},
    setInterval(fn, ms) { intervals.push({fn, ms}); return intervals.length; }, clearInterval() {},
    setTimeout() { return 1; }, clearTimeout() {}, requestAnimationFrame() {},
    location: {hash: ''}, AbortController, console};
  if (html) {
    window.webkit = {messageHandlers: {clock: {postMessage(value) { clockRequests.push(value.t0); clockRequestTime=now; }}}};
    const api = require('../../../Resources/score/DeviceClient.js');
    // Device registration/transport is outside this test; exercise the actual
    // Broadcast HTTP clock path, calibration and WebAudio scheduler together.
    window.ChordCueDeviceClient = {...api, DeviceClient: class {
      constructor(options) { deviceOptions = options; }
      start() { deviceOptions.onSession('first'); deviceOptions.onConnection(true); }
      snapshot() { return {applied:true}; }
      stop() {}
    }};
    sandbox.fetch = () => {clockRequestTime=now;return new Promise(resolve => {clockReply=data=>resolve({ok:true,json:async()=>data});});};
    const inline = resource('Broadcast.html').match(/<script>([\s\S]*?)<\/script>/)[1];
    vm.runInNewContext(inline, sandbox);
  }
  vm.runInNewContext(resource('Metronome.js'), sandbox);
  return {
    window, starts, cancels, state, sandbox, el, documentEvents,
    clock(session, offset=0) {
      window.ChordCueSync.calibrate();
      const midpoint=(clockRequestTime+now)/2+offset,data={received:midpoint,sent:midpoint,session};
      if(native)window.acceptNativeClock(data, clockRequests.at(-1));
      else {clockReply(data);return this.flush();}
    },
    replyClock(data) {
      if(native)window.acceptNativeClock(data,clockRequests.at(-1));
      else {clockReply(data);return this.flush();}
    },
    async flush() { for (let i=0;i<8;i++) await Promise.resolve(); },
    get audioContext() { return audioContext; },
    get sample() { return sample; },
    async enable() { window.ChordCueMetronome.setEnabled(true); for (let i = 0; i < 8; i++) await Promise.resolve(); },
    time(value) { now = value; },
    receive(next) {
      if (html&&native) window.acceptNativeState({name: '项目', transport: next});
      else if(html) {if(!window.ChordCueSync.snapshot().chart)deviceOptions.onChart({name:'项目',revision:next.revision,bars:4,events:[],sections:[]});deviceOptions.onTransport(next);}
      else window.ChordCueMetronome.transportChanged(sample, next);
      sample = next; lastReceived = now;
    },
    tick(value, options) {
      now = value;
      if (options !== false) this.receive(sampleAt(value, options));
      intervals.find(value => value.ms === 25).fn();
    },
  };
}

const route = {
  version:1,endQuarter:4,durationSeconds:1.5,warnings:[],
  occurrences:[
    {id:'pickup@1',sourceMeasureId:'pickup',sourceIndex:1,sourceNumber:'0',startQuarter:0,endQuarter:1,meter:{numerator:4,denominator:4}},
    {id:'six@1',sourceMeasureId:'six',sourceIndex:2,sourceNumber:'1',startQuarter:1,endQuarter:4,meter:{numerator:6,denominator:8}},
  ],
  segments:[
    {startQuarter:0,endQuarter:1,startSeconds:0,endSeconds:.5,bpm:120,occurrenceId:'pickup@1',sourceMeasureId:'pickup',sourceOffsetQuarter:0},
    {startQuarter:1,endQuarter:4,startSeconds:.5,endSeconds:1.5,bpm:180,occurrenceId:'six@1',sourceMeasureId:'six',sourceOffsetQuarter:0},
  ],
};
function routedSample(now,loop=null){
  const base={...sampleAt(0),route,routeId:'test-route',playQuarter:0,playback:{endBeat:4,startTime:400,loop}};
  const p=PlaybackPlan.position(route,base,now);
  return {...base,...p,sampleTime:now,valid:true,precise:true,division:1,tick:0,
    playback:{...base.playback,loop:loop?{...loop,iteration:p.loopIteration}:null}};
}
test('score metronome integrates pickup, tempo change and six eighth-note beats',async()=>{
  const h=harness();await h.enable();
  for(let now=0;now<1900;now+=25){h.time(now);h.receive(routedSample(now));h.tick(now,false);}
  const onsets=h.starts.map(s=>Number(s.at.toFixed(6)));
  const expected=[1.4,1.9,2.066667,2.233333,2.4,2.566667,2.733333];
  assert.equal(onsets.length,expected.length);
  onsets.forEach((at,i)=>assert.ok(Math.abs(at-expected[i])<1e-6));
});
test('score loops across meter and tempo changes keep stable unique onset identities',async()=>{
  const h=harness();await h.enable();
  const loop={startBeat:0,endBeat:4,iteration:0};
  for(let now=0;now<150400;now+=25){h.time(now);h.receive(routedSample(now,loop));h.tick(now,false);}
  const onsets=h.starts.map(s=>Number(s.at.toFixed(6)));
  assert.equal(new Set(onsets).size,onsets.length);
  assert.equal(onsets.length,701); // 100 seven-click routes plus next preparation boundary
  assert.equal(h.cancels.length,0);
});

test('400 ms preparation schedules the first beat before its exact onset', async () => {
  const h = harness(); await h.enable();
  for (let now = 0; now <= 1000; now += 25) h.tick(now);
  assert.deepEqual(h.starts.map(s => Number(s.at.toFixed(6))), [1.4, 1.9]);
  assert.ok(h.starts[0].queuedAt < 400);
  assert.equal(h.window.ChordCueTimeline.position(sampleAt(200), 300).offset, 0);
});

test('audio diagnostics distinguish not started and paused instead of reporting zero latency',async()=>{
  const h=harness({audio:{baseLatency:.01,outputLatency:.03}});
  let d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,null);assert.equal(d.audioLatencyStatus,'not-started');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/音频未开启/);
  await h.enable();h.time(100);assert.equal(h.window.ChordCueMetronome.diagnostics().audioOutputDelayMs,40);
  h.window.ChordCueMetronome.setEnabled(false);
  d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,null);assert.equal(d.audioLatencyStatus,'paused');assert.equal(d.outputMappingSource,'none');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/音频已暂停/);
});

test('missing output latency APIs do not turn clock RTT into audio delay',async()=>{
  const h=harness({audio:{baseLatency:undefined,outputLatency:undefined,getOutputTimestamp:undefined}});await h.enable();h.time(100);h.state.rtt=80;
  const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,null);assert.equal(d.baseLatencyMs,null);assert.equal(d.outputLatencyMs,null);assert.equal(d.outputMappingSource,'current-clock');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/浏览器未提供输出延迟估计/);
});

test('explicit zero latency attributes remain unknown while valid output timestamp still maps scheduling',async()=>{
  const h=harness({audio:{baseLatency:0,outputLatency:0}});await h.enable();h.time(100);
  const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,null);assert.equal(d.baseLatencyMs,0);assert.equal(d.outputLatencyMs,0);assert.equal(d.outputMappingSource,'timestamp');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/浏览器报告 0 ms；未提供可用输出延迟估计/);assert.match(h.el('audioLatency').textContent,/排程：输出时钟映射/);
});

test('base latency alone describes browser processing without inventing hardware output latency',async()=>{
  for(const outputLatency of [undefined,0]){
    const h=harness({audio:{baseLatency:.012,outputLatency}});await h.enable();h.time(100);
    const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,null);assert.equal(d.baseLatencyMs,12);assert.equal(d.outputLatencyMs,outputLatency??null);
    h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/浏览器处理延迟估计 12.0 ms；输出设备延迟未提供/);
  }
});

test('preview can report a running AudioContext estimate without enabling the metronome',async()=>{
  const h=harness({audio:{baseLatency:.01,outputLatency:.03}});h.el('audioPreview').dispatch('click');await h.flush();h.time(100);
  const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioContextState,'running');assert.equal(d.audioLatencyStatus,'estimated');assert.equal(d.audioOutputDelayMs,40);
  h.window.ChordCueMetronome.visual();assert.match(h.el('metroState').textContent,/声音未开启/);assert.match(h.el('audioLatency').textContent,/处理与设备输出延迟估计 40.0 ms/);
  assert.equal(h.starts.length,1);h.tick(300);assert.equal(h.starts.length,1); // Only the preview, no sequenced playback.
});

test('known device output latency can be reported with its missing processing component explicit',async()=>{
  const h=harness({audio:{baseLatency:undefined,outputLatency:.025}});await h.enable();h.time(100);
  const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,25);assert.equal(d.audioLatencySource,'outputLatency');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/设备输出延迟估计 25.0 ms（未包含浏览器未提供的处理延迟）/);
});

test('audio delay estimate uses browser attributes rather than currentTime minus output timestamp',async()=>{
  const h=harness({audio:{baseLatency:.01,outputLatency:.04}});await h.enable();h.time(100);
  h.audioContext.getOutputTimestamp=()=>({contextTime:h.audioContext.currentTime-.2,performanceTime:100});
  const d=h.window.ChordCueMetronome.diagnostics();assert.equal(d.audioOutputDelayMs,50);assert.equal(d.outputMappingSource,'timestamp');assert.equal(d.audioLatencySource,'baseLatency+outputLatency');
  h.window.ChordCueMetronome.visual();assert.match(h.el('audioLatency').textContent,/处理与设备输出延迟估计 50.0 ms/);
});

test('missing, nonfinite, negative and nonnumeric attributes are not valid zero components',async()=>{
  for(const invalid of [undefined,null,NaN,Infinity,-.01,'0.01']){
    const output=harness({audio:{baseLatency:invalid,outputLatency:.025}});await output.enable();output.time(100);
    const d=output.window.ChordCueMetronome.diagnostics();assert.equal(d.baseLatencyMs,null);assert.equal(d.audioOutputDelayMs,25);assert.equal(d.audioLatencySource,'outputLatency');
    const base=harness({audio:{baseLatency:.01,outputLatency:invalid}});await base.enable();base.time(100);
    assert.equal(base.window.ChordCueMetronome.diagnostics().outputLatencyMs,null);assert.equal(base.window.ChordCueMetronome.diagnostics().audioOutputDelayMs,null);
  }
});

test('latency fallback keeps its 150 ms compensation cap without clipping the reported estimate',async()=>{
  for(const [baseLatency,outputLatency,advance] of [[.01,.04,.05],[.1,.2,.15]]){
    const h=harness({audio:{baseLatency,outputLatency,getOutputTimestamp:undefined}});await h.enable();
    for(let now=0;now<=1000;now+=25)h.tick(now);
    assert.deepEqual(h.starts.map(s=>Number(s.at.toFixed(6))),[1.4-advance,1.9-advance].map(v=>Number(v.toFixed(6))));
    assert.equal(h.window.ChordCueMetronome.diagnostics().outputMappingSource,'latency-estimate');
    assert.ok(Math.abs(h.window.ChordCueMetronome.diagnostics().audioOutputDelayMs-(baseLatency+outputLatency)*1000)<1e-6);
    assert.equal(h.cancels.length,0);
  }
});

test('invalid or throwing output timestamps fall back to valid attributes',async()=>{
  for(const timestamp of [()=>({contextTime:NaN,performanceTime:100}),h=>({contextTime:1,performanceTime:h.sandbox.performance.now()+1}),h=>({contextTime:1,performanceTime:h.sandbox.performance.now()-301}),()=>{throw Error('unavailable');}]){
    const h=harness({audio:{baseLatency:.01,outputLatency:.03}});await h.enable();h.time(100);h.audioContext.getOutputTimestamp=()=>timestamp(h);
    assert.equal(h.window.ChordCueMetronome.diagnostics().outputMappingSource,'latency-estimate');h.tick(300);
    assert.equal(Number(h.starts[0].at.toFixed(6)),1.36);assert.equal(h.cancels.length,0);
  }
});

test('timestamp scheduling ignores diagnostic attributes and stays continuous for 100 loops',async()=>{
  const h=harness({audio:{baseLatency:.01,outputLatency:.04}});await h.enable();
  h.audioContext.getOutputTimestamp=()=>({contextTime:h.audioContext.currentTime-.02,performanceTime:h.sandbox.performance.now()});
  for(let now=0;now<=80300;now+=25){
    h.tick(now,{rate:5,loop:[4,8],initial:4});
    assert.equal(h.window.ChordCueMetronome.diagnostics().audioOutputDelayMs,50);
  }
  assert.equal(h.starts.length,401);assert.equal(new Set(h.starts.map(s=>s.at)).size,401);
  h.starts.forEach((s,i)=>assert.ok(Math.abs(s.at-(1.38+i*.2))<1/960/5));
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
});

test('local diagnostics show separately the applied gradual manual compensation and target',async()=>{
  const h=harness();await h.enable();h.tick(200);h.el('metroOffset').value='15';h.el('metroOffset').dispatch('input');h.tick(750);
  h.window.ChordCueMetronome.visual();const text=h.el('audioLatency').textContent;
  assert.match(text,/额外补偿已应用 \+2.8 ms（目标 \+15.0 ms）/);
  assert.equal(h.window.ChordCueMetronome.diagnostics().audioOutputDelayMs,null);assert.equal(h.cancels.length,0);
});

test('resume preparation includes a grid onset and waits until the promised start', async () => {
  const h = harness(); await h.enable();
  const options = {initial: 2, start: 400, epoch: 2};
  for (let now = 0; now <= 1000; now += 25) h.tick(now, options);
  assert.deepEqual(h.starts.map(s => Number(s.at.toFixed(6))), [1.4, 1.9]);
  assert.ok(h.starts.every(s => s.queuedAt < (s.at - 1) * 1000));
});

test('100 natural loops have no duplicate, missed or cancelled scheduled onsets', async () => {
  const h = harness(); await h.enable();
  const options = {rate: 5, loop: [4, 8], initial: 4};
  for (let now = 0; now <= 80_300; now += 25) h.tick(now, options);
  const onsets = h.starts.map(s => Number(s.at.toFixed(6)));
  assert.equal(onsets.length, 401);
  assert.equal(new Set(onsets).size, onsets.length);
  onsets.forEach((at, i) => assert.ok(Math.abs(at - (1.4 + i * 0.2)) < 1 / 960 / 5));
  assert.equal(h.cancels.length, 0);
});

for(const native of [true,false])test(`${native?'native bridge':'LAN HTTP'} congested bootstrap waits for reliable mapping then schedules 100 continuous loops`,async()=>{
  const h=harness({html:true,native}),origin=3e8;
  await h.enable();h.time(256);
  await h.replyClock({received:origin,sent:origin,session:'first'});
  h.tick(256);assert.equal(h.starts.length,0);
  assert.equal(h.window.ChordCueSync.snapshot().clockOffset,null);
  assert.equal(h.window.ChordCueSync.snapshot().clockDiagnostics.status,'calibrating');
  h.time(800);h.window.ChordCueSync.calibrate();h.time(806);
  await h.replyClock({received:origin+803,sent:origin+803,session:'first'});
  assert.equal(h.window.ChordCueSync.snapshot().clockOffset,origin);
  const options={rate:5,loop:[4,8],initial:4,start:origin+1206};
  for(let now=806;now<=81106;now+=25){
    h.time(now);
    if(now>806&&(now-806)%3000===0)h.window.ChordCueSync.calibrate();
    if(now>831&&(now-806)%3000===25)await h.clock('first',origin);
    h.receive(sampleAt(now+origin,options));h.tick(now,false);
    assert.equal(h.window.ChordCueSync.snapshot().clockDiagnostics.status,'valid');
  }
  const onsets=h.starts.map(s=>s.at);
  assert.equal(onsets.length,401);assert.equal(new Set(onsets).size,401);
  onsets.forEach((at,i)=>assert.ok(Math.abs(at-(2.206+i*.2))<1e-6));
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
});

for(const native of [true,false])test(`${native?'native bridge':'LAN HTTP'} calibration probes and bounded drift preserve 100 audible loops`,async()=>{
  const h=harness({html:true,native}),origin=300000000;
  await h.clock('first',origin);await h.enable();
  // Enabling asks for another background probe; keep the valid map meanwhile.
  await h.clock('first',origin);
  const options={rate:5,loop:[4,8],initial:4,start:origin+400};
  let previous=origin,previousTime=0;
  for(let now=0;now<=80300;now+=25){
    h.time(now);const drift=12*Math.sin(now/15000);
    if(now>0&&now%3000===0)h.window.ChordCueSync.calibrate();
    if(now>25&&now%3000===25)await h.clock('first',origin+drift);
    if(now===15000||now===35000||now===55000){h.el('metroOffset').value=now===35000?'-15':'15';h.el('metroOffset').dispatch('input');}
    h.receive(sampleAt(now+origin+drift,options));h.tick(now,false);
    const state=h.window.ChordCueSync.snapshot();
    assert.equal(state.clockDiagnostics.status,'valid');
    assert.ok(Math.abs(state.clockOffset-previous)<=((now-previousTime)*.005)+1e-6);
    assert.ok(state.clockDiagnostics.probeAgeMs<=3000);
    previous=state.clockOffset;previousTime=now;
  }
  const onsets=h.starts.map(s=>s.at);
  assert.equal(onsets.length,401);assert.equal(new Set(onsets).size,401);
  // Applied mapping + manual compensation remain bounded; the logical click
  // sequence is continuous even when the estimate is replaced after 10 s.
  onsets.forEach((at,i)=>assert.ok(Math.abs(at-(1.4+i*.2))<.032));
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
  assert.equal(h.window.ChordCueMetronome.diagnostics().appliedAdvanceMs,15);
});

test('clock discontinuity still cancels queued WebAudio sources',async()=>{
  const h=harness({html:true});h.clock('first');await h.enable();h.clock('first');h.tick(300);
  assert.equal(h.starts.length,1);h.time(310);h.clock('first',2000);
  assert.ok(h.starts[0].source.stops.includes(1.31));
});

test('route with tempo and meter changes plays 100 loops during background calibration',async()=>{
  const h=harness({html:true}),origin=3e8,loop={startBeat:0,endBeat:4,iteration:0};
  h.clock('first',origin);await h.enable();h.clock('first',origin);
  for(let now=0;now<150400;now+=25){
    h.time(now);const drift=10*Math.sin(now/13000);
    if(now>0&&now%3000===0)h.window.ChordCueSync.calibrate();
    if(now>25&&now%3000===25)h.clock('first',origin+drift);
    const next=routedSample(now+drift,loop);next.sampleTime+=origin;next.playback.startTime+=origin;
    h.receive(next);h.tick(now,false);
    assert.equal(h.window.ChordCueSync.snapshot().clockDiagnostics.status,'valid');
  }
  const onsets=h.starts.map(s=>s.at);assert.equal(onsets.length,701);assert.equal(new Set(onsets).size,701);
  const cycle=[0,.5,2/3,5/6,1,7/6,4/3];
  onsets.forEach((at,i)=>assert.ok(Math.abs(at-(1.4+Math.floor(i/7)*1.5+cycle[i%7]))<.015));
  assert.equal(h.cancels.length,0);assert.equal(h.window.ChordCueMetronome.diagnostics().late,0);
});

test('page hiding still cancels queued audio while a refresh probe is pending',async()=>{
  const h=harness({html:true});h.clock('first');await h.enable();h.clock('first');h.tick(300);
  assert.equal(h.starts.length,1);h.time(310);h.window.ChordCueSync.calibrate();
  h.sandbox.document.hidden=true;h.documentEvents.get('visibilitychange')();
  assert.ok(h.starts[0].source.stops.includes(1.31));
  assert.equal(h.window.ChordCueSync.snapshot().clockOffset,null);
});

test('missing initial calibration and a failed background refresh keep factual audio gates',async()=>{
  const h=harness({html:true});await h.enable();h.tick(300);assert.equal(h.starts.length,0);
  // The outstanding initial request took 300 ms: its map remains unknown.
  h.clock('first');assert.equal(h.window.ChordCueSync.snapshot().clockOffset,null);
  h.clock('first');h.tick(325);assert.equal(h.starts.length,1);
  // Transport stays live but no clock response is accepted: no indefinite extension.
  for(let now=350;now<=10325;now+=25)h.tick(now,{loop:[0,4]});
  assert.equal(h.window.ChordCueSync.snapshot().clockDiagnostics.status,'stale');
  assert.ok(h.cancels.length>0);
});

test('lookahead crossing several short loops schedules each unwrapped beat once', async () => {
  const h = harness(); await h.enable();
  const options = {rate: 40, meter: 1, loop: [0, 1]};
  for (let now = 0; now <= 900; now += 25) h.tick(now, options);
  const onsets = h.starts.map(s => Number(s.at.toFixed(6)));
  assert.equal(new Set(onsets).size, onsets.length);
  assert.ok(onsets.length >= 25);
  onsets.forEach((at, i) => assert.ok(Math.abs(at - (1.4 + i * 0.025)) < 1e-6));
  assert.equal(h.cancels.length, 0);
});

test('finite end is exclusive even when lookahead reaches past it', async () => {
  const h = harness(); await h.enable();
  for (let now = 0; now <= 2600; now += 25) h.tick(now, {end: 4});
  assert.deepEqual(h.starts.map(s => Number(s.at.toFixed(6))), [1.4, 1.9, 2.4, 2.9]);
});

test('350 ms stale sample and stale receipt both cancel queued WebAudio sources', async () => {
  for (const receiptOnly of [false, true]) {
    const h = harness(); await h.enable();
    h.tick(300);
    assert.equal(h.starts.length, 1);
    h.time(651);
    if (receiptOnly) h.sample.sampleTime = 651;
    h.tick(651, false);
    assert.ok(h.cancels.length > 0);
    assert.ok(h.starts[0].source.stops.includes(1.651));
  }
});

test('revision, discontinuity and pause transitions cancel old scheduled sources', async () => {
  for (const update of [{revision: 8}, {discontinuity: 2}, {playing: false}]) {
    const h = harness(); await h.enable(); h.tick(300);
    h.time(310); h.receive({...sampleAt(310), ...update});
    assert.ok(h.cancels.length > 0);
    assert.ok(h.starts[0].source.stops.includes(1.31));
  }
});

test('legacy payload retains join-on-next-grid behavior without a finite end', async () => {
  const h = harness({legacy: true}); await h.enable();
  for (let now = 0; now <= 1600; now += 25) {
    h.time(now);
    const next = sampleAt(now, {start: 0}); delete next.playback;
    h.receive(next); h.tick(now, false);
  }
  assert.deepEqual(h.starts.map(s => Number(s.at.toFixed(6))), [1.5, 2, 2.5]);
});

test('native chart follows transport revision and clock-session changes invalidate sample', async () => {
  const h = harness({html: true});
  h.window.acceptNativeState({name: '项目', transport: sampleAt(0)});
  let state = h.window.ChordCueSync.snapshot();
  assert.equal(state.chart.revision, 7);
  h.window.acceptNativeState({name: '项目', transport: sampleAt(0, {revision: 12})});
  assert.equal(h.window.ChordCueSync.snapshot().chart.revision, 12);
  h.clock('first');
  h.clock('second');
  assert.equal(h.window.ChordCueSync.snapshot().sample, null);
});

test('session replacement and manual loop edit stop actual queued AudioContext sources', async () => {
  const h = harness({html: true});
  h.clock('first');
  await h.enable(); h.tick(300);
  assert.equal(h.starts.length, 1);
  h.time(310);
  h.clock('second');
  assert.ok(h.starts[0].source.stops.includes(1.31));
  h.clock('second'); // Replacement session needs its own calibration probe.
  h.tick(325);
  assert.equal(h.starts.length, 2);
  h.time(330);
  h.receive(sampleAt(330, {loop: [0, 4], epoch: 2}));
  assert.ok(h.starts[1].source.stops.includes(1.33));
});

test('future transport and expired calibration cancel actual queued WebAudio sources', async () => {
  for (const reason of ['future', 'clock']) {
    const h = harness();
    await h.enable(); h.tick(300);
    assert.equal(h.starts.length, 1);
    h.time(310);
    if (reason === 'clock') h.state.clockDiagnostics = {version: 1, status: 'stale', jitterMs: null, probeAgeMs: 10000};
    const next = sampleAt(310);
    if (reason === 'future') next.sampleTime += 31;
    h.receive(next); h.tick(310, false);
    assert.ok(h.starts[0].source.stops.includes(1.31));
    assert.equal(h.starts.length, 1);
  }
});

test('shared cursor prediction wraps while legacy cursor stays in its sampled bar', () => {
  const h = harness(), timeline = h.window.ChordCueTimeline;
  const sample = sampleAt(2390, {loop: [0, 4]});
  const cursor = timeline.position(sample, 2420, {clampToBar: true});
  assert.equal(cursor.bar, 1);
  assert.ok(cursor.offset < 0.05);
  delete sample.playback;
  const legacy = timeline.position(sample, 2420, {clampToBar: true});
  assert.equal(legacy.bar, 1);
  assert.equal(legacy.offset, 4);
});
