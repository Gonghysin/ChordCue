(function (root, factory) {
    const api = factory(typeof module === 'object' && module.exports ? require('../vendor/fflate/umd/index.js') : root.fflate);
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.ChordCueScoreIO = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (fflate) {
    'use strict';
    const MAX_BYTES = 32 * 1024 * 1024, MAX_NODES = 2000000, MAX_DEPTH = 128;
    const decode = bytes => new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    function fraction(n, d = 1) {
        if (!Number.isSafeInteger(n) || !Number.isSafeInteger(d) || d <= 0) throw Error('无效谱面时值');
        let a = Math.abs(n), b = d;
        while (b) [a, b] = [b, a % b];
        const result = {numerator: n / (a || 1), denominator: d / (a || 1)};
        if (Math.abs(result.numerator) > 2147483647 || result.denominator > 1000000) throw Error('谱面时值精度超过支持范围');
        return result;
    }
    const add = (a, b) => fraction(a.numerator * b.denominator + b.numerator * a.denominator, a.denominator * b.denominator);
    const sub = (a, b) => add(a, fraction(-b.numerator, b.denominator));
    const value = a => a.numerator / a.denominator;
    function entity(text) {
        return text.replace(/&([^;]+);/g, (_, key) => {
            const named = {amp:'&', lt:'<', gt:'>', quot:'"', apos:"'"};
            if (key in named) return named[key];
            if (!/^#(?:x[0-9a-f]+|[0-9]+)$/i.test(key)) throw Error('不支持自定义 XML 实体');
            const n = key[1].toLowerCase() === 'x' ? parseInt(key.slice(2),16) : parseInt(key.slice(1),10);
            if (n === 0 || n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) throw Error('无效 XML 字符');
            return String.fromCodePoint(n);
        });
    }
    // No DTD resolution, entity declarations, network access, or HTML execution.
    // The same small parser runs in Node, WKWebView and QWebEngine.
    function parseXML(source) {
        if (typeof source !== 'string' || source.length > MAX_BYTES) throw Error('XML 文件过大');
        if (/<!ENTITY|<!DOCTYPE[^>]*\[/i.test(source)) throw Error('不允许 XML 实体声明或内部 DTD');
        source = source.replace(/^\uFEFF/, '').replace(/<!DOCTYPE\s+[^>]*>/gi, '');
        const document = {name:'#document', attrs:{}, children:[], text:''};
        const stack = [document]; let cursor = 0, count = 0;
        const tokens = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[[\s\S]*?\]\]>|<(?:"[^"]*"|'[^']*'|[^'">])*>|[^<]+/g;
        for (const match of source.matchAll(tokens)) {
            if (match.index !== cursor) throw Error('XML 格式错误');
            const token = match[0]; cursor += token.length;
            const parent = stack[stack.length - 1];
            if (token.startsWith('<!--') || token.startsWith('<?')) continue;
            if (token.startsWith('<![CDATA[')) {parent.text += token.slice(9,-3); continue;}
            if (!token.startsWith('<')) {parent.text += entity(token); continue;}
            if (token.startsWith('</')) {
                const closing = token.slice(2,-1).trim().split(':').pop();
                if (stack.length === 1 || parent.name !== closing) throw Error('XML 标签不匹配');
                stack.pop(); continue;
            }
            const open = /^<([A-Za-z_][\w.:-]*)([\s\S]*?)\/?\s*>$/.exec(token);
            if (!open) throw Error('XML 标签错误');
            const attrs = {}; let rest = open[2].trim();
            while (rest) {
                const attr = /^([A-Za-z_][\w.:-]*)\s*=\s*(["'])([\s\S]*?)\2(?:\s+|$)/.exec(rest);
                if (!attr || Object.hasOwn(attrs,attr[1])) throw Error('XML 属性错误');
                attrs[attr[1]] = entity(attr[3]); rest = rest.slice(attr[0].length);
            }
            const node = {name:open[1].split(':').pop(), attrs, children:[], text:''};
            if (++count > MAX_NODES) throw Error('XML 元素过多');
            parent.children.push(node);
            if (!/\/\s*>$/.test(token)) {
                stack.push(node);
                if (stack.length > MAX_DEPTH) throw Error('XML 嵌套过深');
            }
        }
        if (cursor !== source.length || stack.length !== 1 || document.children.length !== 1 || document.text.trim()) throw Error('XML 文档不完整');
        return document.children[0];
    }
    const children = (node, name) => node ? node.children.filter(child => child.name === name) : [];
    const child = (node, name) => children(node,name)[0] || null;
    const text = node => node ? node.text + node.children.map(text).join('') : '';
    const get = (node, name, fallback = '') => text(child(node,name)) || fallback;
    function zipEntries(bytes) {
        if (!(bytes instanceof Uint8Array) || bytes.length > MAX_BYTES) throw Error('压缩谱面过大');
        const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
        let end = -1;
        for (let i = bytes.length - 22; i >= Math.max(0,bytes.length - 65557); --i) {
            if (view.getUint32(i,true) === 0x06054b50 && i + 22 + view.getUint16(i+20,true) === bytes.length) {end=i;break;}
        }
        if (end < 0) throw Error('不支持或损坏的 ZIP 谱面');
        const count = view.getUint16(end+10,true), size = view.getUint32(end+12,true), start = view.getUint32(end+16,true);
        if (view.getUint16(end+4,true) || view.getUint16(end+6,true) || count !== view.getUint16(end+8,true) || count > 256 || start + size > end) throw Error('ZIP 结构超出支持范围');
        const entries = new Map(); let at = start, total = 0;
        for (let i=0;i<count;i++) {
            if (at+46 > start+size || view.getUint32(at,true)!==0x02014b50) throw Error('ZIP 目录损坏');
            const flags=view.getUint16(at+8,true), method=view.getUint16(at+10,true), compressed=view.getUint32(at+20,true), expanded=view.getUint32(at+24,true);
            const nameLength=view.getUint16(at+28,true), extra=view.getUint16(at+30,true), comment=view.getUint16(at+32,true), offset=view.getUint32(at+42,true);
            if (at+46+nameLength+extra+comment > start+size) throw Error('ZIP 条目损坏');
            const name=decode(bytes.subarray(at+46,at+46+nameLength));
            if (flags&1 || ![0,8].includes(method) || name.includes('\\') || name.startsWith('/') || name.includes(':') || name.split('/').includes('..') || entries.has(name)) throw Error('ZIP 含不安全或不支持的条目');
            total += expanded;
            if (expanded > MAX_BYTES || total > MAX_BYTES || offset+30 > start || view.getUint32(offset,true)!==0x04034b50) throw Error('ZIP 展开大小或偏移无效');
            const begin = offset+30+view.getUint16(offset+26,true)+view.getUint16(offset+28,true);
            if (begin+compressed > start) throw Error('ZIP 数据越界');
            entries.set(name,{name,method,expanded,data:bytes.subarray(begin,begin+compressed),crc:view.getUint32(at+16,true)});
            at += 46+nameLength+extra+comment;
        }
        if (at !== start+size) throw Error('ZIP 目录长度不一致');
        return entries;
    }
    function crc32(bytes) {
        let crc = 0xffffffff;
        for (const byte of bytes) {crc ^= byte; for (let k=0;k<8;k++) crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
        return (crc^0xffffffff)>>>0;
    }
    function unpack(entry) {
        let bytes;
        if (entry.method === 0) bytes=entry.data;
        else {
            const chunks=[]; let length=0;
            const inflater=new fflate.Inflate(chunk => {
                length += chunk.length;
                if (length > entry.expanded || length > MAX_BYTES) throw Error('ZIP 实际展开超过声明限制');
                chunks.push(chunk);
            });
            // Small input pushes bound streaming decompressor allocations.
            for(let at=0;at<entry.data.length;at+=1024) inflater.push(entry.data.subarray(at,at+1024),at+1024>=entry.data.length);
            bytes=new Uint8Array(length); let at=0;
            for(const chunk of chunks) {bytes.set(chunk,at);at+=chunk.length;}
        }
        if (bytes.length!==entry.expanded || crc32(bytes)!==entry.crc) throw Error('ZIP 长度或校验错误');
        return bytes;
    }
    function musicXMLBytes(bytes) {
        if(bytes.length>MAX_BYTES) throw Error('谱面文件过大');
        if(bytes[0]!==0x50 || bytes[1]!==0x4b) return bytes;
        const entries=zipEntries(bytes), container=entries.get('META-INF/container.xml');
        if(!container) throw Error('MXL 缺少 META-INF/container.xml');
        const tree=parseXML(decode(unpack(container)));
        const roots=children(child(tree,'rootfiles'),'rootfile');
        const item=roots.find(node=>node.attrs['media-type']==='application/vnd.recordare.musicxml+xml') || roots[0];
        const selected=item && entries.get(item.attrs['full-path']);
        if(!selected) throw Error('MXL 中找不到主乐谱文件');
        return unpack(selected);
    }
    function preflightGpx(bytes, details=null) {
        let payload;
        const header=String.fromCharCode(...bytes.subarray(0,4));
        if(header==='BCFZ') {
            if(bytes.length<8)throw Error('GPX 压缩头不完整');
            const expected=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength).getUint32(4,true);
            if(expected<4||expected>MAX_BYTES)throw Error('GPX 展开大小超过 32 MiB 限制');
            const output=new Uint8Array(expected);let bit=64,used=0;
            const read=(n,reversed=false)=>{if(bit+n>bytes.length*8)throw Error('GPX 压缩数据截断');let value=0;for(let i=0;i<n;i++){const b=(bytes[bit>>>3]>>(7-(bit&7)))&1;bit++;value|=b<<(reversed?i:n-i-1);}return value;};
            while(used<expected) {
                if(read(1)) {
                    const width=read(4),offset=read(width,true),size=read(width,true),count=Math.min(offset,size);
                    if(offset>used||count<=0||used+count>expected)throw Error('GPX 压缩回溯无效');
                    output.set(output.subarray(used-offset,used-offset+count),used);used+=count;
                } else {
                    const count=read(2,true);
                    if(used+count>expected)throw Error('GPX 实际展开超过声明限制');
                    for(let i=0;i<count;i++)output[used++]=read(8);
                }
            }
            if(String.fromCharCode(...output.subarray(0,4))!=='BCFS')throw Error('GPX 压缩结果缺少文件系统头');
            payload=output.subarray(4);
        } else if(header==='BCFS')payload=bytes.subarray(4);
        else throw Error('GPX 格式头错误');
        const view=new DataView(payload.buffer,payload.byteOffset,payload.byteLength),sectorSize=4096;
        let at=sectorSize,files=0,total=0;
        while(at+4<=payload.length) {
            if(view.getInt32(at,true)===2) {
                if(at+sectorSize>payload.length||++files>256)throw Error('GPX 文件目录过大或截断');
                const size=view.getInt32(at+140,true);
                if(size<0||size>MAX_BYTES||(total+=size)>MAX_BYTES)throw Error('GPX 文件大小超过限制');
                let pointer=at+148,last=at/sectorSize,count=0;const sectors=[];
                while(true) {
                    if(pointer+4>at+sectorSize)throw Error('GPX 扇区列表未终止');
                    const sector=view.getInt32(pointer,true);pointer+=4;
                    if(!sector)break;
                    if(sector<=last||(sector+1)*sectorSize>payload.length)throw Error('GPX 扇区回环或越界');
                    last=sector;count++;sectors.push(sector);
                }
                if(size>count*sectorSize)throw Error('GPX 文件长度与扇区不一致');
                let name='';for(let i=0;i<127&&payload[at+4+i];i++)name+=String.fromCharCode(payload[at+4+i]);
                if(name==='score.gpif'){
                    const xml=new Uint8Array(size);let written=0;
                    for(const sector of sectors){const length=Math.min(sectorSize,size-written);if(length<=0)break;xml.set(payload.subarray(sector*sectorSize,sector*sectorSize+length),written);written+=length;}
                    const tree=parseXML(decode(xml));
                    if(tree.name!=='GPIF')throw Error('GPX 主谱面 XML 类型错误');
                    if(details)details.tree=tree;
                }
                at=(last+1)*sectorSize;
            } else at+=sectorSize;
        }
        if(!files)throw Error('GPX 未找到谱面文件目录');
        return payload;
    }
    const techniqueDirections={slide:['shift','legato','inAbove','inBelow','outUp','outDown'],bend:['bend','prebend','release','prebendRelease'],whammy:['bend','prebend','release','prebendRelease'],vibrato:['slight','wide'],harmonic:['natural','artificial','tap','pinch','semi','feedback'],pick:['up','down'],brush:['up','down','arpeggioUp','arpeggioDown']};
    const techniqueKinds=['hammerOn','pullOff','slide','bend','vibrato','harmonic','palmMute','deadNote','letRing','staccato','accent','heavyAccent','pick','tremoloPicking','trill','tap','slap','pop','ghost','brush','whammy'];
    const eventTechniques=['vibrato','pick','tremoloPicking','tap','slap','pop','brush','whammy'];
    const technique=(kind,fields={})=>({kind,targetNoteId:null,value:null,direction:null,curve:[],...fields});
    const curveDirection=points=>points[0].semitones!==0?(points.at(-1).semitones<points[0].semitones?'prebendRelease':'prebend'):(points.some(p=>p.semitones>points.at(-1).semitones)?'release':'bend');
    const shapes={
        QuarterFraction:['numerator','denominator'],ScoreMeter:['numerator','denominator'],ScoreSource:['format','fileName','sha256'],
        ScoreMarker:['id','kind','label','offset'],ScoreNavigation:['kind','targetMarkerId','offset'],
        ScoreMeasure:['id','number','duration','meter','repeatStart','repeatEnd','endingNumbers','markers','navigation'],
        ScoreNote:['id','pitch','string','fret','tieStart','tieStop','unpitched','accidental','writtenPitch','techniques'],
        ScoreEvent:['id','measureId','offset','duration','voice','isRest','grace','notes','techniques'],
        ScoreStaff:['id','name','kind','clef','tuning','capo','events'],ScoreChord:['id','measureId','offset','text','staffId'],
        ScorePart:['id','name','instrument','staves','chords'],ScoreTempoChange:['measureId','offset','bpm'],ScoreKeyChange:['measureId','offset','fifths','mode'],
        ScoreWarning:['code','message','severity','measureId','partId'],ScoreIR:['formatVersion','id','title','source','measures','parts','tempoChanges','keyChanges','warnings'],
        ScoreTechnique:['kind','targetNoteId','value','direction','curve'],ScoreBendPoint:['position','semitones']
    };
    function exact(item,type,legacy=false){
        const fields=shapes[type].filter(k=>!(legacy&&((type==='ScoreNote'&&['writtenPitch','techniques'].includes(k))||(type==='ScoreEvent'&&k==='techniques'))));
        if(!item||Array.isArray(item)||typeof item!=='object'||Object.keys(item).length!==fields.length||fields.some(k=>!Object.hasOwn(item,k)))throw Error('Missing/unknown '+type+' fields');
    }
    function migrateScore(input){
        if(!input||![1,2].includes(input.formatVersion))throw Error('Unsupported score formatVersion');
        jsonBudget(input);
        const score=JSON.parse(JSON.stringify(input));
        if(score.formatVersion===1){
            exact(score,'ScoreIR');
            for(const part of score.parts)for(const staff of part.staves)for(const event of staff.events){
                exact(event,'ScoreEvent',true);event.techniques=[];
                for(const note of event.notes){exact(note,'ScoreNote',true);note.writtenPitch=null;note.techniques=[];}
            }
            score.formatVersion=2;
        }
        validateScore(score);return score;
    }
    function jsonBudget(score){
        let nodes=0;const stack=[[score,0]];
        while(stack.length){const [x,depth]=stack.pop();if(++nodes>MAX_NODES||depth>32)throw Error('Score JSON complexity exceeded');if(typeof x==='number'&&!Number.isFinite(x))throw Error('Non-finite score number');if(typeof x==='string'&&x.length>4096)throw Error('Score text exceeds limit');if(x&&typeof x==='object'){if(!Array.isArray(x)&&Object.prototype.toString.call(x)!=='[object Object]')throw Error('Non-JSON score object');if(Object.keys(x).length>(Array.isArray(x)?1000000:64))throw Error('Score JSON complexity exceeded');for(const v of Object.values(x))stack.push([v,depth+1]);}else if(x!==null&&!['string','number','boolean'].includes(typeof x))throw Error('Non-JSON score value');}
        if(new TextEncoder().encode(JSON.stringify(score)).length>MAX_BYTES)throw Error('Score JSON exceeds 32 MiB');
    }
    function validateScore(score){
        jsonBudget(score);
        const require=(ok,msg)=>{if(!ok)throw Error(msg);};
        const id=x=>require(typeof x==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(x),'Invalid score ID');
        const str=(x,max=1024,empty=false)=>require(typeof x==='string'&&Array.from(x).length<=max&&(empty||x.trim().length>0)&&!/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(x),'Invalid single-line score text');
        const integer=(x,min,max)=>require(Number.isSafeInteger(x)&&x>=min&&x<=max,'Invalid score integer');
        const bool=x=>require(typeof x==='boolean','Invalid score boolean');
        const choice=(x,items)=>require(items.includes(x),'Invalid score enum');
        const arr=(x,max,min=0)=>require(Array.isArray(x)&&x.length>=min&&x.length<=max,'Invalid score array');
        const frac=(x,positive=false)=>{exact(x,'QuarterFraction');integer(x.numerator,0,2147483647);integer(x.denominator,1,1000000);require(value(x)<=4096&&(!positive||value(x)>0),'Invalid score duration');return value(x);};
        const techniques=(items,event=false)=>{arr(items,32);const seen=new Set();for(const t of items){exact(t,'ScoreTechnique');choice(t.kind,techniqueKinds);if(event)choice(t.kind,eventTechniques);else require(!['brush','whammy','tremoloPicking'].includes(t.kind),'Event technique belongs to note');const key=t.kind+':'+t.direction;require(!seen.has(key),'Duplicate technique');seen.add(key);if(t.direction!==null)str(t.direction,64);if(techniqueDirections[t.kind])choice(t.direction,techniqueDirections[t.kind]);else require(t.direction===null,'Unexpected technique direction');if(t.targetNoteId!==null){id(t.targetNoteId);require(['hammerOn','pullOff'].includes(t.kind)||(t.kind==='slide'&&['shift','legato'].includes(t.direction)),'Unexpected technique target');}arr(t.curve,64);if(['bend','whammy'].includes(t.kind)){require(t.curve.length>=2&&t.curve[0].position===0&&t.curve.at(-1).position===1,'Curve must span 0..1');let last=0;for(const p of t.curve){exact(p,'ScoreBendPoint');require(typeof p.position==='number'&&p.position>=last&&p.position<=1&&typeof p.semitones==='number'&&Math.abs(p.semitones)<=24,'Invalid curve point');last=p.position;}}else require(t.curve.length===0,'Unexpected technique curve');if(t.value!==null){require(typeof t.value==='number','Invalid technique value');if(t.kind==='harmonic')require(t.value>=0&&t.value<=99,'Invalid harmonic fret');else if(t.kind==='trill')integer(t.value,0,127);else if(t.kind==='tremoloPicking')choice(t.value,[8,16,32,64,128,256]);else if(t.kind==='brush')require(t.value>=0&&t.value<=16,'Invalid brush duration');else throw Error('Unexpected technique value');}else require(t.kind!=='tremoloPicking','Tremolo requires value');}return items.length;};
        exact(score,'ScoreIR');integer(score.formatVersion,2,2);id(score.id);str(score.title);exact(score.source,'ScoreSource');choice(score.source.format,['musicxml','guitarpro','manual']);if(score.source.fileName!==null){str(score.source.fileName);require(!/[\\/]/.test(score.source.fileName),'Invalid source basename');}if(score.source.sha256!==null)require(typeof score.source.sha256==='string'&&/^[0-9a-f]{64}$/.test(score.source.sha256),'Invalid source hash');
        const ids=new Set([score.id]),unique=x=>{id(x);require(!ids.has(x),'Duplicate score ID');ids.add(x);};const measures=new Map(),markers=new Set();
        arr(score.measures,10000,1);arr(score.parts,64,1);arr(score.warnings,10000);arr(score.tempoChanges,100000);arr(score.keyChanges,100000);
        score.measures.forEach((m,index)=>{exact(m,'ScoreMeasure');unique(m.id);str(m.number,128);frac(m.duration,true);exact(m.meter,'ScoreMeter');integer(m.meter.numerator,1,64);choice(m.meter.denominator,[1,2,4,8,16,32,64]);bool(m.repeatStart);if(m.repeatEnd!==null)integer(m.repeatEnd,2,32);arr(m.endingNumbers,32);require(new Set(m.endingNumbers).size===m.endingNumbers.length,'Duplicate ending');m.endingNumbers.forEach(n=>integer(n,1,32));arr(m.markers,128);arr(m.navigation,16);for(const x of m.markers){exact(x,'ScoreMarker');unique(x.id);markers.add(x.id);choice(x.kind,['section','rehearsal','segno','coda','fine']);str(x.label,1024,true);require(frac(x.offset)<=value(m.duration),'Marker outside measure');}for(const x of m.navigation){exact(x,'ScoreNavigation');choice(x.kind,['dc','ds','toCoda','fine']);if(x.targetMarkerId!==null)id(x.targetMarkerId);require(frac(x.offset)<=value(m.duration),'Navigation outside measure');}measures.set(m.id,{...m,index});});
        const compare=(a,b)=>{const left=BigInt(a.numerator)*BigInt(b.denominator),right=BigInt(b.numerator)*BigInt(a.denominator);return left<right?-1:left>right?1:0;};
        const sumFits=(a,b,end)=>(BigInt(a.numerator)*BigInt(b.denominator)+BigInt(b.numerator)*BigInt(a.denominator))*BigInt(end.denominator)<=BigInt(end.numerator)*BigInt(a.denominator)*BigInt(b.denominator);
        const position=(mid,offset,end=false)=>{id(mid);const m=measures.get(mid);require(!!m,'Unknown source measure');frac(offset);const c=compare(offset,m.duration);require(c<0||(end&&c===0),'Position outside measure');return m;};
        let events=0,notes=0,chords=0,techs=0;const locations=new Map(),links=[];
        for(const p of score.parts){exact(p,'ScorePart');unique(p.id);str(p.name);if(p.instrument!==null)str(p.instrument);arr(p.staves,16,1);arr(p.chords,100000);const staffIds=new Set(p.staves.map(s=>s.id));for(const s of p.staves){exact(s,'ScoreStaff');unique(s.id);str(s.name,1024,true);choice(s.kind,['standard','tab','percussion']);if(s.clef!==null)str(s.clef,64);arr(s.tuning,24);s.tuning.forEach(n=>integer(n,0,127));integer(s.capo,0,24);arr(s.events,500000);const onsets=new Set();events+=s.events.length;for(const [eventIndex,e] of s.events.entries()){exact(e,'ScoreEvent');unique(e.id);bool(e.grace);bool(e.isRest);integer(e.voice,1,16);const m=position(e.measureId,e.offset,e.grace);const length=frac(e.duration,!e.grace);require(sumFits(e.offset,e.duration,m.duration),'Event extends outside measure');arr(e.notes,128);require(e.isRest===!e.notes.length&&!(e.grace&&e.isRest),'Invalid rest notes');const onset=e.measureId+':'+value(e.offset)+':'+e.voice;if(!e.grace){require(!onsets.has(onset),'Duplicate event onset');onsets.add(onset);}techs+=techniques(e.techniques,true);notes+=e.notes.length;for(const n of e.notes){exact(n,'ScoreNote');unique(n.id);bool(n.tieStart);bool(n.tieStop);bool(n.unpitched);if(n.pitch!==null)integer(n.pitch,0,127);if(n.writtenPitch!==null)integer(n.writtenPitch,0,127);require((n.string===null)===(n.fret===null),'TAB string/fret must be paired');if(n.string!==null){integer(n.string,1,24);integer(n.fret,0,99);require(!s.tuning.length||n.string<=s.tuning.length,'TAB string outside tuning');}require(n.pitch!==null||n.string!==null||n.unpitched,'Note has no pitch');if(n.accidental!==null)str(n.accidental,64);techs+=techniques(n.techniques);locations.set(n.id,{staff:s.id,voice:e.voice,measure:m.index,offset:e.offset,string:n.string,grace:e.grace,index:eventIndex});for(const t of n.techniques)if(t.targetNoteId!==null)links.push([n.id,t.targetNoteId]);}}}chords+=p.chords.length;for(const c of p.chords){exact(c,'ScoreChord');unique(c.id);position(c.measureId,c.offset);str(c.text);require(c.staffId===null||staffIds.has(c.staffId),'Chord staff outside part');}}
        require(events<=500000&&notes<=1000000&&chords<=100000&&techs<=200000,'Total score complexity exceeded');
        for(const [a,b] of links){const origin=locations.get(a),target=locations.get(b);require(target&&a!==b&&origin.staff===target.staff&&origin.voice===target.voice&&(origin.measure<target.measure||(origin.measure===target.measure&&(compare(origin.offset,target.offset)<0||(origin.grace&&compare(origin.offset,target.offset)===0&&origin.index<target.index)))),'Invalid later same-staff voice technique target');require(origin.string===null||target.string===null||origin.string===target.string,'Linked technique changes string');}
        for(const [items,type] of [[score.tempoChanges,'ScoreTempoChange'],[score.keyChanges,'ScoreKeyChange']]){const seen=new Set();for(const x of items){exact(x,type);position(x.measureId,x.offset,true);const k=x.measureId+':'+value(x.offset);require(!seen.has(k),'Duplicate metadata onset');seen.add(k);if(type==='ScoreTempoChange')require(typeof x.bpm==='number'&&x.bpm>=1&&x.bpm<=1000,'Invalid tempo');else{integer(x.fifths,-7,7);choice(x.mode,['major','minor','unknown']);}}}
        const partIds=new Set(score.parts.map(p=>p.id));for(const w of score.warnings){exact(w,'ScoreWarning');id(w.code);str(w.message,2048);choice(w.severity,['info','warning']);require(w.measureId===null||measures.has(w.measureId),'Unknown warning measure');require(w.partId===null||partIds.has(w.partId),'Unknown warning part');}
        for(const m of score.measures)for(const n of m.navigation){require(n.targetMarkerId===null||markers.has(n.targetMarkerId),'Unknown navigation target');if(['ds','toCoda'].includes(n.kind)&&n.targetMarkerId===null)require(score.warnings.some(w=>w.measureId===m.id),'Unresolved navigation requires warning');}
        return score;
    }
    return {MAX_BYTES, fraction, add, sub, value, decode, parseXML, children, child, text, get, zipEntries, unpack, musicXMLBytes, preflightGpx, technique, curveDirection, validateScore, migrateScore};
});
