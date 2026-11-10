"""What the server does: users, their tokens, and each user's backups. No web code in here, so it can be tested on its own."""
import json
import re
import shutil
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .db import Database
from .security import hash_token, new_token, tokens_equal

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SUMMARY_TABLES = {
    "workouts": "workouts", "workout_sets": "sets", "body_weights": "weighIns", "progress_photos": "photoRecords",
    "cardio_sessions": "cardioSessions", "gym_days": "templates", "exercises": "exercises",
}


class BackupError(Exception):
    """The uploaded file is not a GymLog backup (the message says why, in plain words)."""


class QuotaError(Exception):
    pass


class NotFound(Exception):
    pass


class NameTaken(Exception):
    pass


def valid_id(value: str) -> bool:
    return bool(ID_PATTERN.match(value or ""))


class Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.data_dir / "gymlog-server.db")
        (settings.data_dir / "users").mkdir(exist_ok=True)
        (settings.data_dir / "tmp").mkdir(exist_ok=True)
        self.admin_token, self.admin_token_is_new = self._load_admin_token()

    # ------------------------------------------------------------------ admin token

    def _load_admin_token(self) -> Tuple[str, bool]:
        """The admin token comes from the environment, else from a file in the data folder, else it is made now (once)."""
        if self.settings.admin_token:
            return self.settings.admin_token, False
        path = self.settings.data_dir / "admin_token"
        if path.exists():
            return path.read_text(encoding="utf-8").strip(), False
        token = new_token("gladmin")
        path.write_text(token + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return token, True

    def is_admin(self, token: str) -> bool:
        return tokens_equal(token, self.admin_token)

    # ------------------------------------------------------------------ users

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join((name or "").split())
        if not 1 <= len(cleaned) <= 40:
            raise ValueError("A name must be 1 to 40 characters.")
        return cleaned

    def create_user(self, name: str) -> Tuple[Dict[str, Any], str]:
        """Makes a user and returns them with their token. The token cannot be shown again."""
        name = self._clean_name(name)
        token = new_token()
        user_id = uuid.uuid4().hex
        try:
            with self.db.connect() as c:
                c.execute(
                    "INSERT INTO users (id, name, name_key, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, name, name.lower(), hash_token(token), int(time.time())),
                )
        except sqlite3.IntegrityError:
            raise NameTaken(f"There is already a user called '{name}'.")
        self.user_dir(user_id).mkdir(parents=True, exist_ok=True)
        return self.get_user(user_id), token

    def get_user(self, user_id: str) -> Dict[str, Any]:
        if not valid_id(user_id):
            raise NotFound("No such user.")
        with self.db.connect() as c:
            row = c.execute(
                "SELECT u.id, u.name, u.created_at, u.disabled, COUNT(b.id) AS backups, COALESCE(SUM(b.size), 0) AS bytes, MAX(b.created_at) AS last_backup "
                "FROM users u LEFT JOIN backups b ON b.user_id = u.id WHERE u.id = ? GROUP BY u.id",
                (user_id,),
            ).fetchone()
        if row is None:
            raise NotFound("No such user.")
        return self._user_dict(row)

    @staticmethod
    def _user_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "createdAt": row["created_at"], "disabled": bool(row["disabled"]),
            "backups": row["backups"], "bytes": row["bytes"], "lastBackupAt": row["last_backup"],
        }

    def list_users(self) -> List[Dict[str, Any]]:
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT u.id, u.name, u.created_at, u.disabled, COUNT(b.id) AS backups, COALESCE(SUM(b.size), 0) AS bytes, MAX(b.created_at) AS last_backup "
                "FROM users u LEFT JOIN backups b ON b.user_id = u.id GROUP BY u.id ORDER BY u.name_key"
            ).fetchall()
        return [self._user_dict(r) for r in rows]

    def user_for_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Who this token belongs to (even if switched off: the caller decides), or None for a token nobody has."""
        with self.db.connect() as c:
            row = c.execute("SELECT id FROM users WHERE token_hash = ?", (hash_token(token),)).fetchone()
        return self.get_user(row["id"]) if row else None

    def reset_token(self, user_id: str) -> str:
        self.get_user(user_id)
        token = new_token()
        with self.db.connect() as c:
            c.execute("UPDATE users SET token_hash = ? WHERE id = ?", (hash_token(token), user_id))
        return token

    def set_disabled(self, user_id: str, disabled: bool) -> Dict[str, Any]:
        self.get_user(user_id)
        with self.db.connect() as c:
            c.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> None:
        self.get_user(user_id)
        with self.db.connect() as c:
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        shutil.rmtree(self.user_dir(user_id).parent, ignore_errors=True)   # the user's whole folder, not just the backups inside it

    # ------------------------------------------------------------------ backups

    def user_dir(self, user_id: str) -> Path:
        if not valid_id(user_id):
            raise NotFound("No such user.")
        return self.settings.data_dir / "users" / user_id / "backups"

    def new_temp_file(self) -> Path:
        return self.settings.data_dir / "tmp" / (uuid.uuid4().hex + ".part")

    def describe_backup(self, path: Path) -> Tuple[Optional[int], Dict[str, int]]:
        """Checks that the file is a GymLog backup and says what is in it. Raises BackupError otherwise."""
        if not zipfile.is_zipfile(path):
            raise BackupError("That is not a zip file, so it is not a GymLog backup.")
        try:
            with zipfile.ZipFile(path) as z:
                try:
                    info = z.getinfo("backup.json")
                except KeyError:
                    raise BackupError("The zip has no backup.json, so it is not a GymLog backup.")
                if info.file_size > self.settings.max_backup_json_bytes:
                    raise BackupError("backup.json is far too large.")
                header = json.loads(z.read(info).decode("utf-8"))
        except BackupError:
            raise
        except (zipfile.BadZipFile, ValueError, UnicodeDecodeError):
            raise BackupError("The backup could not be read (it is damaged or not a GymLog backup).")
        if not isinstance(header, dict) or header.get("app") != "GymLog" or not isinstance(header.get("tables"), dict):
            raise BackupError("That file is not a GymLog backup.")
        summary: Dict[str, int] = {}
        for table, label in SUMMARY_TABLES.items():
            rows = header["tables"].get(table)
            if isinstance(rows, list):
                summary[label] = len(rows)
        photos = header.get("photos")
        if isinstance(photos, list):
            summary["photos"] = len(photos)
        version = header.get("dbVersion")
        return (version if isinstance(version, int) else None), summary

    def add_backup(self, user_id: str, temp: Path, size: int, sha256: str, device: str) -> Tuple[Dict[str, Any], bool]:
        """Keeps an uploaded file for this user. Returns (backup, created); created is False when it is identical to their newest one."""
        db_version, summary = self.describe_backup(temp)
        existing = self.list_backups(user_id)
        if existing and existing[0]["sha256"] == sha256:
            temp.unlink(missing_ok=True)
            return existing[0], False
        used = sum(b["size"] for b in existing)
        if used + size > self.settings.user_quota_bytes:
            # Old backups beyond the newest few can be dropped to make room; if that is not enough, refuse.
            for old in reversed(existing[1:]):
                if used + size <= self.settings.user_quota_bytes:
                    break
                used -= old["size"]
                self.delete_backup(user_id, old["id"])
            if used + size > self.settings.user_quota_bytes:
                temp.unlink(missing_ok=True)
                raise QuotaError("This would go over your storage allowance on the server.")
        backup_id = uuid.uuid4().hex
        target = self.user_dir(user_id) / (backup_id + ".zip")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp), str(target))
        created = int(time.time() * 1000)
        with self.db.connect() as c:
            c.execute(
                "INSERT INTO backups (id, user_id, size, sha256, created_at, device, db_version, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (backup_id, user_id, size, sha256, created, (device or "")[:80], db_version, json.dumps(summary)),
            )
        for old in self.list_backups(user_id)[self.settings.keep_backups:]:
            self.delete_backup(user_id, old["id"])
        return self.get_backup(user_id, backup_id), True

    @staticmethod
    def _backup_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "size": row["size"], "sha256": row["sha256"], "createdAt": row["created_at"], "device": row["device"],
            "dbVersion": row["db_version"], "summary": json.loads(row["summary"] or "{}"),
        }

    def list_backups(self, user_id: str) -> List[Dict[str, Any]]:
        """This user's backups, newest first. Other users' backups are never in this list."""
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM backups WHERE user_id = ? ORDER BY created_at DESC, rowid DESC", (user_id,)).fetchall()
        return [self._backup_dict(r) for r in rows]

    def get_backup(self, user_id: str, backup_id: str) -> Dict[str, Any]:
        if not valid_id(backup_id):
            raise NotFound("No such backup.")
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM backups WHERE id = ? AND user_id = ?", (backup_id, user_id)).fetchone()
        if row is None:
            raise NotFound("No such backup.")
        return self._backup_dict(row)

    def backup_path(self, user_id: str, backup_id: str) -> Path:
        self.get_backup(user_id, backup_id)
        path = self.user_dir(user_id) / (backup_id + ".zip")
        if not path.exists():
            raise NotFound("The backup file is missing on the server.")
        return path

    def delete_backup(self, user_id: str, backup_id: str) -> None:
        self.get_backup(user_id, backup_id)
        with self.db.connect() as c:
            c.execute("DELETE FROM backups WHERE id = ? AND user_id = ?", (backup_id, user_id))
        (self.user_dir(user_id) / (backup_id + ".zip")).unlink(missing_ok=True)
