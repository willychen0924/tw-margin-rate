"""Portable path discovery and iCloud archive readiness checks."""

from __future__ import annotations

import os
import stat
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ICLOUD_ROOT = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
)
LOCAL_SETTINGS_ROOT = Path.home() / "Library" / "Application Support" / "tw-margin-rate"


def local_env_path() -> Path:
    """Return the per-Mac secret location outside the iCloud project."""
    return LOCAL_SETTINGS_ROOT / ".env"


def discover_stock_data(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    configured = os.environ.get("STOCK_DATA_ROOT")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([PROJECT_ROOT.parent / "stock_data", DEFAULT_ICLOUD_ROOT / "stock_data"])
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir():
            return resolved
    rendered = "\n".join(f"- {path}" for path in candidates)
    raise FileNotFoundError(f"找不到 stock_data；已檢查：\n{rendered}")


def _is_offline(path: Path) -> bool:
    flags = getattr(path.stat(), "st_flags", 0)
    offline_flag = getattr(stat, "UF_OFFLINE", 0x40000000)
    return bool(flags & offline_flag)


def latest_parquet(directory: Path) -> Path:
    files = sorted(directory.glob("*/*.parquet"))
    if not files:
        raise FileNotFoundError(f"找不到 parquet：{directory}")
    return files[-1]


def assert_archive_ready(stock_data: Path, warmup_start: str) -> dict[str, str]:
    """Reject placeholders/empty files and touch the latest parquet footer."""
    required = {
        "chips_margin": stock_data / "raw/chips_margin",
        "prices": stock_data / "raw/prices",
        "stock_info": stock_data / "raw/stock_info",
    }
    latest: dict[str, str] = {}
    start_year = int(warmup_start[:4])
    for name, directory in required.items():
        if not directory.is_dir():
            raise FileNotFoundError(f"缺少必要資料夾：{directory}")
        files = sorted(directory.glob("*/*.parquet"))
        eligible = [path for path in files if int(path.parent.name) >= start_year]
        if not eligible:
            raise FileNotFoundError(f"{name} 沒有 {start_year} 年以後的 parquet")
        # The builder scans every margin/price parquet in the warm-up range, but
        # intentionally reads only the newest stock_info snapshot.
        consumed = eligible if name in {"chips_margin", "prices"} else [eligible[-1]]
        zero = [path for path in consumed if path.stat().st_size == 0]
        offline = [path for path in consumed if _is_offline(path)]
        if zero or offline:
            sample = (zero + offline)[:5]
            raise RuntimeError(
                f"{name} 尚未完整下載（共 {len(zero)} 個空檔、"
                f"{len(offline)} 個離線檔）：{sample}"
            )
        newest = eligible[-1]
        with newest.open("rb") as handle:
            if handle.read(4) != b"PAR1":
                raise RuntimeError(f"不是有效 parquet 檔頭：{newest}")
            handle.seek(-4, os.SEEK_END)
            if handle.read(4) != b"PAR1":
                raise RuntimeError(f"不是有效 parquet 檔尾：{newest}")
        latest[name] = newest.stem
    return latest
