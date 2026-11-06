#!/usr/bin/env python3
"""Single safe entry point: check, rebuild, validate, atomically install, optionally publish."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tw_margin_rate.finmind import load_dotenv
from tw_margin_rate.paths import assert_archive_ready, discover_stock_data, local_env_path
from tw_margin_rate.revisions import retain_published_market_caps, json_bytes, install_outputs


def parse_args() -> argparse.Namespace:
    config = json.loads(
        (PROJECT_ROOT / "config/project.json").read_text(encoding="utf-8")
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--end")
    parser.add_argument("--stock-data", type=Path)
    parser.add_argument("--warmup-start", default=config["warmup_start"])
    parser.add_argument("--display-start", default=config["display_start"])
    parser.add_argument(
        "--twse-market-cap-start",
        default=config["market_cap_starts"]["twse"],
    )
    parser.add_argument(
        "--tpex-market-cap-start",
        default=config["market_cap_starts"]["tpex"],
    )
    parser.add_argument("--no-finmind-fetch", action="store_true")
    parser.add_argument("--refresh-reference", action="store_true")
    parser.add_argument(
        "--allow-history-rewrite",
        action="store_true",
        help="Skip the previous-history equality check after an approved correction.",
    )
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--candidate-only", action="store_true", help="Build and test without installing outputs")
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True, env=env)


def publish(latest_day: str) -> None:
    raise RuntimeError("請使用更新並發布.app 的完整查核發布入口")


def main(args=None):
    args = args or parse_args()
    if getattr(args, "publish", False):
        raise RuntimeError("請使用更新並發布.app；舊 --publish 不包含完整獨立驗算與瀏覽器驗收，已停用")
    stock_data = discover_stock_data(args.stock_data)
    latest = assert_archive_ready(stock_data, args.warmup_start)
    print(f"iCloud archive ready: {latest}")

    load_dotenv(local_env_path())
    has_token = bool(os.environ.get("FINMIND_TOKEN"))
    if args.refresh_reference:
        run([sys.executable, "scripts/fetch_market_reference.py"])

    history = PROJECT_ROOT / "data/processed/margin-maintenance-history.json"
    html = PROJECT_ROOT / "docs/index.html"
    root_html = PROJECT_ROOT / "index.html"
    previous = json.loads(history.read_text(encoding="utf-8"))
    previous_end = previous["metadata"]["end"]
    temp_parent = PROJECT_ROOT / "data/tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    interrupted = list(temp_parent.glob("margin-update-*/install-backup/in-progress.json"))
    if interrupted:
        raise RuntimeError(f"發現未完成的產物安裝，請依保存的備份復原後再更新：{interrupted}")
    # Keep run evidence on failure as well as success; never delete the rejected candidate.
    temp_root = Path(tempfile.mkdtemp(prefix="margin-update-", dir=temp_parent))
    print(f"本次候選與查核紀錄：{temp_root}", flush=True)
    money = PROJECT_ROOT / "data/cache/market-margin-money-history.json"
    cap = PROJECT_ROOT / "data/cache/market-cap-history.json"
    staged_money = temp_root / "market-margin-money-history.json"
    staged_cap = temp_root / "market-cap-history.json"
    shutil.copy2(money, staged_money)
    shutil.copy2(cap, staged_cap)
    shutil.copy2(history, temp_root / "previous-history.json")
    prior_cap = json.loads(cap.read_text(encoding="utf-8"))
    protected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (history, html, root_html, money, cap)}
    margin_money_command = [sys.executable, "scripts/fetch_margin_money_history.py",
                            "--start", args.display_start, "--output", str(staged_money)]
    if args.end:
        margin_money_command.extend(["--end", args.end])
    run(margin_money_command)
    market_cap_command = [sys.executable, "scripts/fetch_market_cap_history.py",
                          "--twse-start", args.twse_market_cap_start,
                          "--tpex-start", args.tpex_market_cap_start,
                          "--stock-data", str(stock_data), "--margin-history", str(staged_money),
                          "--output", str(staged_cap)]
    if args.end:
        market_cap_command.extend(["--end", args.end])
    run(market_cap_command)
    refreshed = json.loads(staged_cap.read_text(encoding="utf-8"))
    shutil.copy2(staged_cap, temp_root / "refreshed-market-cap-history.json")
    if not args.allow_history_rewrite:
        ledger = PROJECT_ROOT / "data/reference/market-cap-revisions.json"
        prior_audit = json.loads(ledger.read_text()) if ledger.exists() else None
        accepted, audit = retain_published_market_caps(previous, prior_cap, refreshed, prior_audit)
        staged_cap.write_bytes(json_bytes(accepted))
    else:
        audit = {"published_through": previous_end, "mode": "explicit-history-rewrite", "changes": []}
    staged_audit = temp_root / "market-cap-revisions.json"
    staged_audit.write_bytes(json_bytes(audit))
    print(f"保留已發布市值；待查修訂 {len(audit['changes'])} 筆", flush=True)
    try:
        candidate_history = temp_root / "history.json"
        candidate_html = temp_root / "index.html"
        candidate_root_html = temp_root / "root-index.html"
        shutil.copy2(html, candidate_html)
        command = [
            sys.executable,
            "scripts/build_margin_maintenance_history.py",
            "--stock-data",
            str(stock_data),
            "--workspace",
            str(PROJECT_ROOT),
            "--warmup-start",
            args.warmup_start,
            "--display-start",
            args.display_start,
            "--output",
            str(candidate_history),
            "--margin-money-history", str(staged_money),
            "--market-cap-history", str(staged_cap),
        ]
        if args.end:
            command.extend(["--end", args.end])
        if args.no_finmind_fetch or not has_token:
            command.append("--no-finmind-fetch")
            if not has_token and not args.no_finmind_fetch:
                print("未設定 FINMIND_TOKEN；只使用 iCloud 與專案補充資料。")
        run(command)
        # 2026-11-07: user explicitly removed WantGoo from daily publication gates.
        run(
            [
                sys.executable,
                "scripts/update_margin_maintenance_chart_data.py",
                "--history",
                str(candidate_history),
                "--html",
                str(candidate_html),
            ]
        )
        shutil.copy2(candidate_html, candidate_root_html)
        validate = [
            sys.executable,
            "scripts/validate_margin_outputs.py",
            "--history",
            str(candidate_history),
            "--html",
            str(candidate_html),
        ]
        if args.allow_history_rewrite:
            print(
                "已啟用經核准的歷史更正；跳過舊版逐列相等檢查。",
                flush=True,
            )
        else:
            validate.extend(
                ["--reference", str(history), "--through", previous_end]
            )
        if args.end == "2026-07-30":
            validate.append("--expect-baseline")
        run(validate)
        run(
            [
                sys.executable,
                "scripts/validate_margin_outputs.py",
                "--history",
                str(candidate_history),
                "--html",
                str(candidate_root_html),
            ]
        )
        test_env = os.environ.copy()
        test_env.update(
            {
                "TW_MARGIN_HISTORY_PATH": str(candidate_history),
                "TW_MARGIN_HTML_PATH": str(candidate_html),
                "TW_MARGIN_ROOT_HTML_PATH": str(candidate_root_html),
            }
        )
        run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            env=test_env,
        )
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != digest for p, digest in protected.items()):
            raise RuntimeError("更新期間正式檔案被其他程序或同步修改，停止安裝")
        if getattr(args, "candidate_only", False):
            return temp_root
        install_outputs([(candidate_history, history), (candidate_html, html),
                         (candidate_root_html, root_html), (staged_money, money), (staged_cap, cap),
                         (staged_audit, PROJECT_ROOT / "data/reference/market-cap-revisions.json")],
                        temp_root / "install-backup")
    except BaseException:
        print(f"更新未完成；候選與差異已保留於 {temp_root}", flush=True)
        raise

    run(
        [
            sys.executable,
            "scripts/validate_margin_outputs.py",
            "--history",
            str(history),
            "--html",
            str(html),
        ]
    )
    run(
        [
            sys.executable,
            "scripts/validate_margin_outputs.py",
            "--history",
            str(history),
            "--html",
            str(root_html),
        ]
    )
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    payload = json.loads(history.read_text(encoding="utf-8"))
    latest_day = payload["metadata"]["end"]
    print(f"本地更新與驗證完成：{latest_day}")
    print("瀏覽器檢查：python3 -m http.server 8765 --directory docs")
    if args.publish:
        publish(latest_day)


if __name__ == "__main__":
    lock_path = PROJECT_ROOT / "data/tmp/update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("本機已有更新程序執行中")
        main()
