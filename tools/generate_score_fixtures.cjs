// Original fixture only. alphaTab's MusicXML projection is corrected before GP export.
const fs=require('node:fs');
const alpha=require('../Resources/vendor/alphatab/dist/alphaTab.js');
const XML=require('../Resources/score/MusicXMLImport.js');
const GP=require('../Resources/score/GuitarProImport.js');
const source=new Uint8Array(fs.readFileSync('windows/tests/fixtures/issue1-original.musicxml'));
const original=XML.importScore(source,'issue1-original.musicxml');
const model=alpha.importer.ScoreLoader.loadScoreFromBytes(source);
model.masterBars[1].alternateEndings=1;
model.masterBars[2].alternateEndings=2;
model.masterBars[2].tempoAutomations[0].value=135;
model.tracks[0].staves[0].bars[0].voices[0].beats[0].chord.name='Cmaj7/E';
const bytes=new alpha.exporter.Gp7Exporter().export(model);
fs.writeFileSync('windows/tests/fixtures/issue1-original.gp',bytes);
fs.writeFileSync('windows/tests/fixtures/issue1-original.score.json',JSON.stringify(original,null,2)+'\n');
fs.writeFileSync('windows/tests/fixtures/issue1-original-gp.score.json',JSON.stringify(GP.importScore(bytes,'issue1-original.gp'),null,2)+'\n');
