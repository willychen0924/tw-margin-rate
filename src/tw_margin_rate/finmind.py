from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.finmindtrade.com/api/v4/data"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class FinMindError(RuntimeError):
    pass


class FinMindClient:
    def __init__(self, token: str, cache_dir: Path, timeout: int = 180) -> None:
        if not token or token in {"你的_token", "實際的_token"}:
            raise ValueError("FINMIND_TOKEN 尚未設定為有效 Token")
        self._token = token
        self.cache_dir = cache_dir
        self.timeout = timeout

    def fetch(
        self,
        dataset: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        data_id: str | None = None,
        force: bool = False,
        max_age_hours: float | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"dataset": dataset}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if data_id:
            params["data_id"] = data_id

        cache_file = self._cache_path(dataset, params)
        if not force and self._cache_valid(cache_file, max_age_hours):
            with gzip.open(cache_file, "rt", encoding="utf-8") as handle:
                return json.load(handle)["data"]

        label = dataset
        if start_date:
            label += f" {start_date}..{end_date or 'now'}"
        print(f"[FinMind] 下載 {label}", flush=True)
        payload = self._request(params)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise FinMindError(f"{dataset} 回傳格式缺少 data 陣列")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "query": params,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "data": rows,
        }
        temp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
        with gzip.open(temp_file, "wt", encoding="utf-8") as handle:
            json.dump(envelope, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_file, cache_file)
        print(f"[FinMind] 完成 {dataset}: {len(rows):,} 筆", flush=True)
        return rows

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        url = f"{API_URL}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "User-Agent": "tw-margin-rate/1.0",
            },
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if int(payload.get("status", 0)) != 200:
                    message = payload.get("msg") or payload.get("message") or "未知 API 錯誤"
                    raise FinMindError(f"FinMind status={payload.get('status')}: {message}")
                return payload
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, FinMindError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise FinMindError(f"FinMind 查詢失敗: {last_error}") from last_error

    def _cache_path(self, dataset: str, params: dict[str, str]) -> Path:
        key = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()[:12]
        date_part = params.get("start_date", "all")
        end_part = params.get("end_date", "now")
        data_id = params.get("data_id", "market")
        safe_name = f"{date_part}_{end_part}_{data_id}_{digest}.json.gz".replace("/", "-")
        return self.cache_dir / dataset / safe_name

    @staticmethod
    def _cache_valid(path: Path, max_age_hours: float | None) -> bool:
        if not path.exists():
            return False
        if max_age_hours is None:
            return True
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds <= max_age_hours * 3600
