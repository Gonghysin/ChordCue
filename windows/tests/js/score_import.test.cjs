const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const IO = require('../../../Resources/score/ScoreIO.js');
const XML = require('../../../Resources/score/MusicXMLImport.js');
const zip = require('../../../Resources/vendor/fflate/umd/index.js');
const original = new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original.musicxml')));

test('original multi-part XML preserves source harmony, exact offset, TAB and maps',()=>{
 const score=XML.importScore(original,'issue1-original.musicxml');
 assert.equal(score.parts.length,3);
 assert.equal(score.parts[0].chords[0].text,'Cmaj7/E');
 assert.deepEqual(score.parts[0].chords[1].offset,{numerator:3,denominator:2});
 assert.deepEqual(score.parts[0].staves[0].tuning,[64,59,55,50,45,40]);
 assert.equal(score.parts[0].staves[0].events[0].notes[0].fret,0);
 assert.deepEqual(score.measures[2].meter,{numerator:6,denominator:8});
 assert.deepEqual(score.measures[2].duration,{numerator:3,denominator:1});
 assert.equal(score.tempoChanges[1].bpm,135);
 assert.deepEqual(score.measures[1].endingNumbers,[1]);
 assert.deepEqual(score.measures[2].endingNumbers,[2]);
 assert.equal(score.measures[1].repeatEnd,2);
 assert.equal(score.parts[1].chords.length,0);
 assert.ok(score.warnings.some(w=>w.code==='chords.missing'&&w.partId==='p2'));
});
test('compressed MusicXML resolves container root and matches uncompressed semantics',()=>{
 const container=new TextEncoder().encode('<container><rootfiles><rootfile full-path="score/main.xml" media-type="application/vnd.recordare.musicxml+xml"/></rootfiles></container>');
 const bytes=zip.zipSync({'META-INF/container.xml':container,'score/main.xml':original});
 assert.deepEqual(XML.importScore(bytes),XML.importScore(original));
});
test('ZIP path traversal, duplicate declared sizes and checksum corruption are rejected',()=>{
 const bytes=zip.zipSync({'../bad.xml':original});
 assert.throws(()=>IO.zipEntries(bytes),/不安全/);
 const corrupt=zip.zipSync({'score.xml':original},{level:0});
 const entries=IO.zipEntries(corrupt);entries.get('score.xml').data[0]^=1;
 assert.throws(()=>IO.unpack(entries.get('score.xml')),/校验/);
});
test('XML external entities, malformed tags and deep nesting are rejected',()=>{
 assert.throws(()=>IO.parseXML('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>'),/实体/);
 assert.throws(()=>IO.parseXML('<a><b></a>'),/不匹配/);
 assert.throws(()=>IO.parseXML('<a>'.repeat(130)+'</a>'.repeat(130)),/嵌套/);
 assert.throws(()=>IO.parseXML('<a>&custom;</a>'),/实体/);
});
test('ordinary MusicXML external DTD declaration never resolves a resource',()=>{
 const xml='<?xml version="1.0"?><!DOCTYPE score-partwise PUBLIC "MusicXML" "https://example.invalid/dtd"><score-partwise><part-list/><part id="x"><measure number="0" implicit="yes"><attributes><divisions>3</divisions></attributes><note><rest/><duration>1</duration></note></measure></part></score-partwise>';
 const score=XML.importScore(new TextEncoder().encode(xml));
 assert.deepEqual(score.measures[0].duration,{numerator:1,denominator:3});
});
test('timewise scores retain multiple parts',()=>{
 const xml='<score-timewise><part-list><score-part id="a"><part-name>A</part-name></score-part><score-part id="b"><part-name>B</part-name></score-part></part-list><measure number="1"><part id="a"><note><rest/><duration>4</duration></note></part><part id="b"><note><rest/><duration>4</duration></note></part></measure></score-timewise>';
 assert.equal(XML.importScore(new TextEncoder().encode(xml)).parts.length,2);
});
test('original modern GP file preserves names, string numbering and alternate endings',()=>{
 const gp=require('../../../Resources/score/GuitarProImport.js');
 const bytes=new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original.gp')));
 assert.equal(gp.identify(bytes),'GP8');
 const score=gp.importScore(bytes,'issue1-original.gp');
 assert.equal(score.parts.length,3);
 assert.equal(score.parts[0].chords[0].text,'Cmaj7/E');
 assert.deepEqual(score.measures[1].endingNumbers,[1]);
 assert.deepEqual(score.measures[2].endingNumbers,[2]);
 assert.equal(score.tempoChanges[1].bpm,135);
 assert.equal(score.parts[0].staves[0].events[0].notes[0].string,1);
 assert.throws(()=>gp.importScore(original,'wrong.gp'),/无法识别/);
});
