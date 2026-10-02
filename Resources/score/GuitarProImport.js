(function(root,factory){
    const api=factory(typeof module==='object'&&module.exports?require('./ScoreIO.js'):root.ChordCueScoreIO,typeof module==='object'&&module.exports?require('../vendor/alphatab/dist/alphaTab.js'):root.alphaTab);
    if(typeof module==='object'&&module.exports)module.exports=api;else root.ChordCueGuitarPro=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(IO,alpha){
    'use strict';
    const F=IO.fraction,add=IO.add,value=IO.value,clean=s=>String(s||'').trim().replace(/[\r\n\t]+/g,' ');
    function identify(bytes,details=null){
        if(!(bytes instanceof Uint8Array)||bytes.length<8||bytes.length>IO.MAX_BYTES)throw Error('Guitar Pro 文件大小无效');
        if(bytes[0]===0x50&&bytes[1]===0x4b){
            const entries=IO.zipEntries(bytes),gpif=entries.get('Content/score.gpif');
            if(!gpif)throw Error('ZIP 文件不含 Guitar Pro GPIF 谱面');
            // Validate every declared member before alphaTab's own ZIP reader.
            for(const entry of entries.values())IO.unpack(entry);
            const xml=IO.parseXML(IO.decode(IO.unpack(gpif))),version=IO.get(xml,'GPVersion');
            if(details)details.tree=xml;
            if(version&&!/^[78](?:\.|$)/.test(version))throw Error('尚未支持的 Guitar Pro GPIF 版本：'+version);
            return version.startsWith('8')?'GP8':'GP7';
        }
        const header=new TextDecoder('latin1').decode(bytes.subarray(0,64));
        if(header.includes('FICHIER GUITAR PRO v3.'))return 'GP3';
        if(header.includes('FICHIER GUITAR PRO v4.'))return 'GP4';
        if(header.includes('FICHIER GUITAR PRO v5.'))return 'GP5';
        if(header.startsWith('BCFZ')||header.startsWith('BCFS')){IO.preflightGpx(bytes,details);return 'GP6';}
        throw Error('不支持或无法识别的 Guitar Pro 格式版本');
    }
    // Audit raw GPIF before vendor normalization can discard an unknown effect.
    // Source IDs are traced through master bar -> bar -> voice -> beat -> note.
    function auditGpif(tree,ir){
        const definitions=(container,item)=>new Map(IO.children(IO.child(tree,container),item).map(n=>[n.attrs.id,n]));
        const bars=definitions('Bars','Bar'),voices=definitions('Voices','Voice'),beats=definitions('Beats','Beat'),notes=definitions('Notes','Note');
        const ids=(node,key)=>IO.get(node,key).trim().split(/\s+/).filter(Boolean),locations=new Map();
        const slots=ir.parts.flatMap(part=>part.staves.map(()=>part.id));
        IO.children(IO.child(tree,'MasterBars'),'MasterBar').forEach((master,mi)=>{
            ids(master,'Bars').forEach((barId,slot)=>{
                const location={measureId:ir.measures[mi]?.id||null,partId:slots[slot]||null};
                for(const voiceId of ids(bars.get(barId),'Voices'))for(const beatId of ids(voices.get(voiceId),'Beats')){
                    locations.set('Beat:'+beatId,location);
                    for(const noteId of ids(beats.get(beatId),'Notes'))locations.set('Note:'+noteId,location);
                }
            });
        });
        const noteProperties=new Set(['ShowStringNumber','String','Fret','Midi','Element','Variation','Tapped','HarmonicType','HarmonicFret','Muted','PalmMuted','Octave','Tone','ConcertPitch','TransposedPitch','Bended','BendOriginValue','BendOriginOffset','BendMiddleValue','BendMiddleOffset1','BendMiddleOffset2','BendDestinationValue','BendDestinationOffset','HopoOrigin','HopoDestination','LeftHandTapped','Slide']);
        const beatProperties=new Set(['Brush','PickStroke','Slapped','Popped','VibratoWTremBar','WhammyBar','WhammyBarExtend','WhammyBarOriginValue','WhammyBarOriginOffset','WhammyBarMiddleValue','WhammyBarMiddleOffset1','WhammyBarMiddleOffset2','WhammyBarDestinationValue','WhammyBarDestinationOffset','BarreFret','BarreString','Rasgueado']);
        const noteElements=new Set(['Properties','XProperties','AntiAccent','LetRing','Trill','Accent','Tie','Vibrato','LeftFingering','RightFingering','InstrumentArticulation','Ornament']);
        const beatElements=new Set(['Notes','Rhythm','Fadding','Tremolo','Chord','Hairpin','Arpeggio','Properties','XProperties','FreeText','ConcertPitchStemOrientation','TransposedPitchStemOrientation','UserTransposedPitchStemOrientation','UserConcertPitchStemOrientation','Dynamic','GraceNotes','Legato','Whammy','Ottavia','Lyrics','Slashed','DeadSlapped','Golpe','Wah','Timer']);
        for(const [type,records,properties,elements] of [['Note',notes,noteProperties,noteElements],['Beat',beats,beatProperties,beatElements]]){
            for(const [sourceId,node] of records){
                const {measureId,partId}=locations.get(type+':'+sourceId)||{measureId:null,partId:null};
                const warn=detail=>{if(ir.warnings.length>=10000)throw Error('警告过多');ir.warnings.push({code:'technique.source.unsupported',message:clean('GPIF '+type+' '+sourceId+' 未转换的源属性：'+detail),severity:'warning',measureId,partId});};
                for(const element of node.children){
                    if(!elements.has(element.name))warn(element.name);
                    if(element.name==='Properties')for(const property of element.children){
                        const name=property.attrs.name;
                        if(property.name!=='Property'||!properties.has(name))warn(name||property.name);
                        if(name==='HarmonicType'&&!['noharmonic','natural','artificial','pinch','tap','semi','feedback'].includes(IO.get(property,'HType').toLowerCase()))warn('HarmonicType '+IO.get(property,'HType'));
                        if(name==='Slide'){const flags=Number(IO.get(property,'Flags'));if(!Number.isSafeInteger(flags)||flags<0||flags>255)warn('Slide Flags '+IO.get(property,'Flags'));}
                        if(/^(Bend|WhammyBar).*(Value|Offset)$/.test(name)){const raw=IO.get(property,'Float');if(!raw||!Number.isFinite(Number(raw)))warn(name+' '+raw);}
                        if(['BarreFret','BarreString','WhammyBarExtend'].includes(name))warn(name);
                    }
                    if(element.name==='Legato'||element.name==='Ottavia')warn(element.name);
                }
            }
        }
        return IO.validateScore(ir);
    }
    function duration(beat){
        if(beat.graceType!==0)return F(0);
        let d=beat.duration<0?F(-4*beat.duration):F(4,beat.duration);
        if(beat.dots)d=F(d.numerator*(Math.pow(2,beat.dots+1)-1),d.denominator*Math.pow(2,beat.dots));
        if(beat.tupletNumerator>0&&beat.tupletDenominator>0)d=F(d.numerator*beat.tupletDenominator,d.denominator*beat.tupletNumerator);
        return d;
    }
    function fromAlpha(score,fileName=null,sha256=null,version='GP'){
        if(score.tracks.length>64||score.masterBars.length>10000)throw Error('Guitar Pro 谱面结构过大');
        const ir={formatVersion:2,id:'score',title:clean(score.title||fileName||'导入谱面'),source:{format:'guitarpro',fileName,sha256},measures:[],parts:[],tempoChanges:[],keyChanges:[],warnings:[]};
        const warn=(code,message,measureId=null,partId=null)=>{if(ir.warnings.length>=10000)throw Error('警告过多');ir.warnings.push({code,message,severity:'warning',measureId,partId});};
        const noteIds=new Map(),pendingLinks=[],tempoSeen=new Map();
        const T=IO.technique;
        function curve(points,kind,location,mid,pid){
            if(!points?.length)return null;
            const converted=points.map(p=>({position:p.offset/60,semitones:p.value/2}));
            if(converted.length>64||converted.some((p,i)=>!Number.isFinite(p.position)||p.position<0||p.position>1||!Number.isFinite(p.semitones)||Math.abs(p.semitones)>24||(i&&p.position<converted[i-1].position))){warn('technique.curve.unsupported',location+' 的弯音曲线超出支持范围',mid,pid);return null;}
            if(converted[0].position!==0)converted.unshift({position:0,semitones:converted[0].semitones});
            if(converted.at(-1).position!==1)converted.push({position:1,semitones:converted.at(-1).semitones});
            if(converted.length>64){warn('technique.curve.unsupported',location+' 曲线点数超过64',mid,pid);return null;}
            return T(kind,{direction:IO.curveDirection(converted),curve:converted});
        }
        score.masterBars.forEach((bar,index)=>{
            const id='m'+(index+1),meter={numerator:bar.timeSignatureNumerator,denominator:bar.timeSignatureDenominator};
            const measure={id,number:String(index+1),duration:F(meter.numerator*4,meter.denominator),meter,repeatStart:!!bar.isRepeatStart,repeatEnd:bar.repeatCount>=2?bar.repeatCount:null,endingNumbers:[],markers:[],navigation:[]};
            for(let n=0;n<32;n++)if((bar.alternateEndings>>>n)&1)measure.endingNumbers.push(n+1);
            if(bar.section)measure.markers.push({id:id+'.section',kind:'section',label:clean(bar.section.marker+' '+bar.section.text),offset:F(0)});
            for(const automation of bar.tempoAutomations||[]){
                const position=F(Math.round((automation.ratioPosition||0)*1000000),1000000);
                const offset=F(measure.duration.numerator*position.numerator,measure.duration.denominator*position.denominator);
                if(automation.isLinear)warn('tempo.ramp.unsupported','渐变速度暂按标记点分段处理，请在预览确认',id);
                const onset=id+':'+value(offset),previous=tempoSeen.get(onset);
                if(previous){if(previous.bpm!==automation.value)warn('metadata.conflict','同位置速度标记冲突，采用首个源标记',id);}
                else {const tempo={measureId:id,offset,bpm:automation.value};tempoSeen.set(onset,tempo);ir.tempoChanges.push(tempo);}
            }
            for(const direction of bar.directions||[]){
                const name=alpha.model.Direction[direction];
                const kind=name==='TargetSegno'?'segno':name==='TargetCoda'?'coda':name==='TargetFine'?'fine':null;
                if(kind)measure.markers.push({id:id+'.'+kind,kind,label:kind,offset:F(0)});
                else if(name==='JumpDaCapo')measure.navigation.push({kind:'dc',targetMarkerId:null,offset:measure.duration});
                else if(name==='JumpDalSegno')measure.navigation.push({kind:'ds',targetMarkerId:null,offset:measure.duration});
                else warn('navigation.unsupported','此 Guitar Pro 跳转需要手动确认：'+name,id);
            }
            ir.measures.push(measure);
        });
        const segnos=ir.measures.flatMap(m=>m.markers.filter(marker=>marker.kind==='segno'));
        for(const measure of ir.measures)for(const navigation of measure.navigation){
            if(navigation.kind==='ds'){
                if(segnos.length===1)navigation.targetMarkerId=segnos[0].id;
                else warn('navigation.target','D.S. 目标缺失或不唯一，播放路线需要核对源谱',measure.id);
            }
        }
        if(!ir.tempoChanges.some(t=>t.measureId==='m1'&&value(t.offset)===0))ir.tempoChanges.unshift({measureId:'m1',offset:F(0),bpm:score.tempo||120});
        let events=0,notes=0,chords=0;
        score.tracks.forEach((track,ti)=>{
            const pid='p'+(ti+1),part={id:pid,name:clean(track.name||pid),instrument:null,staves:[],chords:[]};
            if(track.staves.length>16)throw Error('谱表过多');
            track.staves.forEach((staff,si)=>{
                const tuning=Array.from(staff.stringTuning.tunings||[]),sid=pid+'.s'+(si+1);
                const clefName=clef=>({[alpha.model.Clef.G2]:'G2',[alpha.model.Clef.F4]:'F4',[alpha.model.Clef.C3]:'C3',[alpha.model.Clef.C4]:'C4',[alpha.model.Clef.Neutral]:'percussion'})[clef]||null;
                const output={id:sid,name:'谱表 '+(si+1),kind:staff.isPercussion?'percussion':staff.showTablature?'tab':'standard',clef:clefName(staff.bars[0]?.clef),tuning,capo:staff.capo||0,events:[]};
                staff.bars.forEach((bar,mi)=>{
                    const measure=ir.measures[mi];if(!measure)throw Error('GP 声部小节与全局结构不一致');
                    if(clefName(bar.clef)!==output.clef)warn('clef.change.unsupported','谱号变更暂未呈现，五线谱使用该谱表的起始谱号',measure.id,pid);
                    if(ti===0&&si===0){const previous=ir.keyChanges[ir.keyChanges.length-1];const key={measureId:measure.id,offset:F(0),fifths:bar.keySignature,mode:bar.keySignatureType===1?'minor':'major'};if(!previous||previous.fifths!==key.fifths||previous.mode!==key.mode)ir.keyChanges.push(key);}
                    bar.voices.forEach((voice,vi)=>{
                        let cursor=F(0);
                        voice.beats.forEach(beat=>{
                            if(beat.isEmpty)return;
                            if(++events>500000)throw Error('GP 音符事件过多');
                            let length=duration(beat);if(beat.isFullBarRest)length=measure.duration;
                            const event={id:pid+'.e'+events,measureId:measure.id,offset:cursor,duration:length,voice:vi+1,isRest:!!beat.isRest,grace:beat.graceType!==0,notes:[],techniques:[]};
                            const ew=(name)=>warn('technique.unsupported',event.id+' 未转换的整拍技法：'+name,measure.id,pid);
                            for(const [flag,kind] of [['tap','tap'],['slap','slap'],['pop','pop']])if(beat[flag])event.techniques.push(T(kind));
                            if(beat.deadSlapped&&!beat.slap)event.techniques.push(T('slap'));
                            if(beat.pickStroke){const direction={1:'up',2:'down'}[beat.pickStroke];if(direction)event.techniques.push(T('pick',{direction}));else ew('pickStroke '+beat.pickStroke);}
                            if(beat.vibrato){const direction={1:'slight',2:'wide'}[beat.vibrato];if(direction)event.techniques.push(T('vibrato',{direction}));else ew('vibrato '+beat.vibrato);}
                            if(beat.brushType){const direction={1:'up',2:'down',3:'arpeggioUp',4:'arpeggioDown'}[beat.brushType],delay=(beat.brushDuration||0)/960;if(direction&&delay>=0&&delay<=16)event.techniques.push(T('brush',{direction,value:delay}));else ew('brush '+beat.brushType);}
                            if(beat.tremoloPicking){const speed=beat.tremoloSpeed;if([8,16,32,64,128,256].includes(speed))event.techniques.push(T('tremoloPicking',{value:speed}));else ew('tremoloPicking speed '+speed);if(beat.tremoloPicking.style)ew('tremoloPicking style '+beat.tremoloPicking.style);}
                            const whammy=curve(beat.whammyBarPoints,'whammy',event.id,measure.id,pid);if(whammy)event.techniques.push(whammy);
                            for(const flag of ['rasgueado','fade','crescendo','slashed','golpe','wahPedal'])if(beat[flag])ew(flag);
                            if(beat.hasChord){const name=clean(beat.chord.name);if(name){part.chords.push({id:pid+'.c'+(++chords),measureId:measure.id,offset:cursor,text:name,staffId:sid});}else warn('chord.name.missing','源指法图没有和弦名称，未推断名称',measure.id,pid);}
                            for(const note of beat.notes){
                                if(++notes>1000000)throw Error('GP 音符过多');
                                const isString=note.string>0&&tuning.length>0,isUnpitched=!!staff.isPercussion;
                                const pitch=isUnpitched?null:note.realValue;
                                if(pitch!==null&&(!Number.isInteger(pitch)||pitch<0||pitch>127))throw Error('GP 音高超出支持范围');
                                const outputNote={id:pid+'.n'+notes,pitch,string:isString?tuning.length-note.string+1:null,fret:isString?note.fret:null,tieStart:!!note.tieDestination,tieStop:!!note.isTieDestination,unpitched:isUnpitched,accidental:null,writtenPitch:isUnpitched?null:(Number.isInteger(note.displayValueWithoutBend)&&note.displayValueWithoutBend>=0&&note.displayValueWithoutBend<=127?note.displayValueWithoutBend:pitch),techniques:[]};
                                event.notes.push(outputNote);noteIds.set(note,outputNote.id);
                                const nw=(name)=>warn('technique.unsupported',outputNote.id+' 未转换的单音技法：'+name,measure.id,pid);
                                const put=(kind,fields={})=>outputNote.techniques.push(T(kind,fields));
                                for(const [flag,kind] of [['isPalmMute','palmMute'],['isDead','deadNote'],['isLetRing','letRing'],['isStaccato','staccato'],['isGhost','ghost'],['isLeftHandTapped','tap']])if(note[flag])put(kind);
                                if(note.accentuated){const kind={1:'accent',2:'heavyAccent'}[note.accentuated];if(kind)put(kind);else nw('accent '+note.accentuated);}
                                if(note.vibrato){const direction={1:'slight',2:'wide'}[note.vibrato];if(direction)put('vibrato',{direction});else nw('vibrato '+note.vibrato);}
                                if(note.harmonicType){const direction={1:'natural',2:'artificial',3:'pinch',4:'tap',5:'semi',6:'feedback'}[note.harmonicType];if(direction&&Number.isFinite(note.harmonicValue)&&note.harmonicValue>=0&&note.harmonicValue<=99)put('harmonic',{direction,value:note.harmonicValue});else nw('harmonic '+note.harmonicType);}
                                const bend=curve(note.bendPoints,'bend',outputNote.id,measure.id,pid);if(bend)outputNote.techniques.push(bend);
                                if(note.isHammerPullOrigin){const destination=note.hammerPullDestination,kind=destination&&(destination.fret<note.fret||(!isString&&destination.realValue<pitch))?'pullOff':'hammerOn';const t=T(kind);outputNote.techniques.push(t);pendingLinks.push({t,target:destination,id:outputNote.id,mid:measure.id,pid});}
                                if(note.slideInType){const direction={1:'inBelow',2:'inAbove'}[note.slideInType];if(direction)put('slide',{direction});else nw('slideIn '+note.slideInType);}
                                if(note.slideOutType){const direction={1:'shift',2:'legato',3:'outUp',4:'outDown'}[note.slideOutType];if(direction){const t=T('slide',{direction});outputNote.techniques.push(t);if(['shift','legato'].includes(direction))pendingLinks.push({t,target:note.slideTarget,id:outputNote.id,mid:measure.id,pid});}else nw('slideOut '+note.slideOutType);}
                                if(note.isTrill){if(Number.isInteger(note.trillValue)&&note.trillValue>=0&&note.trillValue<=127)put('trill',{value:note.trillValue});else nw('trill pitch');nw('trillSpeed '+note.trillSpeed);}
                                for(const flag of ['isFingering','isSlurOrigin','isContinuedBend','ornament'])if(note[flag])nw(flag);
                            }
                            if(beat.deadSlapped&&!event.notes.length){if(++notes>1000000)throw Error('GP 音符过多');event.notes.push({id:pid+'.n'+notes,pitch:null,string:null,fret:null,tieStart:false,tieStop:false,unpitched:true,accidental:null,writtenPitch:null,techniques:[T('deadNote')]});event.isRest=false;}
                            if(event.isRest)event.notes=[];
                            output.events.push(event);cursor=add(cursor,length);
                        });
                        if(value(cursor)>value(measure.duration))measure.duration=cursor;
                        if(score.masterBars[mi].isAnacrusis&&value(cursor)>0&&value(cursor)<value(measure.duration))measure.duration=cursor;
                    });
                });
                part.staves.push(output);
            });
            if(!part.chords.length)warn('chords.missing','该声部没有显式和弦标记；未从音符推断和弦',null,pid);
            ir.parts.push(part);
        });
        for(const {t,target,id,mid,pid} of pendingLinks){t.targetNoteId=noteIds.get(target)||null;if(t.targetNoteId===null)warn('technique.target.unresolved',id+' 的 '+t.kind+' 缺少可关联的目标音符',mid,pid);}
        warn('source.version','按文件头识别为 '+version+'；不支持的技法在预览中逐项提示');
        return IO.validateScore(ir);
    }
    function importScore(bytes,fileName=null,sha256=null){
        const details={},version=identify(bytes,details);
        const settings=new alpha.Settings();settings.core.logLevel=alpha.LogLevel.None;
        const score=alpha.importer.ScoreLoader.loadScoreFromBytes(bytes,settings);
        const ir=fromAlpha(score,fileName,sha256,version);
        return details.tree?auditGpif(details.tree,ir):ir;
    }
    return {identify,fromAlpha,importScore};
});
