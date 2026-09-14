#!/usr/bin/env python3
"""Single safe entry point: check, rebuild, validate, atomically install, optionally publish."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tw_margin_rate.finmind import load_dotenv
from tw_margin_rate.paths import assert_archive_ready, discover_stock_data, local_env_path
from tw_margin_rate.publishing import assert_official_origin
from tw_margin_rate.revisions import retain_published_market_caps, json_bytes, install_outputs
from check_wantgoo_reference import compare as compare_benchmark


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
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True, env=env)


def publish(latest_day: str) -> None:
    benchmark = compare_benchmark(
        json.loads((PROJECT_ROOT / "data/processed/margin-maintenance-history.json").read_text()),
        json.loads((PROJECT_ROOT / "data/reference/wantgoo-observations.json").read_text()))
    if any(v["status"] != "same_date_available" for v in benchmark["markets"].values()):
        raise RuntimeError("最新日期尚未核對玩股網，請用瀏覽器讀取同日參考值並保存紀錄後發布")
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert_official_origin(remote)
    allowed = {
        "data/processed/margin-maintenance-history.json",
        "docs/index.html",
        "index.html",
        "data/reference/twse-company-info.latest.json",
        "data/reference/twse-delisted.latest.html",
        "data/reference/twse-newlisting.latest.json",
        "data/reference/latest-manifest.json",
        "data/cache/market-margin-money-history.json",
        "data/cache/market-cap-history.json",
        "data/reference/market-cap-revisions.json",
        "data/reference/wantgoo-observations.json",
    }
    cache_path = re.compile(
        r"data/cache/(?:TaiwanStockPrice|TaiwanStockMarginPurchaseShortSale|"
        r"TaiwanStockTotalMarginPurchaseShortSale|TaiwanStockMarketValue)/"
        r"\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}_market_[0-9a-f]{12}\.json\.gz"
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    unexpected = []
    changed = []
    for line in status:
        path = line[3:]
        if path not in allowed and not cache_path.fullmatch(path):
            unexpected.append(line)
        else:
            changed.append(path)
    if unexpected:
        raise RuntimeError(f"有更新流程以外的未提交變更，停止發布：{unexpected}")
    changed = sorted(set(changed))
    if not changed:
        print("沒有需要發布的新變更")
        return
    run(["git", "add", *changed])
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=PROJECT_ROOT
    )
    if staged.returncode == 0:
        print("沒有可提交的內容")
        return
    run(["git", "commit", "-m", f"Update margin rate through {latest_day}"])
    run(["git", "push", "origin", "HEAD:main"])
    print(
        "推送完成；仍須開啟 "
        f"https://willychen0924.github.io/tw-margin-rate/?v={latest_day.replace('-', '')} "
        "核對 Pages。"
    )


def main() -> None:
    args = parse_args()
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
        run([sys.executable, "scripts/check_wantgoo_reference.py", "--history", str(candidate_history),
             "--output", str(temp_root / "wantgoo-comparison.json")])
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
