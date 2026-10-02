const test=require('node:test');const assert=require('node:assert/strict');
const Plan=require('../../../Resources/score/PlaybackPlan.js');
function route(){return {version:1,endQuarter:2,durationSeconds:1.5,occurrences:[
    {id:'m0@1',sourceMeasureId:'m0',sourceIndex:1,sourceNumber:'0',startQuarter:0,endQuarter:.5,meter:{numerator:4,denominator:4}},
    {id:'m1@1',sourceMeasureId:'m1',sourceIndex:2,sourceNumber:'1',startQuarter:.5,endQuarter:2,meter:{numerator:6,denominator:8}}],
    segments:[{startQuarter:0,endQuarter:.5,startSeconds:0,endSeconds:.5,bpm:60,occurrenceId:'m0@1',sourceMeasureId:'m0',sourceOffsetQuarter:0},
    {startQuarter:.5,endQuarter:1,startSeconds:.5,endSeconds:1,bpm:60,occurrenceId:'m1@1',sourceMeasureId:'m1',sourceOffsetQuarter:0},
    {startQuarter:1,endQuarter:2,startSeconds:1,endSeconds:1.5,bpm:120,occurrenceId:'m1@1',sourceMeasureId:'m1',sourceOffsetQuarter:.5}],warnings:[]};}
function sample(loop=null){return {sampleTime:1000,playQuarter:0,playing:true,playback:{startTime:1400,endBeat:2,loop}};}
test('preparation and pickup preserve source correspondence',()=>{
    const r=route(),s=sample();assert.equal(Plan.position(r,s,1399).playQuarter,0);
    assert.equal(Plan.position(r,s,1399).preparing,true);
    const p=Plan.position(r,s,1900);assert.equal(p.playQuarter,.5);assert.equal(p.sourceMeasureId,'m1');
    assert.equal(p.sourceOffsetQuarter,0);assert.equal(p.meterDenominator,8);
});
test('tempo integration and scheduling use seconds across source boundaries',()=>{
    const r=route(),s=sample();assert.equal(Plan.position(r,s,2650).playQuarter,1.5);
    assert.equal(Plan.targetTime(r,s,1.5),2650);
    for(const q of [0,.333,.5,1,1.7,2])assert.ok(Math.abs(Plan.quarterAt(r,Plan.secondsAt(r,q))-q)<1e-12);
    assert.equal(Plan.position(r,s,2900).playing,false);
});
test('many natural loops retain unwrapped iteration and inverse targetTime',()=>{
    const r=route(),s=sample({startBeat:0,endBeat:2,iteration:0});
    const p=Plan.position(r,s,1400+1500*100+1250);
    assert.equal(p.playQuarter,1.5);assert.equal(p.loopIteration,100);
    const q=Plan.unwrappedQuarter(r,s,1400+1500*100+1250);assert.equal(q,201.5);
    assert.equal(Plan.targetTime(r,s,q),1400+1500*100+1250);
    const later={sampleTime:1400+1500*100+1250,playQuarter:1.5,playing:true,playback:{loop:{startBeat:0,endBeat:2,iteration:100}}};
    assert.equal(Plan.position(r,later,later.sampleTime+250).loopIteration,101);
    assert.equal(Plan.position(r,later,later.sampleTime+250).playQuarter,0);
});
test('paused or ended samples do not advance',()=>{
    const r=route(),s={...sample(),playing:false,playQuarter:1.3};
    assert.equal(Plan.position(r,s,99999).playQuarter,1.3);
    assert.equal(Plan.allows(r,s,2),false);
});
test('nonfinite and inconsistent route data are rejected',()=>{
    for(const bad of [NaN,Infinity,-1]){const r=route();r.endQuarter=bad;assert.throws(()=>Plan.validateRoute(r));}
    const r=route();r.segments[0].endQuarter=0;assert.throws(()=>Plan.validateRoute(r));
    assert.throws(()=>Plan.position(route(),sample(),NaN));
});
