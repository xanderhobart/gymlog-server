"""The web side: the addresses the GymLog app (and the admin) talk to."""
import hashlib
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .config import VERSION, Settings
from .security import FailureLimiter, bearer
from .service import BackupError, NameTaken, NotFound, QuotaError, Service

log = logging.getLogger("gymlog.server")


class NewUser(BaseModel):
    name: str


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    service = Service(settings)
    limiter = FailureLimiter(settings.failed_logins_per_minute)
    app = FastAPI(title="GymLog server", version=VERSION)
    app.state.service = service
    app.state.settings = settings

    if service.admin_token_is_new:
        # Printed once, in the server's log, so the person setting it up can copy it (it is also in the data folder).
        log.warning("A new admin token was made: %s  (also saved in %s)", service.admin_token, settings.data_dir / "admin_token")

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, e: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(e)})

    # ---------------------------------------------------------------- who is asking

    def who(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def check_token(request: Request) -> str:
        """The bearer token of the request, or an error. Too many wrong tries from one address are slowed down."""
        if limiter.blocked(who(request)):
            raise HTTPException(status_code=429, detail="Too many wrong tokens. Wait a minute and try again.")
        token = bearer(request.headers.get("authorization"))
        if token is None:
            limiter.failed(who(request))
            raise HTTPException(status_code=401, detail="Send your token as 'Authorization: Bearer <token>'.", headers={"WWW-Authenticate": "Bearer"})
        return token

    def current_user(request: Request) -> Dict[str, Any]:
        token = check_token(request)
        user = service.user_for_token(token)
        if user is None:
            limiter.failed(who(request))
            raise HTTPException(status_code=401, detail="That token is not valid.", headers={"WWW-Authenticate": "Bearer"})
        if user["disabled"]:
            raise HTTPException(status_code=403, detail="This user has been switched off by the server's admin.")
        limiter.succeeded(who(request))
        return user

    def require_admin(request: Request) -> None:
        token = check_token(request)
        if not service.is_admin(token):
            limiter.failed(who(request))
            raise HTTPException(status_code=401, detail="That is not the admin token.", headers={"WWW-Authenticate": "Bearer"})
        limiter.succeeded(who(request))

    # ---------------------------------------------------------------- open to anyone on the network

    @app.get("/health")
    def health():
        return {"status": "ok", "version": VERSION}

    # ---------------------------------------------------------------- a user's own things

    @app.get("/api/me")
    def me(user: Dict[str, Any] = Depends(current_user)):
        return user

    @app.post("/api/backups")
    async def upload_backup(
        request: Request, response: Response, user: Dict[str, Any] = Depends(current_user),
        x_device_name: Optional[str] = Header(default=None),
    ):
        """The body is the backup zip itself (Content-Type: application/zip). Stored under the caller's own name only."""
        limit = settings.max_upload_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail="That file is larger than the server accepts.")
        temp = service.new_temp_file()
        digest = hashlib.sha256()
        size = 0
        try:
            with open(temp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(status_code=413, detail="That file is larger than the server accepts.")
                    digest.update(chunk)
                    f.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="The upload was empty.")
            try:
                backup, created = await run_in_threadpool(service.add_backup, user["id"], temp, size, digest.hexdigest(), x_device_name or "")
            except BackupError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except QuotaError as e:
                raise HTTPException(status_code=507, detail=str(e))
        finally:
            temp.unlink(missing_ok=True)
        response.status_code = 201 if created else 200
        return {"created": created, "backup": backup}

    @app.get("/api/backups")
    def list_backups(user: Dict[str, Any] = Depends(current_user)):
        return {"backups": service.list_backups(user["id"])}

    @app.get("/api/backups/latest")
    def latest_backup(user: Dict[str, Any] = Depends(current_user)):
        backups = service.list_backups(user["id"])
        if not backups:
            raise NotFound("You have no backups on the server yet.")
        return _file(service, user, backups[0])

    @app.get("/api/backups/{backup_id}")
    def get_backup(backup_id: str, user: Dict[str, Any] = Depends(current_user)):
        return _file(service, user, service.get_backup(user["id"], backup_id))

    @app.delete("/api/backups/{backup_id}", status_code=204)
    def delete_backup(backup_id: str, user: Dict[str, Any] = Depends(current_user)):
        service.delete_backup(user["id"], backup_id)
        return Response(status_code=204)

    # ---------------------------------------------------------------- the admin: manages people, never reads their data

    @app.post("/admin/users", status_code=201, dependencies=[Depends(require_admin)])
    def add_user(body: NewUser):
        try:
            user, token = service.create_user(body.name)
        except NameTaken as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"user": user, "token": token, "note": "Copy the token now: it cannot be shown again."}

    @app.get("/admin/users", dependencies=[Depends(require_admin)])
    def users():
        return {"users": service.list_users()}

    @app.post("/admin/users/{user_id}/reset-token", dependencies=[Depends(require_admin)])
    def reset_token(user_id: str):
        return {"user": service.get_user(user_id), "token": service.reset_token(user_id), "note": "The old token no longer works."}

    @app.post("/admin/users/{user_id}/disable", dependencies=[Depends(require_admin)])
    def disable(user_id: str):
        return {"user": service.set_disabled(user_id, True)}

    @app.post("/admin/users/{user_id}/enable", dependencies=[Depends(require_admin)])
    def enable(user_id: str):
        return {"user": service.set_disabled(user_id, False)}

    @app.delete("/admin/users/{user_id}", status_code=204, dependencies=[Depends(require_admin)])
    def remove_user(user_id: str):
        service.delete_user(user_id)
        return Response(status_code=204)

    return app


def _file(service: Service, user: Dict[str, Any], backup: Dict[str, Any]) -> FileResponse:
    path = service.backup_path(user["id"], backup["id"])
    stamp = __import__("datetime").datetime.utcfromtimestamp(backup["createdAt"] / 1000).strftime("%Y-%m-%d-%H%M")
    return FileResponse(path, media_type="application/zip", filename=f"gymlog-backup-{stamp}.zip", headers={"X-Backup-Id": backup["id"], "X-Backup-Sha256": backup["sha256"]})


def app_factory() -> FastAPI:
    """Used by uvicorn: `uvicorn app.main:app_factory --factory`."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
