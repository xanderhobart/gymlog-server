"""The dashboard's data addresses (/api/dash/...). Every one needs the caller's own token, and reads only that user's backups.
The page itself (HTML, scripts, styles) is static and holds no data."""
import json
import threading
import zipfile
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from . import analytics as A
from . import exports
from .config import Settings
from .service import NotFound, Service


class DatasetCache:
    """Keeps the last few parsed backups in memory. A backup never changes once stored, so its id is a safe key."""

    def __init__(self, size: int = 6) -> None:
        self.size = size
        self.items: "OrderedDict[str, A.Dataset]" = OrderedDict()
        self.lock = threading.Lock()

    def get(self, backup_id: str, load: Callable[[], A.Dataset]) -> A.Dataset:
        with self.lock:
            if backup_id in self.items:
                self.items.move_to_end(backup_id)
                return self.items[backup_id]
        ds = load()
        with self.lock:
            self.items[backup_id] = ds
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return ds

    def forget(self, backup_id: str) -> None:
        with self.lock:
            self.items.pop(backup_id, None)


def build_router(service: Service, settings: Settings, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/dash")
    cache = DatasetCache()

    def load_dataset(user: Dict[str, Any], backup: str) -> Tuple[A.Dataset, Dict[str, Any]]:
        backups = service.list_backups(user["id"])
        if not backups:
            raise NotFound("You have no backups on the server yet. Make one from the GymLog app (Settings > Server backup > Back up now).")
        if backup in ("", "latest"):
            info = backups[0]
        else:
            info = service.get_backup(user["id"], backup)

        def load() -> A.Dataset:
            path = service.backup_path(user["id"], info["id"])
            with zipfile.ZipFile(path) as z:
                member = z.getinfo("backup.json")
                if member.file_size > settings.max_backup_json_bytes:
                    raise HTTPException(status_code=413, detail="backup.json is too large to analyse.")
                return A.Dataset(json.loads(z.read(member).decode("utf-8")))

        return cache.get(info["id"], load), info

    def window(frm: Optional[str], to: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
        start, end = A.parse_day(frm), A.parse_day(to)
        if (frm and start is None) or (to and end is None):
            raise HTTPException(status_code=400, detail="Dates look like 2026-03-31.")
        return start, end

    async def data(user: Dict[str, Any], backup: str):
        return await run_in_threadpool(load_dataset, user, backup)

    # -------------------------------------------------------------------- overview and charts

    @router.get("/overview")
    async def overview(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, info = await data(user, backup)
        start, end = window(frm, to)
        span_first, span_last = ds.span()
        today = span_last if span_last is not None else 0
        if end is not None:
            today = min(today, end)
        return {
            "backup": {"id": info["id"], "createdAt": info["createdAt"], "device": info["device"], "size": info["size"]},
            "span": {"first": A.iso(span_first) if span_first is not None else None, "last": A.iso(span_last) if span_last is not None else None},
            "summary": A.overview(ds, start, end),
            "weekly": A.weekly(ds, start, end, "total"),
            "records": A.personal_records(ds, today, 30, 8) if span_last is not None else [],
            "insights": A.insights(ds, start, end),
            "body": {k: v for k, v in A.body(ds, start, end).items() if k != "photo_days"},
            "cardio": {k: v for k, v in A.cardio(ds, start, end).items() if k in ("sessions", "total_minutes")},
        }

    @router.get("/weekly")
    async def weekly(by: str = "total", frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.weekly(ds, start, end, "muscle" if by == "muscle" else "total")

    @router.get("/calendar")
    async def calendar(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.calendar(ds, start, end)

    @router.get("/exercises")
    async def exercises(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.exercise_list(ds, start, end)

    @router.get("/exercise")
    async def exercise(exercise: str, variant: str = "all", frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.exercise_detail(ds, exercise, variant, start, end)

    @router.get("/muscles")
    async def muscles(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.muscles(ds, start, end)

    @router.get("/body")
    async def body(lift: Optional[str] = None, frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        key = None
        if lift and ":" in lift:
            a, b = lift.split(":", 1)
            key = (a, b)
        return A.body(ds, start, end, key)

    @router.get("/cardio")
    async def cardio(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None, backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        start, end = window(frm, to)
        return A.cardio(ds, start, end)

    @router.get("/checks")
    async def checks(backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, _ = await data(user, backup)
        return {"checks": A.data_checks(ds)}

    # -------------------------------------------------------------------- backups: what is stored and how it has changed

    @router.get("/backups")
    def backups(user: Dict[str, Any] = Depends(current_user)):
        items = service.list_backups(user["id"])
        used = sum(b["size"] for b in items)
        oldest_first = list(reversed(items))
        rows = []
        prev: Optional[Dict[str, Any]] = None
        for b in oldest_first:
            s = b["summary"]
            delta = None
            if prev is not None:
                ps = prev["summary"]
                delta = {k: s.get(k, 0) - ps.get(k, 0) for k in ("workouts", "sets", "weighIns", "photos", "cardioSessions") if k in s or k in ps}
            rows.append({**b, "delta": delta})
            prev = b
        return {
            "backups": list(reversed(rows)),
            "count": len(items), "used_bytes": used, "quota_bytes": settings.user_quota_bytes, "keep": settings.keep_backups,
            "newest": items[0]["createdAt"] if items else None, "oldest": items[-1]["createdAt"] if items else None,
        }

    # -------------------------------------------------------------------- downloads

    @router.get("/export")
    async def export(table: str = "all", fmt: str = Query("xlsx", alias="format"), frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                     backup: str = "latest", user: Dict[str, Any] = Depends(current_user)):
        ds, info = await data(user, backup)
        start, end = window(frm, to)
        if fmt not in ("xlsx", "csv"):
            raise HTTPException(status_code=400, detail="format is xlsx or csv.")
        if table != "all" and table not in exports.TABLE_TITLES and table != "backups":
            raise HTTPException(status_code=400, detail="Unknown table.")

        def make() -> Tuple[bytes, str, str]:
            tables = exports.build_tables(ds, start, end)
            history = _backup_table(service.list_backups(user["id"]))
            if table == "all":
                if fmt == "xlsx":
                    return exports.to_xlsx(tables, {"backups": history}), "gymlog-dashboard-export.xlsx", XLSX
                return exports.to_csv_zip({**tables, "backups": history}), "gymlog-dashboard-export.zip", "application/zip"
            chosen = history if table == "backups" else tables[table]
            if fmt == "xlsx":
                return exports.to_xlsx({table: chosen}), f"gymlog-{table}.xlsx", XLSX
            return exports.to_csv(chosen), f"gymlog-{table}.csv", "text/csv; charset=utf-8"

        content, name, media = await run_in_threadpool(make)
        return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    return router


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _backup_table(backups) -> exports.Table:
    import datetime as dt
    rows = []
    for b in backups:
        s = b["summary"]
        rows.append([dt.datetime.fromtimestamp(b["createdAt"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), b["device"], round(b["size"] / 1e6, 1),
                     s.get("workouts"), s.get("sets"), s.get("weighIns"), s.get("photos"), s.get("cardioSessions"), s.get("templates"), s.get("exercises")])
    return (["Backed up (UTC)", "Device", "Size (MB)", "Workouts", "Sets", "Weigh-ins", "Photos", "Cardio sessions", "Templates", "Exercises"], rows)
