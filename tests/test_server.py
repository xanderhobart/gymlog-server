import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.service import Service


def backup_zip(workouts: int = 3, extra: str = "", app_name: str = "GymLog") -> bytes:
    """A small but real-looking GymLog backup file."""
    header = {
        "format": 1, "app": app_name, "dbVersion": 21, "createdAt": 1,
        "tables": {"workouts": [{"id": str(i)} for i in range(workouts)], "workout_sets": [{"id": "s"}] * 5, "body_weights": [{"id": "w"}]},
        "preferences": {}, "photos": ["a.jpg", "b.jpg"], "note": extra,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("backup.json", json.dumps(header))
        z.writestr("photos/a.jpg", b"jpeg-bytes")
    return out.getvalue()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.admin_token = "admin-secret-for-tests"
    s.keep_backups = 3
    s.max_upload_bytes = 1024 * 1024
    s.user_quota_bytes = 10 * 1024 * 1024
    s.failed_logins_per_minute = 5
    return s


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


ADMIN = {"Authorization": "Bearer admin-secret-for-tests"}


def make_user(client: TestClient, name: str):
    r = client.post("/admin/users", json={"name": name}, headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()
    return body["user"], {"Authorization": "Bearer " + body["token"]}


# ---------------------------------------------------------------------------------------------- who you are

def test_health_is_open(client):
    assert client.get("/health").json()["status"] == "ok"


def test_every_api_call_needs_a_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/backups").status_code == 401
    assert client.post("/api/backups", content=b"x").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_each_user_has_their_own_identity(client):
    sam, sam_h = make_user(client, "Sam")
    alex, alex_h = make_user(client, "Alex")
    assert sam["id"] != alex["id"]
    assert client.get("/api/me", headers=sam_h).json()["name"] == "Sam"
    assert client.get("/api/me", headers=alex_h).json()["name"] == "Alex"


def test_names_are_unique_ignoring_capitals_and_spaces(client):
    make_user(client, "Sam")
    r = client.post("/admin/users", json={"name": "  sAm "}, headers=ADMIN)
    assert r.status_code == 409
    assert client.post("/admin/users", json={"name": ""}, headers=ADMIN).status_code == 400
    assert client.post("/admin/users", json={"name": "x" * 41}, headers=ADMIN).status_code == 400


def test_a_users_token_is_not_stored_in_the_clear(client, settings):
    _, headers = make_user(client, "Sam")
    token = headers["Authorization"].split()[1]
    for path in settings.data_dir.rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path


# ---------------------------------------------------------------------------------------------- keeping users apart

def test_users_cannot_see_each_others_backups(client):
    _, sam = make_user(client, "Sam")
    _, alex = make_user(client, "Alex")
    up = client.post("/api/backups", content=backup_zip(7), headers=sam)
    assert up.status_code == 201
    sam_backup = up.json()["backup"]["id"]

    assert client.get("/api/backups", headers=sam).json()["backups"][0]["id"] == sam_backup
    assert client.get("/api/backups", headers=alex).json()["backups"] == []
    assert client.get(f"/api/backups/{sam_backup}", headers=alex).status_code == 404
    assert client.delete(f"/api/backups/{sam_backup}", headers=alex).status_code == 404
    assert client.get("/api/backups/latest", headers=alex).status_code == 404
    # ...and Sam's is still there
    assert client.get(f"/api/backups/{sam_backup}", headers=sam).status_code == 200


def test_files_are_kept_in_each_users_own_folder(client, settings):
    sam_user, sam = make_user(client, "Sam")
    alex_user, alex = make_user(client, "Alex")
    client.post("/api/backups", content=backup_zip(1), headers=sam)
    client.post("/api/backups", content=backup_zip(2), headers=alex)
    assert len(list((settings.data_dir / "users" / sam_user["id"] / "backups").glob("*.zip"))) == 1
    assert len(list((settings.data_dir / "users" / alex_user["id"] / "backups").glob("*.zip"))) == 1


def test_odd_backup_ids_cannot_reach_other_files(client):
    _, sam = make_user(client, "Sam")
    for bad in ("..", "..%2F..%2Fetc%2Fpasswd", "../../x", "%2e%2e", "not-a-hex-id", "0" * 31, "g" * 32):
        r = client.get(f"/api/backups/{bad}", headers=sam)
        assert r.status_code in (404, 405, 307), (bad, r.status_code)


def test_the_admin_token_is_not_a_user_token_and_users_are_not_admins(client):
    _, sam = make_user(client, "Sam")
    assert client.get("/api/me", headers=ADMIN).status_code == 401
    assert client.get("/admin/users", headers=sam).status_code == 401
    assert client.post("/admin/users", json={"name": "Eve"}, headers=sam).status_code == 401
    assert client.get("/admin/users").status_code == 401


def test_the_admin_sees_who_exists_but_not_their_data(client):
    sam_user, sam = make_user(client, "Sam")
    client.post("/api/backups", content=backup_zip(1), headers=sam)
    listing = client.get("/admin/users", headers=ADMIN).json()["users"]
    assert listing[0]["name"] == "Sam" and listing[0]["backups"] == 1
    assert "token" not in json.dumps(listing)
    # there is no admin address that returns a user's backup
    assert client.get(f"/admin/users/{sam_user['id']}/backups", headers=ADMIN).status_code in (404, 405)


# ---------------------------------------------------------------------------------------------- backups

def test_upload_list_and_download_round_trip(client):
    _, sam = make_user(client, "Sam")
    data = backup_zip(4)
    r = client.post("/api/backups", content=data, headers={**sam, "X-Device-Name": "Sam's S22"})
    assert r.status_code == 201
    b = r.json()["backup"]
    assert b["summary"]["workouts"] == 4 and b["summary"]["sets"] == 5 and b["summary"]["photos"] == 2
    assert b["dbVersion"] == 21 and b["device"] == "Sam's S22" and b["size"] == len(data)
    latest = client.get("/api/backups/latest", headers=sam)
    assert latest.status_code == 200 and latest.content == data
    assert client.get(f"/api/backups/{b['id']}", headers=sam).content == data


def test_uploading_the_same_file_again_changes_nothing(client):
    _, sam = make_user(client, "Sam")
    data = backup_zip(2)
    first = client.post("/api/backups", content=data, headers=sam)
    again = client.post("/api/backups", content=data, headers=sam)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["created"] is False and again.json()["backup"]["id"] == first.json()["backup"]["id"]
    assert len(client.get("/api/backups", headers=sam).json()["backups"]) == 1


def test_only_gymlog_backups_are_accepted(client):
    _, sam = make_user(client, "Sam")
    assert client.post("/api/backups", content=b"just text", headers=sam).status_code == 400
    assert client.post("/api/backups", content=b"", headers=sam).status_code == 400
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("hello.txt", "hi")
    assert client.post("/api/backups", content=plain.getvalue(), headers=sam).status_code == 400
    assert client.post("/api/backups", content=backup_zip(app_name="Other"), headers=sam).status_code == 400
    assert client.get("/api/backups", headers=sam).json()["backups"] == []


def test_big_uploads_are_refused(client, settings):
    _, sam = make_user(client, "Sam")
    r = client.post("/api/backups", content=b"0" * (settings.max_upload_bytes + 10), headers=sam)
    assert r.status_code == 413


def test_only_the_newest_few_backups_are_kept(client, settings):
    sam_user, sam = make_user(client, "Sam")
    for i in range(5):
        assert client.post("/api/backups", content=backup_zip(i + 1), headers=sam).status_code == 201
    backups = client.get("/api/backups", headers=sam).json()["backups"]
    assert len(backups) == settings.keep_backups
    assert [b["summary"]["workouts"] for b in backups] == [5, 4, 3]          # newest first
    assert len(list((settings.data_dir / "users" / sam_user["id"] / "backups").glob("*.zip"))) == settings.keep_backups


def test_a_user_can_delete_their_own_backup(client):
    _, sam = make_user(client, "Sam")
    b = client.post("/api/backups", content=backup_zip(), headers=sam).json()["backup"]
    assert client.delete(f"/api/backups/{b['id']}", headers=sam).status_code == 204
    assert client.get(f"/api/backups/{b['id']}", headers=sam).status_code == 404


def test_the_storage_allowance_is_per_user(client, settings):
    settings.user_quota_bytes = len(backup_zip(1, "x" * 2000)) * 2 + 50
    service: Service = client.app.state.service
    _, sam = make_user(client, "Sam")
    _, alex = make_user(client, "Alex")
    sizes = []
    for i in range(4):
        r = client.post("/api/backups", content=backup_zip(1, "pad%d" % i + "x" * 2000), headers=sam)
        assert r.status_code == 201, r.text
        sizes.append(r.json()["backup"]["size"])
    # old ones were dropped to stay inside the allowance, the newest is kept
    kept = client.get("/api/backups", headers=sam).json()["backups"]
    assert sum(b["size"] for b in kept) <= settings.user_quota_bytes and len(kept) >= 1
    # Alex has a full allowance of their own
    assert client.post("/api/backups", content=backup_zip(1, "y" * 2000), headers=alex).status_code == 201


def test_one_file_over_the_whole_allowance_is_refused(client, settings):
    settings.user_quota_bytes = 100
    _, sam = make_user(client, "Sam")
    assert client.post("/api/backups", content=backup_zip(1), headers=sam).status_code == 507


# ---------------------------------------------------------------------------------------------- looking after users

def test_a_switched_off_user_is_locked_out_and_can_be_switched_back_on(client):
    user, sam = make_user(client, "Sam")
    assert client.post(f"/admin/users/{user['id']}/disable", headers=ADMIN).json()["user"]["disabled"] is True
    assert client.get("/api/me", headers=sam).status_code == 403
    assert client.post("/api/backups", content=backup_zip(), headers=sam).status_code == 403
    client.post(f"/admin/users/{user['id']}/enable", headers=ADMIN)
    assert client.get("/api/me", headers=sam).status_code == 200


def test_resetting_a_token_ends_the_old_one(client):
    user, old = make_user(client, "Sam")
    r = client.post(f"/admin/users/{user['id']}/reset-token", headers=ADMIN)
    new = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/api/me", headers=old).status_code == 401
    assert client.get("/api/me", headers=new).json()["name"] == "Sam"


def test_deleting_a_user_removes_their_files_but_not_anyone_elses(client, settings):
    sam_user, sam = make_user(client, "Sam")
    alex_user, alex = make_user(client, "Alex")
    client.post("/api/backups", content=backup_zip(1), headers=sam)
    client.post("/api/backups", content=backup_zip(2), headers=alex)
    assert client.delete(f"/admin/users/{sam_user['id']}", headers=ADMIN).status_code == 204
    assert not (settings.data_dir / "users" / sam_user["id"]).exists()
    assert client.get("/api/me", headers=sam).status_code == 401
    assert len(client.get("/api/backups", headers=alex).json()["backups"]) == 1
    assert client.delete("/admin/users/" + "0" * 32, headers=ADMIN).status_code == 404


def test_users_and_their_backups_survive_a_restart(settings):
    first = TestClient(create_app(settings))
    _, sam = make_user(first, "Sam")
    first.post("/api/backups", content=backup_zip(3), headers=sam)
    second = TestClient(create_app(settings))     # a new server on the same data folder
    assert second.get("/api/me", headers=sam).json()["name"] == "Sam"
    assert len(second.get("/api/backups", headers=sam).json()["backups"]) == 1


# ---------------------------------------------------------------------------------------------- guessing

def test_guessing_tokens_gets_slowed_down(client, settings):
    for _ in range(settings.failed_logins_per_minute):
        assert client.get("/api/me", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer wrong"}).status_code == 429


def test_an_admin_token_is_made_and_kept_when_none_is_set(settings):
    settings.admin_token = ""
    first = Service(settings)
    assert first.admin_token_is_new and first.admin_token.startswith("gladmin_")
    second = Service(settings)
    assert not second.admin_token_is_new and second.admin_token == first.admin_token


# ---------------------------------------------------------------------------------------------- the command line

def test_the_command_line_makes_and_lists_users(settings, capsys, monkeypatch):
    from app import cli
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    assert cli.main(["add-user", "Sam"]) == 0
    out = capsys.readouterr().out
    assert "Token" in out and "gl_" in out
    assert cli.main(["add-user", "sam"]) == 1                     # same name
    assert cli.main(["list-users"]) == 0 and "Sam" in capsys.readouterr().out
    assert cli.main(["disable-user", "SAM"]) == 0
    assert cli.main(["delete-user", "Sam"]) == 1                  # asks for --yes
    assert cli.main(["delete-user", "Sam", "--yes"]) == 0
    assert cli.main(["delete-user", "Sam", "--yes"]) == 1
