(function(root,factory){
    const api=factory();
    if(typeof module==='object'&&module.exports)module.exports=api;
    else root.ChordCuePlaybackPlan=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
    'use strict';
    const checked=new WeakSet();
    const sourceCounts=new WeakMap();
    function finite(x){return typeof x==='number'&&Number.isFinite(x);}
    function validateRoute(route){
        if(!route||route.version!==1||!finite(route.endQuarter)||route.endQuarter<=0||route.endQuarter>1000000||
            !finite(route.durationSeconds)||route.durationSeconds<=0||!Array.isArray(route.occurrences)||
            !route.occurrences.length||route.occurrences.length>100000||!Array.isArray(route.segments)||
            !route.segments.length||route.segments.length>200000)throw Error('Invalid playback route');
        let q=0,seconds=0,sourceCount=0;const ids=new Set();
        for(const o of route.occurrences){
            if(typeof o.id!=='string'||ids.has(o.id)||typeof o.sourceMeasureId!=='string'||
                !Number.isInteger(o.sourceIndex)||o.sourceIndex<1||!finite(o.startQuarter)||
                !finite(o.endQuarter)||Math.abs(o.startQuarter-q)>1e-7||o.endQuarter<=q||
                !o.meter||!Number.isInteger(o.meter.numerator)||o.meter.numerator<1||o.meter.numerator>64||
                ![1,2,4,8,16,32,64].includes(o.meter.denominator))throw Error('Invalid playback occurrence');
            ids.add(o.id);q=o.endQuarter;sourceCount=Math.max(sourceCount,o.sourceIndex);
        }
        if(Math.abs(q-route.endQuarter)>1e-7)throw Error('Playback occurrence end mismatch');
        q=0;
        for(const s of route.segments){
            if(!finite(s.startQuarter)||!finite(s.endQuarter)||s.endQuarter<=s.startQuarter||
                Math.abs(s.startQuarter-q)>1e-7||!finite(s.startSeconds)||!finite(s.endSeconds)||
                s.endSeconds<=s.startSeconds||Math.abs(s.startSeconds-seconds)>1e-7||
                !finite(s.bpm)||s.bpm<=0||!ids.has(s.occurrenceId)||
                Math.abs((s.endQuarter-s.startQuarter)*60/s.bpm-(s.endSeconds-s.startSeconds))>1e-6)
                throw Error('Invalid tempo route segment');
            q=s.endQuarter;seconds=s.endSeconds;
        }
        if(Math.abs(q-route.endQuarter)>1e-7||Math.abs(seconds-route.durationSeconds)>1e-6)
            throw Error('Playback tempo end mismatch');
        checked.add(route);sourceCounts.set(route,sourceCount);return route;
    }
    function ready(route){if(!checked.has(route))validateRoute(route);return route;}
    function find(array,key,value){
        let lo=0,hi=array.length;
        while(lo<hi){const mid=(lo+hi)>>>1;if(array[mid][key]<=value)lo=mid+1;else hi=mid;}
        return array[Math.max(0,lo-1)];
    }
    function secondsAt(route,q){
        ready(route);if(!finite(q))throw Error('Quarter must be finite');
        q=Math.max(0,Math.min(route.endQuarter,q));if(q===route.endQuarter)return route.durationSeconds;
        const s=find(route.segments,'startQuarter',q);return s.startSeconds+(q-s.startQuarter)*60/s.bpm;
    }
    function quarterAt(route,seconds){
        ready(route);if(!finite(seconds))throw Error('Seconds must be finite');
        seconds=Math.max(0,Math.min(route.durationSeconds,seconds));
        if(seconds===route.durationSeconds)return route.endQuarter;
        const s=find(route.segments,'startSeconds',seconds);return s.startQuarter+(seconds-s.startSeconds)*s.bpm/60;
    }
    function occurrenceAt(route,q){ready(route);return find(route.occurrences,'startQuarter',Math.max(0,Math.min(route.endQuarter,q)));}
    function loopBounds(route,sample){
        const loop=sample.playback&&sample.playback.loop;
        if(!loop)return null;
        if(!finite(loop.startBeat)||!finite(loop.endBeat)||loop.startBeat<0||loop.endBeat>route.endQuarter||loop.endBeat<=loop.startBeat)
            throw Error('Invalid playback loop');
        return {start:loop.startBeat,end:loop.endBeat,iteration:Math.max(0,Number.isInteger(loop.iteration)?loop.iteration:0),
            startSeconds:secondsAt(route,loop.startBeat),endSeconds:secondsAt(route,loop.endBeat)};
    }
    function baseQuarter(sample){const q=finite(sample.playQuarter)?sample.playQuarter:sample.positionQuarter;return finite(q)?q:0;}
    function position(route,sample,targetTimeMs){
        ready(route);if(!sample||!finite(sample.sampleTime)||!finite(targetTimeMs))throw Error('Invalid playback sample/time');
        let q=Math.max(0,Math.min(route.endQuarter,baseQuarter(sample))),playing=!!sample.playing;
        const loop=loopBounds(route,sample);let iteration=loop?loop.iteration:0;
        const announced=sample.playback&&sample.playback.startTime;
        const start=Math.max(sample.sampleTime,finite(announced)?announced:sample.sampleTime);
        if(playing){
            let seconds=secondsAt(route,q)+Math.max(0,targetTimeMs-start)/1000;
            if(loop){
                const length=loop.endSeconds-loop.startSeconds;
                const elapsed=seconds-loop.startSeconds,ratio=elapsed/length,nearest=Math.round(ratio);
                // Integer cycle boundaries can divide just below the integer
                // (16.2 / 5.4, for example). Snap only within double rounding
                // precision, rather than advance across a musical time window.
                const tolerance=Math.min(0.5e-9/length,2*Number.EPSILON*Math.max(1,Math.abs(ratio)));
                const boundary=Math.abs(ratio-nearest)<=tolerance;
                const cycle=boundary?nearest:Math.floor(ratio);
                seconds=loop.startSeconds+(boundary?0:elapsed-cycle*length);
                iteration+=cycle;q=quarterAt(route,seconds);
            }else{q=quarterAt(route,seconds);if(seconds>=route.durationSeconds)playing=false;}
        }
        const o=occurrenceAt(route,q),s=find(route.segments,'startQuarter',q),offset=q-o.startQuarter;
        const atEnd=q>=route.endQuarter;
        return {playQuarter:q,positionQuarter:q,sourceMeasureId:o.sourceMeasureId,sourceOffsetQuarter:offset,
            occurrenceId:o.id,bar:atEnd?sourceCounts.get(route)+1:o.sourceIndex,
            beat:atEnd?1:Math.floor(offset)+1,bpm:s.bpm,rate:s.bpm/60,
            meterNumerator:o.meter.numerator,meterDenominator:o.meter.denominator,
            meter:o.meter.numerator+'/'+o.meter.denominator,playing,preparing:playing&&targetTimeMs<start,
            loopIteration:iteration};
    }
    function unwrappedQuarter(route,sample,targetTimeMs){
        const p=position(route,sample,targetTimeMs),loop=loopBounds(route,sample);
        return p.playQuarter+(loop?p.loopIteration*(loop.end-loop.start):0);
    }
    function targetTime(route,sample,q){
        ready(route);if(!finite(q)||!sample||!finite(sample.sampleTime))throw Error('Invalid target quarter/sample');
        const loop=loopBounds(route,sample),base=baseQuarter(sample);
        let targetSeconds;
        let baseSeconds=secondsAt(route,base);
        if(loop){
            const quarters=loop.end-loop.start,seconds=loop.endSeconds-loop.startSeconds;
            const iteration=Math.floor((q-loop.start)/quarters);
            const wrapped=loop.start+((q-loop.start)%quarters+quarters)%quarters;
            targetSeconds=secondsAt(route,wrapped)+iteration*seconds;
            baseSeconds+=loop.iteration*seconds;
        }else targetSeconds=secondsAt(route,q);
        const announced=sample.playback&&sample.playback.startTime;
        const start=Math.max(sample.sampleTime,finite(announced)?announced:sample.sampleTime);
        return start+(targetSeconds-baseSeconds)*1000;
    }
    function allows(route,sample,q){
        if(!finite(q))return false;
        const loop=loopBounds(route,sample);
        return loop?q>=loop.start:0<=q&&q<route.endQuarter;
    }
    return Object.freeze({validateRoute,secondsAt,quarterAt,occurrenceAt,position,unwrappedQuarter,targetTime,allows});
});
