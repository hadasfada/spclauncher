import os
import platform
import subprocess
from pathlib import Path

import minecraft_launcher_lib
from PyQt6.QtCore import QThread, pyqtSignal

from clientnewcode import ModDownloader, _sha256

# ── Constants ───────────────────────────────────────────────────────

system = platform.system()
MC_VERSION = "1.21.1"

if system == "Windows":
    _appdata = os.getenv("APPDATA")
    MC_DIR = str(Path(_appdata) / "SpecterCraft") if _appdata else str(Path.home() / "SpecterCraft")
elif system == "Darwin":
    MC_DIR = str(Path.home() / "Library" / "Application Support" / "SpecterCraft")
else:
    MC_DIR = str(Path.home() / ".spectercraft")

LOG_FILE = Path(MC_DIR) / "crash.log"


# ── Utility functions ───────────────────────────────────────────────


def _log(msg):
    """Append a timestamped message to the crash log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[LaunchWorker] {msg}\n")


def get_java_path():
    """Return the path to the bundled Java executable using the launcher lib."""
    try:
        path = minecraft_launcher_lib.runtime.get_executable_path("java-runtime-delta", MC_DIR)
        return path
    except Exception:
        return None


def find_neoforge():
    """Scan the versions directory for a NeoForge installation folder."""
    versions_dir = Path(MC_DIR) / "versions"
    if not versions_dir.exists():
        return None
    for name in os.listdir(str(versions_dir)):
        if "neoforge" in name:
            return name
    return None


# ── Worker threads ──────────────────────────────────────────────────


class InstallWorker(QThread):
    """Installs Minecraft, NeoForge, and syncs mods from the server."""

    progress = pyqtSignal(str, int, int)    # (status_text, progress, max)
    sync_result = pyqtSignal(bool, str)     # (success, error_message)

    def __init__(self, server_url=None, secret_key=None):
        super().__init__()
        self.server_url = server_url
        self.secret_key = secret_key

    def run(self):
        def emit(status="", prog=-1, maxv=-1):
            self.progress.emit(status, prog, maxv)

        try:
            # Callbacks for minecraft_launcher_lib progress reporting
            callback = {
                "setStatus": lambda s: emit(status=s),
                "setProgress": lambda v: emit(prog=v),
                "setMax": lambda v: emit(maxv=v),
            }

            # Step 1: Install Minecraft
            Path(MC_DIR).mkdir(parents=True, exist_ok=True)
            minecraft_launcher_lib.install.install_minecraft_version(
                MC_VERSION, MC_DIR, callback=callback
            )

            # Step 2: Install JVM runtime
            emit("Installing Java runtime...")
            try:
                minecraft_launcher_lib.runtime.install_jvm_runtime(
                    "java-runtime-delta", MC_DIR, callback=callback
                )
            except Exception as e:
                emit(f"Java runtime install failed: {e}")

            # Step 3: Install NeoForge (if not already present)
            neoforge = minecraft_launcher_lib.mod_loader.get_mod_loader("neoforge")
            existing_neoforge = find_neoforge()

            if existing_neoforge is None:
                try:
                    neoforge.install(MC_VERSION, MC_DIR, callback=callback, java=get_java_path())
                    emit("NeoForge installed")
                except Exception as e:
                    emit(f"NeoForge install failed: {e}")
            else:
                emit(f"NeoForge {existing_neoforge} found")

            # Step 4: Sync mods from server
            if self.server_url and self.secret_key:
                try:
                    self._sync_mods(emit)
                    self._sync_options(emit)
                    self._sync_config(emit)
                    self.sync_result.emit(True, "")
                except Exception as e:
                    emit(f"Mod sync failed: {e}")
                    self.sync_result.emit(False, str(e))
            else:
                self.sync_result.emit(True, "")

        except Exception as e:
            emit(f"Install failed: {e}")
            self.sync_result.emit(False, str(e))

        emit("ready")

    def _sync_mods(self, emit):
        """Download/verify mods and remove stale files."""
        dl = ModDownloader(self.server_url, self.secret_key)
        emit("Checking mods...")

        manifest = dl.get_manifest()
        total_size = sum(mod["size"] for mod in manifest)
        mods_dir = Path(MC_DIR) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)

        # Download or verify each mod
        bytes_processed = 0
        for mod in manifest:
            name = mod["filename"]
            local_path = mods_dir / name

            if local_path.exists() and _sha256(local_path) == mod["sha256"]:
                bytes_processed += mod["size"]
                emit(f"Verified {name}", bytes_processed, total_size)
                continue

            emit(f"Downloading {name}", bytes_processed, total_size)

            def on_chunk(filename, got, total, _base=bytes_processed):
                emit(f"Downloading {filename}", _base + got, total_size)

            dl.download_mod(name, mods_dir, progress_cb=on_chunk)
            bytes_processed += mod["size"]

        # Remove local mods that are no longer on the server
        if manifest:
            manifest_names = {m["filename"] for m in manifest}
            for local_file in mods_dir.glob("*.jar"):
                if local_file.name not in manifest_names:
                    local_file.rename(local_file.with_suffix(local_file.suffix + ".m"))
                    emit(f"Disabled stale mod {local_file.name}")

        emit("Mods synced")

    def _sync_options(self, emit):
        """Fetch options.txt from the server and write it to the game directory."""
        emit("Syncing options...")
        dl = ModDownloader(self.server_url, self.secret_key)
        content = dl.get_options()
        options_path = Path(MC_DIR) / "options.txt"
        options_path.parent.mkdir(parents=True, exist_ok=True)
        options_path.write_text(content, encoding="utf-8")
        emit("Options synced")

    def _sync_config(self, emit):
        """Fetch config files from the server and write them to the game directory."""
        emit("Syncing config...")
        dl = ModDownloader(self.server_url, self.secret_key)
        files = dl.get_config()
        config_dir = Path(MC_DIR) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (config_dir / name).write_bytes(content)
        emit("Config synced")


class ServerCheckWorker(QThread):
    """Pings the server manifest endpoint to check connectivity."""

    check_result = pyqtSignal(bool)

    def __init__(self, server_url, secret_key):
        super().__init__()
        self.server_url = server_url
        self.secret_key = secret_key

    def run(self):
        try:
            ModDownloader(self.server_url, self.secret_key).get_manifest()
            self.check_result.emit(True)
        except Exception:
            self.check_result.emit(False)


class ModVerifyWorker(QThread):
    """Pre-launch check: compares local mod hashes against the server.

    Emits verify_result(ok, reason) where reason is:
        ""            — all mods match
        "mismatch"    — mods don't match the server
        "unreachable" — couldn't connect to server
    """

    verify_result = pyqtSignal(bool, str)

    def __init__(self, server_url, secret_key):
        super().__init__()
        self.server_url = server_url
        self.secret_key = secret_key

    def run(self):
        try:
            mods_dir = Path(MC_DIR) / "mods"
            dl = ModDownloader(self.server_url, self.secret_key)
            manifest = dl.get_manifest()

            # Build expected mod map: {filename: sha256}
            expected = {m["filename"]: m["sha256"] for m in manifest}

            # Get list of locally installed mod filenames
            local_names = (
                {f.name for f in mods_dir.glob("*.jar")}
                if mods_dir.exists()
                else set()
            )

            # Check for extra or missing mods
            if local_names != set(expected.keys()):
                self.verify_result.emit(False, "mismatch")
                return

            # Check each mod's hash
            for name, expected_sha in expected.items():
                if _sha256(mods_dir / name) != expected_sha:
                    self.verify_result.emit(False, "mismatch")
                    return

            self.verify_result.emit(True, "")
        except Exception:
            self.verify_result.emit(False, "unreachable")


class LaunchWorker(QThread):
    """Launches Minecraft with the given username, JVM args, and RAM."""

    finished = pyqtSignal()
    error = pyqtSignal(str)
    process_started = pyqtSignal(object)
    process_exited = pyqtSignal()

    def __init__(self, username, java_args, ram_mb):
        super().__init__()
        self.username = username
        self.java_args = java_args
        self.ram_mb = ram_mb
        self._process = None

    def run(self):
        try:
            java_path = get_java_path()
            neoforge = find_neoforge()
            _log(f"java_path={java_path} neoforge={neoforge}")

            # Build Minecraft launch options
            options = {
                "username": self.username,
                "uuid": "a126cc8a-0616-4743-b3c7-f4217b13e545",
                "token": "0",
            }

            if java_path and Path(java_path).exists():
                options["executablePath"] = java_path

            # Build JVM arguments
            args = self.java_args.split() if self.java_args else []
            args.insert(0, f"-Xmx{self.ram_mb}M")
            args.insert(1, f"-Xms{self.ram_mb}M")
            args.insert(2, "-Duser.language=en -Duser.country=US -Duser.variant=US")
            options["jvmArguments"] = args

            # Get launch command and start process
            cmd = minecraft_launcher_lib.command.get_minecraft_command(
                neoforge, MC_DIR, options
            )
            _log(f"cmd={cmd}")

            popen_kwargs = {"cwd": MC_DIR}
            if system == "Windows":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(cmd, **popen_kwargs)
            _log(f"subprocess started pid={self._process.pid}")

            self.process_started.emit(self._process)
            self.finished.emit()

            self._process.wait()
            self._process = None
            self.process_exited.emit()

        except Exception as e:
            _log(f"ERROR: {e}")
            self.error.emit(str(e))

    def kill(self):
        """Terminate the running Minecraft process."""
        if self._process and self._process.poll() is None:
            self._process.kill()
            self._process = None
