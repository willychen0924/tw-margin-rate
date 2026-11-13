import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from tw_margin_rate import trading_calendar as cal


class TradingCalendarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / 'out'
        self.out.mkdir()
        self.cache = self.root / 'cache'
        self.reference = self.root / 'reference'
        self.reference.mkdir()
        self.now = datetime(2026, 11, 13, 23, 41, tzinfo=ZoneInfo('Asia/Taipei'))
        self.rows = {}
        for year in (2026, 2027):
            self.rows[year] = json.loads((cal.REFERENCE / f'twse-{year}.json').read_text())['rows']
        self.seed(2026)
        self.fetch = Mock(return_value=self.rows[2027])

    def seed(self, year):
        value = cal.snapshot(self.rows[year], '2026-11-13T02:38:02+08:00')
        body = json.dumps(value, ensure_ascii=False).encode()
        (self.reference / f'twse-{year}.json').write_bytes(body)
        path = self.reference / 'manifest.json'
        manifest = json.loads(path.read_text()) if path.exists() else {'years': {}}
        manifest['years'][str(year)] = {'file': f'twse-{year}.json', 'sha256': hashlib.sha256(body).hexdigest()}
        path.write_text(json.dumps(manifest))

    def load(self, now=None, prior='2026-11-12'):
        return cal.load_calendars(now or self.now, prior, self.out, self.fetch,
                                  reference=self.reference, cache=self.cache)

    def test_early_next_year_release_retains_reviewed_current_year(self):
        rows = self.load()
        self.assertEqual(cal.expected_day(self.now, rows), '2026-11-13')
        proof = json.loads((self.out / 'official-calendar-check.json').read_text())
        self.assertEqual(proof['live_year'], 2027)
        self.assertEqual(proof['required_years'], [2026])
        self.assertEqual(proof['selected'][0]['selection'], 'reviewed-reference')
        self.assertTrue((self.cache / 'twse-2027.json').exists())
        self.fetch.assert_called_once_with(cal.SOURCE)

    def test_live_current_year_still_refreshed_on_every_run(self):
        self.fetch.return_value = self.rows[2026]
        self.load()
        self.load()
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(cal.read_snapshot(self.cache / 'twse-2026.json', 2026)['rows'], self.rows[2026])

    def test_saved_current_year_survives_next_year_cache_write(self):
        self.fetch.return_value = self.rows[2026]
        self.load()
        path = self.cache / 'twse-2026.json'
        before = path.read_bytes()
        self.fetch.return_value = self.rows[2027]
        self.assertEqual(cal.expected_day(self.now, self.load()), '2026-11-13')
        self.assertEqual(path.read_bytes(), before)

    def test_no_valid_calendar_never_guesses_weekday(self):
        (self.reference / 'manifest.json').unlink()
        with self.assertRaisesRegex(RuntimeError, '沒有已核驗'):
            self.load()

    def test_january_first_backtracks_using_previous_calendar(self):
        now = datetime(2027, 1, 1, 19, tzinfo=ZoneInfo('Asia/Taipei'))
        rows = self.load(now, '2026-12-30')
        self.assertEqual(cal.expected_day(now, rows), '2026-12-31')
        proof = json.loads((self.out / 'official-calendar-check.json').read_text())
        self.assertEqual(proof['required_years'], [2026, 2027])

    def test_before_cutoff_and_first_trading_day(self):
        rows = self.load(datetime(2027, 1, 4, 17, 59, tzinfo=ZoneInfo('Asia/Taipei')), '2026-12-31')
        self.assertEqual(cal.expected_day(datetime(2027, 1, 4, 17, 59), rows), '2026-12-31')
        self.assertEqual(cal.expected_day(datetime(2027, 1, 4, 18), rows), '2027-01-04')

    def test_unknown_previous_year_stops_during_backtrack(self):
        with self.assertRaisesRegex(RuntimeError, '2026'):
            cal.expected_day(datetime(2027, 1, 1, 19), self.rows[2027])

    def test_network_failure_does_not_fallback_to_snapshot(self):
        self.fetch.side_effect = RuntimeError('官方來源暫時無法讀取')
        with self.assertRaisesRegex(RuntimeError, '官方來源'):
            self.load()

    def test_malformed_live_response_does_not_fallback(self):
        for value in ([], {}, [{'Date': '1160101', 'Name': '元旦'}]):
            with self.subTest(value=value):
                self.fetch.return_value = value
                with self.assertRaises(RuntimeError):
                    self.load()

    def test_duplicate_and_conflicting_dates_stop(self):
        for name in ('元旦', '國曆新年開始交易日'):
            rows = copy.deepcopy(self.rows[2027])
            rows.append({'Date': '1160101', 'Name': name})
            with self.assertRaisesRegex(RuntimeError, '重複或衝突'):
                cal.snapshot(rows, self.now.isoformat())

    def test_bad_date_weekday_and_mixed_year_stop(self):
        for field, value in (('Date', '1160230'), ('Weekday', '一'), ('Date', '1150101')):
            rows = copy.deepcopy(self.rows[2027])
            rows[0][field] = value
            with self.assertRaises(RuntimeError):
                cal.snapshot(rows, self.now.isoformat())

    def test_modified_reviewed_reference_stops(self):
        path = self.reference / 'twse-2026.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(RuntimeError, '核准檔案雜湊'):
            self.load()

    def test_corrupt_saved_calendar_stops_even_with_valid_reference(self):
        self.cache.mkdir()
        (self.cache / 'twse-2026.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, '損壞'):
            self.load()

    def test_modified_cache_contents_stop(self):
        value = cal.snapshot(self.rows[2026], self.now.isoformat())
        value['rows'][0]['Name'] = 'invalid change'
        cal.save_snapshot(self.cache / 'twse-2026.json', value)
        with self.assertRaisesRegex(RuntimeError, '內容雜湊'):
            self.load()

    def test_all_gap_years_must_be_available(self):
        with self.assertRaisesRegex(RuntimeError, '2025'):
            self.load(prior='2025-11-01')

    def test_official_live_stale_year_stops_even_if_reference_available(self):
        self.fetch.return_value = self.rows[2026]
        self.seed(2027)
        with self.assertRaisesRegex(RuntimeError, '年份落後'):
            self.load(datetime(2027, 2, 1, 19, tzinfo=ZoneInfo('Asia/Taipei')), '2027-01-29')

    def test_published_date_in_future_stops(self):
        with self.assertRaisesRegex(RuntimeError, '倒退更新'):
            self.load(prior='2026-11-16')
        self.fetch.assert_not_called()

    def test_repository_snapshots_match_reviewed_hashes(self):
        manifest = json.loads((cal.REFERENCE / 'manifest.json').read_text())
        for year, entry in manifest['years'].items():
            value = cal.read_snapshot(cal.REFERENCE / entry['file'], int(year), entry['sha256'])
            self.assertEqual(len(value['rows']), entry['row_count'])


if __name__ == '__main__':
    unittest.main()
