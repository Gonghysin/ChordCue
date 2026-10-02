const test=require('node:test');
const assert=require('node:assert/strict');
const XML=require('../../../Resources/score/MusicXMLImport.js');
const GP=require('../../../Resources/score/GuitarProImport.js');
const View=require('../../../Resources/score/ScoreView.js');
const alpha=require('../../../Resources/vendor/alphatab/dist/alphaTab.js');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const importXML=body=>XML.importScore(new TextEncoder().encode('<score-partwise><part id="original"><measure number="1">'+body+'<note><rest/><duration>4</duration></note></measure></part></score-partwise>'));

test('MusicXML rejects staff-line allocation requests beyond the tuning contract',()=>{
 for(const lines of [0,25,1000000000])assert.throws(()=>importXML('<attributes><staff-details><staff-lines>'+lines+'</staff-lines><staff-tuning line="1"><tuning-step>E</tuning-step><tuning-octave>4</tuning-octave></staff-tuning></staff-details></attributes>'),/调弦|谱表|staff|线|限制/);
});

test('MusicXML validates repeat-ending ranges before expanding them',()=>{
 for(const number of ['1-1000000000','0-2','3-1','32-33'])assert.throws(()=>importXML('<barline><ending number="'+number+'" type="start"/></barline>'),/跳房子|编号|范围/);
 assert.deepEqual(importXML('<barline><ending number="1-3" type="start"/></barline>').measures[0].endingNumbers,[1,2,3]);
});

test('MusicXML rejects arbitrary harmony accidental string expansion',()=>{
 for(const element of [
  '<root><root-step>C</root-step><root-alter>1000000000</root-alter></root><kind>major</kind>',
  '<root><root-step>C</root-step></root><kind>major</kind><bass><bass-step>G</bass-step><bass-alter>-1000000000</bass-alter></bass>',
  '<root><root-step>C</root-step></root><kind>major</kind><degree><degree-value>9</degree-value><degree-alter>1000000000</degree-alter><degree-type>add</degree-type></degree>'
 ])assert.throws(()=>importXML('<harmony>'+element+'</harmony>'),/alter|升降|变音|支持|范围/);
});

test('a mid-measure source key change carries into the following rendered bar',()=>{
 const score=XML.importScore(new TextEncoder().encode('<score-partwise><part id="original"><measure number="1"><attributes><key><fifths>0</fifths><mode>major</mode></key></attributes><note><rest/><duration>2</duration></note><attributes><key><fifths>2</fifths><mode>major</mode></key></attributes><note><rest/><duration>2</duration></note></measure><measure number="2"><note><rest/><duration>4</duration></note></measure></part></score-partwise>'));
 assert.deepEqual(score.keyChanges.map(change=>change.fifths),[0,2]);
 assert.equal(View.toAlpha(score,'p1','staff').model.tracks[0].staves[0].bars[1].keySignature,2);
 assert.deepEqual(View.toAlpha(score,'p1','staff',2).model.tracks[0].staves[0].bars.map(bar=>bar.keySignature),[2,4]);
});

test('staff transposition moves major and minor key changes together with pitches without rewriting source',()=>{
 for(const [mode,fifths,expected] of [['major',[0,2],[2,4]],['minor',[-1,-3],[1,-1]]]){
  const body='<score-partwise><part id="original">'+fifths.map((key,i)=>'<measure number="'+(i+1)+'"><attributes><key><fifths>'+key+'</fifths><mode>'+mode+'</mode></key></attributes><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure>').join('')+'</part></score-partwise>';
  const score=XML.importScore(new TextEncoder().encode(body)),before=JSON.stringify(score);
  const bars=View.toAlpha(score,'p1','staff',2).model.tracks[0].staves[0].bars;
  assert.deepEqual(bars.map(bar=>bar.keySignature),expected);
  assert.deepEqual(bars.map(bar=>bar.keySignatureType),[mode==='minor'?1:0,mode==='minor'?1:0]);
  assert.deepEqual(bars.map(bar=>bar.voices[0].beats[0].notes[0].realValue),[66,66]);
  assert.equal(JSON.stringify(score),before);
 }
});

test('zero and octave transposition preserve source enharmonic signatures while shifted keys stay bounded',()=>{
 for(const fifths of [-7,-6,6,7]){
  const score=importXML('<attributes><key><fifths>'+fifths+'</fifths><mode>major</mode></key></attributes>');
  for(const shift of [0,12,-12])assert.equal(View.toAlpha(score,'p1','staff',shift).model.tracks[0].staves[0].bars[0].keySignature,fifths);
  for(const shift of [-14,-1,1,14]){
   const signature=View.toAlpha(score,'p1','staff',shift).model.tracks[0].staves[0].bars[0].keySignature;
   assert.ok(signature>=-7&&signature<=7);
   assert.equal(((signature*7)%12+12)%12,((fifths*7+shift)%12+12)%12);
  }
 }
 const source=importXML('<attributes><key><fifths>-1</fifths><mode>major</mode></key></attributes>');
 assert.equal(View.toAlpha(source,'p1','staff',1).model.tracks[0].staves[0].bars[0].keySignature,-6);
});

