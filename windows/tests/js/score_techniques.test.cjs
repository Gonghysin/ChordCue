const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const IO=require('../../../Resources/score/ScoreIO.js');
const XML=require('../../../Resources/score/MusicXMLImport.js');
const GP=require('../../../Resources/score/GuitarProImport.js');
const alpha=require('../../../Resources/vendor/alphatab/dist/alphaTab.js');
const zip=require('../../../Resources/vendor/fflate/umd/index.js');
const file=name=>new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures',name)));
const notes=score=>score.parts[0].staves[0].events.flatMap(e=>e.notes);
const kind=(n,k)=>n.techniques.find(t=>t.kind===k);
const small=body=>XML.importScore(new TextEncoder().encode('<score-partwise><part id="g"><measure number="1"><attributes><divisions>1</divisions></attributes><note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type>'+body+'</note></measure></part></score-partwise>'));

test('MusicXML structured techniques retain explicit links, curves and markings after JSON re-open',()=>{
 const score=XML.importScore(file('techniques.musicxml'),'techniques.musicxml'),n=notes(score);
 assert.equal(score.formatVersion,2);
 assert.equal(kind(n[0],'hammerOn').targetNoteId,n[1].id);
 assert.equal(kind(n[1],'pullOff').targetNoteId,n[2].id);
 assert.equal(kind(n[2],'slide').targetNoteId,n[3].id);
 assert.equal(kind(n[4],'bend').direction,'release');
 assert.deepEqual(kind(n[4],'bend').curve,[{position:0,semitones:0},{position:.5,semitones:2},{position:1,semitones:0}]);
 assert.equal(kind(n[5],'bend').direction,'prebend');
 assert.equal(kind(n[6],'bend').direction,'prebendRelease');
 assert.equal(kind(n[7],'harmonic').direction,'natural');
 assert.equal(kind(n[8],'harmonic').direction,'artificial');
 assert.equal(score.parts[0].staves[0].events[8].techniques[0].value,32);
 assert.ok(kind(n[1],'letRing'));
 assert.ok(kind(n[3],'staccato')&&kind(n[3],'heavyAccent'));
 assert.ok(kind(n[10],'ghost')&&kind(n[11],'deadNote'));
 assert.equal(n[14].tieStart,true);assert.equal(n[15].tieStop,true);
 assert.ok(score.warnings.some(w=>w.message.includes(n[13].id)&&w.message.includes('other-technical')&&w.measureId==='m4'&&w.partId==='p1'));
 assert.equal(kind(n[13],'palmMute'),undefined);
 assert.deepEqual(IO.migrateScore(JSON.parse(JSON.stringify(score))),score);
});

test('real GP8 file retains note and beat techniques plus sounding harmonic pitch',()=>{
 const score=GP.importScore(file('techniques.gp'),'techniques.gp'),n=notes(score),e=score.parts[0].staves[0].events;
 assert.equal(kind(n[0],'hammerOn').targetNoteId,n[1].id);
 assert.equal(kind(n[1],'pullOff').targetNoteId,n[2].id);
 assert.equal(kind(n[2],'slide').direction,'shift');assert.equal(kind(n[2],'slide').targetNoteId,n[3].id);
 assert.equal(kind(n[3],'slide').direction,'legato');assert.equal(kind(n[3],'slide').targetNoteId,n[4].id);
 assert.equal(kind(n[4],'slide').direction,'inBelow');assert.equal(kind(n[5],'slide').direction,'outUp');assert.equal(kind(n[6],'slide').direction,'inAbove');
 assert.equal(kind(n[4],'bend').curve[1].semitones,2);
 assert.equal(kind(n[5],'bend').direction,'prebend');assert.equal(kind(n[6],'bend').direction,'prebendRelease');
 assert.equal(n[7].pitch,76);assert.equal(n[8].pitch,81);assert.equal(n[8].writtenPitch,69);
 assert.equal(kind(n[8],'harmonic').direction,'artificial');assert.equal(kind(n[8],'harmonic').value,12);
 assert.ok(kind(n[0],'palmMute')&&kind(n[0],'accent')&&kind(n[1],'letRing')&&kind(n[2],'deadNote'));
 assert.equal(kind(n[1],'vibrato').direction,'wide');assert.ok(kind(n[8],'ghost'));
 assert.equal(e[8].techniques.find(t=>t.kind==='tremoloPicking').value,32);
 assert.ok(e[9].techniques.some(t=>t.kind==='tap')&&e[10].techniques.some(t=>t.kind==='slap')&&e[11].techniques.some(t=>t.kind==='pop'));
 assert.equal(e[11].techniques.find(t=>t.kind==='whammy').curve[1].semitones,-2);
 assert.equal(e[13].techniques.find(t=>t.kind==='brush').value,.125);
 assert.equal(kind(n[12],'trill').value,66);
 assert.deepEqual(IO.migrateScore(JSON.parse(JSON.stringify(score))),score);
});

