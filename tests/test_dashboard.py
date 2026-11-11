import io
import json
import zipfile
from datetime import date

import pytest

from app import analytics as A
from app import muscles as M
from tests.test_server import ADMIN, client, make_user, settings  # noqa: F401  (fixtures)

KG = 2.2046226218


def day(y, m, d):
    return A.to_day(date(y, m, d))


def backup_dict():
    """A tiny backup: bench press (lb) and assisted pull-ups, 3 workouts over 2 weeks, a weigh-in, one cardio session."""
    ex_bench, ex_pull, ex_rdl = "e1", "e2", "e3"
    t = {
        "exercises": [
            {"id": ex_bench, "name": "Bench Press", "muscleGroup": "Other", "unit": "LB", "assisted": 0, "bodyweight": 0},
            {"id": ex_pull, "name": "Pull-Up", "muscleGroup": "Other", "unit": "LB", "assisted": 1, "bodyweight": 1},
            {"id": ex_rdl, "name": "Mystery Machine", "muscleGroup": "Other", "unit": "KG", "assisted": 0, "bodyweight": 0},
        ],
        "variants": [{"id": "v1", "name": "Barbell"}, {"id": "v2", "name": "Standard"}],
        "gym_days": [{"id": "g1", "title": "Day A", "sortOrder": 0}],
        "workouts": [
            {"id": "w1", "gymDayId": "g1", "dateEpochDay": day(2026, 3, 2), "status": "SUBMITTED"},      # a Monday
            {"id": "w2", "gymDayId": "g1", "dateEpochDay": day(2026, 3, 8), "status": "SUBMITTED"},      # the Sunday of that week
            {"id": "w3", "gymDayId": "g1", "dateEpochDay": day(2026, 3, 9), "status": "SUBMITTED"},      # next Monday
            {"id": "w4", "gymDayId": "g1", "dateEpochDay": day(2026, 3, 10), "status": "IN_PROGRESS"},   # not counted
        ],
        "logged_exercises": [
            {"id": "l1", "workoutId": "w1", "exerciseId": ex_bench, "variantId": "v1"},
            {"id": "l2", "workoutId": "w1", "exerciseId": ex_pull, "variantId": "v2"},
            {"id": "l3", "workoutId": "w2", "exerciseId": ex_bench, "variantId": "v1"},
            {"id": "l4", "workoutId": "w3", "exerciseId": ex_bench, "variantId": "v1"},
            {"id": "l5", "workoutId": "w4", "exerciseId": ex_bench, "variantId": "v1"},
            {"id": "l6", "workoutId": "w3", "exerciseId": ex_rdl, "variantId": "v2"},
        ],
        "workout_sets": [
            {"id": "s1", "loggedExerciseId": "l1", "setNumber": 1, "weight": 100, "unit": "LB", "fullReps": 10, "partialReps": 0},
            {"id": "s2", "loggedExerciseId": "l1", "setNumber": 2, "weight": 100, "unit": "LB", "fullReps": 8, "partialReps": 2},     # 9 reps counted
            {"id": "s3", "loggedExerciseId": "l1", "setNumber": 3, "weight": 100, "unit": "LB", "fullReps": 0, "partialReps": 0},     # blank: ignored
            {"id": "s4", "loggedExerciseId": "l2", "setNumber": 1, "weight": -40, "unit": "LB", "fullReps": 5, "partialReps": 0},     # assisted
            {"id": "s5", "loggedExerciseId": "l3", "setNumber": 1, "weight": 110, "unit": "LB", "fullReps": 5, "partialReps": 0},
            {"id": "s6", "loggedExerciseId": "l4", "setNumber": 1, "weight": 140, "unit": "LB", "fullReps": 1, "partialReps": 0},
            {"id": "s7", "loggedExerciseId": "l5", "setNumber": 1, "weight": 500, "unit": "LB", "fullReps": 5, "partialReps": 0},     # in-progress workout
            {"id": "s8", "loggedExerciseId": "l6", "setNumber": 1, "weight": 50, "unit": "KG", "fullReps": 10, "partialReps": 0},
        ],
        "body_weights": [{"id": "b1", "dateEpochDay": day(2026, 3, 1), "weightKg": 150 / KG, "source": "manual"}],
        "cardio_sessions": [{"id": "c1", "dateEpochDay": day(2026, 3, 4), "activity": "Run", "durationSeconds": 1800, "distanceMeters": 5000, "avgHeartRate": 150, "note": ""}],
        "progress_photos": [{"id": "p1", "dateEpochDay": day(2026, 3, 1), "takenMillis": 1, "source": "Camera"}],
    }
    return {"format": 1, "app": "GymLog", "dbVersion": 21, "createdAt": 1, "tables": t, "preferences": {}, "photos": []}