function notationHarness(){
 let newApiMode='success';
 const element=tag=>({tag,hidden:false,children:[],style:{},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=[...children];},scrollIntoView(){}});
 class Events{
  constructor(){this.listeners=new Set();}
  on(listener){this.listeners.add(listener);}
  off(listener){this.listeners.delete(listener);}
  trigger(value){for(const listener of [...this.listeners])listener(value);}
 }
 class FakeApi{
  constructor(canvas,settings){this.canvas=canvas;this.settings=settings;this.beatMouseDown=new Events();this.renderFinished=new Events();this.error=new Events();this.mode=newApiMode;this.renderer={boundsLookup:{findBeat:()=>({visualBounds:{x:1,y:2,h:30}})}};}
  updateSettings(){}
  renderScore(model){this.model=model;if(!this.canvas.children.length)this.canvas.append(element('svg'));if(this.mode==='error')this.error.trigger(Error('original render error'));else if(this.mode==='success')this.renderFinished.trigger({});}
  destroy(){this.destroyed=true;this.canvas.replaceChildren();}
 }
 const context={ChordCueScoreIO:require('../../../Resources/score/ScoreIO.js'),alphaTab:{...alpha,AlphaTabApi:FakeApi},document:{createElement:element}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../../Resources/score/ScoreView.js'),'utf8'),context);
 const seeks=[],view=new context.ChordCueScoreView.ScoreView(element('main'),{onSeek:(...seek)=>seeks.push(seek)});
 const score=XML.importScore(new TextEncoder().encode('<score-partwise><part id="original"><measure number="1"><note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note></measure></part></score-partwise>'));
 const click=(api=view.api)=>api.beatMouseDown.trigger({beat:api.model.tracks[0].staves[0].bars[0].voices[0].beats[0]});
 return {view,score,seeks,click,setNewApiMode:mode=>{newApiMode=mode;}};
}

test('failed or pending notation cannot expose the previous score as an active seek surface',async()=>{
 const {view,score,seeks,click}=notationHarness();
 assert.equal(await view.show(score,'p1'),true);click();assert.equal(seeks.length,1);
 view.api.mode='pending';const pending=view.show(score,'p1');assert.equal(view.rendered,false);click();assert.equal(seeks.length,1);
 view.api.renderFinished.trigger({});assert.equal(await pending,true);click();assert.equal(seeks.length,2);
 view.api.mode='error';await assert.rejects(view.show(score,'p1'),/original render error/);
 assert.equal(view.canvas.hidden,true);assert.equal(view.rendered,false);click();assert.equal(seeks.length,2);
 view.api.mode='success';assert.equal(await view.show(score,'p1'),true);assert.equal(view.canvas.hidden,false);
 assert.throws(()=>view.show(score,'p1','tab'),/源谱缺少/);assert.equal(view.rendered,false);assert.equal(view.canvas.hidden,true);click();assert.equal(seeks.length,2);
});

test('external notation cursor clears on lost transport and restores on the next rendered position',async()=>{
 const {view,score}=notationHarness();await view.show(score,'p1');
 const sample={sourceMeasureId:'m1',sourceOffsetQuarter:0,playing:false};
 view.setPosition(sample);assert.equal(view.cursor.hidden,false);
 view.setPosition(null);assert.equal(view.cursor.hidden,true);
 view.setPosition(sample);assert.equal(view.cursor.hidden,false);
 view.api.mode='pending';const pending=view.show(score,'p1');assert.equal(view.cursor.hidden,true);
 view.setPosition(sample);assert.equal(view.cursor.hidden,true);
 view.api.renderFinished.trigger({});await pending;view.setPosition(sample);assert.equal(view.cursor.hidden,false);
 view.setPosition({sourceMeasureId:'missing',sourceOffsetQuarter:0});assert.equal(view.cursor.hidden,true);
});

test('a second pending render cancels the first and accepts completion only from its replacement API',async()=>{
 const {view,score,seeks,click,setNewApiMode}=notationHarness();await view.show(score,'p1');
 const oldApi=view.api;oldApi.mode='pending';const first=view.show(score,'p1');assert.equal(view.api,oldApi);
 const queuedDone=[...oldApi.renderFinished.listeners][0],queuedError=[...oldApi.error.listeners][0];
 const nextScore=structuredClone(score);nextScore.measures[0].id='next';nextScore.parts[0].staves[0].events[0].measureId='next';
 setNewApiMode('pending');const second=view.show(nextScore,'p1'),newApi=view.api;
 assert.notEqual(newApi,oldApi);assert.equal(oldApi.destroyed,true);assert.equal(await first,false);
 oldApi.renderFinished.trigger({});oldApi.error.trigger(Error('late previous error'));queuedDone();queuedError(Error('queued previous error'));click(oldApi);
 assert.equal(view.rendered,false);assert.equal(seeks.length,0);
 newApi.renderFinished.trigger({});assert.equal(await second,true);assert.equal(view.rendered,true);
 oldApi.renderFinished.trigger({});click(oldApi);assert.equal(seeks.length,0);click(newApi);assert.deepEqual(seeks,[['next',0]]);
 newApi.mode='success';await view.show(nextScore,'p1');assert.equal(view.api,newApi);
});

