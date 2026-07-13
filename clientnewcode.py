import base64
import hashlib
import hmac
import time
from pathlib import Path

import requests

CHUNK_SIZE = 65536


class ModDownloader:
    """Downloads mods from the SpecterCraft backend using HMAC-signed requests."""

    def __init__(self, base_url: str, secret_key: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret_key.encode()

    # ── Authentication ──────────────────────────────────────────────

    def _sign(self, path: str) -> dict:
        """Create HMAC-signed headers for a given request path."""
        timestamp = str(int(time.time()))
        message = f"{timestamp}:{path}".encode()
        signature = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        return {"X-Launcher-Auth": f"{timestamp}:{signature}"}

    # ── Server communication ────────────────────────────────────────

    def get_manifest(self) -> list[dict]:
        """Fetch the mod manifest from the server."""
        resp = requests.get(
            f"{self.base_url}/manifest",
            headers=self._sign("/manifest"),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("mods", [])

    def download_mod(self, filename: str, dest_dir: Path, progress_cb=None) -> Path:
        """Download a single mod file, with path traversal protection."""
        if "/" in filename or "\\" in filename or ".." in filename:
            raise RuntimeError(f"Invalid filename: {filename}")

        path = f"/download/{filename}"
        resp = requests.get(
            f"{self.base_url}{path}",
            headers=self._sign(path),
            stream=True,
            timeout=30,
        )
        resp.raise_for_status()

        dest = dest_dir / filename
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(filename, downloaded, total)

        return dest

    # ── Options.txt ─────────────────────────────────────────────────

    def get_options(self) -> str:
        """Fetch options.txt content from the server."""
        resp = requests.get(
            f"{self.base_url}/options",
            headers=self._sign("/options"),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("options", "")

    def get_config(self) -> dict[str, bytes]:
        """Fetch config files from the server as {filename: content}."""
        resp = requests.get(
            f"{self.base_url}/config",
            headers=self._sign("/config"),
            timeout=15,
        )
        resp.raise_for_status()
        files = resp.json().get("files", {})
        return {name: base64.b64decode(data) for name, data in files.items()}



# ── Helpers ─────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()
