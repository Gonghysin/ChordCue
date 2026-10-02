// Original one-bar E4, F#4, D4 practice phrase, not a transcribed song.
// Encodings follow the bundled alphaTab Gp3To5Importer and GpxFileSystem.
// These fixtures exercise basic legacy/container paths, not all vendor effects.
const fs=require('node:fs');
const path=require('node:path');
const alpha=require('../Resources/vendor/alphatab/dist/alphaTab.js');
const IO=require('../Resources/score/ScoreIO.js');
const GP=require('../Resources/score/GuitarProImport.js');
const output=path.join(__dirname,'../windows/tests/fixtures');
class Writer{
 constructor(){this.chunks=[];}
 bytes(value){this.chunks.push(Buffer.from(value));}
 zeros(count){this.bytes(Buffer.alloc(count));}
 byte(value){this.bytes([value&255]);}
 int(value){const data=Buffer.alloc(4);data.writeInt32LE(value);this.bytes(data);}
 short(value){const data=Buffer.alloc(2);data.writeInt16LE(value);this.bytes(data);}
 text(value){const data=Buffer.from(value,'ascii');this.int(data.length+1);this.byte(data.length);this.bytes(data);}
 fixed(value,size){const data=Buffer.from(value,'ascii');if(data.length>size)throw Error('fixture field too long');this.byte(data.length);this.bytes(data);this.zeros(size-data.length);}
 finish(){return Buffer.concat(this.chunks);}
}
function legacy(version){
 const w=new Writer();w.fixed('FICHIER GUITAR PRO v'+version+'.00',30);
 const fields=['ChordCue original legacy practice','','ChordCue','',''];
 if(version===5)fields.push('');fields.push('Original ChordCue test fixture','ChordCue','');
 for(const value of fields)w.text(value);w.int(0); // No notices.
 if(version<5)w.byte(0); // Straight rhythm.
 if(version>=4){w.int(0);for(let i=0;i<5;i++){w.int(1);w.int(0);}} // No lyrics.
 if(version===5){w.zeros(28);w.short(0);for(let i=0;i<10;i++)w.text('');w.text('');}
 w.int(108);w.int(0);if(version>=4)w.byte(0);
 for(let i=0;i<64;i++){w.int(25);w.byte(100);w.byte(64);w.zeros(6);}
 if(version===5){for(let i=0;i<19;i++)w.short(-1);w.zeros(4);}
 w.int(1);w.int(1); // One source bar, one source track.
 w.byte(0x4f);w.byte(4);w.byte(4);w.byte(version===5?2:1);w.byte(0);w.byte(0);
 if(version===5){w.zeros(4);w.byte(0);w.byte(0);w.byte(0);}
 w.byte(version===5?8:0);w.fixed('Original guitar',40);w.int(6);
 for(const pitch of [64,59,55,50,45,40,0])w.int(pitch);
 w.int(1);w.int(1);w.int(2);w.int(24);w.int(0);w.bytes([40,80,150,0]);
 if(version===5){w.bytes([3,0,0,0,0]);w.int(0);w.int(0);w.int(0);w.zeros(10);w.zeros(2);w.zeros(16);w.byte(0);}
 w.int(3);
 for(const [index,string,fret,duration] of [[0,1,0,0],[1,1,2,0],[2,2,3,-1]]){
  w.byte(index===0?2:0);w.byte(duration);
  if(index===0){
   if(version===5){w.zeros(17);w.fixed('Cmaj7/E',21);w.zeros(4);w.int(0);for(let i=0;i<7;i++)w.int(-1);w.byte(0);w.zeros(5);w.zeros(26);}
   else{w.byte(0);w.text('Cmaj7/E');w.int(0);}
  }
  w.byte(1<<(7-string));w.byte(32);w.byte(1);w.byte(fret);if(version===5){w.byte(0);w.short(0);}
 }
 if(version===5)w.int(0); // Empty second voice.
 return new Uint8Array(w.finish());
}
function bcfs(xml){
 const sectors=Math.ceil(xml.length/4096),data=Buffer.alloc((sectors+2)*4096);
 data.writeInt32LE(2,4096);data.write('score.gpif',4100,'ascii');data.writeInt32LE(xml.length,4096+140);
 for(let i=0;i<sectors;i++)data.writeInt32LE(i+2,4096+148+i*4);
 Buffer.from(xml).copy(data,8192);return new Uint8Array(Buffer.concat([Buffer.from('BCFS'),data]));
}
function bcfz(source){
 const bytes=[],bits=[];
 const bit=value=>{bits.push(value);if(bits.length===8){bytes.push(bits.reduce((a,b)=>(a<<1)|b,0));bits.length=0;}};
 for(let at=0;at<source.length;at+=3){const count=Math.min(3,source.length-at);bit(0);bit(count&1);bit(count>>1);for(let i=0;i<count;i++)for(let shift=7;shift>=0;shift--)bit(source[at+i]>>shift&1);}
 while(bits.length)bit(0);
 const length=Buffer.alloc(4);length.writeInt32LE(source.length);return new Uint8Array(Buffer.concat([Buffer.from('BCFZ'),length,Buffer.from(bytes)]));
}
function write(name,bytes){
 const score=GP.importScore(bytes,name); // Validate before persisting any fixture.
 if(score.parts[0].staves[0].events.length!==3)throw Error('Fixture note shape changed');
 fs.writeFileSync(path.join(output,name),bytes);return score;
}
function generate(){
 let gp5;
 for(const version of [3,4,5]){const bytes=legacy(version);write('issue1-original-legacy.gp'+version,bytes);if(version===5)gp5=bytes;}
 const model=alpha.importer.ScoreLoader.loadScoreFromBytes(gp5);
 const exported=new alpha.exporter.Gp7Exporter().export(model),entry=IO.zipEntries(exported).get('Content/score.gpif');
 const xml=IO.decode(IO.unpack(entry)).replace(/<GPVersion>[^<]*<\/GPVersion>/,'<GPVersion>6.0</GPVersion>');
 const container=bcfs(new TextEncoder().encode(xml));
 write('issue1-original-legacy.gpx',container);write('issue1-original-legacy-compressed.gpx',bcfz(container));
}
if(require.main===module)generate();
module.exports={legacy,bcfs,bcfz,generate};
