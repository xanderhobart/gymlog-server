#!/usr/bin/env python3
"""Developer tool: turn a copy of the phone's gymlog.db into a backup zip in the same format the GymLog app makes (no photos),
so the server and dashboard can be tried on real data. Usage: python db_to_backup.py gymlog.db out.zip"""
import json
import sqlite3
import sys
import time
import zipfile

TABLES = ["exercises", "variants", "gym_days", "planned_exercises", "planned_sets", "workouts", "logged_exercises", "workout_sets",
          "missed_exercises", "body_weights", "progress_photos", "app_settings", "cardio_sessions", "splits"]


def main(db_path: str, out: str) -> None:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    tables = {}
    for t in TABLES:
        try:
            tables[t] = [dict(r) for r in c.execute(f"SELECT * FROM {t}")]
        except sqlite3.OperationalError:
            tables[t] = []
    header = {"format": 1, "app": "GymLog", "dbVersion": c.execute("PRAGMA user_version").fetchone()[0], "createdAt": int(time.time() * 1000),
              "tables": tables, "preferences": {}, "photos": []}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("backup.json", json.dumps(header))
    print("wrote", out, {k: len(v) for k, v in tables.items() if v})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
