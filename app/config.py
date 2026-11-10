"""Settings, read from environment variables (see .env.example)."""
import os
from pathlib import Path

VERSION = "1.0"


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    """Read when the server starts. Tests make their own with different folders."""

    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("GYMLOG_DATA_DIR", "/data"))
        # If this is not set, one is made on first start and kept in the data folder (see README).
        self.admin_token = os.environ.get("GYMLOG_ADMIN_TOKEN", "").strip()
        self.max_upload_bytes = _int("GYMLOG_MAX_UPLOAD_MB", 500) * 1024 * 1024
        self.keep_backups = max(1, _int("GYMLOG_KEEP_BACKUPS", 10))
        self.user_quota_bytes = _int("GYMLOG_USER_QUOTA_MB", 5000) * 1024 * 1024
        self.failed_logins_per_minute = max(1, _int("GYMLOG_FAILED_LOGINS_PER_MINUTE", 10))
        # Reading backup.json to describe a backup is capped so a zip bomb cannot eat the server's memory.
        self.max_backup_json_bytes = 200 * 1024 * 1024
