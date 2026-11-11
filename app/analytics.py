"""The numbers behind the dashboard. Pure Python, no web and no files: give it the parsed contents of a GymLog backup (backup.json) and it
answers questions about it. The rules are the same ones the GymLog app uses, so the dashboard and the phone always agree:

* a partial rep counts as half a rep;
* an assisted set (negative weight) counts as your body weight that day minus the assist, and as 0 if there is no weigh-in at all;
* the estimated one-rep max is Epley: weight x (1 + reps / 30), for weighted sets of 1 to 30 full reps.
"""
import bisect
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import muscles as M

LB_PER_KG = 2.2046226218
PARTIAL_REP_VALUE = 0.5
EPOCH = date(1970, 1, 1)


def to_date(day: int) -> date:
    return EPOCH + timedelta(days=int(day))


def to_day(d: date) -> int:
    return (d - EPOCH).days


def iso(day: int) -> str:
    return to_date(day).isoformat()


def parse_day(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    try:
        return to_day(date.fromisoformat(text))
    except ValueError:
        return None


def week_start(day: int) -> int:
    """The Monday of the week that holds [day] (1970-01-01 was a Thursday)."""
    return day - ((day + 3) % 7)


def estimated_max(weight_lb: float, reps: int) -> float:
    return weight_lb if reps <= 1 else weight_lb * (1 + reps / 30.0)


def to_lb(weight: float, unit: str) -> float:
    return weight if str(unit).upper() == "LB" else weight * LB_PER_KG


class BodyWeightLookup:
    """Your body weight on any day from the weigh-ins there are: that day's, else the closest (an earlier day wins a tie)."""

    def __init__(self, lb_by_day: Dict[int, float]) -> None:
        self.days = sorted(lb_by_day)
        self.weights = [lb_by_day[d] for d in self.days]

    def lb_on(self, day: int) -> Optional[float]:
        if not self.days:
            return None
        i = bisect.bisect_left(self.days, day)
        if i < len(self.days) and self.days[i] == day:
            return self.weights[i]
        before, after = i - 1, i
        if before < 0:
            return self.weights[after]
        if after >= len(self.days):
            return self.weights[before]
        return self.weights[before] if day - self.days[before] <= self.days[after] - day else self.weights[after]


@dataclass
class SetRow:
    day: int
    workout_id: str
    gym_day: str
    exercise_id: str
    exercise: str
    variant_id: str
    variant: str
    muscle: str            # the main muscle, e.g. "Lats and mid back" ("Other" when the exercise is not in the map)
    region: str            # Chest, Back, Shoulders, Arms, Core or Legs ("Other" when unknown)
    set_number: int
    weight: float          # as typed (negative = assisted)
    unit: str
    weight_lb: float       # signed, in pounds
    reps: int
    partials: int
    volume_lb: float
    e1rm_lb: Optional[float]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.exercise_id, self.variant_id)


