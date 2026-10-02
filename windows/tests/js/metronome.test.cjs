// Real metronome code, deterministic clocks and AudioContext scheduling calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const resource = name => fs.readFileSync(path.join(__dirname, '../../..', 'Resources', name), 'utf8');

class Element {
  constructor() {
    this.children = []; this.value = ''; this.dataset = {}; this.style = {};
    this.checked = true; this.hidden = false; this.textContent = '';
    this.classList = {toggle() {}, add() {}, remove() {}};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
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

function harness({legacy = false, html = false, native = true} = {}) {
  let now = 0, sample = sampleAt(0), lastReceived = 0;
  if (legacy) delete sample.playback;
  const elements = new Map(), intervals = [], starts = [], cancels = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const state = {chart: {revision: 7}, connected: true, clockOffset: 0, rtt: 0, page: 'metronome'};
  const gainParam = () => ({value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {},
    setTargetAtTime() {}, cancelScheduledValues(at) { cancels.push(at); }});
  class AudioContext {
    constructor() { this.state = 'suspended'; this.sampleRate = 48000; this.baseLatency = 0; this.destination = {}; }
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
  const window = {AudioContext, ChordCueNative: native, addEventListener() {},
    ChordCueSync: {snapshot: () => ({...state, sample, lastReceived}), calibrate() {}}};
  const sandbox = {window, document: {getElementById: el, createElement: () => new Element(),
    querySelector: el, addEventListener() {}, hidden: false, body: new Element()},
    performance: {now: () => now}, localStorage: {getItem: key => key.endsWith('subdivision') ? 'quarter' : null, setItem() {}},
    setInterval(fn, ms) { intervals.push({fn, ms}); return intervals.length; }, clearInterval() {},
    setTimeout() { return 1; }, clearTimeout() {}, requestAnimationFrame() {},
    location: {hash: ''}, AbortController, console};
  if (html) {
    window.webkit = {messageHandlers: {clock: {postMessage() {}}}};
    const inline = resource('Broadcast.html').match(/<script>([\s\S]*?)<\/script>/)[1];
    vm.runInNewContext(inline, sandbox);
  }
  vm.runInNewContext(resource('Metronome.js'), sandbox);
  return {
    window, starts, cancels, state, sandbox, el,
    get sample() { return sample; },
    async enable() { window.ChordCueMetronome.setEnabled(true); for (let i = 0; i < 8; i++) await Promise.resolve(); },
    time(value) { now = value; },
    receive(next) {
      if (html) window.acceptNativeState({name: '项目', transport: next});
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

test('400 ms preparation schedules the first beat before its exact onset', async () => {
  const h = harness(); await h.enable();
  for (let now = 0; now <= 1000; now += 25) h.tick(now);
  assert.deepEqual(h.starts.map(s => Number(s.at.toFixed(6))), [1.4, 1.9]);
  assert.ok(h.starts[0].queuedAt < 400);
  assert.equal(h.window.ChordCueTimeline.position(sampleAt(200), 300).offset, 0);
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
  h.window.acceptNativeClock({received: 0, sent: 0, session: 'first'}, 0);
  h.window.acceptNativeClock({received: 0, sent: 0, session: 'second'}, 0);
  assert.equal(h.window.ChordCueSync.snapshot().sample, null);
});

test('session replacement and manual loop edit stop actual queued AudioContext sources', async () => {
  const h = harness({html: true});
  h.window.acceptNativeClock({received: 0, sent: 0, session: 'first'}, 0);
  await h.enable(); h.tick(300);
  assert.equal(h.starts.length, 1);
  h.time(310);
  h.window.acceptNativeClock({received: 310, sent: 310, session: 'second'}, 310);
  assert.ok(h.starts[0].source.stops.includes(1.31));
  h.tick(325);
  assert.equal(h.starts.length, 2);
  h.time(330);
  h.receive(sampleAt(330, {loop: [0, 4], epoch: 2}));
  assert.ok(h.starts[1].source.stops.includes(1.33));
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
