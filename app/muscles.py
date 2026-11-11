"""Which muscles an exercise works. A port of the GymLog app's MuscleMap, so the dashboard and the phone agree.

The library's muscle group is often just "Other", so the exercise name is read first (a bench press is chest with some shoulders and triceps),
and the group is only a fallback. A main muscle counts fully for every set, a helper muscle counts half.
"""
import re
from typing import Dict, List, Optional, Tuple

MAIN = 1.0
HELPER = 0.5

# Muscle -> the broader region used for charts that need only a handful of colours.
REGION: Dict[str, str] = {
    "Chest": "Chest",
    "Front shoulders": "Shoulders", "Side shoulders": "Shoulders", "Rear shoulders": "Shoulders",
    "Traps": "Back", "Lats and mid back": "Back", "Lower back": "Back",
    "Biceps": "Arms", "Triceps": "Arms", "Forearms": "Arms",
    "Abs": "Core",
    "Glutes": "Legs", "Quads": "Legs", "Hamstrings": "Legs", "Inner thighs": "Legs", "Calves": "Legs",
}
REGIONS = ["Chest", "Back", "Shoulders", "Arms", "Core", "Legs"]

_RULES: List[Tuple["re.Pattern[str]", List[str], List[str]]] = [
    (re.compile(p), main, helpers) for p, main, helpers in [
        (r"rear.?delt|reverse.?fly|rear.?fly", ["Rear shoulders"], ["Traps"]),
        (r"face.?pull", ["Rear shoulders"], ["Traps", "Biceps"]),
        (r"lat(eral)? raise|side raise|lat raise", ["Side shoulders"], []),
        (r"front raise", ["Front shoulders"], []),
        (r"shoulder press|overhead press|military|arnold|push press", ["Front shoulders", "Side shoulders"], ["Triceps"]),
        (r"upright row", ["Side shoulders", "Traps"], []),
        (r"shrug", ["Traps"], ["Forearms"]),
        (r"hamstring curl|leg curl|nordic", ["Hamstrings"], []),
        (r"leg extension", ["Quads"], []),
        (r"calf|calves", ["Calves"], []),
        (r"adductor|inner thigh", ["Inner thighs"], []),
        (r"abductor", ["Glutes"], []),
        (r"hip thrust|glute|kickback|bridge", ["Glutes"], ["Hamstrings"]),
        (r"deadlift|rdl|good morning", ["Hamstrings", "Glutes", "Lower back"], ["Traps", "Forearms"]),
        (r"back extension|hyperextension", ["Lower back"], ["Glutes", "Hamstrings"]),
        (r"squat|leg press|lunge|step.?up", ["Quads", "Glutes"], ["Hamstrings"]),
        (r"tricep|pushdown|push down|skull|french|overhead extension", ["Triceps"], []),
        (r"dip", ["Chest", "Triceps"], ["Front shoulders"]),
        (r"pull.?up|chin.?up|pulldown|pull down", ["Lats and mid back"], ["Biceps", "Rear shoulders"]),
        (r"\brows?\b|rowing", ["Lats and mid back"], ["Biceps", "Rear shoulders", "Traps"]),
        (r"fly|flye|crossover|pec deck|pec fly", ["Chest"], ["Front shoulders"]),
        (r"bench|incline|decline|chest press|supine press|push.?up|press", ["Chest"], ["Front shoulders", "Triceps"]),
        (r"curl", ["Biceps"], ["Forearms"]),
        (r"wrist|forearm|farmer|grip", ["Forearms"], []),
        (r"crunch|sit.?up|leg raise|knee raise|plank|\bab\b|abs|woodchop|twist|russian", ["Abs"], []),
    ]
]

_BY_GROUP: Dict[str, List[str]] = {
    "chest": ["Chest"], "back": ["Lats and mid back"], "shoulders": ["Side shoulders"], "shoulder": ["Side shoulders"],
    "biceps": ["Biceps"], "triceps": ["Triceps"], "forearms": ["Forearms"], "core": ["Abs"], "abs": ["Abs"], "glutes": ["Glutes"],
    "quads": ["Quads"], "hamstrings": ["Hamstrings"], "calves": ["Calves"], "legs": ["Quads", "Hamstrings", "Glutes"],
}


def muscles_for(name: str, group: str = "") -> Dict[str, float]:
    """The muscles an exercise works, each with how much one set counts for it (1.0 main, 0.5 helper). Empty when nothing is known."""
    lower = (name or "").strip().lower()
    for pattern, main, helpers in _RULES:
        if pattern.search(lower):
            out = {m: MAIN for m in main}
            for h in helpers:
                out.setdefault(h, HELPER)
            return out
    return {m: MAIN for m in _BY_GROUP.get((group or "").strip().lower(), [])}


def primary(name: str, group: str = "") -> Tuple[Optional[str], Optional[str]]:
    """(muscle, region) of the first main muscle, or (None, None) when the exercise is unknown to the map."""
    m = muscles_for(name, group)
    if not m:
        return None, None
    first = next(iter(m))
    return first, REGION[first]
