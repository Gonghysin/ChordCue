// SPDX-License-Identifier: MIT
// Scheduling reference: Chris Wilson's cwilso/metronome (MIT); see THIRD_PARTY_NOTICES.md.
/* Quarter-note timeline shared by the display and the audio scheduler. Kept in
 * this resource so existing WebKit bundles and LAN resource routes still work. */
(()=>{
'use strict';
const beats=s=>Number(s.meter.split('/')[0])||4;
const offset=s=>s.beat-1+(s.division-1)/4+s.tick/960;
const absolute=s=>(s.bar-1)*beats(s)+offset(s);
const loop=s=>s.playback?.loop||null;
const length=s=>loop(s)?loop(s).endBeat-loop(s).startBeat:0;
const base=s=>absolute(s)+(loop(s)?loop(s).iteration*length(s):0);
const anchor=s=>s.playback&&Number.isFinite(s.playback.startTime)?Math.max(s.sampleTime,s.playback.startTime):s.sampleTime;
function unwrapped(s,time){return base(s)+(s.playing&&s.rate>0?s.rate*Math.max(0,time-anchor(s))/1000:0)}
function musical(s,value){if(!s.playing)return absolute(s);const l=loop(s);return l?l.startBeat+((value-l.startBeat)%length(s)+length(s))%length(s):s.playback?Math.min(value,s.playback.endBeat):value}
function position(s,time,{clampToBar=false}={}){
 const total=unwrapped(s,time),value=musical(s,total),count=beats(s);
 if(clampToBar&&!s.playback)return {bar:s.bar,offset:Math.min(count,value-(s.bar-1)*count),unwrapped:total,ended:false};
 return {bar:Math.floor(value/count)+1,offset:value%count,unwrapped:total,ended:!!s.playback&&!loop(s)&&value>=s.playback.endBeat};
}
function targetTime(s,unwrappedBeat){return anchor(s)+(unwrappedBeat-base(s))/s.rate*1000}
function firstStep(s,time,subdivision){
 const value=unwrapped(s,time)*subdivision;
 // A future start is an explicit promise: include its first grid boundary.
 return s.playback&&Number.isFinite(s.playback.startTime)&&time<=s.playback.startTime?Math.ceil(value-1e-8):Math.floor(value)+1;
}
function allows(s,value){return !s.playback||!!loop(s)||value<s.playback.endBeat}
window.ChordCueTimeline={beats,offset,absolute,base,anchor,unwrapped,musical,position,targetTime,firstStep,allows};
})();
/* Local sound generation. AudioContext schedules notes; UI frames never trigger notes. */
(()=>{
'use strict';
const el=id=>document.getElementById(id),sync=window.ChordCueSync,timeline=window.ChordCueTimeline;
let context=null,master=null,noise=null,enabled=false,patterns={},pattern=[],patternKey='',scheduled=new Map(),message='',nextStepIndex=null;
let starting=false,activation=0,epoch=0,patternHost='';
const host=()=>window.ChordCueHostLabel|| (sync.snapshot().sample?.playback?'主机':'Logic');
function audioState(){el('headerAudio').textContent=enabled?'关闭节拍器':starting?'正在开启…':'开启节拍器';el('headerAudio').disabled=starting;if(window.ChordCueBridge)window.ChordCueBridge.audioState(enabled);else window.webkit?.messageHandlers?.audioState?.postMessage(enabled)}
const diagnostics={scheduled:0,cancelled:0,late:0,stale:0};
const read=(key,fallback)=>{try{return localStorage.getItem('chordcue-metro-'+key)||fallback}catch{return fallback}};
const save=(key,value)=>{try{localStorage.setItem('chordcue-metro-'+key,String(value))}catch{}};
try{const saved=read('patterns-v2','');patterns=JSON.parse(saved||read('patterns','{}'));if(!patterns||typeof patterns!=='object'||Array.isArray(patterns))patterns={};if(!saved){for(const [key,steps] of Object.entries(patterns)){if(!Array.isArray(steps)){delete patterns[key];continue}patterns[key]=steps.map(p=>({...p,level:p?.level===2?3:p?.level===1?2:0}))}save('patterns-v2',JSON.stringify(patterns))}}catch{patterns={}}
el('metroMode').value=read('mode','classic');if(!el('metroMode').value)el('metroMode').value='classic';
el('metroSubdivision').value=read('subdivision','eighth');if(!el('metroSubdivision').value)el('metroSubdivision').value='eighth';
el('metroVolume').value=String(Math.max(0,Math.min(100,Number(read('volume','35'))||0)));
el('metroOffset').value=String(Math.max(-150,Math.min(150,Number(read('advance','0'))||0)));
const isDrums=()=>el('metroMode').value==='drums',divisions=()=>el('metroSubdivision').value==='eighth'?2:1;
function meter(){const value=sync.snapshot().sample?.meter||'4/4',parts=value.split('/').map(Number);return {value,beats:parts[0],denominator:parts[1],supported:Number.isInteger(parts[0])&&parts[0]>0&&parts[0]<=12&&parts[1]===4}}
function defaults(beats){return Array.from({length:beats*divisions()},(_,i)=>({level:i===0?3:i%divisions()?1:2,voice:i%divisions()? 'hat':i/divisions()%2===1?'snare':i===0?'kick':'hat'}))}
function renderPattern(){
 const m=meter();if(!m.supported){el('patternDescription').textContent='当前 '+m.value+'；此版本声音支持 1–12 拍、四分音符为拍单位。';patternKey='';pattern=[];el('beatPattern').replaceChildren();el('beatLights').replaceChildren();return}
 const key=el('metroMode').value+'|'+m.value+'|'+el('metroSubdivision').value;
 if(key===patternKey&&patternHost===host())return;if(key!==patternKey)cancel();patternKey=key;patternHost=host();
 const stored=patterns[key];pattern=Array.isArray(stored)&&stored.length===m.beats*divisions()?stored.map((p,i)=>({level:[0,1,2,3].includes(p?.level)?p.level:defaults(m.beats)[i].level,voice:['kick','snare','hat'].includes(p?.voice)?p.voice:defaults(m.beats)[i].voice})):defaults(m.beats);
 el('patternDescription').textContent='拍号 '+m.value+' 跟随 '+host()+' · '+pattern.length+' 个'+(divisions()===2?'八分音符':'拍点')+'。0 格不响，1 格轻拍，2 格正常，3 格重拍。点击设置；再点最高亮格降低一档。'+(isDrums()?'每柱可独立选择鼓音色。':'');
 const root=el('beatPattern');root.replaceChildren();el('beatLights').replaceChildren();
 for(let beat=0;beat<m.beats;beat++){
  const light=document.createElement('span');light.className='beat-light';light.textContent=String(beat+1);el('beatLights').append(light);
  for(let part=0;part<divisions();part++){
   const index=beat*divisions()+part,group=document.createElement('div'),title=document.createElement('strong'),bars=document.createElement('div'),status=document.createElement('span'),names=['不响','轻拍','正常','重拍'],label='第 '+(beat+1)+' 拍'+(part?'后半拍':'拍点');
   group.className='strength-column';group.classList.toggle('offbeat',part>0);title.textContent=part?'&':String(beat+1);title.title=label;group.append(title);bars.className='strength-bars';status.className='strength-label';status.setAttribute('aria-live','polite');
   const refresh=()=>{for(const button of bars.children)button.setAttribute('aria-pressed',String(Number(button.dataset.level)<=pattern[index].level));status.textContent=names[pattern[index].level];group.dataset.strength=String(pattern[index].level)};
   for(const level of [3,2,1]){const button=document.createElement('button');button.type='button';button.className='strength-cell';button.dataset.level=String(level);button.setAttribute('aria-label',label+'：'+names[level]);button.title=names[level];button.addEventListener('click',()=>{pattern[index].level=pattern[index].level===level?level-1:level;refresh();storePattern()});bars.append(button)}group.append(bars,status);refresh();
   if(isDrums()){const voice=document.createElement('select');voice.setAttribute('aria-label',label+'鼓音色');for(const [value,text] of [['kick','底鼓'],['snare','军鼓'],['hat','踩镲']]){const option=document.createElement('option');option.value=value;option.textContent=text;voice.append(option)}voice.value=pattern[index].voice;voice.addEventListener('change',()=>{pattern[index].voice=voice.value;storePattern()});group.append(voice)}
   root.append(group);
  }
 }
}
function storePattern(){patterns[patternKey]=pattern;save('patterns-v2',JSON.stringify(patterns));cancel(false,true)}
for(const id of ['metroMode','metroSubdivision'])el(id).addEventListener('change',()=>{save(id==='metroMode'?'mode':'subdivision',el(id).value);patternKey='';renderPattern()});
el('patternReset').addEventListener('click',()=>{delete patterns[patternKey];save('patterns-v2',JSON.stringify(patterns));patternKey='';renderPattern()});
el('metroVolume').addEventListener('input',()=>{save('volume',el('metroVolume').value);if(master)master.gain.setTargetAtTime(Number(el('metroVolume').value)/100,context.currentTime,0.01)});
function updateAdvance(){save('advance',el('metroOffset').value);el('metroOffsetLabel').textContent=(Number(el('metroOffset').value)>0?'+':'')+el('metroOffset').value+' ms';cancel()}
el('metroOffset').addEventListener('input',updateAdvance);updateAdvance();
async function audio(){
 if(!context){const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)throw Error('此浏览器不支持音频，请换用 Safari 或 Chrome。');context=new Audio({latencyHint:'interactive'});master=context.createGain();master.gain.value=Number(el('metroVolume').value)/100;master.connect(context.destination);noise=context.createBuffer(1,Math.ceil(context.sampleRate*0.22),context.sampleRate);const data=noise.getChannelData(0);for(let i=0;i<data.length;i++)data[i]=Math.random()*2-1;context.onstatechange=()=>{if(context.state!=='running'&&enabled){enabled=false;cancel();el('audioEnable').textContent='恢复声音';message='音频被系统暂停，请点恢复声音。';audioState()}}}
 const target=context;let timeout;
 try{await Promise.race([target.resume(),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('音频启动超时，请再点开启声音；若仍失败，请检查系统输出设备。')),3000)})]);if(target.state!=='running')throw Error('声音尚未开启，请再点一次。')}
 catch(error){if(context===target){target.onstatechange=null;target.close().catch(()=>{});context=null;master=null;noise=null}throw error}finally{clearTimeout(timeout)}
}
async function startAudio(preview=false){
 if(starting)return;starting=true;const attempt=++activation;message='正在启动音频…';el('audioEnable').disabled=true;el('audioPreview').disabled=true;el('audioEnable').textContent='正在开启…';el('headerAudio').disabled=true;el('headerAudio').textContent='正在开启…';
 try{await audio();if(attempt!==activation)return;message='';if(preview)sound({level:3,voice:isDrums()?'kick':'hat'},context.currentTime+0.015,'preview-'+performance.now(),performance.now()+15);else{enabled=true;sync.calibrate();cancel()}}
 catch(error){if(attempt===activation)message=error.message}
 finally{if(attempt===activation){starting=false;el('audioEnable').disabled=false;el('audioPreview').disabled=false;el('audioEnable').textContent=enabled?'关闭声音':'开启声音';audioState()}}
}
el('audioEnable').addEventListener('click',()=>{if(enabled){disable();return}startAudio()});
el('audioPreview').addEventListener('click',()=>startAudio(true));
el('headerAudio').addEventListener('click',()=>{if(enabled)disable();else startAudio()});
function cancel(includePreview=false,futureOnly=false){
 for(const [key,value] of scheduled){if(!includePreview&&key.startsWith('preview-'))continue;if(futureOnly&&context&&value.at<=context.currentTime+0.04)continue;if(context){try{value.gain.gain.cancelScheduledValues(context.currentTime);value.gain.gain.setValueAtTime(0,context.currentTime)}catch{}for(const source of value.sources){try{source.stop(context.currentTime)}catch{}}}scheduled.delete(key);diagnostics.cancelled++}
 nextStepIndex=null;if(!futureOnly)epoch++;
}
function disable(){activation++;starting=false;enabled=false;message='';cancel(true);el('audioEnable').disabled=false;el('audioPreview').disabled=false;el('audioEnable').textContent='开启声音';if(context)context.suspend().catch(()=>{});audioState()}
function sound(step,at,key,performanceTime){
 const gain=context.createGain(),sources=[];gain.connect(master);const accent=[0,0.45,0.8,1][step.level]??0;
 function oscillator(type,frequency,endFrequency,duration,level){const source=context.createOscillator();source.type=type;source.frequency.setValueAtTime(frequency,at);if(endFrequency)source.frequency.exponentialRampToValueAtTime(endFrequency,at+duration*0.8);source.connect(gain);source.start(at);source.stop(at+duration);sources.push(source);gain.gain.setValueAtTime(level*accent,at);gain.gain.exponentialRampToValueAtTime(0.0001,at+duration)}
 if(!isDrums())oscillator('sine',step.level===3?1800:1100,0,0.05,0.85);
 else if(step.voice==='kick')oscillator('sine',145,45,0.16,0.95);
 else{const source=context.createBufferSource();source.buffer=noise;const filter=context.createBiquadFilter();filter.type='highpass';filter.frequency.value=step.voice==='hat'?6500:1400;source.connect(filter);filter.connect(gain);const duration=step.voice==='hat'?0.045:0.12;gain.gain.setValueAtTime((step.voice==='hat'?0.55:0.65)*accent,at);gain.gain.exponentialRampToValueAtTime(0.0001,at+duration);source.start(at);source.stop(at+duration);sources.push(source)}
 const value={gain,sources,at,performanceTime,finished:false};scheduled.set(key,value);diagnostics.scheduled++;sources[0].onended=()=>{gain.disconnect();value.finished=true};
}
function outputTime(targetPerformance){
 if(typeof context.getOutputTimestamp==='function'){const timestamp=context.getOutputTimestamp();if(timestamp.contextTime>0&&timestamp.performanceTime>0&&performance.now()-timestamp.performanceTime<300){return timestamp.contextTime+(targetPerformance-timestamp.performanceTime)/1000}}
 const latency=Math.max(0,Number(context.baseLatency)||0)+Math.max(0,Number(context.outputLatency)||0);
 return context.currentTime+(targetPerformance-performance.now())/1000-Math.min(0.15,latency);
}
function transportChanged(old,next){
 const oldLoop=old?.playback?.loop,nextLoop=next.playback?.loop;
 if(!old||!next.valid||!next.playing||old.revision!==next.revision||old.discontinuity!==next.discontinuity||old.meter!==next.meter||!!old.playback!==!!next.playback||oldLoop?.startBeat!==nextLoop?.startBeat||oldLoop?.endBeat!==nextLoop?.endBeat||old.playback?.startTime!==next.playback?.startTime){cancel();return}
 // Compare unwrapped positions: a natural loop boundary never changes epoch.
 if(context&&old.rate>0&&next.rate>0){const predicted=timeline.base(next)-timeline.unwrapped(old,next.sampleTime);if(Math.abs(predicted/next.rate)>0.045||Math.abs(next.rate-old.rate)>old.rate*0.03)cancel(false,true)}
}
function health(){
 const state=sync.snapshot(),s=state.sample,now=performance.now(),age=s&&state.clockOffset!==null?now+state.clockOffset-s.sampleTime:Infinity;
 if(!state.connected)return {state,reason:'连接中断，已停声'};
 if(!s?.valid||!s.precise)return {state,reason:'等待 '+host()+' 精细播放头位置，已停声'};
 if(state.clockOffset===null)return {state,reason:'正在校准时钟，暂不出声'};
 if(age< -30||age>350||now-state.lastReceived>350||state.chart?.revision!==s.revision)return {state,reason:'同步数据过期，已停声'};
 if(!meter().supported)return {state,reason:'当前拍号 '+s.meter+' 尚不支持音频排程'};
 if(!s.playing||!(s.rate>0))return {state,reason:'等待 '+host()+' 播放；开始后从下一个拍点加入'};
 if(document.hidden&&!window.ChordCueNative)return {state,reason:'页面在后台，已停声；返回后重新开启'};
 return {state,age:Math.max(0,age),reason:''};
}
function schedule(){
 for(const [key,value] of scheduled)if(value.finished&&performance.now()>value.performanceTime+400)scheduled.delete(key);
 renderPattern();if(!enabled||context?.state!=='running')return;
 const h=health();if(h.reason){if(scheduled.size)diagnostics.stale++;cancel();return}
 const {sample:s,clockOffset}=h.state,m=meter(),rate=s.rate,sub=divisions(),now=performance.now();
 const serverNow=now+clockOffset,total=timeline.unwrapped(s,serverNow);
 // Unwrapped step identities survive natural wraps, including multiple loops
 // inside one lookahead window. Manual transitions invalidate their epoch.
 if(nextStepIndex===null)nextStepIndex=timeline.firstStep(s,serverNow,sub);
 const advance=Number(el('metroOffset').value),deviceLatency=Math.min(0.15,Math.max(0,context.currentTime-outputTime(now)));
 const horizon=0.1+deviceLatency+Math.max(0,advance/1000);
 const limit=Math.floor((total+horizon*rate+1)*sub);
 while(nextStepIndex<=limit){
  const i=nextStepIndex;
  const unwrapped=i/sub;if(!timeline.allows(s,unwrapped))break;
  const musical=timeline.musical(s,unwrapped),step=Math.round((musical%m.beats)*sub),key=epoch+':'+i;
  const targetPerformance=timeline.targetTime(s,unwrapped)-clockOffset-advance;
  const at=outputTime(targetPerformance);if(at>context.currentTime+0.14)break;
  nextStepIndex++;
  if(scheduled.has(key)||!pattern[step]?.level)continue;
  if(at<context.currentTime-0.02){diagnostics.late++;continue}
  sound(pattern[step],Math.max(context.currentTime+0.003,at),key,targetPerformance);
 }
}
function visual(){
 if(sync.snapshot().page!=='metronome')return;renderPattern();const h=health(),s=h.state.sample;
 el('metroTempo').textContent=s?.bpm>0?Number(s.bpm.toFixed(2))+' BPM':'— BPM';
 const live=enabled&&context?.state==='running'&&!h.reason;
 el('metroState').textContent=message||(!enabled?'声音未开启；各人的拍点与音量独立设置。':h.reason||'同步播放 · '+s.meter+' · 网络／本机桥接往返 '+h.state.rtt.toFixed(0)+' ms');
 const position=live?timeline.position(s,performance.now()+h.state.clockOffset):null;
 const phase=position&&!position.ended?position.offset:-1;
 [...el('beatLights').children].forEach((light,index)=>light.classList.toggle('lit',phase>=0&&Math.floor(phase)%meter().beats===index));
 if(context){const latency=Math.max(0,context.currentTime-outputTime(performance.now()));el('audioLatency').textContent='设备输出延迟估计 '+(latency*1000).toFixed(0)+' ms · '+(typeof context.getOutputTimestamp==='function'?'使用输出时钟映射':'使用浏览器延迟估计')+' · 仍需耳机实测校准'}
}
window.ChordCueMetronome={cancel,disable,setEnabled:value=>{if(value){if(!enabled&&!starting)startAudio()}else disable()},transportChanged,visual,diagnostics:()=>({...diagnostics}),shutdown:()=>{disable();clearInterval(timer);if(context)context.close().catch(()=>{})}};
window.addEventListener('pagehide',disable);
const timer=setInterval(schedule,25);renderPattern();
})();
