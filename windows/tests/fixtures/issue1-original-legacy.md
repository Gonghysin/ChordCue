These original fixtures contain one bar composed for ChordCue tests: E4 and
F#4 quarter notes, then a D4 half note, at 108 BPM in 4/4. The first beat has
an explicit `Cmaj7/E` annotation, and the bar repeats twice. No external song
or third-party tablature is used.

Regenerate them with `node tools/generate_legacy_gp_fixtures.cjs`.

The GP3, GP4 and GP5 files use the respective binary record layouts accepted
by the bundled alphaTab `Gp3To5Importer`. The GP6 files exercise both BCFS
and BCFZ container decoding; their basic GPIF projection comes from alphaTab's
exporter, with its version field set to 6.0 and wrapped by the fixture generator.
They are handcrafted test files, not exports from the proprietary Guitar Pro
applications. They verify basic source notes, string/fret references, harmony,
meter, tempo and repeat metadata, and do not establish support for every
legacy effect or layout option.
