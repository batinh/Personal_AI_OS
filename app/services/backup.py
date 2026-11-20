import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.logging_conf import get_module_logger

logger = get_module_logger("backup")
_ROOT = Path(__file__).resolve().parents[2]


def _chroma_files(directory):
    return {
        path.relative_to(directory): (stat.st_size, stat.st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
        for stat in (path.stat(),)
    }


def perform_backup():
    """Publish a verified SQLite snapshot and a stable copy of the data files."""
    source = _ROOT / "data"
    backups = _ROOT / "backups"
    try:
        backups.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=backups) as temp:
            stage = Path(temp) / "data"
            chroma = source / "chroma_db"
            before = _chroma_files(chroma)
            shutil.copytree(
                source,
                stage,
                ignore=lambda directory, names: {
                    n
                    for n in names
                    if Path(directory) == source
                    and n
                    in {
                        "os_core.db",
                        "os_core.db-wal",
                        "os_core.db-shm",
                        "os_core.db-journal",
                    }
                },
            )
            if before != _chroma_files(chroma):
                raise RuntimeError("Chroma files changed while taking backup")
            db = source / "os_core.db"
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as live:
                with closing(sqlite3.connect(stage / db.name)) as snapshot:
                    live.backup(snapshot)
                    if snapshot.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise RuntimeError("SQLite backup verification failed")
            archive = Path(
                shutil.make_archive(str(Path(temp) / "snapshot"), "zip", stage)
            )
            target = backups / datetime.now().strftime("coach_data_%Y%m%d_%H%M%S.zip")
            archive.replace(target)
        for old in sorted(backups.glob("coach_data_*.zip"))[:-7]:
            old.unlink()
        logger.info("[BACKUP] Successfully backed up: %s", target.name)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        logger.error("[BACKUP] Backup failed: %s", exc)
