import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import update_and_publish as runner
import official_daily_audit as audit


class PublisherTests(unittest.TestCase):
    def test_friday_after_cutoff_and_weekend(self):
        holidays=[{'Date':'1150101','Name':'元旦'}]
        for stamp in ('2026-11-06T19:00:00','2026-11-07T10:00:00','2026-11-09T10:00:00'):
            self.assertEqual(runner.expected_day(datetime.fromisoformat(stamp),holidays),'2026-11-06')

    def test_holiday_and_trading_marker(self):
        rows=[{'Date':'1150101','Name':'元旦'},{'Date':'1151106','Name':'休市'}]
        self.assertEqual(runner.expected_day(datetime(2026,11,7,10),rows),'2026-11-05')
        rows[-1]['Name']='國曆新年開始交易日'
        self.assertEqual(runner.expected_day(datetime(2026,11,7,10),rows),'2026-11-06')

    def test_unknown_calendar_year_stops(self):
        with self.assertRaises(RuntimeError): runner.expected_day(datetime(2027,2,3,19),[{'Date':'1150101','Name':'元旦'}])

    def test_report_contains_status_and_never_claims_success(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            runner.write_report(out,{'status':'已停止','stage':'官方驗算','started_at':'2026-11-07','message':'輸入不符','pushed':False})
            text=(out/'報告.txt').read_text()
            self.assertIn('已停止',text);self.assertIn('輸入不符',text)
            self.assertNotIn('發布成功',text)

    def test_dirty_tree_stops_before_network_data_and_build(self):
        def git(*args):
            return {'remote':'git@github.com:willychen0924/tw-margin-rate.git','diff':'','rev-parse':'abc','ls-remote':'abc refs/heads/main','status':' M web/chart.js'}[args[0]]
        with tempfile.TemporaryDirectory() as directory, patch.object(runner,'git',side_effect=git), patch.object(runner,'request_json') as fetch:
            with self.assertRaisesRegex(RuntimeError,'未提交'): runner.execute(Path(directory),{'status':'執行中','stage':'','started_at':'test'})
            fetch.assert_not_called()

    def test_wrong_remote_stops(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner,'git',return_value='git@github.com:someone/else.git'):
            with self.assertRaises(RuntimeError): runner.execute(Path(directory),{'status':'執行中','stage':'','started_at':'test'})

    def fixture(self):
        return {'date':'2026-11-06','market':'twse','missing_local':[],'margin_mismatches':[],'valid_close_mismatches':[],'identity_errors':[],'cost_mismatches':[],'universe_diff':{'official_only':[],'local_only':[]},'same_rounded_rate':True,'same_balance':True,'same_money':True,'ratio_arithmetic_correct':True,'official_index':123,'published_index':123,'discontinuities':[]}

    def test_independent_input_failures_stop(self):
        for key in ('missing_local','margin_mismatches','valid_close_mismatches','identity_errors','cost_mismatches'):
            row=self.fixture(); row[key]=['bad']
            with patch.object(audit,'DAYS',['2026-11-06']),self.assertRaises(RuntimeError): audit.validate_report({'days':[row,row]})

    def test_independent_aggregate_failures_stop(self):
        for key in ('same_rounded_rate','same_balance','same_money','ratio_arithmetic_correct'):
            row=self.fixture();row[key]=False
            with patch.object(audit,'DAYS',['2026-11-06']),self.assertRaises(RuntimeError): audit.validate_report({'days':[row,row]})

    def test_large_discontinuity_stops_small_official_adjustment_logged(self):
        row=self.fixture();row['discontinuities']=[['1234',1000,1005]]
        with patch.object(audit,'DAYS',['2026-11-06']): audit.validate_report({'days':[row,row]})
        row['discontinuities']=[['1234',1000,2000]]
        with patch.object(audit,'DAYS',['2026-11-06']),self.assertRaises(RuntimeError): audit.validate_report({'days':[row,row]})

    def test_missing_days_and_universe_stops(self):
        with patch.object(audit,'DAYS',['2026-11-06']),self.assertRaises(RuntimeError): audit.validate_report({'days':[]})
        row=self.fixture();row['universe_diff']['local_only']=['1234']
        with patch.object(audit,'DAYS',['2026-11-06']),self.assertRaises(RuntimeError): audit.validate_report({'days':[row,row]})

    def test_retries_are_bounded(self):
        import requests
        with patch.object(requests.Session,'request',side_effect=requests.Timeout),patch.object(audit.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'官方來源'): audit.request_json(requests.Session(),'GET','https://example.test')

    def test_no_model_runtime_dependencies(self):
        text=(ROOT/'scripts/launch_publisher.sh').read_text()
        self.assertNotIn('codex ',text)
        self.assertNotIn('OPENAI_API_KEY',text)
        self.assertIn('update_and_publish.py',text)

    def test_deploy_failure_never_runs_public_browser(self):
        runs={'workflow_runs':[{'id':1,'head_sha':'abc','status':'completed','conclusion':'failure'}]}
        with tempfile.TemporaryDirectory() as d,patch.object(runner,'request_json',return_value=runs),patch('browser_acceptance.verify') as browser:
            with self.assertRaisesRegex(RuntimeError,'部署失敗'): runner.verify_public('abc',Path(d)/'missing.html',{},Path(d),timeout=1)
            browser.assert_not_called()

    def test_public_wrong_bytes_never_success(self):
        from unittest.mock import Mock
        runs={'workflow_runs':[{'id':1,'head_sha':'abc','status':'completed','conclusion':'success'}]}
        response=Mock(status_code=200,content=b'old version')
        with tempfile.TemporaryDirectory() as d,patch.object(runner,'request_json',return_value=runs),patch('requests.Session.get',return_value=response),patch.object(runner.time,'monotonic',side_effect=[0,0,2]),patch.object(runner.time,'sleep'),patch('browser_acceptance.verify') as browser:
            p=Path(d)/'index.html';p.write_bytes(b'new version')
            with self.assertRaisesRegex(RuntimeError,'逾時'): runner.verify_public('abc',p,{},Path(d),timeout=1)
            browser.assert_not_called()

    def test_public_success_requires_run_bytes_and_browser(self):
        from unittest.mock import Mock
        runs={'workflow_runs':[{'id':1,'head_sha':'abc','status':'completed','conclusion':'success'}]}
        response=Mock(status_code=200,content=b'verified')
        with tempfile.TemporaryDirectory() as d,patch.object(runner,'request_json',return_value=runs),patch('requests.Session.get',return_value=response),patch('browser_acceptance.verify',return_value={'passed':True}) as browser:
            p=Path(d)/'index.html';p.write_bytes(b'verified')
            result=runner.verify_public('abc',p,{},Path(d),timeout=1)
            self.assertEqual(result['sha'],'abc');browser.assert_called_once()

    def test_legacy_publish_cannot_bypass_checks(self):
        import update_margin_rate
        with self.assertRaisesRegex(RuntimeError,'完整查核'): update_margin_rate.publish('2026-11-06')

    def test_native_layout_matches_sp_daily_updater_spec(self):
        source=(ROOT/'scripts/PublisherApp.swift').read_text()
        for snippet in ('width: 500, height: 270','boldSystemFont(ofSize: 17)','preferredFont(forTextStyle: .body)','systemFont(ofSize: 11)','x: 24, y: 214, width: 452, height: 26','x: 24, y: 87, width: 452, height: 116','x: 278, y: 24, width: 90, height: 28','x: 376, y: 24, width: 100, height: 28'):
            self.assertIn(snippet,source)

    def test_stage_progress_only_public_success_reaches_100(self):
        stages=['啟動','前置檢查','候選重算與測試','官方獨立驗算與公司行動','本機網站驗收','安裝、提交及推送','等待部署及公開驗收','公開網站驗收']
        values=[runner.progress_percent({'stage':s,'status':'執行中'}) for s in stages]
        self.assertEqual(values,sorted(set(values)))
        self.assertLess(max(values),100)
        for status in ('已停止','部署未完成','驗證通過（未發布）'):
            self.assertLess(runner.progress_percent({'stage':stages[-1],'status':status}),100)
        for status in ('發布成功','已是最新，公開驗證通過'):
            self.assertEqual(runner.progress_percent({'stage':stages[-1],'status':status}),100)

    def test_native_progress_bar_remains_visible_on_completion(self):
        source=(ROOT/'scripts/PublisherApp.swift').read_text()
        self.assertIn('progress.isIndeterminate = false',source)
        self.assertIn('progress.maxValue = 100',source)
        self.assertNotIn('progress.isHidden = true',source)
        self.assertIn('state["progress_percent"]',source)

if __name__=='__main__': unittest.main()
