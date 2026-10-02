const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const View=require('../../../Resources/score/ScoreView.js');
const MusicXML=require('../../../Resources/score/MusicXMLImport.js');
const score=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original.score.json'),'utf8'));

test('notation model preserves source TAB string, rest duration, repeat and meter',()=>{
    const {model}=View.toAlpha(score,'p1','tab');
    const bars=model.tracks[0].staves[0].bars;
    assert.equal(bars[0].voices[0].beats[0].notes[0].string,6); // vendor strings ascend
    assert.equal(bars[0].voices[0].beats[0].notes[0].fret,0);
    assert.equal(bars[0].voices[0].beats[0].notes[0].realValue,64);
    assert.equal(bars[0].voices[0].beats[2].isRest,true);
    assert.equal(model.masterBars[0].isRepeatStart,true);
    assert.equal(model.masterBars[1].repeatCount,2);
    assert.equal(model.masterBars[2].timeSignatureDenominator,8);
});

test('staff transposition changes sounding pitch while TAB stays source fingering',()=>{
    const {model}=View.toAlpha(score,'p1','staff',2);
    assert.equal(model.tracks[0].staves[0].bars[0].voices[0].beats[0].notes[0].realValue,66);
    assert.throws(()=>View.toAlpha(score,'p1','tab',2),/TAB/);
    assert.throws(()=>View.toAlpha(score,'p3','tab'),/调弦/);
});

test('exact duration mapping covers dotted notes and triplets without rounding',()=>{
    assert.equal(View.durationSettings({numerator:3,denominator:2}).dots,1);
    const triplet=View.durationSettings({numerator:1,denominator:3});
    assert.equal(4/triplet.duration*triplet.tupletDenominator/triplet.tupletNumerator,1/3);
    assert.throws(()=>View.durationSettings({numerator:1,denominator:997}),/时值/);
});

test('XML attributes may contain a quoted greater-than sign',()=>{
    const xml='<score-partwise><part-list><score-part id="p"><part-name>Test</part-name></score-part></part-list><part id="p"><measure number="1"><attributes><divisions>1</divisions></attributes><harmony><root><root-step>C</root-step></root><kind text="maj&gt;7">major</kind></harmony><note><rest/><duration>4</duration></note></measure></part></score-partwise>';
    const raw=xml.replace('maj&gt;7','maj>7');
    assert.equal(MusicXML.importScore(new TextEncoder().encode(raw)).parts[0].chords[0].text,'Cmaj>7');
});

test('display intervals retain simultaneous voices and generated rests with exclusive ends',()=>{
    const source=structuredClone(score),staff=source.parts[0].staves[0];
    const first=staff.events.find(e=>e.measureId==='m1'&&!e.isRest);
    const sustained=structuredClone(first);sustained.id='sustained';sustained.voice=2;sustained.duration={numerator:4,denominator:1};
    sustained.notes.forEach((n,i)=>{n.id='sustained.n'+i});staff.events.push(sustained);
    const {beatMap}=View.toAlpha(source,'p1'),entries=beatMap.get('m1');
    assert.ok(View.activeEntries(entries,2).some(e=>e.eventId==='sustained'));
    assert.ok(View.activeEntries(entries,2).some(e=>e.voice===1));
    assert.equal(View.activeEntries(entries,4).length,0);
    assert.ok([...beatMap.values()].flat().some(e=>e.synthetic));
});

test('technique model preserves bend curves, harmonics and beat effects without enabling a player',()=>{
    const source=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/techniques-gp.score.json'),'utf8'));
    for(const view of ['staff','tab']){
        const {noteMap,model}=View.toAlpha(source,'p1',view);
        const notes=source.parts[0].staves[0].events.flatMap(e=>e.notes);
        for(const original of notes){
            const n=noteMap.get(original.id);
            for(const t of original.techniques){
                if(t.kind==='harmonic')assert.equal(n.realValue,original.pitch);
                if(t.kind==='bend')assert.deepEqual(n.bendPoints.map(p=>[p.offset/60,p.value/2]),t.curve.map(p=>[p.position,p.semitones]));
                if(t.kind==='palmMute')assert.equal(n.isPalmMute,true);
                if(t.kind==='deadNote')assert.equal(n.isDead,true);
                if(t.kind==='hammerOn'||t.kind==='pullOff'){
                    assert.equal(n.slurDestination,noteMap.get(t.targetNoteId));
                    if(view==='tab'){assert.equal(n.isEffectSlurOrigin,true);assert.equal(n.effectSlurDestination,noteMap.get(t.targetNoteId));}
                }
            }
        }
        assert.ok(model.tracks[0].staves[0].bars.flatMap(b=>b.voices.flatMap(v=>v.beats)).some(b=>b.pickStroke));
    }
});