def zipped(backup=None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("backup.json", json.dumps(backup or backup_dict()))
    return out.getvalue()


# ------------------------------------------------------------------------------------------------ the numbers

def test_rules_match_the_app():
    ds = A.Dataset(backup_dict())
    by_id = {}
    for r in ds.sets:
        by_id.setdefault(r.exercise, []).append(r)
    bench = by_id["Bench Press"]
    assert [r.volume_lb for r in bench if r.day == day(2026, 3, 2)] == [1000.0, 100 * 9.0]    # a partial rep counts half
    assert len(ds.sets) == 6                                    # the blank set and the in-progress workout are not counted
    assert all(r.weight_lb != 500 for r in ds.sets)


def test_assisted_sets_count_against_body_weight():
    ds = A.Dataset(backup_dict())
    pull = [r for r in ds.sets if r.exercise == "Pull-Up"][0]
    assert pull.volume_lb == pytest.approx((150 - 40) * 5)
    assert pull.e1rm_lb is None                                 # assisted sets have no estimated max
    no_weighin = backup_dict()
    no_weighin["tables"]["body_weights"] = []
    assert [r for r in A.Dataset(no_weighin).sets if r.exercise == "Pull-Up"][0].volume_lb == 0


def test_estimated_max_is_epley():
    assert A.estimated_max(100, 1) == 100
    assert A.estimated_max(100, 10) == pytest.approx(133.33, abs=0.01)
    ds = A.Dataset(backup_dict())
    top = max(r.e1rm_lb for r in ds.sets if r.exercise == "Bench Press")
    assert top == pytest.approx(max(110 * (1 + 5 / 30), 140.0, 100 * (1 + 10 / 30)))   # 140 for the single


def test_kilograms_are_converted():
    ds = A.Dataset(backup_dict())
    kg = [r for r in ds.sets if r.exercise == "Mystery Machine"][0]
    assert kg.weight_lb == pytest.approx(50 * KG)
    assert kg.unit == "KG"


def test_weeks_start_on_monday():
    assert A.to_date(A.week_start(day(2026, 3, 8))) == date(2026, 3, 2)      # Sunday belongs to the week before
    assert A.to_date(A.week_start(day(2026, 3, 9))) == date(2026, 3, 9)
    w = A.weekly(A.Dataset(backup_dict()), None, None, "total")
    assert w["weeks"] == ["2026-03-02", "2026-03-09"]
    assert w["workouts"] == [2, 1]


def test_date_window_applies_everywhere():
    ds = A.Dataset(backup_dict())
    s = A.overview(ds, day(2026, 3, 8), None)
    assert s["workouts"] == 2 and s["first_day"] == "2026-03-08"
    assert A.exercise_list(ds, day(2026, 3, 9), None)["exercises"][0]["sessions"] == 1


def test_muscle_map_reads_names_first():
    assert M.muscles_for("Bench Press", "Other") == {"Chest": 1.0, "Front shoulders": 0.5, "Triceps": 0.5}
    assert M.primary("Hack Squat", "Other") == ("Quads", "Legs")
    assert M.muscles_for("Mystery", "Back") == {"Lats and mid back": 1.0}
    assert M.muscles_for("Mystery", "Other") == {}


def test_personal_records():
    ds = A.Dataset(backup_dict())
    prs = A.personal_records(ds, day(2026, 3, 9), window_days=3)
    assert prs and prs[0]["name"] == "Bench Press"


def test_exercise_detail_has_sessions_and_variants():
    ds = A.Dataset(backup_dict())
    d = A.exercise_detail(ds, "e1", "all", None, None)
    assert d["name"] == "Bench Press" and len(d["combined"]) == 3
    assert d["variants"][0]["name"] == "Barbell"
    assert d["combined"][0]["sets"] == 2 and d["combined"][0]["best_e1rm_lb"] > 100
    assert any(r["reps"] == 10 and r["weight_lb"] == 100 for r in d["records"])


def test_cardio_and_body():
    ds = A.Dataset(backup_dict())
    c = A.cardio(ds, None, None)
    assert c["sessions"] == 1 and c["total_minutes"] == 30
    b = A.body(ds, None, None)
    assert b["latest_lb"] == pytest.approx(150, abs=0.1) and b["points"][0]["avg7"] == pytest.approx(150, abs=0.1)


def test_a_backup_with_nothing_in_it_does_not_crash():
    empty = {"format": 1, "app": "GymLog", "dbVersion": 21, "createdAt": 0, "tables": {}, "preferences": {}, "photos": []}
    ds = A.Dataset(empty)
    assert A.overview(ds, None, None)["workouts"] == 0
    assert A.weekly(ds, None, None)["weeks"] == []
    assert A.exercise_list(ds, None, None)["exercises"] == []
    assert A.muscles(ds, None, None)["by_region"] == []
    assert A.insights(ds, None, None) == []
    assert A.cardio(ds, None, None)["sessions"] == 0


# ------------------------------------------------------------------------------------------------ the web side

def upload(client, headers, data=None):
    r = client.post("/api/backups", content=data or zipped(), headers={**headers, "Content-Type": "application/zip"})
    assert r.status_code in (200, 201), r.text
    return r.json()["backup"]


def test_dashboard_data_needs_a_token(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    settings.failed_logins_per_minute = 100            # this test makes many deliberate bad requests
    client = TestClient(create_app(settings))
    for path in ("/overview", "/exercises", "/muscles", "/body", "/cardio", "/calendar", "/weekly", "/backups", "/checks", "/export"):
        assert client.get("/api/dash" + path).status_code == 401, path
    assert client.get("/api/dash/overview", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_page_itself_is_public_but_holds_no_data(client):
    r = client.get("/dashboard/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard/"


def test_no_backup_yet_gives_a_clear_message(client):
    _, h = make_user(client, "Sam")
    r = client.get("/api/dash/overview", headers=h)
    assert r.status_code == 404 and "no backups" in r.json()["detail"].lower()


def test_overview_reads_the_users_latest_backup(client):
    _, h = make_user(client, "Sam")
    upload(client, h)
    o = client.get("/api/dash/overview", headers=h).json()
    assert o["summary"]["workouts"] == 3 and o["summary"]["sets"] == 6
    assert o["weekly"]["weeks"] == ["2026-03-02", "2026-03-09"]
    assert o["body"]["latest_lb"] == pytest.approx(150, abs=0.1)


def test_users_only_ever_see_their_own_data(client):
    _, sam = make_user(client, "Sam")
    _, alex = make_user(client, "Alex")
    big = backup_dict()
    big["tables"]["workouts"] = big["tables"]["workouts"][:1]
    big["tables"]["logged_exercises"] = big["tables"]["logged_exercises"][:2]
    upload(client, sam)                      # Sam: 3 workouts
    upload(client, alex, zipped(big))        # Alex: 1 workout
    assert client.get("/api/dash/overview", headers=sam).json()["summary"]["workouts"] == 3
    assert client.get("/api/dash/overview", headers=alex).json()["summary"]["workouts"] == 1
    sam_backup = client.get("/api/backups", headers=sam).json()["backups"][0]["id"]
    # Alex asking for Sam's backup by id is told it does not exist.
    assert client.get("/api/dash/overview?backup=" + sam_backup, headers=alex).status_code == 404


def test_date_window_over_http(client):
    _, h = make_user(client, "Sam")
    upload(client, h)
    o = client.get("/api/dash/overview?from=2026-03-08&to=2026-03-09", headers=h).json()
    assert o["summary"]["workouts"] == 2
    assert client.get("/api/dash/overview?from=banana", headers=h).status_code == 400


def test_exercises_and_detail(client):
    _, h = make_user(client, "Sam")
    upload(client, h)
    lst = client.get("/api/dash/exercises", headers=h).json()["exercises"]
    bench = next(e for e in lst if e["name"] == "Bench Press")
    d = client.get("/api/dash/exercise", params={"exercise": bench["exercise_id"]}, headers=h).json()
    assert d["name"] == "Bench Press" and len(d["combined"]) == 3


def test_backup_history_shows_what_changed(client):
    _, h = make_user(client, "Sam")
    first = backup_dict()
    first["tables"]["workouts"] = first["tables"]["workouts"][:2]
    upload(client, h, zipped(first))
    upload(client, h)
    r = client.get("/api/dash/backups", headers=h).json()
    assert r["count"] == 2 and r["used_bytes"] > 0 and r["quota_bytes"] > 0
    newest = r["backups"][0]
    assert newest["delta"]["workouts"] == 2           # 4 workouts now, 2 before
    assert r["backups"][1]["delta"] is None


def test_csv_export(client):
    _, h = make_user(client, "Sam")
    upload(client, h)
    r = client.get("/api/dash/export?table=sets&format=csv", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig").splitlines()
    assert text[0].startswith("Date,Workout day,Exercise") and len(text) == 1 + 6
    assert "2026-03-02" in text[1]
    assert client.get("/api/dash/export?table=nonsense&format=csv", headers=h).status_code == 400
    assert client.get("/api/dash/export?table=sets&format=pdf", headers=h).status_code == 400


def test_excel_export_has_a_sheet_for_each_table(client):
    _, h = make_user(client, "Sam")
    upload(client, h)
    r = client.get("/api/dash/export?table=all&format=xlsx", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        wb = z.read("xl/workbook.xml").decode()
    for sheet in ("Every set", "Workouts", "Exercises", "Weekly volume", "Body weight", "Cardio", "Backup history"):
        assert sheet in wb, sheet
    zipped_csv = client.get("/api/dash/export?table=all&format=csv", headers=h)
    with zipfile.ZipFile(io.BytesIO(zipped_csv.content)) as z:
        assert "gymlog-sets.csv" in z.namelist()


def test_csv_text_that_looks_like_a_formula_is_defused(client):
    data = backup_dict()
    data["tables"]["exercises"][0]["name"] = '=HYPERLINK("http://example.com","click")'
    _, h = make_user(client, "Sam")
    upload(client, h, zipped(data))
    r = client.get("/api/dash/export?table=sets&format=csv", headers=h)
    assert r.content.startswith(b"\xef\xbb\xbf")      # the BOM that makes Excel read accents properly
    body = r.content.decode("utf-8-sig")
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body
