import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MODS_DIRECTORY = os.environ.get("SC_MODS_DIR", "/home/hadas/mods")
SECRET_KEY = os.environ.get("SC_SECRET_KEY")
SIGNATURE_MAX_AGE = 300
DAILY_DOWNLOAD_LIMIT = 2000


# ── Rate limiter ────────────────────────────────────────────────────

_download_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_cleanup: float = 0.0


def _check_rate_limit(ip: str):
    global _last_cleanup
    today = date.today().isoformat()

    # Periodically drop stale entries to prevent memory leaks
    if time.time() - _last_cleanup > 3600:
        stale = [k for k, v in _download_counts.items() if today not in v]
        for k in stale:
            del _download_counts[k]
        _last_cleanup = time.time()

    _download_counts[ip][today] += 1
    if _download_counts[ip][today] > DAILY_DOWNLOAD_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily download limit reached. Try again later.",
        )


def _get_secret():
    if not SECRET_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: SC_SECRET_KEY not set.")
    return SECRET_KEY.encode()


def _compute_manifest(directory: str) -> list[dict]:
    p = Path(directory)
    if not p.exists():
        return []
    return [
        {"filename": f.name, "size": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
        for f in sorted(p.iterdir()) if f.is_file() and f.suffix == ".jar"
    ]


def _verify_auth(auth_header: str | None, secret: bytes, path: str):
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authentication.")

    parts = auth_header.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid auth format.")

    try:
        ts = int(parts[0])
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp.")

    if abs(time.time() - ts) > SIGNATURE_MAX_AGE:
        raise HTTPException(status_code=401, detail="Signature expired.")

    expected_mac = hmac.new(secret, f"{ts}:{path}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(parts[1], expected_mac):
        raise HTTPException(status_code=401, detail="Invalid signature.")


@app.get("/manifest")
async def get_manifest(x_launcher_auth: str = Header(None)):
    secret = _get_secret()
    _verify_auth(x_launcher_auth, secret, "/manifest")
    return JSONResponse(content={"mods": _compute_manifest(MODS_DIRECTORY)})


@app.get("/download/{filename}")
async def download_mod(filename: str, request: Request, x_launcher_auth: str = Header(None)):
    secret = _get_secret()
    _verify_auth(x_launcher_auth, secret, f"/download/{filename}")
    _check_rate_limit(request.client.host)

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    file_path = Path(MODS_DIRECTORY) / filename
    try:
        resolved = file_path.resolve()
        if not str(resolved).startswith(str(Path(MODS_DIRECTORY).resolve())):
            raise HTTPException(status_code=400, detail="Invalid filename.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Mod file not found.")

    return FileResponse(str(file_path), media_type="application/java-archive", filename=filename)


@app.get("/options")
async def get_options(x_launcher_auth: str = Header(None)):
    secret = _get_secret()
    _verify_auth(x_launcher_auth, secret, "/options")
    options_path = Path("/home/hadas/options.txt")
    if not options_path.is_file():
        return JSONResponse(content={"options": ""})
    return JSONResponse(content={"options": options_path.read_text(encoding="utf-8")})


@app.get("/config")
async def get_config(x_launcher_auth: str = Header(None)):
    secret = _get_secret()
    _verify_auth(x_launcher_auth, secret, "/config")
    config_dir = Path("/home/hadas/config")
    if not config_dir.is_dir():
        return JSONResponse(content={"files": {}})
    files = {}
    for f in sorted(config_dir.iterdir()):
        if f.is_file():
            files[f.name] = base64.b64encode(f.read_bytes()).decode()
    return JSONResponse(content={"files": files})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
