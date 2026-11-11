"""Tables for download: the same data the dashboard shows, as CSV (one table) or Excel (a sheet per table). Weights are in pounds."""
import csv
import io
import zipfile
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import xlsxwriter

from . import analytics as A

Table = Tuple[List[str], List[List[Any]]]

TABLE_TITLES = {
    "sets": "Every set",
    "workouts": "Workouts",
    "exercises": "Exercises",
    "weekly": "Weekly volume",
    "bodyweight": "Body weight",
    "cardio": "Cardio",
    "backups": "Backup history",
}


def build_tables(ds: A.Dataset, start: Optional[int], end: Optional[int]) -> Dict[str, Table]:
    sets = ds.sets_between(start, end)
    tables: Dict[str, Table] = {}

    tables["sets"] = (
        ["Date", "Workout day", "Exercise", "Variant", "Main muscle", "Region", "Set", "Weight (lb)", "Weight as typed", "Unit", "Full reps", "Partial reps",
         "Volume (lb)", "Estimated max (lb)"],
        [[A.to_date(r.day), r.gym_day, r.exercise, r.variant, r.muscle, r.region, r.set_number, round(r.weight_lb, 2), r.weight, r.unit, r.reps, r.partials,
          round(r.volume_lb, 1), round(r.e1rm_lb, 1) if r.e1rm_lb is not None else None] for r in sets],
    )

    by_workout: Dict[str, List[A.SetRow]] = defaultdict(list)
    for r in sets:
        by_workout[r.workout_id].append(r)
    wrows = []
    for rs in sorted(by_workout.values(), key=lambda x: (x[0].day, x[0].workout_id)):
        wrows.append([A.to_date(rs[0].day), rs[0].gym_day, len({r.exercise_id for r in rs}), len(rs), sum(r.reps for r in rs), round(sum(r.volume_lb for r in rs))])
    tables["workouts"] = (["Date", "Workout day", "Exercises", "Sets", "Reps", "Volume (lb)"], wrows)

    ex = A.exercise_list(ds, start, end)["exercises"]
    tables["exercises"] = (
        ["Exercise", "Variant", "Main muscle", "Sessions", "Sets", "First", "Last", "Best estimated max (lb)", "Best on", "Latest estimated max (lb)", "Volume (lb)"],
        [[e["name"], e["variant"], e["muscle"], e["sessions"], e["sets"], date.fromisoformat(e["first"]), date.fromisoformat(e["last"]), e["best_e1rm_lb"],
          date.fromisoformat(e["best_date"]) if e["best_date"] else None, e["latest_e1rm_lb"], e["volume_lb"]] for e in ex],
    )

    wk = A.weekly(ds, start, end, "muscle")
    groups = [s["name"] for s in wk["series"]]
    rows = []
    for i, w in enumerate(wk["weeks"]):
        vols = [s["volume_lb"][i] for s in wk["series"]]
        rows.append([date.fromisoformat(w), wk["workouts"][i], sum(vols)] + vols)
    tables["weekly"] = (["Week starting (Monday)", "Workouts", "Total volume (lb)"] + [f"{g} volume (lb)" for g in groups], rows)

    b = A.body(ds, start, end)
    tables["bodyweight"] = (["Date", "Weight (lb)", "7-day average (lb)"], [[date.fromisoformat(p["date"]), p["lb"], p["avg7"]] for p in b["points"]])

    c = [x for x in ds.cardio if A.in_range(x["day"], start, end)]
    tables["cardio"] = (
        ["Date", "Activity", "Minutes", "Distance (miles)", "Average heart rate", "Note"],
        [[A.to_date(x["day"]), x["activity"], x["minutes"], round(x["distance_m"] / 1609.344, 2) if x["distance_m"] else None, x["avg_hr"] or None, x["note"]]
         for x in sorted(c, key=lambda x: x["day"])],
    )
    return tables


def _cell(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if v is None:
        return ""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v            # Excel would run text starting like this as a formula
    return v


def to_csv(table: Table) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(table[0])
    for row in table[1]:
        w.writerow([_cell(v) for v in row])
    return ("﻿" + out.getvalue()).encode("utf-8")      # BOM so Excel reads accents and symbols correctly


def to_csv_zip(tables: Dict[str, Table]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, table in tables.items():
            z.writestr(f"gymlog-{name}.csv", to_csv(table))
    return buf.getvalue()


def to_xlsx(tables: Dict[str, Table], extra_sheets: Optional[Dict[str, Table]] = None) -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "default_date_format": "yyyy-mm-dd"})
    head = wb.add_format({"bold": True, "bg_color": "#EEEEEE", "bottom": 1})
    datef = wb.add_format({"num_format": "yyyy-mm-dd"})
    for name, table in {**tables, **(extra_sheets or {})}.items():
        title = TABLE_TITLES.get(name, name)[:31]
        ws = wb.add_worksheet(title)
        headers, rows = table
        for c, h in enumerate(headers):
            ws.write(0, c, h, head)
        widths = [max(10, len(str(h)) + 2) for h in headers]
        for r, row in enumerate(rows, start=1):
            for c, v in enumerate(row):
                if v is None:
                    continue
                if isinstance(v, date):
                    ws.write_datetime(r, c, __import__("datetime").datetime(v.year, v.month, v.day), datef)
                elif isinstance(v, bool):
                    ws.write_boolean(r, c, v)
                elif isinstance(v, (int, float)):
                    ws.write_number(r, c, v)
                else:
                    ws.write_string(r, c, str(v))
                    widths[c] = min(40, max(widths[c], len(str(v)) + 2))
        for c, w in enumerate(widths):
            ws.set_column(c, c, w)
        ws.freeze_panes(1, 0)
        if rows:
            ws.autofilter(0, 0, len(rows), len(headers) - 1)
    wb.close()
    return buf.getvalue()
