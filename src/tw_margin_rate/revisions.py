"""Separate accepted history from observations; keep evidence and roll back failed installs."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def save_observation(directory: Path, payload: dict) -> Path:
    """Content-addressed, immutable source envelopes (no headers or credentials)."""
    raw = json_bytes(payload)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (hashlib.sha256(raw).hexdigest() + ".json.gz")
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            handle.write(gzip.compress(raw, mtime=0))
            name = handle.name
        os.replace(name, target)
    return target


def rows_by_date(rows: list[dict]) -> dict[str, dict]:
    result = {r["date"]: r for r in rows}
    if len(result) != len(rows) or list(result) != sorted(result):
        raise ValueError("市值日期重複或未遞增")
    return result


def retain_published_market_caps(published: dict, prior: dict, refreshed: dict, prior_audit: dict | None = None) -> tuple[dict, dict]:
    """Retain only market cap history. All other calculated fields remain strictly checked."""
    if not refreshed["metadata"].get("complete") or not refreshed["metadata"].get("validation_passed"):
        raise ValueError("新抓市值未完整通過驗證")
    cutoff = published["metadata"]["end"]
    accepted = copy.deepcopy(refreshed)
    changes = []
    for market in ("twse", "tpex"):
        base = rows_by_date(published["markets"][market])
        old = rows_by_date(prior["markets"][market])
        new = rows_by_date(refreshed["markets"][market])
        expected = {d for d, r in base.items() if r.get("market_cap") is not None}
        if {d for d in new if d <= cutoff} != expected:
            raise ValueError(f"{market} 市值歷史日期新增或缺漏")
        for d, row in new.items():
            cap = row.get("market_cap")
            if not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
                raise ValueError(f"{market} {d} 市值無效")
            if d not in expected:
                continue
            if d not in old or old[d]["market_cap"] != base[d]["market_cap"]:
                raise ValueError(f"{market} {d} 正式市值快取與已發布值不符，須先復原正式基準")
            if cap != base[d]["market_cap"]:
                changes.append({"market": market, "date": d, "accepted": base[d]["market_cap"],
                                "observed": cap, "difference": round(cap-base[d]["market_cap"], 2),
                                "status": "pending_verification"})
            new[d] = copy.deepcopy(old[d])
        accepted["markets"][market] = [new[d] for d in sorted(new)]
    # Pending findings must not disappear when their date leaves the refresh window.
    pending = {(r['market'], r['date']): copy.deepcopy(r) for r in (prior_audit or {}).get('changes', [])
               if r.get('status') == 'pending_verification'}
    pending.update({(r['market'], r['date']): r for r in changes})
    changes = [pending[key] for key in sorted(pending)]
    audit = {"generated_at": datetime.now(timezone.utc).isoformat(), "published_through": cutoff,
             "published_sha256": hashlib.sha256(json_bytes(published)).hexdigest(),
             "refreshed_sha256": hashlib.sha256(json_bytes(refreshed)).hexdigest(),
             "changes": changes}
    # These checks describe observations, not an independent validation of retained old values.
    accepted["validation"] = {"refreshed_observations": refreshed["validation"],
                              "retained_history": {"through": cutoff, "matches_published": True}}
    accepted["metadata"]["revision_policy"] = "retain published market caps; audit refreshed observations separately"
    accepted["metadata"]["pending_revision_count"] = len(changes)
    return accepted, audit


def install_outputs(pairs: list[tuple[Path, Path]], backup_dir: Path) -> None:
    """Rollback on an install error. Leave a durable journal for interrupted-process recovery."""
    backup_dir.mkdir(parents=True, exist_ok=False)
    journal = []
    for index, (candidate, target) in enumerate(pairs):
        backup = backup_dir / str(index)
        exists = target.exists()
        if exists:
            shutil.copy2(target, backup)
        journal.append({"target": str(target), "backup": str(backup), "existed": exists})
    marker = backup_dir / "in-progress.json"
    marker.write_bytes(json_bytes(journal))
    try:
        for candidate, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate, target)
    except BaseException:
        for record in journal:
            target = Path(record["target"])
            if record["existed"]:
                shutil.copy2(record["backup"], target)
            else:
                target.unlink(missing_ok=True)
        marker.rename(backup_dir / "rolled-back.json")
        raise
    marker.rename(backup_dir / "installed.json")
