#!/usr/bin/env python3
"""Single safe entry point: check, rebuild, validate, atomically install, optionally publish."""

from __future__ import annotations

import argparse
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


def parse_args() -> argparse.Namespace:
    config = json.loads(
        (PROJECT_ROOT / "config/project.json").read_text(encoding="utf-8")
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--end")
    parser.add_argument("--stock-data", type=Path)
    parser.add_argument("--warmup-start", default=config["warmup_start"])
    parser.add_argument("--display-start", default=config["display_start"])
    parser.add_argument("--no-finmind-fetch", action="store_true")
    parser.add_argument("--refresh-reference", action="store_true")
    parser.add_argument("--publish", action="store_true")
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True, env=env)


def publish(latest_day: str) -> None:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if "willychen0924/tw-margin-rate" not in remote:
        raise RuntimeError(f"origin 不是指定公開儲存庫：{remote}")
    allowed = {
        "data/processed/margin-maintenance-history.json",
        "docs/index.html",
        "index.html",
        "data/reference/twse-company-info.latest.json",
        "data/reference/twse-delisted.latest.html",
        "data/reference/latest-manifest.json",
    }
    cache_path = re.compile(
        r"data/cache/(?:TaiwanStockPrice|TaiwanStockMarginPurchaseShortSale)/"
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
    with tempfile.TemporaryDirectory(prefix="margin-update-", dir=temp_parent) as temp:
        temp_root = Path(temp)
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
        ]
        if args.end:
            command.extend(["--end", args.end])
        if args.no_finmind_fetch or not has_token:
            command.append("--no-finmind-fetch")
            if not has_token and not args.no_finmind_fetch:
                print("未設定 FINMIND_TOKEN；只使用 iCloud 與專案補充資料。")
        run(command)
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
            "--reference",
            str(history),
            "--through",
            previous_end,
        ]
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
        os.replace(candidate_history, history)
        os.replace(candidate_html, html)
        os.replace(candidate_root_html, root_html)

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
    main()