test('a conversion failure cancels prior pending rendering and late events cannot revive it',async()=>{
 const {view,score,seeks,click}=notationHarness();await view.show(score,'p1');
 const oldApi=view.api;oldApi.mode='pending';const pending=view.show(score,'p1');
 assert.throws(()=>view.show(score,'p1','tab'),/源谱缺少/);assert.equal(await pending,false);assert.equal(oldApi.destroyed,true);
 oldApi.renderFinished.trigger({});oldApi.error.trigger(Error('late previous error'));click(oldApi);
 assert.equal(view.rendered,false);assert.equal(view.canvas.hidden,true);assert.equal(view.api,null);assert.equal(seeks.length,0);
 await view.show(score,'p1');assert.equal(view.rendered,true);assert.equal(view.canvas.hidden,false);
 oldApi.renderFinished.trigger({});click(oldApi);assert.equal(seeks.length,0);
});

test('destroy resolves pending notation as cancelled and ignores late completion and seeking',async()=>{
 const {view,score,seeks,click}=notationHarness();await view.show(score,'p1');
 const oldApi=view.api;oldApi.mode='pending';const pending=view.show(score,'p1');view.destroy();
 assert.equal(await pending,false);assert.equal(oldApi.destroyed,true);assert.equal(view.api,null);
 oldApi.renderFinished.trigger({});oldApi.error.trigger(Error('late previous error'));click(oldApi);
 assert.equal(view.rendered,false);assert.equal(seeks.length,0);assert.equal(view.element.children.length,0);
 assert.throws(()=>view.show(score,'p1'),/已关闭/);
});

test('note boxes follow real systems once and preserve highlight when following is disabled',async()=>{
 const {view,score}=notationHarness();await view.show(score,'p1');
 const root={clientHeight:500,scrollTop:0,scrolls:[],scrollTo(value){this.scrolls.push(value);this.scrollTop=value.top;},getBoundingClientRect(){return {top:0}}};
 view.scrollRoot=()=>root;view.topInset=()=>40;
 let system={index:0,realBounds:{y:100}},noteBox={x:12,y:110,w:8,h:10};
 view.api.renderer.boundsLookup.findBeats=()=>[{notes:[{noteHeadBounds:noteBox}],barBounds:{masterBarBounds:{staffSystemBounds:system}}}];
 view.element.getBoundingClientRect=()=>({left:0,top:20-root.scrollTop});view.canvas.getBoundingClientRect=()=>({left:8,top:60-root.scrollTop});
 const sample={sourceMeasureId:'m1',sourceOffsetQuarter:0,playing:true,valid:true};
 view.setPosition(sample);assert.equal(root.scrolls.length,1);assert.equal(root.scrollTop,120);
 assert.match(view.cursor.style.cssText,/width:14px;height:16px/);
 view.setPosition({...sample,sourceOffsetQuarter:1});assert.equal(root.scrolls.length,1);
 system={index:1,realBounds:{y:300}};view.setPosition({...sample,sourceOffsetQuarter:2});assert.equal(root.scrolls.length,2);assert.equal(root.scrollTop,320);
 view.setFollow(false);system={index:2,realBounds:{y:500}};view.setPosition({...sample,sourceOffsetQuarter:3});assert.equal(root.scrolls.length,2);assert.equal(view.cursor.hidden,false);
 view.setPosition({...sample,valid:false});assert.equal(view.cursor.hidden,true);
 view.setActive(false);view.setPosition(sample);assert.equal(view.cursor.hidden,true);
 view.setActive(true);assert.equal(view.cursor.hidden,false);
});

test('Guitar Pro bass source clef survives the IR adapter and staff rendering',()=>{
 const bytes=new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original.gp')));
 const model=alpha.importer.ScoreLoader.loadScoreFromBytes(bytes);assert.equal(model.tracks[1].staves[0].bars[0].clef,alpha.model.Clef.F4);
 const score=GP.importScore(bytes);assert.equal(score.parts[1].staves[0].clef,'F4');
 assert.equal(View.toAlpha(score,'p2','staff').model.tracks[0].staves[0].bars[0].clef,alpha.model.Clef.F4);
});

test('Guitar Pro unresolved DS navigation has a measure-scoped warning for the IR contract',()=>{
 const bytes=new Uint8Array(fs.readFileSync(path.join(__dirname,'../fixtures/issue1-original-legacy.gp5'))),model=alpha.importer.ScoreLoader.loadScoreFromBytes(bytes);
 model.masterBars[0].addDirection(alpha.model.Direction.JumpDalSegno);
 const score=GP.fromAlpha(model);assert.ok(score.measures[0].navigation.some(nav=>nav.kind==='ds'));
 assert.ok(score.warnings.some(warning=>warning.measureId==='m1'&&warning.code.startsWith('navigation.')));
});
