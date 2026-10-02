(function(root,factory){
    const api=factory(typeof module==='object'&&module.exports?require('./ScoreIO.js'):root.ChordCueScoreIO,typeof module==='object'&&module.exports?require('../vendor/alphatab/dist/alphaTab.js'):root.alphaTab);
    if(typeof module==='object'&&module.exports)module.exports=api;else root.ChordCueScoreView=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(IO,alpha){
    'use strict';
    const q=IO.value;
    function transposedFifths(fifths,shift){
        if(shift%12===0)return fifths;
        const pitch=((fifths*7+shift)%12+12)%12;
        // Use an equivalent key within the supported seven-accidental range.
        // At the F-sharp/G-flat tie, retain the source's sharp/flat preference.
        return pitch===6&&fifths<0?-6:[0,-5,2,-3,4,-1,6,1,-4,3,-2,5][pitch];
    }
    function durationSettings(duration){
        const length=q(duration);
        for(const base of [-4,-2,1,2,4,8,16,32,64,128,256])for(let dots=0;dots<=3;dots++){
            const quarters=base<0?-4*base:4/base;
            if(Math.abs(quarters*(2-Math.pow(0.5,dots))-length)<1e-10)return {duration:base,dots,tupletNumerator:-1,tupletDenominator:-1};
        }
        for(const base of [4,8,16,2,32,1,64,128,256]){
            const ratio=IO.fraction(duration.numerator*base,duration.denominator*4);
            if(ratio.numerator>0&&ratio.numerator<=32&&ratio.denominator<=32)return {duration:base,dots:0,tupletNumerator:ratio.denominator,tupletDenominator:ratio.numerator};
        }
        throw Error('此时值暂不能准确排版，请选择和弦视图并查看源时值');
    }
    function applyTechniques(target,techniques,beat){
        for(const effect of techniques||[]){
            switch(effect.kind){
            case 'hammerOn':case 'pullOff':{
                // Source IDs, rather than the vendor's nearest-string guess,
                // determine these links after the model has finished.
                const label=effect.kind==='hammerOn'?'H':'P';
                if(!(beat.text||'').split(' ').includes(label))beat.text=(beat.text?beat.text+' ':'')+label;
                break;
            }
            case 'slide':{
                const incoming={inAbove:alpha.model.SlideInType.IntoFromAbove,inBelow:alpha.model.SlideInType.IntoFromBelow};
                if(effect.direction in incoming)target.slideInType=incoming[effect.direction];
                else target.slideOutType=({shift:1,legato:2,outUp:3,outDown:4})[effect.direction]||0;
                break;
            }
            case 'bend':case 'whammy':
                for(const point of effect.curve){const p=new alpha.model.BendPoint(point.position*60,point.semitones*2);if(effect.kind==='bend')target.addBendPoint(p);else beat.addWhammyBarPoint(p);}break;
            case 'vibrato':target.vibrato=effect.direction==='wide'?2:1;break;
            case 'harmonic':target.harmonicType=({natural:1,artificial:2,pinch:3,tap:4,semi:5,feedback:6})[effect.direction]||0;target.harmonicValue=effect.value??0;break;
            case 'palmMute':target.isPalmMute=true;break;
            case 'deadNote':target.isDead=true;break;
            case 'letRing':target.isLetRing=true;break;
            case 'staccato':target.isStaccato=true;break;
            case 'accent':target.accentuated=1;break;
            case 'heavyAccent':target.accentuated=2;break;
            case 'ghost':target.isGhost=true;break;
            case 'pick':beat.pickStroke=effect.direction==='up'?1:2;break;
            case 'tap':beat.tap=true;break;
            case 'slap':beat.slap=true;break;
            case 'pop':beat.pop=true;break;
            case 'brush':beat.brushType=({up:1,down:2,arpeggioUp:3,arpeggioDown:4})[effect.direction]||0;beat.brushDuration=(effect.value??0)*960;break;
            case 'tremoloPicking':beat.tremoloSpeed=effect.value;break;
            case 'trill':if(effect.value!==null){target.trillValue=effect.value;target.trillSpeed=alpha.model.Duration.Sixteenth;}else beat.text=(beat.text?beat.text+' ':'')+'tr';break;
            }
        }
    }
    function activeEntries(entries,offset){
        return entries.filter(item=>item.grace?Math.abs(offset-item.start)<1e-8:item.start<=offset&&offset<item.end);
    }
    function instrumentTranspositions(staff){
        const notes=staff.events.flatMap(e=>e.notes).filter(n=>n.pitch!==null&&n.writtenPitch!=null);
        const ordinary=notes.filter(n=>!(n.techniques||[]).some(t=>t.kind==='harmonic'));
        if(ordinary.length)return new Set(ordinary.map(n=>n.pitch-n.writtenPitch));
        // A harmonic-only staff still has explicit written pitches. Artificial
        // harmonics display their base pitch, so remove only the known harmonic
        // interval before deriving the instrument's display transposition.
        return new Set(notes.map(n=>{
            const harmonic=(n.techniques||[]).find(t=>t.kind==='harmonic');
            let interval=0;
            if(harmonic&&harmonic.direction!=='natural'&&harmonic.value!==null&&n.string!==null){
                const probe=new alpha.model.Note();probe.string=1;probe.harmonicType=2;probe.harmonicValue=harmonic.value;interval=probe.harmonicPitch;
            }
            return n.pitch-n.writtenPitch-interval;
        }));
    }
    function toAlpha(score,partId,view='staff',shift=0){
        const source=score.parts.find(part=>part.id===partId);if(!source)throw Error('声部不存在');
        if(view==='tab'&&!source.staves.some(staff=>staff.tuning.length&&staff.events.some(event=>event.notes.some(note=>note.string!==null))))throw Error('源谱缺少调弦或弦品资料，无法显示源 TAB');
        if(view==='tab'&&shift!==0)throw Error('源 TAB 指法保持原调；移调请使用五线谱或和弦视图');
        const model=new alpha.model.Score();model.title=score.title;
        const index=new Map(score.measures.map((m,i)=>[m.id,i]));
        for(const measure of score.measures){const bar=new alpha.model.MasterBar();bar.timeSignatureNumerator=measure.meter.numerator;bar.timeSignatureDenominator=measure.meter.denominator;bar.isRepeatStart=measure.repeatStart;bar.repeatCount=measure.repeatEnd||0;bar.alternateEndings=measure.endingNumbers.reduce((mask,n)=>mask|(1<<(n-1)),0);bar.isAnacrusis=q(measure.duration)<measure.meter.numerator*4/measure.meter.denominator;if(measure.markers.length){bar.section=new alpha.model.Section();bar.section.marker=measure.markers.map(m=>m.label).join(' · ');}model.addMasterBar(bar);}
        const track=new alpha.model.Track();track.name=source.name;model.addTrack(track);
        const beatMap=new Map(),noteMap=new Map(),relationships=[];
        for(const staffSource of source.staves){
            let effectiveKey={fifths:0,mode:'major'};
            const staffVoiceIds=[...new Set(staffSource.events.map(e=>e.voice))].sort((a,b)=>a-b);if(!staffVoiceIds.length)staffVoiceIds.push(1);
            if(view==='tab'&&!staffSource.tuning.length)continue;
            const staff=new alpha.model.Staff();staff.stringTuning.tunings=[...staffSource.tuning];staff.capo=staffSource.capo;staff.isPercussion=staffSource.kind==='percussion';staff.showTablature=view==='tab';staff.showStandardNotation=view==='staff';track.addStaff(staff);
            const transpositions=instrumentTranspositions(staffSource);
            const instrumentTranspose=transpositions.size===1?[...transpositions][0]:0;
            staff.transpositionPitch=-shift;staff.displayTranspositionPitch=instrumentTranspose;
            for(const measure of score.measures){
                const bar=new alpha.model.Bar();bar.clef=({'F4':alpha.model.Clef.F4,'C3':alpha.model.Clef.C3,'C4':alpha.model.Clef.C4})[staffSource.clef]??(staff.isPercussion?alpha.model.Clef.Neutral:alpha.model.Clef.G2);
                const keys=score.keyChanges.filter(k=>k.measureId===measure.id&&q(k.offset)===0);if(keys.length)effectiveKey=keys[0];bar.keySignature=transposedFifths(effectiveKey.fifths,shift);bar.keySignatureType=effectiveKey.mode==='minor'?1:0;staff.addBar(bar);
                const events=staffSource.events.filter(e=>e.measureId===measure.id),voiceIds=staffVoiceIds;
                for(const voiceId of voiceIds){
                    const voice=new alpha.model.Voice();bar.addVoice(voice);let cursor=IO.fraction(0);
                    const remember=(beat,start,end,event=null)=>{if(!beatMap.has(measure.id))beatMap.set(measure.id,[]);beatMap.get(measure.id).push({offset:start,start,end,staffId:staffSource.id,voice:voiceId,eventId:event?.id||null,noteIds:event?.notes.map(n=>n.id)||[],grace:!!event?.grace,synthetic:!event,beat});};
                    const rest=length=>{if(q(length)<=0)return;const beat=new alpha.model.Beat();Object.assign(beat,durationSettings(length));voice.addBeat(beat);remember(beat,q(cursor),q(cursor)+q(length));};
                    for(const event of events.filter(e=>e.voice===voiceId).sort((a,b)=>q(a.offset)-q(b.offset))){
                        if(q(event.offset)<q(cursor)-1e-10)throw Error('同声部音符重叠，无法准确排版');rest(IO.sub(event.offset,cursor));
                        const beat=new alpha.model.Beat();Object.assign(beat,durationSettings(q(event.duration)>0?event.duration:IO.fraction(1,8)));if(event.grace)beat.graceType=alpha.model.GraceType.BeforeBeat;
                        for(const sourceNote of event.notes){
                            const note=new alpha.model.Note();
                            if(sourceNote.pitch!==null&&(sourceNote.pitch+shift<0||sourceNote.pitch+shift>127))throw Error('移调后音高超出范围');
                            const written=sourceNote.writtenPitch??sourceNote.pitch;
                            if(written!==null&&(written+shift<0||written+shift>127))throw Error('移调后记谱音高超出范围');
                            const unknownHarmonic=(sourceNote.techniques||[]).some(t=>t.kind==='harmonic'&&t.value===null);
                            if(sourceNote.string!==null&&staffSource.tuning.length&&(view==='tab'||!unknownHarmonic)){note.string=staffSource.tuning.length-sourceNote.string+1;note.fret=sourceNote.fret;}
                            else if(sourceNote.pitch!==null){const pitch=transpositions.size>1?(sourceNote.writtenPitch??sourceNote.pitch):sourceNote.pitch;note.octave=Math.floor(pitch/12);note.tone=pitch%12;}
                            else if(sourceNote.unpitched){note.percussionArticulation=38;}
                            else throw Error('源音符缺少五线谱音高');
                            applyTechniques(note,sourceNote.techniques,beat);
                            note.isTieDestination=sourceNote.tieStop;beat.addNote(note);noteMap.set(sourceNote.id,note);
                            for(const effect of sourceNote.techniques||[])if(effect.targetNoteId)relationships.push({note,effect});
                        }
                        applyTechniques(beat,event.techniques,beat);
                        voice.addBeat(beat);
                        remember(beat,q(event.offset),q(event.offset)+q(event.duration),event);
                        if(!event.grace)cursor=IO.add(event.offset,event.duration);
                    }
                    rest(IO.sub(measure.duration,cursor));
                }
                const lastKey=score.keyChanges.filter(k=>k.measureId===measure.id).sort((a,b)=>q(a.offset)-q(b.offset)).at(-1);if(lastKey)effectiveKey=lastKey;
            }
        }
        if(!track.staves.length)throw Error('没有可呈现的谱表');
        for(const {note,effect} of relationships){const target=noteMap.get(effect.targetNoteId);if(effect.kind==='slide')note.slideTarget=target;else note.hammerPullDestination=target;}
        model.finish(new alpha.Settings());
        for(const {note,effect} of relationships){
            const target=noteMap.get(effect.targetNoteId);if(!target)continue;
            if(effect.kind==='slide'){note.slideTarget=target;target.slideOrigin=note;}
            else {
                note.hammerPullDestination=target;
                note.slurDestination=target;target.slurOrigin=note;target.isSlurDestination=true;
                // TAB uses effect slurs; set the explicit endpoints only after
                // finish, which would otherwise infer a nearest-string target.
                if(view==='tab'){note.hasEffectSlur=true;note.isEffectSlurOrigin=true;note.effectSlurDestination=target;target.effectSlurOrigin=note;}
            }
        }
        return {model,beatMap,index,noteMap};
    }
    class ScoreView{
        constructor(element,{assetBase='vendor/alphatab/dist/',onSeek=null,scrollRoot=()=>document.scrollingElement,topInset=()=>0}={}){
            this.element=element;this.assetBase=assetBase;this.onSeek=onSeek;this.api=null;this.score=null;this.partId=null;this.view='staff';this.lastMeasure=null;this.closed=false;this.rendered=false;this._pendingRender=null;
            this.heading=document.createElement('div');this.heading.className='score-view-heading';this.canvas=document.createElement('div');this.canvas.className='score-view-canvas';this.element.replaceChildren(this.heading,this.canvas);
            this.scrollRoot=scrollRoot;this.topInset=topInset;this.follow=true;this.active=true;this.highlights=[];this.latestSample=null;this.layoutRevision=0;this.lastSystem=null;
            this.element.style.position='relative';this.layer=document.createElement('div');this.layer.className='score-playback-layer';this.element.append(this.layer);
            this._resize=()=>{this.lastSystem=null;this._updateTailSpace();if(this.latestSample)this.setPosition(this.latestSample);};
            this._observer=typeof ResizeObserver!=='undefined'?new ResizeObserver(this._resize):null;this._observer?.observe(this.canvas);
            document.fonts?.ready.then(()=>{if(!this.closed)this._resize();});
        }
        _clearHighlights(){for(const box of this.highlights)box.hidden=true;}
        _cancelPendingRender(){
            const pending=this._pendingRender;if(!pending)return;
            pending.cancel();
            // alphaTab render events have no request identity. Retire an API
            // with unfinished work before accepting a replacement request.
            if(this.api===pending.api){this.api=null;pending.api.destroy();this.canvas.replaceChildren();this.cursor=null;this.canvas.hidden=true;}
        }
        show(score,partId,view='staff',shift=0){
            if(this.closed)throw Error('谱面视图已关闭');
            this.rendered=false;
            this._clearHighlights();this.lastSystem=null;
            this._cancelPendingRender();
            this.score=score;this.partId=partId;this.view=view;this.lastMeasure=null;
            this.canvas.hidden=true;
            if(this.cursor)this.cursor.hidden=true;
            const converted=toAlpha(score,partId,view,shift);this.converted=converted;
            this.canvas.hidden=false;
            const source=score.parts.find(p=>p.id===partId);
            this.heading.textContent=source.name+' · '+(view==='tab'?'源 TAB':'五线谱')+' · '+score.measures.length+' 小节';
            if(score.keyChanges.some(k=>q(k.offset)>0))this.heading.textContent+=' · 中途调号：本小节按起始调号排版，下小节继承新调号';
            if(score.warnings.some(w=>w.code==='clef.change.unsupported'))this.heading.textContent+=' · 谱号变更未呈现，请核对源谱';
            if(source.staves.some(s=>s.events.some(e=>e.notes.some(n=>(n.techniques||[]).some(t=>t.kind==='harmonic'&&t.value===null)))))this.heading.textContent+=' · 部分泛音缺少触弦节点：五线谱使用源记谱音高，TAB保留源指法';
            if(source.staves.some(s=>instrumentTranspositions(s).size>1))this.heading.textContent+=' · 中途乐器移调按源记谱音高呈现';
            if(!this.api){
                const api=this.api=new alpha.AlphaTabApi(this.canvas,{core:{fontDirectory:this.assetBase+'font/',useWorkers:false,engine:'svg',includeNoteBounds:true},player:{enablePlayer:false},display:{scale:1,layoutMode:alpha.LayoutMode.Page},notation:{elements:{scoreTitle:false,scoreArtist:false}}});
                api.beatMouseDown.on(args=>{if(this.api===api&&this.rendered&&this.onSeek&&args.beat){const i=args.beat.voice.bar.masterBar.index,measure=this.score.measures[i];if(measure)this.onSeek(measure.id,args.beat.displayStart/960);}});
                api.renderFinished.on(()=>{if(this.api===api&&!this._pendingRender&&this.rendered){this.layoutRevision++;this.lastSystem=null;this._updateTailSpace();if(this.latestSample)this.setPosition(this.latestSample);}});
            }
            this.api.settings.display.staveProfile=view==='tab'?alpha.StaveProfile.Tab:alpha.StaveProfile.Score;
            this.api.updateSettings();
            const api=this.api,request={api,cancel:null};
            const completion=new Promise((resolve,reject)=>{
                const current=()=>this._pendingRender===request&&this.api===api&&!this.closed;
                const done=()=>{if(!current())return;cleanup();this.rendered=true;this.layoutRevision++;this.lastSystem=null;this._updateTailSpace();if(this.latestSample)this.setPosition(this.latestSample);resolve(true);};
                const failed=error=>{if(!current())return;cleanup();this.rendered=false;this.canvas.hidden=true;if(this.cursor)this.cursor.hidden=true;reject(Error(error?.message||'谱面排版失败'));};
                const cleanup=()=>{api.renderFinished.off(done);api.error.off(failed);if(this._pendingRender===request)this._pendingRender=null;};
                request.cancel=()=>{cleanup();resolve(false);};this._pendingRender=request;
                api.renderFinished.on(done);api.error.on(failed);
                try{api.renderScore(converted.model,[0]);}catch(error){failed(error);}
            });
            // Explicit chord source annotations use textContent; vendor-normalized names
            // never replace the source annotations or execute markup.
            const annotations=document.createElement('div');annotations.className='score-source-chords';
            for(const measure of score.measures){const values=source.chords.filter(c=>c.measureId===measure.id);if(values.length){const line=document.createElement('span');line.textContent=measure.number+': '+values.map(c=>c.text+' @'+q(c.offset)).join(' · ');annotations.append(line);}}
            this.heading.append(annotations);
            return completion;
        }
        setFollow(enabled){if(this.follow===!!enabled)return;this.follow=!!enabled;this.lastSystem=null;if(enabled&&this.latestSample)this.setPosition(this.latestSample);}
        setActive(active){if(this.active===!!active)return;this.active=!!active;this.lastSystem=null;if(active&&this.latestSample)this.setPosition(this.latestSample);else this._clearHighlights();}
        _origin(){const source=this.api?.canvasElement?.element||this.canvas;const a=source.getBoundingClientRect?.()||{left:0,top:0},b=this.element.getBoundingClientRect?.()||{left:0,top:0};return {x:a.left-b.left,y:a.top-b.top};}
        _updateTailSpace(){const root=this.scrollRoot();const height=root?.clientHeight||globalThis.innerHeight||0;this.canvas.style.marginBottom=Math.max(0,height-this.topInset())+'px';}
        _scrollToSystem(system,sample){
            const identity=this.layoutRevision+':'+system.index+':'+(sample.discontinuity??0);
            if(!this.follow||!sample.playing||sample.preparing||identity===this.lastSystem)return;
            const root=this.scrollRoot();if(!root)return;
            this.lastSystem=identity;
            const origin=this._origin(),rect=this.element.getBoundingClientRect?.()||{top:0},bodyRoot=root===document.scrollingElement;
            const inset=bodyRoot?0:(root.getBoundingClientRect?.().top||0);
            const top=Math.max(0,(root.scrollTop||0)+rect.top+origin.y+system.realBounds.y-inset-this.topInset());
            if(root.scrollTo)root.scrollTo({top,behavior:'instant'});else root.scrollTop=top;
        }
        setPosition(sample){
            const previous=this.latestSample;this.latestSample=sample;this._clearHighlights();
            if(!this.active||!this.api||!this.rendered||sample?.valid===false||!sample?.sourceMeasureId)return;
            const measureId=sample.sourceMeasureId,offset=typeof sample.sourceOffsetQuarter==='number'?sample.sourceOffsetQuarter:q(sample.sourceOffsetQuarter||IO.fraction(0));
            if(!Number.isFinite(offset))return;
            if(sample.playing&&!previous?.playing)this.lastSystem=null;
            if(previous?.playing&&previous.sourceMeasureId===measureId&&typeof previous.sourceOffsetQuarter==='number'&&offset<previous.sourceOffsetQuarter-1e-8)this.lastSystem=null;
            if(previous?.playing&&(this.converted.index.get(measureId)??0)<(this.converted.index.get(previous.sourceMeasureId)??0))this.lastSystem=null;
            const entries=activeEntries(this.converted.beatMap.get(measureId)||[],offset),origin=this._origin();let count=0,system=null;
            for(const entry of entries){
                const lookup=this.api.renderer.boundsLookup,bounds=lookup?.findBeats?lookup.findBeats(entry.beat):[lookup?.findBeat(entry.beat)];
                for(const bound of bounds||[]){
                    if(!bound)continue;system ||= bound.barBounds?.masterBarBounds?.staffSystemBounds;
                    let boxes=(bound.notes||[]).map(n=>n.noteHeadBounds).filter(Boolean);
                    if(!boxes.length){
                        const glyph=this.canvas.querySelector?.('g.b'+entry.beat.id),rect=glyph?.getBoundingClientRect?.(),source=(this.api?.canvasElement?.element||this.canvas).getBoundingClientRect?.();
                        if(rect?.width&&source)boxes=[{x:rect.left-source.left,y:rect.top-source.top,w:rect.width,h:rect.height}];
                        else {const b=bound.visualBounds||bound.realBounds;if(b)boxes=[{x:b.x,y:b.y+(b.h||14)/2-7,w:Math.min(b.w||12,18),h:14}];}
                    }
                    for(const box of boxes){
                        if(![box.x,box.y,box.w,box.h].every(Number.isFinite)||box.w<=0||box.h<=0)continue;
                        let marker=this.highlights[count];if(!marker){marker=document.createElement('div');marker.className='score-note-highlight';this.layer.append(marker);this.highlights.push(marker);}
                        marker.hidden=false;marker.style.cssText='left:'+(origin.x+box.x-3)+'px;top:'+(origin.y+box.y-3)+'px;width:'+(box.w+6)+'px;height:'+(box.h+6)+'px';
                        count++;
                    }
                }
            }
            this.cursor=this.highlights[0]||this.cursor;
            if(system)this._scrollToSystem(system,sample);
        }
        destroy(){this.closed=true;this.rendered=false;this._observer?.disconnect();this._clearHighlights();this._cancelPendingRender();this.api?.destroy();this.api=null;this.element.replaceChildren();}
    }
    return {toAlpha,durationSettings,activeEntries,applyTechniques,ScoreView};
});