test('v1 JSON migration strictly adds empty techniques without recovering discarded data',()=>{
 const source=JSON.parse(Buffer.from(file('issue1-original.score.json')).toString('utf8')),before=JSON.stringify(source);
 const migrated=IO.migrateScore(source);
 assert.equal(migrated.formatVersion,2);assert.equal(JSON.stringify(source),before);
 assert.ok(notes(migrated).every(n=>n.writtenPitch===null&&n.techniques.length===0));
 source.parts[0].staves[0].events[0].notes[0].techniques=[];
 assert.throws(()=>IO.migrateScore(source),/fields/);
});

test('v2 validation rejects future fields, broken links, curves, misplaced techniques and nonfinite migration input',()=>{
 const base=XML.importScore(file('techniques.musicxml'));
 const mutate=fn=>{const score=JSON.parse(JSON.stringify(base));fn(score);assert.throws(()=>IO.validateScore(score));};
 mutate(s=>s.formatVersion=3);
 mutate(s=>notes(s)[0].techniques[0].targetNoteId='missing');
 mutate(s=>notes(s)[0].techniques[0].targetNoteId=notes(s)[0].id);
 mutate(s=>notes(s)[0].techniques[0].kind='future');
 mutate(s=>kind(notes(s)[4],'bend').curve[1].semitones=Infinity);
 mutate(s=>kind(notes(s)[4],'bend').curve[1].position=2);
 mutate(s=>kind(notes(s)[4],'bend').curve[0].position=.1);
 mutate(s=>s.parts[0].staves[0].events[0].techniques=[IO.technique('deadNote')]);
 mutate(s=>notes(s)[0].techniques.push(IO.technique('hammerOn')));
 mutate(s=>notes(s)[0].future='must not disappear');
 const invalid=JSON.parse(JSON.stringify(base));notes(invalid)[0].pitch=NaN;
 assert.throws(()=>IO.migrateScore(invalid),/Non-finite/);
});

test('unknown MusicXML technical structures warn with note, measure and part source location; text is never guessed',()=>{
 const score=small('<notations><technical><other-technical>vibrato palm mute let ring</other-technical><future-effect/></technical></notations>');
 assert.deepEqual(notes(score)[0].techniques,[]);
 for(const name of ['other-technical','future-effect'])assert.ok(score.warnings.some(w=>w.message.includes('p1.n1')&&w.message.includes(name)&&w.measureId==='m1'&&w.partId==='p1'));
 const open=small('<notations><technical><hammer-on type="start"/></technical></notations>');
 assert.equal(kind(notes(open)[0],'hammerOn').targetNoteId,null);
 assert.ok(open.warnings.some(w=>w.code==='technique.target.unresolved'&&w.message.includes('p1.n1')));
});

test('unmapped alphaTab technique enumerations are explicit source warnings instead of silent loss',()=>{
 const model=alpha.importer.ScoreLoader.loadScoreFromBytes(file('techniques.gp'));
 const beat=model.tracks[0].staves[0].bars[0].voices[0].beats[0],note=beat.notes[0];
 note.slideOutType=alpha.model.SlideOutType.PickSlideDown;note.accentuated=alpha.model.AccentuationType.Tenuto;beat.fade=alpha.model.FadeType.FadeIn;
 const score=GP.fromAlpha(model);
 assert.ok(score.warnings.some(w=>w.message.includes('p1.n1')&&w.message.includes('slideOut')));
 assert.ok(score.warnings.some(w=>w.message.includes('p1.n1')&&w.message.includes('accent')));
 assert.ok(score.warnings.some(w=>w.message.includes('p1.e1')&&w.message.includes('fade')));
});

test('raw GPIF unknown effects warn before alphaTab can discard them, with source bar and part',()=>{
 const bytes=file('techniques.gp'),members=zip.unzipSync(bytes),name='Content/score.gpif';
 let xml=new TextDecoder().decode(members[name]);
 xml=xml.replace(/(<Note\s+id="[^"]+"[^>]*>[\s\S]*?<Properties>)/,'$1<Property name="FutureBendTechnique"><Enable/></Property>');
 assert.ok(xml.includes('FutureBendTechnique'));
 members[name]=new TextEncoder().encode(xml);
 const score=GP.importScore(zip.zipSync(members));
 assert.ok(score.warnings.some(w=>w.code==='technique.source.unsupported'&&w.message.includes('GPIF Note')&&w.message.includes('FutureBendTechnique')&&w.measureId==='m1'&&w.partId==='p1'));
});