test('MusicXML H/P without string metadata keeps an explicit slur and written transposition bounds',()=>{
    const xml='<score-partwise><part id="p"><measure number="1"><attributes><divisions>1</divisions></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><notations><technical><hammer-on type="start" number="1">H</hammer-on></technical></notations></note><note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><notations><technical><hammer-on type="stop" number="1"/></technical></notations></note></measure></part></score-partwise>';
    const source=MusicXML.importScore(new TextEncoder().encode(xml));
    const {noteMap}=View.toAlpha(source,'p1'),[origin,target]=source.parts[0].staves[0].events.map(e=>e.notes[0]);
    assert.equal(noteMap.get(origin.id).slurDestination,noteMap.get(target.id));
    assert.match(noteMap.get(origin.id).beat.text,/H/);
    origin.writtenPitch=127;origin.pitch=115;
    assert.throws(()=>View.toAlpha(source,'p1','staff',1),/记谱音高/);
});

test('unknown MusicXML harmonic node preserves explicit staff pitch and source TAB fingering',()=>{
    const source=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/techniques-xml.score.json'),'utf8'));
    const harmonic=source.parts[0].staves[0].events.flatMap(e=>e.notes).find(n=>n.techniques.some(t=>t.kind==='harmonic'&&t.direction==='artificial'));
    const staff=View.toAlpha(source,'p1','staff').noteMap.get(harmonic.id);
    assert.equal(staff.realValue,harmonic.pitch);assert.equal(staff.displayValueWithoutBend,harmonic.writtenPitch);
    const tab=View.toAlpha(source,'p1','tab').noteMap.get(harmonic.id);
    assert.equal(tab.fret,harmonic.fret);
    const xml='<score-partwise><part id="p"><measure number="1"><attributes><divisions>1</divisions><transpose><chromatic>-2</chromatic></transpose></attributes><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note></measure></part></score-partwise>';
    const transposed=MusicXML.importScore(new TextEncoder().encode(xml)),note=View.toAlpha(transposed,'p1','staff',2).model.tracks[0].staves[0].bars[0].voices[0].beats[0].notes[0];
    assert.equal(note.realValue,60);assert.equal(note.displayValueWithoutBend,62);
    const guitar=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/techniques-gp.score.json'),'utf8'));
    for(const e of guitar.parts[0].staves[0].events)for(const n of e.notes)if(!n.techniques.some(t=>t.kind==='harmonic'))n.writtenPitch=n.pitch+12;
    const first=guitar.parts[0].staves[0].events[0].notes[0];
    const rendered=View.toAlpha(guitar,'p1','staff').noteMap.get(first.id);
    assert.equal(rendered.realValue,first.pitch);assert.equal(rendered.displayValueWithoutBend,first.writtenPitch);
});

test('harmonic-only staves preserve written octave transposition for XML and GP',()=>{
    const xml='<score-partwise><part id="p"><measure number="1"><attributes><divisions>1</divisions><transpose><octave-change>-1</octave-change></transpose></attributes><note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration><notations><technical><harmonic><natural/><sounding-pitch/></harmonic></technical></notations></note></measure></part></score-partwise>';
    const source=MusicXML.importScore(new TextEncoder().encode(xml)),original=source.parts[0].staves[0].events[0].notes[0];
    assert.equal(original.pitch,64);assert.equal(original.writtenPitch,76);
    for(const shift of [0,2]){
        const n=View.toAlpha(source,'p1','staff',shift).noteMap.get(original.id);
        assert.equal(n.realValue,64+shift);assert.equal(n.displayValueWithoutBend,76+shift);
    }
    for(const direction of ['natural','artificial']){
        const gp=JSON.parse(fs.readFileSync(path.join(__dirname,'../fixtures/techniques-gp.score.json'),'utf8'));
        const staff=gp.parts[0].staves[0],event=structuredClone(staff.events.find(e=>e.notes.some(n=>n.techniques.some(t=>t.kind==='harmonic'&&t.direction===direction))));
        const original=event.notes.find(n=>n.techniques.some(t=>t.kind==='harmonic'&&t.direction===direction));
        event.notes=[original];event.offset={numerator:0,denominator:1};event.duration={numerator:4,denominator:1};
        staff.events=[event];original.writtenPitch+=12;
        const n=View.toAlpha(gp,'p1','staff').noteMap.get(original.id);
        assert.equal(n.realValue,original.pitch);assert.equal(n.displayValueWithoutBend,original.writtenPitch);
    }
});
