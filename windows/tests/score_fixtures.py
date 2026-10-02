"""Generated valid score exceeding the old LAN budget, without a giant fixture."""
import json
from pathlib import Path

from chordcue.score_models import ScoreIR


def large_score() -> ScoreIR:
    raw = json.loads((Path(__file__).parent / "fixtures/techniques-xml.score.json").read_text("utf-8"))
    raw["warnings"] += [{"code": "source.detail", "message": "Source detail " + "x" * 2000,
                         "severity": "warning", "measureId": raw["measures"][0]["id"],
                         "partId": raw["parts"][0]["id"]} for _ in range(1200)]
    return ScoreIR.from_dict(raw)
