"""Year-aware official TWSE calendars; never infer an uncovered year's holidays."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule'
REFERENCE = ROOT / 'data/reference/trading-calendars'
CACHE = ROOT / 'data/cache/trading-calendars'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def content_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def parse_rows(rows):
    require(isinstance(rows, list) and rows, '官方休市表為空或格式不符')
    parsed = {}
    for row in rows:
        require(isinstance(row, dict), '官方休市表列格式不符')
        text = row.get('Date')
        require(isinstance(text, str) and re.fullmatch(r'[0-9]{7}', text), '官方休市表日期格式不符')
        try:
            day = date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
        except ValueError:
            raise RuntimeError('官方休市表包含無效日期') from None
        require(isinstance(row.get('Name'), str) and row['Name'].strip(), '官方休市表缺少名稱')
        require(day not in parsed, '官方休市表日期重複或衝突')
        if 'Weekday' in row:
            require(row['Weekday'] == '一二三四五六日'[day.weekday()], '官方休市表星期與日期不符')
        parsed[day] = row
    return parsed


def validate_annual(rows, year):
    parsed = parse_rows(rows)
    require({day.year for day in parsed} == {year}, f'官方休市表年份不符：需要 {year} 年')
    # An annual response must not silently become a partial month/quarter list.
    require(len(parsed) >= 15 and date(year, 1, 1) in parsed and max(parsed).month == 12,
            f'{year} 年官方休市表年度範圍不完整')
    names = [row['Name'] for row in rows]
    for marker in ('國曆新年開始交易', '農曆春節前最後交易', '農曆春節後開始交易'):
        require(any(marker in name for name in names), f'{year} 年官方休市表缺少交易日標記')
    return parsed


def cutoff_day(now):
    return now.date() if now.hour >= 18 else now.date() - timedelta(days=1)


def expected_day(now, holidays):
    parsed = parse_rows(holidays)
    years = {day.year for day in parsed}
    day = cutoff_day(now)
    for _ in range(30):
        # Check every year crossed, not only the starting year on January 1.
        require(day.year in years, f'官方休市表未涵蓋所需年份：{day.year}')
        row = parsed.get(day)
        explicitly_open = row and ('開始交易' in row['Name'] or '最後交易' in row['Name'])
        if explicitly_open or (day.weekday() < 5 and row is None):
            return day.isoformat()
        day -= timedelta(days=1)
    raise RuntimeError('無法判定最新完整交易日')


def snapshot(rows, captured_at):
    years = {day.year for day in parse_rows(rows)}
    require(len(years) == 1, '官方最新休市表混有多個年份，須查證')
    year = next(iter(years))
    validate_annual(rows, year)
    return {'schema': 1, 'year': year, 'source': SOURCE, 'captured_at': captured_at,
            'rows_sha256': content_hash(rows), 'rows': rows}


def read_snapshot(path, year, expected_hash=None):
    try:
        raw = path.read_bytes()
        require(expected_hash is None or hashlib.sha256(raw).hexdigest() == expected_hash,
                f'{year} 年休市表核准檔案雜湊不符')
        value = json.loads(raw)
        require(value['schema'] == 1 and value['year'] == year and value['source'] == SOURCE,
                f'{year} 年休市表來源或版本不符')
        require(value['rows_sha256'] == content_hash(value['rows']), f'{year} 年休市表內容雜湊不符')
        stamp = datetime.fromisoformat(value['captured_at'])
        require(stamp.tzinfo is not None, f'{year} 年休市表缺少來源時間時區')
        validate_annual(value['rows'], year)
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError(f'{year} 年休市表保存檔損壞或不可讀，停止而不猜測') from None


def save_snapshot(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def load_calendars(now, published_end, out, fetch, *, reference=REFERENCE, cache=CACHE):
    """Fetch live every run; a verified annual snapshot may fill a rolled-off year.

    No fallback on network failure or malformed live responses. Snapshots are not
    proof of ad-hoc closures: missing official daily inputs still stop publication.
    """
    prior = date.fromisoformat(published_end)
    cutoff = cutoff_day(now)
    require(prior <= cutoff, '已發布日期晚於本次截止日，停止避免倒退更新')
    first = min(prior, cutoff) - timedelta(days=30)
    years = set(range(first.year, cutoff.year + 1))
    live_rows = fetch(SOURCE)
    (out / 'official-holidays-live.json').write_text(json.dumps(live_rows, ensure_ascii=False, indent=2))
    live = snapshot(live_rows, now.isoformat())
    require(cutoff.year <= live['year'] <= now.year + 1, '官方最新休市表年份落後或異常超前，須查證')
    # Refresh only that year; next year's release never overwrites this year's file.
    save_snapshot(cache / f"twse-{live['year']}.json", live)
    selected = []
    merged = []
    for year in sorted(years):
        if year == live['year']:
            value, origin = live, 'live'
        else:
            choices = []
            if (reference / 'manifest.json').exists():
                try:
                    manifest = json.loads((reference / 'manifest.json').read_text())
                    entry = manifest['years'].get(str(year))
                    if entry:
                        require(entry['file'] == f'twse-{year}.json', '休市表 manifest 路徑不符')
                        choices.append((read_snapshot(reference / entry['file'], year, entry['sha256']), 'reviewed-reference'))
                except (OSError, ValueError, KeyError, TypeError):
                    raise RuntimeError('休市表年度 manifest 損壞或不可讀') from None
            cached = cache / f'twse-{year}.json'
            if cached.exists():
                choices.append((read_snapshot(cached, year), 'saved-official'))
            require(choices, f'官方休市表未涵蓋所需年份：{year}；沒有已核驗年度保存檔')
            choices.sort(key=lambda item: datetime.fromisoformat(item[0]['captured_at']))
            if len(choices) == 2 and choices[0][0]['captured_at'] == choices[1][0]['captured_at']:
                require(choices[0][0]['rows_sha256'] == choices[1][0]['rows_sha256'], '同時間官方休市表保存檔互相衝突')
            value, origin = choices[-1]
        require(datetime.fromisoformat(value['captured_at']) <= now, '官方休市表來源時間晚於現在')
        merged.extend(value['rows'])
        selected.append({key: value[key] for key in ('year', 'source', 'captured_at', 'rows_sha256')})
        selected[-1]['selection'] = origin
    parse_rows(merged)
    proof = {'live_year': live['year'], 'required_years': sorted(years), 'selected': selected,
             'note': '年度表不代表已證實臨時休市；來源缺日或官方逐股輸入不符仍停止。'}
    (out / 'official-calendar-check.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    (out / 'official-holidays.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    return merged
