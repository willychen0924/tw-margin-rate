import copy
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from tw_margin_rate.revisions import retain_published_market_caps, install_outputs
from tw_margin_rate.finmind import FinMindClient
from validate_margin_outputs import compare_rows
from check_wantgoo_reference import compare


class RevisionTests(unittest.TestCase):
    def fixture(self):
        p = {'metadata': {'end': '2026-09-11'}, 'markets': {
            m: [{'date': '2026-09-11', 'market_cap': 100., 'maintenance': 160., 'financed_amount': 1., 'margin_market_cap_ratio': 1.}]
            for m in ('twse', 'tpex')}}
        old = {'metadata': {}, 'markets': {m: [{'date':'2026-09-11','market_cap':100., 'source_row_count': 1000}] for m in p['markets']}}
        new = copy.deepcopy(old)
        new['metadata'] = {'complete':True, 'validation_passed':True}
        new['validation'] = {'scope': {'gating': True}}
        for m in new['markets']:
            new['markets'][m][0]['market_cap'] = 101.
            new['markets'][m].append({'date':'2026-09-14','market_cap':110.})
        return p, old, new

    def test_old_caps_retained_new_day_kept_and_observations_not_mutated(self):
        p, old, new = self.fixture()
        result, audit = retain_published_market_caps(p, old, new)
        self.assertEqual(len(audit['changes']), 2)
        self.assertEqual(result['markets']['twse'], [old['markets']['twse'][0], {'date':'2026-09-14','market_cap':110.}])
        self.assertEqual(new['markets']['twse'][0]['market_cap'], 101.)
        self.assertIn('refreshed_observations', result['validation'])

    def test_missing_added_duplicate_dates_and_invalid_values_still_stop(self):
        for mode in ('missing', 'added', 'duplicate', 'nan', 'zero', 'incomplete', 'failed', 'baseline'):
            with self.subTest(mode=mode):
                p, old, new = self.fixture()
                if mode=='missing': new['markets']['twse'].pop(0)
                if mode=='added': new['markets']['twse'].insert(0, {'date':'2026-09-10','market_cap':99.})
                if mode=='duplicate': new['markets']['twse'].insert(0, new['markets']['twse'][0])
                if mode=='nan': new['markets']['twse'][-1]['market_cap']=float('nan')
                if mode=='zero': new['markets']['twse'][-1]['market_cap']=0.
                if mode=='incomplete': new['metadata']['complete']=False
                if mode=='failed': new['metadata']['validation_passed']=False
                if mode=='baseline': old['markets']['twse'][0]['market_cap']=90.
                with self.assertRaises(ValueError): retain_published_market_caps(p, old, new)

    def test_pending_findings_survive_leaving_refresh_window(self):
        p, old, new = self.fixture()
        for market in new['markets']: new['markets'][market][0]['market_cap']=100.
        pending={'changes':[{'market':'twse','date':'2026-09-11','accepted':100.,'observed':101.,'status':'pending_verification'}]}
        accepted,audit=retain_published_market_caps(p,old,new,pending)
        self.assertEqual(audit['changes'],pending['changes'])
        self.assertEqual(accepted['metadata']['pending_revision_count'],1)

    def test_retention_does_not_mask_other_historical_changes(self):
        p, _, _ = self.fixture()
        for key in ('maintenance', 'financed_amount', 'margin_market_cap_ratio'):
            candidate = copy.deepcopy(p)
            candidate['markets']['twse'][0][key] += 1
            with self.assertRaises(AssertionError): compare_rows(candidate, p, p['metadata']['end'])

    def test_fetched_source_keeps_both_versions_without_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FinMindClient('secret-test-token', Path(tmp))
            params = dict(start_date='2026-09-11', end_date='2026-09-12', force=True)
            for amount in (100,101):
                with patch.object(client, '_request', return_value={'data':[{'date':'2026-09-11','stock_id':'2330','market_value':amount}]}):
                    client.fetch('TaiwanStockMarketValue', **params)
            contents = [gzip.decompress(p.read_bytes()) for p in Path(tmp,'versions').rglob('*.gz')]
            self.assertEqual({json.loads(c)['data'][0]['market_value'] for c in contents if 'data' in json.loads(c)}, {100,101})
            self.assertTrue(all(b'secret-test-token' not in c for c in contents))
            diff = next(Path(tmp,'versions').rglob('diffs/*.gz'))
            change = json.loads(gzip.decompress(diff.read_bytes()))['changes'][0]
            self.assertEqual(change['before']['market_value'], 100)
            self.assertEqual(change['after']['market_value'], 101)
            cache = next(Path(tmp,'TaiwanStockMarketValue').glob('*.gz'))
            before = cache.read_bytes()
            with patch.object(client, '_request', side_effect=RuntimeError('network failure')):
                with self.assertRaises(RuntimeError): client.fetch('TaiwanStockMarketValue', **params)
            self.assertEqual(cache.read_bytes(), before)

    def test_install_failure_restores_all_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a,b,c,d=[root/name for name in ('a','b','c','d')]
            a.write_text('new a'); b.write_text('old b'); c.write_text('new c'); d.write_text('old d')
            import os
            real_replace=os.replace
            def fail_second(src,dst):
                if src==c: raise OSError('simulated disk failure')
                return real_replace(src,dst)
            with patch('tw_margin_rate.revisions.os.replace',side_effect=fail_second):
                with self.assertRaises(OSError): install_outputs([(a,b),(c,d)],root/'backups')
            self.assertEqual(b.read_text(),'old b')
            self.assertEqual(d.read_text(),'old d')
            self.assertTrue((root/'backups/rolled-back.json').exists())

    def test_stale_benchmark_is_not_current_and_opposite_move_is_visible(self):
        history={'metadata':{'end':'2026-09-14'},'markets':{m:[{'date':'2026-09-11','maintenance':160.}, {'date':'2026-09-14','maintenance':159.}] for m in ('twse','tpex')}}
        obs={'sources':{'twse':'example','tpex':'example'},'markets':{'twse':[{'date':'2026-09-11','maintenance':160.}], 'tpex':[{'date':'2026-09-11','maintenance':158.},{'date':'2026-09-14','maintenance':161.}]}}
        result=compare(history,obs)
        self.assertEqual(result['markets']['twse']['status'],'needs_browser_check')
        self.assertTrue(result['markets']['tpex']['latest_change']['opposite_direction'])

    def test_upstream_failure_leaves_formal_cache_and_history_untouched(self):
        import argparse
        import update_margin_rate as update
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            original={}
            for path,content in {'data/processed/margin-maintenance-history.json':'{"metadata":{"end":"2026-09-11"}}', 'data/cache/market-cap-history.json':'{}','data/cache/market-margin-money-history.json':'{}','docs/index.html':'old html','index.html':'old html'}.items():
                p=root/path; p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content);original[p]=p.read_bytes()
            args=argparse.Namespace(stock_data=None,warmup_start='2001-01-05',refresh_reference=False,end='2026-09-14',display_start='2017-07-03',twse_market_cap_start='2017-07-03',tpex_market_cap_start='2017-07-03')
            def failing_fetch(cmd,**kwargs):
                candidate=Path(cmd[cmd.index('--output')+1]);candidate.write_text('partial response')
                raise RuntimeError('failed upstream fetch')
            with patch.object(update,'PROJECT_ROOT',root),patch.object(update,'parse_args',return_value=args),patch.object(update,'discover_stock_data',return_value=root),patch.object(update,'assert_archive_ready',return_value={}),patch.object(update,'load_dotenv'),patch.object(update,'run',side_effect=failing_fetch):
                with self.assertRaises(RuntimeError): update.main()
            self.assertTrue(all(p.read_bytes()==value for p,value in original.items()))
            self.assertTrue(list((root/'data/tmp').glob('margin-update-*/market-margin-money-history.json')))


if __name__=='__main__': unittest.main()
