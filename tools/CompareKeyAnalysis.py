"""Offline diagnostic: compare a Logic chord snapshot with music21 baselines.

Usage: uv run --with music21 python tools/CompareKeyAnalysis.py /tmp/chordcue-key-analysis snapshot.txt [beats-per-bar]
The application does not import Python or contact any server.
"""
import json
import re
import subprocess
import sys

from music21 import chord, harmony, stream

binary, snapshot = sys.argv[1:3]
meter = int(sys.argv[3]) if len(sys.argv) > 3 else 4
result = json.loads(subprocess.check_output([binary, snapshot, str(meter)], text=True))
events = result["chords"]
score = stream.Stream()
skipped = []


def onset(event):
    return ((event["bar"] - 1) * meter + event["beat"] - 1
            + (event["division"] - 1) / 4 + event["tick"] / 960)


for index, event in enumerate(events):
    symbol = event["symbol"]
    if symbol == "N.C.":
        continue
    normalized = symbol.replace("♯", "#").replace("♭", "b")
    normalized = re.sub(r"([A-G])b", r"\1-", normalized)
    normalized = normalized.replace("m(maj7)", "mM7")
    try:
        parsed = harmony.ChordSymbol(normalized)
    except (ValueError, harmony.ChordStepModificationException) as error:
        skipped.append({"bar": event["bar"], "symbol": symbol, "error": str(error)})
        continue
    start = onset(event)
    end = onset(events[index + 1]) if index + 1 < len(events) else events[-1]["bar"] * meter
    if end <= start:
        continue
    notes = chord.Chord(parsed.pitches)
    notes.quarterLength = end - start
    score.insert(start, notes)

print("ChordCue 全曲：", result["global"])
print("ChordCue 段落：", result["sections"])
print("和弦数：", len(events), "，跳过：", len(skipped))
if skipped:
    print(json.dumps(skipped, ensure_ascii=False))
if not score.notes:
    raise RuntimeError("没有可用于 music21 比较的和弦")
for algorithm in ("Krumhansl", "TemperleyKostkaPayne", "BellmanBudge"):
    key = score.analyze(algorithm)
    print(f"music21 {algorithm}: {key} (相关系数 {key.correlationCoefficient:.3f})")
