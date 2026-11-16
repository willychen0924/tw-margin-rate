import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
import official_daily_audit as audit
from tw_margin_rate import publication_reviews as reviews

TW_FIELDS = ['恢復買賣日期','股票代號','名稱','停止買賣前收盤價格','恢復買賣參考價',
             '漲停價格','跌停價格','開盤競價基準','除權參考價','減資原因','詳細資料']
TP_FIELDS = ['代號','名稱','停止買賣日期','減資換股率','每股退還股款(元)','減資原因',
             '恢復買賣日期','減資後現金增資配股率','現金增資認購價(元)','恢復買賣參考價']


class CapitalActionReviewTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(reviews.CAPITAL_ACTION_REVIEW_FILE.read_text())
        self.events = copy.deepcopy(self.record['company_actions'])
        self.holdings = [{'stock_id':r['code'], 'market':r['market'],
                         'MarginPurchaseTodayBalance':r['balance'], 'average_cost':r['average_cost']}
                         for r in self.record['portfolios']]

    def run_actions(self, events=None, holdings=None, day='2026-11-16', tw_fields=None, tp_fields=None):
        events = self.events if events is None else events
        holdings = self.holdings if holdings is None else holdings
        tw = {'stat':'OK', 'fields':TW_FIELDS if tw_fields is None else tw_fields,
              'data':[e['row'] for e in events if e['market']=='twse']}
        tp = {'tables':[{'fields':TP_FIELDS if tp_fields is None else tp_fields,
                         'data':[e['row'] for e in events if e['market']=='tpex']}]}
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory); (out/'official').mkdir()
            (out/f'portfolio-{day}.json').write_text(json.dumps(holdings))
            with patch.object(audit,'OUT',out), patch.object(audit,'DAYS',[day]), \
                 patch.object(audit,'request_json',side_effect=[tw,tp]):
                audit.check_actions()
            return json.loads((out/'company-action-review.json').read_text())

    def test_only_two_events_two_portfolios_one_adjustment_authorized(self):
        self.assertEqual([e['row'][1 if e['market']=='twse' else 0] for e in self.events], ['3714','4534'])
        self.assertEqual(self.record['portfolios'], [
            {'date':'2026-11-16','market':'twse','code':'3714','balance':10481,'average_cost':66.1883793655},
            {'date':'2026-11-16','market':'tpex','code':'4534','balance':0,'average_cost':28.9}])
        self.assertEqual(self.record['balance_adjustments'], [
            {'date':'2026-11-16','market':'twse','code':'3714','prior':20369,'yesterday':10705}])
        self.assertNotIn('market_cap_observations',self.record)
        for kind in ('company_actions','portfolios','balance_adjustments'):
            for row in self.record[kind]:
                self.assertTrue(reviews.reviewed(kind,row))
                changed=copy.deepcopy(row);changed['extra']='not reviewed'
                self.assertFalse(reviews.reviewed(kind,changed))

    def test_twse_uses_second_column_tpex_first_and_binds_costs(self):
        result=self.run_actions()
        self.assertEqual(result['events_in_window'],self.events)
        self.assertEqual(result['unreviewed'],[])

    def test_old_tpex_review_still_works(self):
        record=json.loads(reviews.REVIEW_FILE.read_text())
        holdings=[{'stock_id':r['code'],'market':r['market'],'MarginPurchaseTodayBalance':r['balance'],
                   'average_cost':r['average_cost']} for r in record['portfolios']]
        self.assertEqual(self.run_actions(record['company_actions'],holdings,day='2026-11-09')['unreviewed'],[])

    def test_changed_event_fields_or_unreviewed_event_stop(self):
        for i,event in enumerate(self.events):
            for column in range(len(event['row'])):
                # TPEx dates outside the queried window are not events in this window.
                if event['market']=='tpex' and column==6:
                    changed=copy.deepcopy(event);changed['row'][column]='1151117'
                    self.assertFalse(reviews.reviewed('company_actions',changed))
                    continue
                changed=copy.deepcopy(self.events);changed[i]['row'][column]+='changed'
                with self.subTest(market=event['market'],column=column), self.assertRaises(RuntimeError):
                    self.run_actions(changed)
        unknown=copy.deepcopy(self.events[0]);unknown['row'][1]='1234'
        with self.assertRaises(RuntimeError): self.run_actions(self.events+[unknown])

    def test_missing_duplicate_or_wrong_market_holdings_stop(self):
        for i in range(2):
            for mode in ('missing','duplicate','market'):
                holdings=copy.deepcopy(self.holdings)
                if mode=='missing': holdings.pop(i)
                elif mode=='duplicate': holdings.append(copy.deepcopy(holdings[i]))
                else: holdings[i]['market']='tpex' if i==0 else 'twse'
                with self.subTest(stock=i,mode=mode), self.assertRaisesRegex(RuntimeError,'候選持倉'):
                    self.run_actions(holdings=holdings)

    def test_changed_balance_cost_and_date_stop(self):
        for i in range(2):
            for key,change in (('MarginPurchaseTodayBalance',1),('average_cost',0.00000001)):
                holdings=copy.deepcopy(self.holdings);holdings[i][key]+=change
                with self.subTest(stock=i,field=key), self.assertRaisesRegex(RuntimeError,'持倉或成本改變'):
                    self.run_actions(holdings=holdings)
        with self.assertRaisesRegex(RuntimeError,'持倉或成本改變'):
            self.run_actions(day='2026-11-17')

    def test_twse_and_tpex_header_changes_stop(self):
        tw=TW_FIELDS.copy();tw[0],tw[1]=tw[1],tw[0]
        with self.assertRaisesRegex(RuntimeError,'TWSE.*欄位'):self.run_actions(tw_fields=tw)
        tp=TP_FIELDS.copy();tp[0]='changed'
        with self.assertRaisesRegex(RuntimeError,'TPEx.*欄位'):self.run_actions(tp_fields=tp)
        with self.assertRaisesRegex(RuntimeError,'TWSE.*欄位'):self.run_actions(tw_fields=[])

    def test_capital_review_cannot_authorize_market_cap_changes(self):
        record=copy.deepcopy(self.record)
        row={'market':'twse','date':'2026-11-16','accepted':1,'observed':2,'difference':1,'status':'pending_verification'}
        record['market_cap_observations']=[row]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json';path.write_text(json.dumps(record))
            with patch.object(reviews,'CAPITAL_ACTION_REVIEW_FILE',path):
                self.assertFalse(reviews.reviewed('market_cap_observations',row))
                self.assertFalse(reviews.revisions_reviewed([],[row]))

    def fixture_report(self):
        days=[]
        for market in ('twse','tpex'):
            days.append({'date':'2026-11-16','market':market,'missing_local':[],
                         'margin_mismatches':[],'valid_close_mismatches':[],'identity_errors':[],
                         'cost_mismatches':[],'universe_diff':{'official_only':[],'local_only':[]},
                         'same_rounded_rate':True,'same_balance':True,'same_money':True,
                         'ratio_arithmetic_correct':True,'official_index':1,'published_index':1,
                         'discontinuities':[['3714',20369,10705]] if market=='twse' else []})
        return {'days':days}

    def test_exact_adjustment_passes_but_other_values_do_not(self):
        with patch.object(audit,'DAYS',['2026-11-16']):
            audit.validate_report(self.fixture_report())
            for pos,value in ((0,'1234'),(1,20368),(2,10706)):
                report=self.fixture_report();report['days'][0]['discontinuities'][0][pos]=value
                with self.subTest(position=pos), self.assertRaisesRegex(RuntimeError,'跨日餘額'):
                    audit.validate_report(report)
            report=self.fixture_report();report['days'][0]['date']='2026-11-17'
            with self.assertRaises(RuntimeError):audit.validate_report(report)

    def test_all_other_independent_checks_still_stop_approved_adjustment(self):
        values={k:['mismatch'] for k in ('missing_local','margin_mismatches','valid_close_mismatches','identity_errors','cost_mismatches')}
        values.update({k:False for k in ('same_rounded_rate','same_balance','same_money','ratio_arithmetic_correct')})
        values.update(official_index=2,universe_diff={'official_only':['1234'],'local_only':[]})
        with patch.object(audit,'DAYS',['2026-11-16']):
            for key,value in values.items():
                report=self.fixture_report();report['days'][0][key]=value
                with self.subTest(field=key), self.assertRaises(RuntimeError):audit.validate_report(report)


if __name__=='__main__': unittest.main()
