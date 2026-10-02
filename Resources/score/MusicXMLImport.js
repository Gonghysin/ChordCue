(function(root,factory){
    const api=factory(typeof module==='object' && module.exports ? require('./ScoreIO.js') : root.ChordCueScoreIO);
    if(typeof module==='object' && module.exports) module.exports=api; else root.ChordCueMusicXML=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(IO){
    'use strict';
    const {child,children,get,text,fraction:F,add,sub,value} = IO;
    const clean = s => String(s || '').trim().replace(/[\r\n\t]+/g,' ');
    const integer=(s,fallback=0)=>{const n=Number(s);if(!Number.isSafeInteger(n))throw Error('谱面包含无效整数');return s===''?fallback:n;};
    const unit={whole:4,half:2,quarter:1,eighth:0.5,'16th':0.25,'32nd':0.125,'64th':0.0625};
    const semitone={C:0,D:2,E:4,F:5,G:7,A:9,B:11};
    function importScore(bytes, fileName=null, sha256=null){
        const tree=IO.parseXML(IO.decode(IO.musicXMLBytes(bytes)));
        if(!['score-partwise','score-timewise'].includes(tree.name)) throw Error('请选择 MusicXML 乐谱，而非其他 XML 文件');
        const score={formatVersion:2,id:'score',title:clean(get(child(tree,'work'),'work-title')||get(tree,'movement-title')||fileName||'导入谱面'),source:{format:'musicxml',fileName,sha256},measures:[],parts:[],tempoChanges:[],keyChanges:[],warnings:[]};
        function warn(code,message,measureId=null,partId=null){if(score.warnings.length>=10000)throw Error('谱面警告数量超过限制');score.warnings.push({code,message:clean(message),severity:'warning',measureId,partId});}
        const list=children(child(tree,'part-list'),'score-part');
        let sources;
        if(tree.name==='score-partwise') sources=children(tree,'part').map(p=>({sourceId:p.attrs.id,measures:children(p,'measure')}));
        else {
            const outer=children(tree,'measure');
            const ids=[...new Set(outer.flatMap(m=>children(m,'part').map(p=>p.attrs.id)))];
            sources=ids.map(id=>({sourceId:id,measures:outer.map(m=>{const p=children(m,'part').find(p=>p.attrs.id===id);return {name:'measure',attrs:m.attrs,children:p?p.children:[],text:''};})}));
        }
        if(!sources.length || sources.length>64) throw Error('乐谱声部数超出支持范围');
        let eventCount=0,noteCount=0,chordCount=0;
        const tempoSeen=new Map(), keySeen=new Map();
        function metadata(map,array,item,partId){
            const k=item.measureId+':'+item.offset.numerator+'/'+item.offset.denominator;
            const previous=map.get(k);
            if(previous){if(JSON.stringify(previous)!==JSON.stringify(item))warn('metadata.conflict','声部间的速度或调号标记冲突，预览采用首个标记',item.measureId,partId);return;}
            map.set(k,item);array.push(item);
        }
        for(let pi=0;pi<sources.length;pi++){
            const source=sources[pi], info=list.find(n=>n.attrs.id===source.sourceId), partId='p'+(pi+1);
            const part={id:partId,name:clean(get(info,'part-name')||source.sourceId||partId),instrument:clean(get(child(info,'score-instrument'),'instrument-name'))||null,staves:[],chords:[]};
            const staves=new Map(),openTechniques=new Map(); let divisions=1,meter={numerator:4,denominator:4},transpose=0,ending=[];
            function importTechniques(node,note,event,staffId,mid){
                const notation=child(node,'notations'),technical=child(notation,'technical'),T=IO.technique;
                const warning=(name,message='未转换的 MusicXML 技法')=>warn('technique.unsupported',note.id+' '+message+'：'+name,mid,partId);
                const put=(kind,fields={})=>{const t=T(kind,fields);if(!note.techniques.some(x=>x.kind===t.kind&&x.direction===t.direction))note.techniques.push(t);return t;};
                const link=(tag,kind,direction=null)=>{const key=staffId+':'+event.voice+':'+tag.name+':'+(tag.attrs.number||'1');if(tag.attrs.type==='start'){if(openTechniques.has(key))warning(tag.name,'重复的技法起点');const t=put(kind,direction?{direction}:{});openTechniques.set(key,{t,mid,id:note.id});}else if(tag.attrs.type==='stop'){const origin=openTechniques.get(key);if(origin){origin.t.targetNoteId=note.id;openTechniques.delete(key);}else warning(tag.name,'技法终点缺少起点');}else warning(tag.name+' type='+tag.attrs.type);};
                for(const item of technical?.children||[]){
                    if(['string','fret'].includes(item.name))continue;
                    if(item.name==='hammer-on'||item.name==='pull-off')link(item,item.name==='hammer-on'?'hammerOn':'pullOff');
                    else if(item.name==='harmonic'){
                        const natural=!!child(item,'natural'),artificial=!!child(item,'artificial');
                        if(natural!==artificial)put('harmonic',{direction:natural?'natural':'artificial'});else warning('harmonic','泛音类型缺失或冲突');
                        if(child(item,'base-pitch')||child(item,'touching-pitch'))warning('harmonic pitch','源谱未提供实际泛音响音，保留源音高');
                        for(const n of item.children)if(!['natural','artificial','base-pitch','touching-pitch','sounding-pitch'].includes(n.name))warning('harmonic/'+n.name);
                    }else if(item.name==='bend'){
                        const amount=Number(get(item,'bend-alter','NaN')),pre=!!child(item,'pre-bend'),release=!!child(item,'release');
                        if(!Number.isFinite(amount)||Math.abs(amount)>24){warning('bend-alter');continue;}
                        let curve=pre?[{position:0,semitones:amount},{position:1,semitones:release?0:amount}]:release?[{position:0,semitones:0},{position:0.5,semitones:amount},{position:1,semitones:0}]:[{position:0,semitones:0},{position:1,semitones:amount}];
                        if(note.techniques.some(t=>t.kind==='bend'))warning('bend','同音多个bend尚不能合并，保留首个');else put('bend',{direction:pre?(release?'prebendRelease':'prebend'):release?'release':'bend',curve});
                        if(item.attrs['first-beat']||item.attrs['last-beat']||item.attrs['with-bar']||child(item,'with-bar'))warning('bend timing','弯音时间/摇把属性未转换，曲线仅用于记谱显示');
                    }else if(['up-bow','down-bow'].includes(item.name))put('pick',{direction:item.name==='up-bow'?'up':'down'});
                    else if(item.name==='tap')put('tap');else warning(item.name);
                }
                for(const item of notation?.children||[]){
                    if(['technical','tuplet'].includes(item.name))continue;
                    if(item.name==='tied'){if(item.attrs.type==='let-ring')put('letRing');continue;}
                    if(item.name==='slide'||item.name==='glissando'){link(item,'slide','shift');if(item.attrs.type==='start')warning(item.name,'源结构未区分shift/legato，连线按shift显示');}
                    else if(item.name==='articulations')for(const a of item.children){const map={staccato:'staccato',accent:'accent','strong-accent':'heavyAccent'};if(map[a.name])put(map[a.name]);else warning('articulations/'+a.name);}
                    else if(item.name==='ornaments')for(const o of item.children){
                        if(o.name==='trill-mark')put('trill');
                        else if(o.name==='tremolo'&&(!o.attrs.type||o.attrs.type==='single')){const marks=integer(text(o)),denominator=4/(unit[get(node,'type')]||1)*Math.pow(2,marks);if(marks>=1&&marks<=6&&[8,16,32,64,128,256].includes(denominator)){if(!event.techniques.some(t=>t.kind==='tremoloPicking'))event.techniques.push(T('tremoloPicking',{value:denominator}));}else warning('tremolo');}
                        else warning('ornaments/'+o.name);
                    }else warning(item.name);
                }
                const head=child(node,'notehead');if(head){if(head.attrs.parentheses==='yes')put('ghost');if(text(head)==='x'&&note.string!==null)put('deadNote');}
            }
            function staff(index){
                if(!Number.isInteger(index)||index<1||index>16)throw Error('谱表数超出支持范围');
                if(!staves.has(index)){
                    const s={id:partId+'.s'+index,name:'谱表 '+index,kind:'standard',clef:null,tuning:[],capo:0,events:[]};staves.set(index,s);part.staves.push(s);
                }
                return staves.get(index);
            }
            if(source.measures.length>10000)throw Error('小节数超出支持范围');
            source.measures.forEach((measure,mi)=>{
                const mid='m'+(mi+1); let cursor=F(0),furthest=F(0),lastEvent=null,clearEnding=false;
                const local={id:mid,number:clean(measure.attrs.number||String(mi+1)),duration:F(meter.numerator*4,meter.denominator),meter:{...meter},repeatStart:false,repeatEnd:null,endingNumbers:[...ending],markers:[],navigation:[]};
                const advance=amount=>{cursor=add(cursor,amount);if(value(cursor)<0)throw Error('MusicXML backup 越过小节起点');if(value(cursor)>value(furthest))furthest=cursor;};
                const marker=(kind,label,offset)=>{const id=mid+'.marker'+(local.markers.length+1);local.markers.push({id,kind,label:clean(label),offset});return id;};
                for(const node of measure.children){
                    if(node.name==='attributes'){
                        const d=get(node,'divisions');if(d){divisions=integer(d);if(divisions<1||divisions>1000000)throw Error('MusicXML divisions 超出支持范围');}
                        const time=child(node,'time');if(time){
                            const beats=get(time,'beats'),den=get(time,'beat-type');
                            if(!/^\d+$/.test(beats)||!/^\d+$/.test(den)||children(time,'beats').length!==1){warn('meter.unsupported','复合拍号表达式暂不支持',mid,partId);throw Error('复合拍号表达式暂不支持，请导出单一拍号');}
                            meter={numerator:integer(beats),denominator:integer(den)};
                            if(![1,2,4,8,16,32,64].includes(meter.denominator)||meter.numerator<1||meter.numerator>64)throw Error('不支持的拍号');
                            local.meter={...meter};local.duration=F(meter.numerator*4,meter.denominator);
                            if(value(cursor)>0)warn('meter.midbar','小节中途的拍号改变暂按该小节有效拍号显示',mid,partId);
                        }
                        const trans=child(node,'transpose');if(trans)transpose=integer(get(trans,'chromatic','0'))+12*integer(get(trans,'octave-change','0'));
                        const key=child(node,'key');if(key){
                            if(value(cursor)>0)warn('key.midbar','小节中途调号已保留；五线谱本小节按起始调号排版，和弦级数按精确位置换调',mid,partId);
                            const fifths=get(key,'fifths');
                            if(fifths==='')warn('key.unsupported','非标准调号暂未转换',mid,partId);
                            else metadata(keySeen,score.keyChanges,{measureId:mid,offset:cursor,fifths:integer(fifths),mode:['major','minor'].includes(get(key,'mode'))?get(key,'mode'):'unknown'},partId);
                        }
                        for(const clef of children(node,'clef')){
                            const s=staff(integer(clef.attrs.number||'1')),nextClef=clean(get(clef,'sign')+get(clef,'line'));
                            if(s.clef&&s.clef!==nextClef)warn('clef.change.unsupported','谱号变更暂未呈现，五线谱使用该谱表的起始谱号',mid,partId);else s.clef=nextClef;
                            if(get(clef,'sign')==='TAB')s.kind='tab';if(get(clef,'sign')==='percussion')s.kind='percussion';
                        }
                        const count=integer(get(node,'staves','1'));for(let si=1;si<=count;si++)staff(si);
                        for(const details of children(node,'staff-details')){
                            const s=staff(integer(details.attrs.number||'1'));
                            s.capo=integer(get(details,'capo','0'));
                            const tuning=children(details,'staff-tuning');
                            if(tuning.length){
                                const lines=integer(get(details,'staff-lines',String(tuning.length)));
                                if(lines<1||lines>24)throw Error('TAB 调弦弦数须在 1–24 范围');
                                const pitches=Array(lines).fill(null);
                                for(const t of tuning){const line=integer(t.attrs.line||'1');const step=get(t,'tuning-step'),octave=integer(get(t,'tuning-octave','4')),alter=integer(get(t,'tuning-alter','0'));if(!(step in semitone)||line<1||line>lines)throw Error('无效 TAB 调弦');pitches[lines-line]=12*(octave+1)+semitone[step]+alter;}
                                if(pitches.some(p=>p===null))warn('tab.tuning.partial','调弦资料不完整，保留源指法并限制 TAB 视图',mid,partId);else s.tuning=pitches;
                            }
                        }
                    }else if(node.name==='backup'||node.name==='forward'){
                        const duration=F(integer(get(node,'duration')),divisions);advance(node.name==='backup'?F(-duration.numerator,duration.denominator):duration);lastEvent=null;
                    }else if(node.name==='note'){
                        const s=staff(integer(get(node,'staff','1'))),voice=integer(get(node,'voice','1'));
                        if(voice<1||voice>16)throw Error('声部编号超出支持范围');
                        const grace=!!child(node,'grace'),duration=F(integer(get(node,'duration',grace?'0':'')),divisions),isChord=!!child(node,'chord'),isRest=!!child(node,'rest');
                        if(!grace&&value(duration)<=0)throw Error('音符缺少有效时值');
                        let event;
                        if(isChord){if(!lastEvent||lastEvent.staffId!==s.id||lastEvent.voice!==voice||lastEvent.isRest)throw Error('MusicXML 和声音符缺少同声部起始音符');event=lastEvent.event;}
                        else{
                            if(++eventCount>500000)throw Error('音符事件过多');
                            event={id:partId+'.e'+eventCount,measureId:mid,offset:cursor,duration,voice,isRest,grace,notes:[],techniques:[]};s.events.push(event);lastEvent={event,staffId:s.id,voice,isRest};if(!grace)advance(duration);
                        }
                        if(!isRest){
                            const pitchNode=child(node,'pitch'),unpitched=!!child(node,'unpitched'),tech=child(child(node,'notations'),'technical');
                            let pitch=null;
                            if(pitchNode){const step=get(pitchNode,'step'),alter=Number(get(pitchNode,'alter','0'));if(!(step in semitone)||!Number.isInteger(alter))throw Error('微分音音高暂不支持，请检查导出选项');pitch=12*(integer(get(pitchNode,'octave'))+1)+semitone[step]+alter+transpose;}
                            const string=child(tech,'string')?integer(get(tech,'string')):null,fret=child(tech,'fret')?integer(get(tech,'fret')):null;
                            if(++noteCount>1000000)throw Error('谱面音符过多');
                            const ties=[...children(node,'tie'),...children(child(node,'notations'),'tied')];
                            const note={id:partId+'.n'+noteCount,pitch,string,fret,tieStart:ties.some(t=>t.attrs.type==='start'),tieStop:ties.some(t=>t.attrs.type==='stop'),unpitched,accidental:clean(get(node,'accidental'))||null,writtenPitch:pitch===null?null:pitch-transpose,techniques:[]};
                            event.notes.push(note);importTechniques(node,note,event,s.id,mid);
                        }
                    }else if(node.name==='harmony'){
                        const rootNode=child(node,'root'),kind=child(node,'kind'),bass=child(node,'bass');
                        const alteration=a=>{if(a< -2||a>2)throw Error('和弦变化音超过支持的双升降范围');return a>0?'#'.repeat(a):'b'.repeat(-a);};
                        const pitchName=p=>{if(!p)return '';const n=child(p,p.name==='bass'?'bass-step':'root-step'),alter=integer(get(p,p.name==='bass'?'bass-alter':'root-alter','0'));return (n&&n.attrs.text)||text(n)+alteration(alter);};
                        const kindName={major:'',minor:'m',dominant:'7','major-seventh':'maj7','minor-seventh':'m7',diminished:'dim',augmented:'aug','half-diminished':'m7b5','diminished-seventh':'dim7','major-sixth':'6','minor-sixth':'m6','suspended-second':'sus2','suspended-fourth':'sus4',none:'N.C.'};
                        const kindText=kind&&(kind.attrs.text||kindName[text(kind)]);
                        let chordText=kind&&text(kind)==='none'?'N.C.':pitchName(rootNode)+(kindText===undefined?text(kind):kindText||'');
                        if(bass)chordText+='/'+pitchName(bass);
                        for(const degree of children(node,'degree')){const n=get(degree,'degree-value'),alter=integer(get(degree,'degree-alter','0')),type=get(degree,'degree-type');chordText+='('+ (type==='subtract'?'no':type==='add'?'add':'')+alteration(alter)+n+')';}
                        if(!chordText){chordText=clean(get(node,'function')||get(node,'numeral'));warn('harmony.unsupported','未支持的和弦标记保留其源文字',mid,partId);}
                        if(++chordCount>100000)throw Error('和弦标记过多');
                        const offset=add(cursor,F(integer(get(node,'offset','0')),divisions));
                        part.chords.push({id:partId+'.c'+chordCount,measureId:mid,offset,text:clean(chordText)||'?',staffId:child(node,'staff')?staff(integer(get(node,'staff'))).id:null});
                        if(child(node,'inversion'))warn('harmony.inversion','已保留源低音标记；数字转位未转换为指法',mid,partId);
                    }else if(node.name==='direction'||node.name==='sound'){
                        const offset=node.name==='direction'?add(cursor,F(integer(get(node,'offset','0')),divisions)):cursor;
                        const sound=node.name==='sound'?node:child(node,'sound'),types=children(node,'direction-type');
                        let bpm=sound&&sound.attrs.tempo?Number(sound.attrs.tempo):null;
                        for(const type of types){
                            const met=child(type,'metronome');if(met&&!bpm){const scale=unit[get(met,'beat-unit')],dots=children(met,'beat-unit-dot').length,per=Number(get(met,'per-minute'));if(scale&&Number.isFinite(per))bpm=per*scale*(2-Math.pow(0.5,dots));else warn('tempo.unsupported','不支持的速度表达式',mid,partId);}
                            for(const n of type.children){if(n.name==='rehearsal')marker('rehearsal',text(n),offset);if(n.name==='segno'||n.name==='coda')marker(n.name,text(n)||n.name,offset);}
                        }
                        if(bpm!==null){if(!Number.isFinite(bpm)||bpm<1||bpm>1000)throw Error('谱面速度超出支持范围');metadata(tempoSeen,score.tempoChanges,{measureId:mid,offset,bpm},partId);}
                        if(sound){
                            for(const [attribute,kind] of [['dacapo','dc'],['dalsegno','ds'],['tocoda','toCoda'],['fine','fine']])if(sound.attrs[attribute]){local.navigation.push({kind,targetMarkerId:null,offset});if(kind==='ds'||kind==='toCoda')warn('navigation.target','跳转目标需要预览确认',mid,partId);}
                        }
                    }else if(node.name==='barline'){
                        const repeat=child(node,'repeat');if(repeat){if(repeat.attrs.direction==='forward')local.repeatStart=true;else if(repeat.attrs.direction==='backward')local.repeatEnd=integer(repeat.attrs.times||'2');}
                        const e=child(node,'ending');if(e){const passes=(e.attrs.number||'').split(/[ ,]+/).filter(Boolean).flatMap(s=>{const range=/^(\d+)-(\d+)$/.exec(s);if(!range)return [integer(s)];const first=integer(range[1]),last=integer(range[2]);if(first<1||last>32||last<first)throw Error('不支持的跳房子编号');return Array.from({length:last-first+1},(_,i)=>first+i);});if(passes.some(n=>n<1||n>32))throw Error('不支持的跳房子编号');if(e.attrs.type==='start'){ending=passes;local.endingNumbers=[...ending];}else{if(!local.endingNumbers.length)local.endingNumbers=passes;clearEnding=true;}}
                    }else if(!['print','credit'].includes(node.name))warn('element.unsupported','未呈现的谱面元素：'+node.name,mid,partId);
                }
                if(measure.attrs.implicit==='yes'&&value(furthest)>0)local.duration=furthest;
                else if(value(furthest)>value(local.duration))local.duration=furthest;
                const existing=score.measures[mi];
                if(!existing)score.measures.push(local);
                else{
                    if(value(local.duration)>value(existing.duration))existing.duration=local.duration;
                    if(JSON.stringify(existing.meter)!==JSON.stringify(local.meter))warn('meter.parts.conflict','声部间拍号不同，采用首个声部的拍号',mid,partId);
                    if(local.repeatStart)existing.repeatStart=true;if(local.repeatEnd)existing.repeatEnd=local.repeatEnd;
                    if(!existing.endingNumbers.length&&local.endingNumbers.length)existing.endingNumbers=local.endingNumbers;
                    if(local.markers.length||local.navigation.length)warn('structure.other-part','声部特有段落或跳转标记尚未合并；播放路线采用首声部标记，请核对源谱',mid,partId);
                }
                if(clearEnding)ending=[];
            });
            if(!part.staves.length)staff(1);
            if(!part.chords.length)warn('chords.missing','该声部没有显式和弦标记；未从音符推断和弦',null,partId);
            for(const {t,mid,id} of openTechniques.values())warn('technique.target.unresolved',id+' 的 '+t.kind+' 缺少技法终点，已保留起点',mid,partId);
            score.parts.push(part);
        }
        if(!score.measures.length)throw Error('谱面没有小节');
        for(const part of score.parts){
            for(const staff of part.staves)for(const event of staff.events){const measure=score.measures.find(m=>m.id===event.measureId);if(!measure||value(add(event.offset,event.duration))>value(measure.duration)+1e-10)throw Error('音符超出有效小节长度');}
        }
        return IO.validateScore(score);
    }
    return {importScore};
});