class Dataset:
    """One backup, parsed. [sets] only holds sets that were really done (some reps) in submitted workouts."""

    def __init__(self, backup: Dict[str, Any]) -> None:
        t = backup.get("tables", {})
        self.created_at: int = int(backup.get("createdAt") or 0)
        self.db_version: int = int(backup.get("dbVersion") or 0)
        exercises = {e["id"]: e for e in t.get("exercises", [])}
        variants = {v["id"]: v["name"] for v in t.get("variants", [])}
        days = {d["id"]: d["title"] for d in t.get("gym_days", [])}
        self.exercises = exercises
        self.variants = variants

        by_day: Dict[int, List[float]] = defaultdict(list)
        for b in t.get("body_weights", []):
            by_day[int(b["dateEpochDay"])].append(float(b["weightKg"]) * LB_PER_KG)
        self.body_lb: Dict[int, float] = {d: statistics.mean(v) for d, v in by_day.items()}
        self.body = BodyWeightLookup(self.body_lb)

        self.workouts: Dict[str, Dict[str, Any]] = {}
        for w in t.get("workouts", []):
            if str(w.get("status", "SUBMITTED")).upper() == "SUBMITTED":
                self.workouts[w["id"]] = w
        logged = {l["id"]: l for l in t.get("logged_exercises", []) if l["workoutId"] in self.workouts}

        self.sets: List[SetRow] = []
        for s in t.get("workout_sets", []):
            le = logged.get(s["loggedExerciseId"])
            if le is None:
                continue
            reps, partials = int(s.get("fullReps") or 0), int(s.get("partialReps") or 0)
            if reps + partials <= 0:
                continue
            w = self.workouts[le["workoutId"]]
            ex = exercises.get(le["exerciseId"], {"name": "(deleted exercise)", "muscleGroup": "Other"})
            day = int(w["dateEpochDay"])
            wl = to_lb(float(s["weight"]), s.get("unit", "LB"))
            if wl >= 0:
                load = wl
            else:
                bw = self.body.lb_on(day)
                load = 0.0 if bw is None else max(bw + wl, 0.0)
            volume = load * (reps + partials * PARTIAL_REP_VALUE)
            e1rm = estimated_max(wl, reps) if wl > 0 and 1 <= reps <= 30 else None
            muscle, region = M.primary(ex["name"], ex.get("muscleGroup") or "")
            gd = days.get(w.get("gymDayId")) or w.get("label") or ""
            self.sets.append(SetRow(
                day=day, workout_id=w["id"], gym_day=gd, exercise_id=le["exerciseId"], exercise=ex["name"],
                variant_id=le["variantId"], variant=variants.get(le["variantId"], ""), muscle=muscle or "Other", region=region or "Other",
                set_number=int(s["setNumber"]), weight=float(s["weight"]), unit=str(s.get("unit", "LB")).upper(), weight_lb=wl,
                reps=reps, partials=partials, volume_lb=volume, e1rm_lb=e1rm,
            ))
        self.sets.sort(key=lambda r: (r.day, r.workout_id, r.exercise, r.set_number))

        self.cardio: List[Dict[str, Any]] = [
            {"day": int(c["dateEpochDay"]), "activity": c.get("activity", ""), "minutes": round(int(c.get("durationSeconds") or 0) / 60.0, 1),
             "distance_m": float(c.get("distanceMeters") or 0), "avg_hr": int(c.get("avgHeartRate") or 0), "note": c.get("note", "")}
            for c in t.get("cardio_sessions", [])
        ]
        self.photos_by_day = sorted({int(p["dateEpochDay"]) for p in t.get("progress_photos", [])})
        # Workout dates (submitted) including ones whose sets were all blank, for consistency.
        self.workout_days: List[int] = sorted(int(w["dateEpochDay"]) for w in self.workouts.values())

    # ------------------------------------------------------------------ slicing

    def span(self) -> Tuple[Optional[int], Optional[int]]:
        days = [r.day for r in self.sets] + [c["day"] for c in self.cardio] + list(self.body_lb)
        return (min(days), max(days)) if days else (None, None)

    def sets_between(self, start: Optional[int], end: Optional[int]) -> List[SetRow]:
        return [r for r in self.sets if (start is None or r.day >= start) and (end is None or r.day <= end)]


def in_range(day: int, start: Optional[int], end: Optional[int]) -> bool:
    return (start is None or day >= start) and (end is None or day <= end)


# ---------------------------------------------------------------------- overview

