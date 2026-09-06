"""Application configuration, including optional ECU transaction logging (R20)."""

from __future__ import annotations

import configparser
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from raspberryduc.version import APP_NAME, __version__

LOG = logging.getLogger(__name__)

CONFIG_FILENAME = "raspberryduc.ini"
DEFAULT_LOG_DIR = "logs"
MAX_SESSION_LOGS = 5
LOG_STAMP_FORMAT = "%Y_%d_%m_%H_%M_%S"  # R20.4 YYYY_DD_MM_HH_MM_SS
LOG_NAME_RE = re.compile(
    r"^raspberryduc_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.log$"
)
_TRUTH = {"1", "true", "yes", "on"}


class DurableFileHandler(logging.FileHandler):
    """Flush the stdio buffer and fsync after every record (hard power-off)."""

    def flush(self) -> None:
        super().flush()
        stream = getattr(self, "stream", None)
        if stream is None:
            return
        try:
            stream.flush()
            os.fsync(stream.fileno())
        except (OSError, ValueError, AttributeError):
            pass


@dataclass(frozen=True)
class AppConfig:
    logging_enabled: bool = False
    log_dir: Path = Path(DEFAULT_LOG_DIR)
    config_path: Optional[Path] = None


def config_search_paths() -> List[Path]:
    paths: List[Path] = []
    env = os.environ.get("RASPBERRYDUC_CONFIG")
    if env:
        paths.append(Path(env).expanduser())
    paths.append(Path.cwd() / CONFIG_FILENAME)
    paths.append(Path(__file__).resolve().parent.parent / CONFIG_FILENAME)
    seen = set()
    unique: List[Path] = []
    for path in paths:
        resolved = path if path.is_absolute() else path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load raspberryduc.ini; missing file means logging stays off."""
    candidates: Iterable[Path]
    if path is not None:
        candidates = (path,)
    else:
        candidates = config_search_paths()
    for candidate in candidates:
        if candidate.is_file():
            return _parse_ini(candidate)
    return AppConfig()


def session_log_name(when: datetime) -> str:
    """R20.4: raspberryduc_YYYY_DD_MM_HH_MM_SS.log"""
    return f"raspberryduc_{when.strftime(LOG_STAMP_FORMAT)}.log"


def parse_log_stamp(name: str) -> Optional[datetime]:
    match = LOG_NAME_RE.match(name)
    if not match:
        return None
    year, day, month, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def list_session_logs(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    files = [path for path in directory.iterdir() if path.is_file() and parse_log_stamp(path.name)]
    files.sort(key=lambda path: parse_log_stamp(path.name) or datetime.min)
    return files


def prune_session_logs(directory: Path, keep: int = MAX_SESSION_LOGS) -> None:
    """R20.3: keep at most `keep` session logs; delete the oldest first."""
    files = list_session_logs(directory)
    if keep < 0:
        keep = 0
    for old in files[:-keep] if keep else files:
        try:
            old.unlink()
        except OSError as exc:
            LOG.warning("Could not delete old log %s: %s", old, exc)


def apply_logging(config: AppConfig, now: Optional[datetime] = None) -> Optional[Path]:
    """Create a new timestamped session log when logging is enabled (R20 / R20.1 / R20.3)."""
    if not config.logging_enabled:
        return None
    log_dir = config.log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        LOG.warning("Cannot create log directory %s: %s", log_dir, exc)
        return None
    stamp = now or datetime.now()
    log_path = log_dir / session_log_name(stamp)
    if log_path.exists():
        log_path = log_dir / session_log_name(stamp.replace(second=(stamp.second + 1) % 60))
    handler = DurableFileHandler(log_path, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    package = logging.getLogger("raspberryduc")
    package.setLevel(logging.DEBUG)
    package.addHandler(handler)
    prune_session_logs(log_dir, MAX_SESSION_LOGS)
    src = config.config_path or "(defaults)"
    package.info(
        "%s %s ECU transaction logging enabled (config=%s file=%s)",
        APP_NAME,
        __version__,
        src,
        log_path,
    )
    return log_path


def _parse_ini(path: Path) -> AppConfig:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except OSError:
        return AppConfig()
    enabled = False
    dir_name = DEFAULT_LOG_DIR
    if parser.has_section("logging"):
        raw_enabled = parser.get("logging", "enabled", fallback="false")
        enabled = raw_enabled.strip().lower() in _TRUTH
        raw_dir = parser.get("logging", "directory", fallback="").strip()
        if not raw_dir:
            legacy = parser.get("logging", "file", fallback="").strip()
            if legacy:
                legacy_path = Path(legacy)
                raw_dir = str(legacy_path.parent) if legacy_path.suffix else legacy
        dir_name = raw_dir or DEFAULT_LOG_DIR
    log_dir = Path(dir_name).expanduser()
    if not log_dir.is_absolute():
        log_dir = (path.parent / log_dir).resolve()
    return AppConfig(logging_enabled=enabled, log_dir=log_dir, config_path=path.resolve())
