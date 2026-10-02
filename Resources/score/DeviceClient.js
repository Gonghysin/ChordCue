(function(root,factory){
    const api=factory();
    if(typeof module==='object'&&module.exports)module.exports=api;else root.ChordCueDeviceClient=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
    'use strict';
    const VIEWS=Object.freeze(['chords','numbers','staff','tab','metronome']);
    const ID=/^[A-Za-z0-9_.:-]{1,128}$/;
    const revision=value=>Number.isSafeInteger(value)&&value>=0;
    // Offset maps independent monotonic origins; it is not audible sync error.
    // A probe belongs to one connection/page generation and one host session.
    class ClockCalibration{
        constructor(){this.generation=0;this.session=null;this.reset();}
        reset(session=this.session){++this.generation;this.session=session;this.samples=[];this.offset=null;this.best=null;this.latest=null;this.observed=null;}
        observe(now,wall){
            if(this.observed&&(now<this.observed.now||now-this.observed.now>5000||Math.abs((wall-this.observed.wall)-(now-this.observed.now))>1000))this.reset();
            // Queued audio keeps its onset. Only subsequently scheduled beats
            // follow the estimate, at most 5 ms per second in either direction.
            if(this.observed&&this.offset!==null&&this.best){const step=(now-this.observed.now)*0.005;this.offset+=Math.max(-step,Math.min(step,this.best.offset-this.offset));}
            this.observed={now,wall};
        }
        begin(now,wall){this.observe(now,wall);return {owner:this,generation:this.generation,t0:now,wall,session:this.session};}
        accept(data,probe,t3,wall){
            this.observe(t3,wall);
            if(!probe||probe.owner!==this||probe.generation!==this.generation||!data||typeof data.session!=='string'||!data.session||
                ![probe.t0,t3,data.received,data.sent].every(Number.isFinite)||t3<probe.t0||t3-probe.t0>2500||
                data.sent<data.received||(this.session&&data.session!==this.session))return false;
            const latency=t3-probe.t0-(data.sent-data.received);
            const offset=((data.received-probe.t0)+(data.sent-t3))/2;
            if(!Number.isFinite(latency)||latency<0||latency>2500||!Number.isFinite(offset)||Math.abs(offset)>1e12)return false;
            this.session=data.session;
            this.samples=this.samples.filter(v=>t3-v.time>=0&&t3-v.time<10000);
            // A confident clock discontinuity needs a new audio epoch. Ordinary
            // drift and probe noise preserve the mapping while it is refined.
            if(this.best&&Math.abs(offset-this.best.offset)>Math.max(30,(latency+this.best.rtt)/2)){this.samples=[];this.offset=null;++this.generation;}
            this.samples.push({time:t3,offset,rtt:latency});
            this.latest=t3;this.samples=this.samples.slice(-20);this.best=this.samples.reduce((a,b)=>a.rtt<b.rtt?a:b);
            if(this.offset===null)this.offset=this.best.offset;
            return true;
        }
        diagnostics(now,wall){
            this.observe(now,wall);
            // Low RTT estimates may be older than the last successful probe;
            // their replacement must not briefly invalidate a healthy clock.
            const age=this.latest===null?null:now-this.latest,valid=age!==null&&age>=0&&age<10000;
            const values=this.samples.filter(v=>now-v.time>=0&&now-v.time<10000);
            const jitter=values.length>1?Math.sqrt(values.reduce((sum,v)=>sum+(v.offset-this.best.offset)**2,0)/values.length):null;
            return {version:1,status:valid?'valid':this.best?'stale':'calibrating',jitterMs:jitter,probeAgeMs:age};
        }
    }
    function transportFresh(sample,chart,now,offset,lastReceived,connected=true){
        const age=sample&&offset!==null?now+offset-sample.sampleTime:NaN;
        return connected&&!!sample?.valid&&sample.revision===chart?.revision&&Number.isFinite(age)&&age>=-30&&age<=350&&now-lastReceived>=0&&now-lastReceived<=350;
    }
    function identity(crypto){
        if(crypto?.randomUUID)return 'browser-'+crypto.randomUUID();
        if(crypto?.getRandomValues){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return 'browser-'+Array.from(bytes,v=>v.toString(16).padStart(2,'0')).join('');}
        return 'browser-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
    }
    function validAssignment(value,clientId){
        return !!value&&value.protocolVersion===2&&value.clientId===clientId&&
            (value.partId===null||typeof value.partId==='string'&&ID.test(value.partId))&&
            typeof value.label==='string'&&value.label.length>0&&value.label.length<=128&&
            !/[\x00-\x1f]/.test(value.label)&&VIEWS.includes(value.view)&&
            revision(value.assignmentRevision)&&revision(value.scoreRevision);
    }
    function numberChord(text,key){
        if(!key||key.mode==='unknown'||text==='N.C.')return text;
        const family=(key.fifths*7%12+12)%12,degrees=['1','♭2','2','♭3','3','4','♯4','5','♭6','6','♭7','7'];
        return text.split('/').map((part,index)=>{
            const match=/^([A-G])([♯♭#b]?)(.*)$/.exec(part);if(!match)return part;
            const pitch={C:0,D:2,E:4,F:5,G:7,A:9,B:11}[match[1]]+(match[2]==='#'||match[2]==='♯'?1:match[2]==='b'||match[2]==='♭'?-1:0);
            const suffix=index?match[3]:match[3].replace(/[0-9]/g,n=>'⁰¹²³⁴⁵⁶₇⁸⁹'[Number(n)]).replaceAll('b⁵','b5');
            return degrees[((pitch-family)%12+12)%12]+suffix;
        }).join('/');
    }
    // Derive only a display adapter from explicit source harmony annotations.
    // The host chart and complete ScoreIR remain unchanged.
    function partChart(chart,partId){
        if(!chart.score)return chart;
        const score=chart.score,part=score.parts.find(value=>value.id===partId);
        if(!part)throw Error('分配的声部不在当前谱面中');
        const q=value=>value.numerator/value.denominator,index=new Map(score.measures.map((measure,i)=>[measure.id,i]));
        const keys=[...score.keyChanges].sort((a,b)=>index.get(a.measureId)-index.get(b.measureId)||q(a.offset)-q(b.offset));
        let keyIndex=0,key=null;
        const events=[...part.chords].sort((a,b)=>index.get(a.measureId)-index.get(b.measureId)||q(a.offset)-q(b.offset)).map(chord=>{
            const bar=index.get(chord.measureId)+1,offset=q(chord.offset);
            while(keyIndex<keys.length&&(index.get(keys[keyIndex].measureId)<bar-1||index.get(keys[keyIndex].measureId)===bar-1&&q(keys[keyIndex].offset)<=offset))key=keys[keyIndex++];
            return {id:chord.id,bar,beat:1,division:1,tick:offset*960,measureId:chord.measureId,offsetQuarter:offset,symbol:chord.text,number:numberChord(chord.text,key),keyKnown:!!key&&key.mode!=='unknown'};
        });
        const sections=keys.filter(key=>q(key.offset)===0).map(key=>({bar:index.get(key.measureId)+1,family:((key.fifths*7)%12+12)%12,root:((key.fifths*7+(key.mode==='minor'?9:0))%12+12)%12,minor:key.mode==='minor',unknown:key.mode==='unknown'}));
        return {...chart,bars:score.measures.length,events,sections,partName:part.name,measures:score.measures};
    }
    // The recovery key stays in this tab's session storage. Separate tabs can
    // receive separate assignments; a reload recovers this tab's assignment.
    class DeviceClient{
        constructor(options={}){
            this.fetch=options.fetch||globalThis.fetch?.bind(globalThis);
            this.EventSource=options.EventSource||globalThis.EventSource;
            this.storage=options.storage;
            if(!Object.hasOwn(options,'storage')){try{this.storage=globalThis.sessionStorage;}catch{}}
            this.baseURL=options.baseURL||new URL('.',globalThis.location.href).href;
            this.storageKey='chordcue-device-v2:'+this.baseURL;
            this.setTimeout=options.setTimeout||globalThis.setTimeout.bind(globalThis);
            this.clearTimeout=options.clearTimeout||globalThis.clearTimeout.bind(globalThis);
            this.setInterval=options.setInterval||globalThis.setInterval.bind(globalThis);
            this.clearInterval=options.clearInterval||globalThis.clearInterval.bind(globalThis);
            this.onChart=options.onChart||(()=>{});this.onTransport=options.onTransport||(()=>{});
            this.applyAssignment=options.applyAssignment||(()=>false);
            this.onConnection=options.onConnection||(()=>{});this.onError=options.onError||(()=>{});
            this.onSession=options.onSession||(()=>{});this.serverCapabilities=[];
            this.metrics=options.metrics||(()=>({}));
            this.capabilities=[...(options.capabilities||VIEWS)];
            this.credential=null;
            try{const stored=JSON.parse(this.storage?.getItem(this.storageKey)||'null');if(stored&&typeof stored.clientId==='string'&&ID.test(stored.clientId)&&typeof stored.resumeKey==='string'&&/^[a-f0-9]{64}$/.test(stored.resumeKey))this.credential=stored;}catch{}
            if(!this.credential)this.credential={clientId:identity(options.crypto||globalThis.crypto),resumeKey:null};
            this.label=options.label||'浏览器 '+this.credential.clientId.slice(-6);
            this.connected=false;this.running=false;this.chart=null;this.assignment=null;this.sessionId=null;
            this.appliedAssignmentRevision=0;this.appliedScoreRevision=0;
            this.generation=0;this.applyGeneration=0;this.retry=1000;
            this.stream=null;this.retryTimer=null;this.telemetryTimer=null;this.telemetryBusy=false;
            this.applyingKey=null;this.requests=new Set();
        }
        snapshot(){return {clientId:this.credential.clientId,connected:this.connected,assignment:this.assignment,
            appliedAssignmentRevision:this.appliedAssignmentRevision,scoreRevision:this.appliedScoreRevision,
            applied:!!this.assignment&&this.appliedAssignmentRevision===this.assignment.assignmentRevision&&this.appliedScoreRevision===this.assignment.scoreRevision};}
        start(){if(this.running)return;this.running=true;this.telemetryTimer=this.setInterval(()=>this.sendTelemetry(),2000);void this._connect();}
        stop(){
            this.running=false;++this.generation;++this.applyGeneration;this.applyingKey=null;
            this.clearTimeout(this.retryTimer);this.clearInterval(this.telemetryTimer);this.retryTimer=this.telemetryTimer=null;
            this.stream?.close();this.stream=null;for(const request of this.requests)request.abort();this.requests.clear();this._connection(false);
        }
        _connection(value){if(this.connected===value)return;this.connected=value;this.onConnection(value);}
        _save(){try{this.storage?.setItem(this.storageKey,JSON.stringify(this.credential));}catch{}}
        _session(value){
            if(typeof value!=='string'||!value)return;
            if(this.sessionId&&this.sessionId!==value){this.chart=null;this.assignment=null;this.appliedAssignmentRevision=this.appliedScoreRevision=0;++this.applyGeneration;this.applyingKey=null;}
            this.sessionId=value;
            this.onSession(value);
        }
        async _post(route,payload){
            const controller=new AbortController();this.requests.add(controller);
            const timer=this.setTimeout(()=>controller.abort(),4000);
            try{
                const response=await this.fetch(new URL(route,this.baseURL).href,{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
                if(!response.ok){const error=Error('设备连接失败 ('+response.status+')');error.status=response.status;throw error;}
                return await response.json();
            }finally{this.clearTimeout(timer);this.requests.delete(controller);}
        }
        async _connect(){
            if(!this.running)return;const generation=++this.generation;
            try{
                const registration=await this._post('register',{clientId:this.credential.clientId,...(this.credential.resumeKey?{resumeKey:this.credential.resumeKey}:{}),label:this.label,capabilities:this.capabilities});
                if(!this.running||generation!==this.generation)return;
                if(!validAssignment(registration,this.credential.clientId)||!/^[a-f0-9]{64}$/.test(registration.resumeKey||''))throw Error('主机返回了无效设备注册');
                this.serverCapabilities=Array.isArray(registration.serverCapabilities)?registration.serverCapabilities:[];
                this._session(registration.sessionId);this.credential.resumeKey=registration.resumeKey;this._save();this._assignment(registration);
                const url=new URL('events',this.baseURL);url.searchParams.set('clientId',this.credential.clientId);url.searchParams.set('resumeKey',this.credential.resumeKey);
                const stream=new this.EventSource(url.href);this.stream=stream;
                const active=()=>this.running&&generation===this.generation&&this.stream===stream;
                stream.onopen=()=>{if(active()){this.retry=1000;this._connection(true);void this.sendTelemetry();}};
                stream.onerror=()=>{if(active()){stream.close();this.stream=null;this._connection(false);this._retry();}};
                const receive=(kind,callback)=>stream.addEventListener(kind,event=>{if(!active())return;try{callback(JSON.parse(event.data));}catch(error){this.onError(error);}});
                receive('chart',value=>{if(!value||!revision(value.revision)||!Array.isArray(value.events))throw Error('主机返回了无效谱面');this.chart=value;this.onChart(value);this._apply();});
                receive('assignment',value=>this._assignment(value));
                receive('transport',value=>{if(value&&revision(value.revision))this.onTransport(value);});
            }catch(error){
                if(!this.running||generation!==this.generation)return;
                // A restarted host may no longer know this tab. Registering with
                // the same ID succeeds on a new registry; an occupied ID with an
                // invalid key needs a fresh identity rather than another tab's key.
                if(error.status===403){this.credential={clientId:identity(globalThis.crypto),resumeKey:null};this._save();this.assignment=null;this.chart=null;this.appliedAssignmentRevision=this.appliedScoreRevision=0;}
                this._connection(false);this.onError(error);this._retry();
            }
        }
        _retry(){if(!this.running||this.retryTimer!==null)return;const delay=this.retry;this.retry=Math.min(10000,this.retry*2);this.retryTimer=this.setTimeout(()=>{this.retryTimer=null;void this._connect();},delay);}
        _assignment(value){
            if(!validAssignment(value,this.credential.clientId))throw Error('主机返回了无效设备分配');
            if(value.sessionId&&this.sessionId&&value.sessionId!==this.sessionId)throw Error('设备分配来自不同主机会话');
            if(this.assignment&&(value.scoreRevision<this.assignment.scoreRevision||value.scoreRevision===this.assignment.scoreRevision&&value.assignmentRevision<this.assignment.assignmentRevision))return;
            const changed=!this.assignment||value.assignmentRevision!==this.assignment.assignmentRevision||value.scoreRevision!==this.assignment.scoreRevision||value.partId!==this.assignment.partId||value.view!==this.assignment.view;
            this.assignment={protocolVersion:2,clientId:value.clientId,label:value.label,partId:value.partId,view:value.view,assignmentRevision:value.assignmentRevision,scoreRevision:value.scoreRevision};
            if(changed){this.appliedAssignmentRevision=this.appliedScoreRevision=0;++this.applyGeneration;this.applyingKey=null;}
            this._apply();
        }
        _apply(){
            const assignment=this.assignment,chart=this.chart;
            if(!assignment||!chart||assignment.scoreRevision!==chart.revision)return;
            const key=assignment.assignmentRevision+':'+assignment.scoreRevision+':'+assignment.label;
            if(this.applyingKey===key)return;
            this.applyingKey=key;const generation=++this.applyGeneration;
            Promise.resolve().then(()=>generation===this.applyGeneration&&this.running?this.applyAssignment(assignment,chart):false).then(applied=>{
                if(generation!==this.applyGeneration||!this.running)return;
                if(applied===true){this.appliedAssignmentRevision=assignment.assignmentRevision;this.appliedScoreRevision=chart.revision;void this.sendTelemetry();}
                else this.applyingKey=null;
            }).catch(error=>{if(generation===this.applyGeneration){this.applyingKey=null;this.onError(error);}});
        }
        async sendTelemetry(){
            if(!this.running||!this.connected||!this.credential.resumeKey||this.telemetryBusy)return;
            this.telemetryBusy=true;const generation=this.generation;
            try{
                const raw=this.metrics()||{},metric=(name,maximum,positive=false)=>typeof raw[name]==='number'&&Number.isFinite(raw[name])&&Math.abs(raw[name])<=maximum&&(!positive||raw[name]>=0)?raw[name]:null;
                const result=await this._post('telemetry',{clientId:this.credential.clientId,resumeKey:this.credential.resumeKey,
                    appliedAssignmentRevision:this.appliedAssignmentRevision,scoreRevision:this.appliedScoreRevision,
                    rttMs:metric('rttMs',60000,true),clockOffsetMs:metric('clockOffsetMs',1e12),audioOutputDelayMs:metric('audioOutputDelayMs',10000),freshnessMs:metric('freshnessMs',3600000,true),
                    syncStatus:['calibrating','synchronized','stale','disabled'].includes(raw.syncStatus)?raw.syncStatus:'calibrating',
                    ...(this.serverCapabilities.includes('clockDiagnosticsV1')&&raw.clockDiagnostics?{clockDiagnostics:raw.clockDiagnostics}:{})});
                if(this.running&&generation===this.generation)this._assignment(result);
            }catch(error){
                if(this.running&&generation===this.generation){this.onError(error);if(error.status===403){this.stream?.close();this.stream=null;this._connection(false);this._retry();}}
            }finally{this.telemetryBusy=false;}
        }
    }
    return {DeviceClient,ClockCalibration,transportFresh,VIEWS,validAssignment,partChart,numberChord};
});