def overview(ds: Dataset, start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    sets = ds.sets_between(start, end)
    wdays = sorted({r.day for r in sets})
    workouts = [d for d in ds.workout_days if in_range(d, start, end)]
    total_volume = sum(r.volume_lb for r in sets)
    first, last = (wdays[0], wdays[-1]) if wdays else (None, None)
    weeks = ((last - first) // 7 + 1) if first is not None else 0
    recent = _recent_weekly_counts(ds, last if last is not None else 0, 8)
    return {
        "workouts": len(workouts), "workout_days": len(wdays), "sets": len(sets), "reps": sum(r.reps for r in sets),
        "volume_lb": round(total_volume), "first_day": iso(first) if first is not None else None, "last_day": iso(last) if last is not None else None,
        "weeks": weeks, "avg_per_week": round(len(workouts) / weeks, 2) if weeks else 0,
        "recent_avg_per_week": round(sum(recent) / len(recent), 2) if recent else 0,
        "exercises": len({r.exercise_id for r in sets}),
        "current_streak_weeks": _streak_weeks(ds, last) if last is not None else 0,
        "longest_gap_days": _longest_gap(wdays),
    }


def _recent_weekly_counts(ds: Dataset, today: int, n: int) -> List[int]:
    start = week_start(today) - 7 * (n - 1)
    counts = defaultdict(int)
    for d in ds.workout_days:
        if d >= start:
            counts[week_start(d)] += 1
    return [counts.get(start + 7 * i, 0) for i in range(n)]


def _streak_weeks(ds: Dataset, today: int) -> int:
    weeks = {week_start(d) for d in ds.workout_days}
    w = week_start(today)
    n = 0
    while w in weeks:
        n += 1
        w -= 7
    return n


def _longest_gap(days: List[int]) -> int:
    return max((b - a for a, b in zip(days, days[1:])), default=0)


def weekly(ds: Dataset, start: Optional[int], end: Optional[int], by: str = "total") -> Dict[str, Any]:
    """Volume and sets per week (Monday start). by='muscle' splits each week by muscle group."""
    sets = ds.sets_between(start, end)
    if not sets:
        return {"weeks": [], "series": []}
    first, last = week_start(min(r.day for r in sets)), week_start(max(r.day for r in sets))
    weeks = list(range(first, last + 1, 7))
    index = {w: i for i, w in enumerate(weeks)}
    names = sorted({r.region for r in sets}) if by == "muscle" else ["All exercises"]
    vol = {n: [0.0] * len(weeks) for n in names}
    cnt = {n: [0] * len(weeks) for n in names}
    for r in sets:
        n = r.region if by == "muscle" else "All exercises"
        i = index[week_start(r.day)]
        vol[n][i] += r.volume_lb
        cnt[n][i] += 1
    wk = defaultdict(set)
    for d in ds.workout_days:
        if first <= week_start(d) <= last and in_range(d, start, end):
            wk[week_start(d)].add(d)
    series = [{"name": n, "volume_lb": [round(v) for v in vol[n]], "sets": cnt[n]} for n in names]
    # most volume first so the legend and the stack order are stable and meaningful
    series.sort(key=lambda s: -sum(s["volume_lb"]))
    return {"weeks": [iso(w) for w in weeks], "series": series, "workouts": [len(wk.get(w, ())) for w in weeks]}


def calendar(ds: Dataset, start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    """Per day: how many workouts and whether there was cardio, for the consistency calendar."""
    out: Dict[int, Dict[str, int]] = defaultdict(lambda: {"workouts": 0, "cardio": 0, "photo": 0})
    for d in ds.workout_days:
        if in_range(d, start, end):
            out[d]["workouts"] += 1
    for c in ds.cardio:
        if in_range(c["day"], start, end):
            out[c["day"]]["cardio"] += 1
    for d in ds.photos_by_day:
        if in_range(d, start, end):
            out[d]["photo"] = 1
    return {"days": [{"date": iso(d), **v} for d, v in sorted(out.items())]}


# ---------------------------------------------------------------------- exercises

def _sessions(rows: Iterable[SetRow]) -> List[Dict[str, Any]]:
    by_day: Dict[int, List[SetRow]] = defaultdict(list)
    for r in rows:
        by_day[r.day].append(r)
    out = []
    for day in sorted(by_day):
        rs = by_day[day]
        weighted = [r for r in rs if r.weight_lb > 0]
        e1 = [r.e1rm_lb for r in rs if r.e1rm_lb is not None]
        top = max(weighted, key=lambda r: (r.weight_lb, r.reps)) if weighted else None
        out.append({
            "date": iso(day), "sets": len(rs), "reps": sum(r.reps for r in rs), "volume_lb": round(sum(r.volume_lb for r in rs)),
            "top_weight_lb": round(top.weight_lb, 1) if top else None, "top_reps": top.reps if top else None,
            "best_e1rm_lb": round(max(e1), 1) if e1 else None,
            "avg_weight_lb": round(statistics.mean(r.weight_lb for r in weighted), 1) if weighted else None,
            "assisted": any(r.weight_lb < 0 for r in rs),
        })
    return out


def exercise_list(ds: Dataset, start: Optional[int], end: Optional[int], today: Optional[int] = None) -> Dict[str, Any]:
    groups: Dict[Tuple[str, str], List[SetRow]] = defaultdict(list)
    for r in ds.sets_between(start, end):
        groups[r.key].append(r)
    last_day = max((r.day for r in ds.sets), default=0)
    today = last_day if today is None else today
    items = []
    for key, rows in groups.items():
        sess = _sessions(rows)
        e1 = [(s["date"], s["best_e1rm_lb"]) for s in sess if s["best_e1rm_lb"] is not None]
        best = max((v for _, v in e1), default=None)
        best_date = next((d for d, v in e1 if v == best), None)
        latest = e1[-1][1] if e1 else None
        first_e = e1[0][1] if e1 else None
        items.append({
            "exercise_id": key[0], "variant_id": key[1], "name": rows[0].exercise, "variant": rows[0].variant, "muscle": rows[0].muscle, "region": rows[0].region,
            "sessions": len(sess), "sets": len(rows), "last": sess[-1]["date"], "first": sess[0]["date"],
            "best_e1rm_lb": best, "best_date": best_date, "latest_e1rm_lb": latest,
            "change_lb": round(latest - first_e, 1) if latest is not None and first_e is not None and len(e1) > 1 else None,
            "plateau_days": (today - to_day(date.fromisoformat(best_date))) if best_date and len(e1) >= 8 and (today - to_day(date.fromisoformat(sess[-1]["date"]))) <= 56 else None,
            "volume_lb": round(sum(r.volume_lb for r in rows)),
        })
    items.sort(key=lambda i: (-i["sessions"], i["name"], i["variant"]))
    return {"exercises": items}


def exercise_detail(ds: Dataset, exercise_id: str, variant_id: Optional[str], start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    rows = [r for r in ds.sets_between(start, end) if r.exercise_id == exercise_id and (variant_id in (None, "", "all") or r.variant_id == variant_id)]
    if not rows:
        return {"name": ds.exercises.get(exercise_id, {}).get("name", ""), "variants": [], "combined": [], "records": []}
    by_variant: Dict[str, List[SetRow]] = defaultdict(list)
    for r in rows:
        by_variant[r.variant or "Standard"].append(r)
    records = _personal_bests(rows)
    return {
        "name": rows[0].exercise, "muscle": rows[0].muscle,
        "combined": _sessions(rows),
        "variants": [{"name": n, "sessions": _sessions(rs)} for n, rs in sorted(by_variant.items(), key=lambda kv: -len(kv[1]))],
        "records": records,
        "recent_sets": [
            {"date": iso(r.day), "variant": r.variant, "set": r.set_number, "weight": r.weight, "unit": r.unit, "reps": r.reps, "partials": r.partials}
            for r in rows[-24:]
        ][::-1],
    }


def _personal_bests(rows: List[SetRow]) -> List[Dict[str, Any]]:
    """Heaviest weight lifted for each rep count (1 to 12), with the day it was done."""
    best: Dict[int, SetRow] = {}
    for r in rows:
        if r.weight_lb > 0 and 1 <= r.reps <= 12:
            cur = best.get(r.reps)
            if cur is None or r.weight_lb > cur.weight_lb:
                best[r.reps] = r
    return [{"reps": k, "weight_lb": round(v.weight_lb, 1), "date": iso(v.day)} for k, v in sorted(best.items())]


def personal_records(ds: Dataset, today: int, window_days: int = 30, limit: int = 8) -> List[Dict[str, Any]]:
    """Exercises where something in the last [window_days] beat everything before, biggest relative gain first (like the phone's home screen)."""
    start = today - window_days + 1
    groups: Dict[Tuple[str, str], List[SetRow]] = defaultdict(list)
    for r in ds.sets:
        if r.e1rm_lb is not None:
            groups[r.key].append(r)
    out = []
    for rows in groups.values():
        before = [r for r in rows if r.day < start]
        within = [r for r in rows if start <= r.day <= today]
        if not before or not within:
            continue
        old = max(r.e1rm_lb for r in before)
        top = max(within, key=lambda r: r.e1rm_lb)
        if top.e1rm_lb > old + 0.5:
            out.append({"name": top.exercise, "variant": top.variant, "date": iso(top.day), "weight_lb": round(top.weight_lb, 1), "reps": top.reps,
                        "e1rm_lb": round(top.e1rm_lb, 1), "gain_lb": round(top.e1rm_lb - old, 1), "gain_pct": round(100 * (top.e1rm_lb - old) / old, 1)})
    out.sort(key=lambda p: -p["gain_pct"])
    return out[:limit]


# ---------------------------------------------------------------------- muscles

def muscles(ds: Dataset, start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    """Work per muscle. Sets are counted the way the app does: a main muscle gets a whole set, a helper muscle half of one."""
    sets = ds.sets_between(start, end)
    weeks = max(1, ((max(r.day for r in sets) - min(r.day for r in sets)) // 7 + 1)) if sets else 1
    fractional: Dict[str, float] = defaultdict(float)
    reps_by_muscle: Dict[str, int] = defaultdict(int)
    for r in sets:
        ex = ds.exercises.get(r.exercise_id, {})
        weights = M.muscles_for(r.exercise, ex.get("muscleGroup") or "") or {"Other": 1.0}
        for m, w in weights.items():
            fractional[m] += w
            reps_by_muscle[m] += r.reps
    total = sum(fractional.values()) or 1.0
    by_muscle = [{"name": m, "region": M.REGION.get(m, "Other"), "sets": round(v, 1), "reps": reps_by_muscle[m],
                  "share_pct": round(100 * v / total, 1), "sets_per_week": round(v / weeks, 1)} for m, v in fractional.items()]
    by_muscle.sort(key=lambda x: -x["sets"])
    by_region: Dict[str, Dict[str, float]] = defaultdict(lambda: {"sets": 0.0, "volume_lb": 0.0})
    for m in by_muscle:
        by_region[m["region"]]["sets"] += m["sets"]
    for r in sets:
        by_region[r.region]["volume_lb"] += r.volume_lb
    regions = [{"name": k, "sets": round(v["sets"], 1), "volume_lb": round(v["volume_lb"]), "sets_per_week": round(v["sets"] / weeks, 1),
                "share_pct": round(100 * v["sets"] / total, 1)} for k, v in by_region.items()]
    regions.sort(key=lambda x: -x["sets"])

    def table(keyfn) -> List[Dict[str, Any]]:
        agg: Dict[str, Dict[str, float]] = defaultdict(lambda: {"sets": 0, "volume_lb": 0.0, "reps": 0})
        for r in sets:
            a = agg[keyfn(r)]
            a["sets"] += 1
            a["volume_lb"] += r.volume_lb
            a["reps"] += r.reps
        tot = sum(a["sets"] for a in agg.values()) or 1
        rows = [{"name": k or "Standard", "sets": int(v["sets"]), "volume_lb": round(v["volume_lb"]), "reps": int(v["reps"]),
                 "share_pct": round(100 * v["sets"] / tot, 1), "sets_per_week": round(v["sets"] / weeks, 1)} for k, v in agg.items()]
        rows.sort(key=lambda x: -x["sets"])
        return rows

    unmapped = sorted({r.exercise for r in sets if r.region == "Other"})
    return {"weeks": weeks, "by_region": regions, "by_muscle": by_muscle, "by_exercise": table(lambda r: r.exercise)[:25],
            "by_variant": table(lambda r: r.variant), "by_gym_day": table(lambda r: r.gym_day or "No template"), "unmapped": unmapped}


# ---------------------------------------------------------------------- body & cardio

def body(ds: Dataset, start: Optional[int], end: Optional[int], lift: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    days = sorted(d for d in ds.body_lb if in_range(d, start, end))
    pts = [(d, ds.body_lb[d]) for d in days]
    avg = []
    for i, (d, _) in enumerate(pts):
        window = [v for dd, v in pts[: i + 1] if d - dd < 7]
        avg.append(round(statistics.mean(window), 1))
    out: Dict[str, Any] = {
        "points": [{"date": iso(d), "lb": round(v, 1), "avg7": a} for (d, v), a in zip(pts, avg)],
        "photo_days": [iso(d) for d in ds.photos_by_day if in_range(d, start, end)],
    }
    if pts:
        out["latest_lb"] = round(pts[-1][1], 1)
        out["first_lb"] = round(pts[0][1], 1)
        out["min_lb"] = round(min(v for _, v in pts), 1)
        out["max_lb"] = round(max(v for _, v in pts), 1)
    if lift:
        rows = [r for r in ds.sets_between(start, end) if r.key == lift and r.e1rm_lb is not None]
        sess = _sessions(rows)
        rel = []
        for s in sess:
            if s["best_e1rm_lb"] is None:
                continue
            bw = ds.body.lb_on(to_day(date.fromisoformat(s["date"])))
            if bw:
                rel.append({"date": s["date"], "e1rm_lb": s["best_e1rm_lb"], "bodyweight_lb": round(bw, 1), "ratio": round(s["best_e1rm_lb"] / bw, 2)})
        out["relative_strength"] = rel
    return out


def cardio(ds: Dataset, start: Optional[int], end: Optional[int]) -> Dict[str, Any]:
    rows = [c for c in ds.cardio if in_range(c["day"], start, end)]
    if not rows:
        return {"sessions": 0, "weeks": [], "minutes": [], "by_activity": [], "recent": []}
    first, last = week_start(min(c["day"] for c in rows)), week_start(max(c["day"] for c in rows))
    weeks = list(range(first, last + 1, 7))
    minutes = [0.0] * len(weeks)
    sessions = [0] * len(weeks)
    for c in rows:
        i = (week_start(c["day"]) - first) // 7
        minutes[i] += c["minutes"]
        sessions[i] += 1
    act: Dict[str, Dict[str, float]] = defaultdict(lambda: {"sessions": 0, "minutes": 0.0, "distance_m": 0.0})
    for c in rows:
        a = act[c["activity"] or "Cardio"]
        a["sessions"] += 1
        a["minutes"] += c["minutes"]
        a["distance_m"] += c["distance_m"]
    by_activity = sorted(({"name": k, "sessions": int(v["sessions"]), "minutes": round(v["minutes"]), "distance_m": round(v["distance_m"])} for k, v in act.items()), key=lambda x: -x["minutes"])
    recent = sorted(rows, key=lambda c: -c["day"])[:15]
    return {
        "sessions": len(rows), "total_minutes": round(sum(c["minutes"] for c in rows)),
        "weeks": [iso(w) for w in weeks], "minutes": [round(m) for m in minutes], "sessions_per_week": sessions,
        "by_activity": by_activity,
        "recent": [{"date": iso(c["day"]), "activity": c["activity"], "minutes": c["minutes"], "distance_m": c["distance_m"], "avg_hr": c["avg_hr"], "note": c["note"]} for c in recent],
    }


# ---------------------------------------------------------------------- recommendations & data checks

def insights(ds: Dataset, start: Optional[int], end: Optional[int]) -> List[Dict[str, str]]:
    """Plain-language things worth a look. Each: {kind: good|note|warn, title, detail}. Short and few on purpose."""
    out: List[Dict[str, str]] = []
    sets = ds.sets_between(start, end)
    if not sets:
        return out
    today = max(r.day for r in sets)
    lst = exercise_list(ds, start, end, today)["exercises"]

    stalled = sorted((e for e in lst if e["plateau_days"] and e["plateau_days"] >= 42), key=lambda e: -e["plateau_days"])[:3]
    for e in stalled:
        name = e["name"] + (f" ({e['variant']})" if e["variant"] and e["variant"] != "Standard" else "")
        out.append({"kind": "note", "title": f"{name} has not set a new best in {e['plateau_days'] // 7} weeks",
                    "detail": f"Best estimated max {e['best_e1rm_lb']:.0f} lb on {e['best_date']}. Worth trying a small change: more reps, a deload week, or a different variant."})

    prs = personal_records(ds, today, 30, 3)
    for p in prs:
        out.append({"kind": "good", "title": f"New best: {p['name']}", "detail": f"{p['weight_lb']:.0f} lb x {p['reps']} on {p['date']} (estimated max up {p['gain_lb']:.0f} lb, {p['gain_pct']}%)."})

    m = muscles(ds, today - 27, today)
    regions = [r for r in m["by_region"] if r["name"] != "Other"]
    if len(regions) >= 3:
        top, low = regions[0], regions[-1]
        if low["sets"] > 0 and top["sets"] >= 3 * low["sets"]:
            out.append({"kind": "note", "title": f"{low['name']} gets much less work than {top['name']}",
                        "detail": f"Last 4 weeks: about {low['sets']:.0f} sets for {low['name']} against {top['sets']:.0f} for {top['name']}."})
        trained = {r["name"] for r in regions}
        missing = [g for g in M.REGIONS if g not in trained]
        if missing:
            out.append({"kind": "note", "title": f"Nothing for {', '.join(missing)} in the last 4 weeks", "detail": "No sets logged for that part of the body."})

    counts = _recent_weekly_counts(ds, today, 8)
    if len(counts) == 8 and sum(counts[:4]) > 0 and sum(counts[4:]) < 0.6 * sum(counts[:4]):
        out.append({"kind": "warn", "title": "Training has dropped off", "detail": f"{sum(counts[4:])} workouts in the last 4 weeks, {sum(counts[:4])} in the 4 before."})
    elif len(counts) == 8 and sum(counts) >= 28:
        out.append({"kind": "good", "title": "Steady training", "detail": f"{sum(counts)} workouts in the last 8 weeks."})

    gap = today - (ds.workout_days[-1] if ds.workout_days else today)
    if gap >= 10:
        out.append({"kind": "warn", "title": f"No workout logged for {gap} days", "detail": "The last backup may be missing recent sessions, or it has been a while."})
    return out


def data_checks(ds: Dataset) -> List[Dict[str, Any]]:
    """Possible typing mistakes: a weight far from what that exercise usually uses, or the same exercise logged in two units."""
    out: List[Dict[str, Any]] = []
    by_ex: Dict[Tuple[str, str], List[SetRow]] = defaultdict(list)
    for r in ds.sets:
        if r.weight_lb > 0:
            by_ex[r.key].append(r)
    for rows in by_ex.values():
        if len(rows) < 12:
            continue
        med = statistics.median(r.weight_lb for r in rows)
        for r in rows:
            if r.weight_lb > 3 * med or r.weight_lb < med / 3:
                out.append({"date": iso(r.day), "exercise": r.exercise, "variant": r.variant, "weight": r.weight, "unit": r.unit, "reps": r.reps,
                            "why": f"far from the usual {med:.0f} lb for this exercise"})
        if len({r.unit for r in rows}) > 1:
            out.append({"date": iso(rows[-1].day), "exercise": rows[0].exercise, "variant": rows[0].variant, "weight": None, "unit": "mixed", "reps": None,
                        "why": "logged in both lb and kg"})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:40]
