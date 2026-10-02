const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const GP=require('../../../Resources/score/GuitarProImport.js');
const IO=require('../../../Resources/score/ScoreIO.js');
const View=require('../../../Resources/score/ScoreView.js');
const generator=require('../../../tools/generate_legacy_gp_fixtures.cjs');
const fixture=name=>new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures',name)));

for(const [name,version] of [
 ['issue1-original-legacy.gp3','GP3'],['issue1-original-legacy.gp4','GP4'],['issue1-original-legacy.gp5','GP5'],
 ['issue1-original-legacy.gpx','GP6'],['issue1-original-legacy-compressed.gpx','GP6']
])test('original '+version+' fixture imports real notes, source harmony, timing and repeat: '+name,()=>{
 const bytes=fixture(name);assert.equal(GP.identify(bytes),version);
 const score=GP.importScore(bytes,name),part=score.parts[0],staff=part.staves[0];
 assert.equal(score.title,'ChordCue original legacy practice');assert.equal(score.parts.length,1);assert.equal(score.measures.length,1);
 assert.equal(score.measures[0].repeatStart,true);assert.equal(score.measures[0].repeatEnd,2);assert.deepEqual(score.measures[0].meter,{numerator:4,denominator:4});
 assert.equal(part.chords[0].text,'Cmaj7/E');assert.equal(part.chords.length,1);
 assert.deepEqual(staff.tuning,[64,59,55,50,45,40]);assert.deepEqual(staff.events.map(event=>event.notes[0].pitch),[64,66,62]);
 assert.deepEqual(staff.events.map(event=>event.notes[0].string),[1,1,2]);assert.deepEqual(staff.events.map(event=>event.notes[0].fret),[0,2,3]);
 assert.deepEqual(staff.events.map(event=>event.offset),[{numerator:0,denominator:1},{numerator:1,denominator:1},{numerator:2,denominator:1}]);
 assert.deepEqual(staff.events.map(event=>event.duration),[{numerator:1,denominator:1},{numerator:1,denominator:1},{numerator:2,denominator:1}]);
 assert.equal(score.tempoChanges[0].bpm,108);assert.equal(score.keyChanges[0].fifths,0);
 assert.ok(score.warnings.some(value=>value.code==='source.version'&&value.message.includes(version)));
 assert.doesNotThrow(()=>View.toAlpha(score,part.id,'tab'));assert.doesNotThrow(()=>View.toAlpha(score,part.id,'staff'));
});

test('legacy fixtures are reproducible from the checked-in original phrase generator',()=>{
 for(const version of [3,4,5])assert.deepEqual(generator.legacy(version),fixture('issue1-original-legacy.gp'+version));
 assert.deepEqual(generator.bcfz(fixture('issue1-original-legacy.gpx')),fixture('issue1-original-legacy-compressed.gpx'));
});

test('truncated GP3/GP4/GP5 bodies fail rather than importing an empty project',()=>{
 for(const version of [3,4,5])assert.throws(()=>GP.importScore(fixture('issue1-original-legacy.gp'+version).subarray(0,31)),Error);
});

test('GP6 compressed declaration above the output budget is rejected before allocation',()=>{
 const bytes=new Uint8Array(8);bytes.set(new TextEncoder().encode('BCFZ'));
 new DataView(bytes.buffer).setUint32(4,IO.MAX_BYTES+1,true);
 assert.throws(()=>GP.identify(bytes),/展开大小/);
});

test('GP6 compressed declared size must agree with its real literal stream',()=>{
 const original=fixture('issue1-original-legacy-compressed.gpx');
 const tooSmall=original.slice();new DataView(tooSmall.buffer).setUint32(4,4,true);
 assert.throws(()=>GP.identify(tooSmall),/声明限制/);
 const truncated=original.subarray(0,original.length-20);assert.throws(()=>GP.identify(truncated),/截断/);
});

test('GP6 repeated and self-referencing sector pointers fail in both container encodings',()=>{
 const original=fixture('issue1-original-legacy.gpx'),pointer=4+4096+148;
 for(const [at,sector] of [[pointer,1],[pointer+4,2]]){
  const bytes=original.slice();new DataView(bytes.buffer).setInt32(at,sector,true);
  assert.throws(()=>GP.identify(bytes),/扇区回环/);
  assert.throws(()=>GP.identify(generator.bcfz(bytes)),/扇区回环/);
 }
});

test('GP6 directory sizes cannot overrun their pointed sectors',()=>{
 const bytes=fixture('issue1-original-legacy.gpx').slice();
 new DataView(bytes.buffer).setInt32(4+4096+140,IO.MAX_BYTES,true);
 assert.throws(()=>GP.identify(bytes),/长度与扇区/);
});
