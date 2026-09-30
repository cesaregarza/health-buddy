#!/usr/bin/env python3
"""Pure context-selection helpers; the old HTTP/provider lifecycle is retired.

Use the canonical health-buddy server with an explicit private workspace.
Provider credentials and outbound requests belong to admitted operations.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_pack as cp

INCLUDE_AT = 0.5
LEAN_BAND = (0.35, 0.65)
EVERYTHING_AT = 0.6
WINDOW_DEFAULT = 30


def jev_questions() -> dict:
    """One yes/no question per section, one for 'overview', one choice for the window.
    They run in parallel in a single request and cannot see each other."""
    q = {}
    for s in cp.SCOPES:
        q[f"need_{s.id}"] = {
            "type": "noul",
            "instructions": f"Answering `request` well needs the person's \"{s.label}\" section of their health data. That section contains: {s.description}.",
            "criteria": {"true": "The request is about this, or an answer would be materially better with it as context.",
                         "false": "This is unrelated to the request, or would only add length without helping."},
        }
    q["everything"] = {
        "type": "noul",
        "instructions": "`request` asks for a general or complete overview of the person's health, rather than a specific topic.",
        "criteria": {"true": "Words like overall, everything, full picture, general check-in, or no particular topic.",
                     "false": "A specific question or topic is named."},
    }
    q["window"] = {
        "type": "choice",
        "instructions": "How much recent history does `request` need?",
        "criteria": {"14": "Only the last two weeks: a specific recent event, this week, right now, since a recent change.",
                     "30": "About a month: a normal check-in on current trends. Use this when the request does not say.",
                     "90": "About a season: comparing months, the effect of a medication or program change, longer trends.",
                     "all": "Everything on record: the overall journey, since the start, full history, a first introduction of the person."},
    }
    return q

def decide(answers: dict) -> dict:
    """Deterministic policy over Jev's raw probabilities; the raw values are returned too so the
    page can show them and the thresholds can be tuned without re-asking."""
    scopes = {s.id: float((answers.get(f"need_{s.id}") or {}).get("noul") or 0.0) for s in cp.SCOPES}
    everything = float((answers.get("everything") or {}).get("noul") or 0.0)
    selected = [k for k, p in scopes.items() if p >= INCLUDE_AT]
    reason = "per-section picks"
    if everything >= EVERYTHING_AT:
        selected, reason = list(cp.SCOPE_IDS), "request reads as an overview"
    elif not selected:
        selected, reason = list(cp.SCOPE_IDS), "no section cleared the threshold, so everything is included"
    leaning = [k for k, p in scopes.items() if LEAN_BAND[0] <= p < LEAN_BAND[1]]
    win = answers.get("window") or {}
    choice = str(win.get("choice") or WINDOW_DEFAULT)
    days = 0 if choice == "all" else int(choice) if choice.isdigit() else WINDOW_DEFAULT
    return {"scopes": scopes, "selected": selected, "leaning": leaning, "everything": everything, "reason": reason,
            "days": days, "window_confidence": win.get("confidence"), "window_probabilities": win.get("probabilities")}



def main(argv=None) -> int:
    print("The legacy context HTTP service is retired. Use health-buddy --workspace PATH [--development] serve.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
