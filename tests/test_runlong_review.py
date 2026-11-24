"""The November 25 review is not a tolerance increase or a market-cap approval."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tw_margin_rate.publication_reviews import RUNLONG_REVIEW_FILE, reviewed


class RunlongReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = json.loads(RUNLONG_REVIEW_FILE.read_text())

    def test_one_exact_event_and_three_dated_positions(self):
        event = {'market': 'twse', 'row': [
            '115/11/23','1808','潤隆','31.00','36.25','39.85','32.65','36.25',
            '--','退還股款','1808  ,20261111']}
        self.assertEqual(self.record['company_actions'], [event])
        self.assertTrue(reviewed('company_actions', event))
        for i in range(len(event['row'])):
            row = event['row'].copy()
            row[i] += '?'
            self.assertFalse(reviewed('company_actions', dict(event, row=row)))
        holdings = [dict(market='twse', code='1808', date=date, balance=balance,
                         average_cost=cost) for date, balance, cost in (
            ('2026-11-20', 3828, 33.5354842234), ('2026-11-23', 2988, 33.5610002592),
            ('2026-11-24', 3000, 33.6248845859))]
        self.assertEqual(self.record['portfolios'], holdings)
        for h in holdings:
            self.assertTrue(reviewed('portfolios', h))
            for change in (dict(balance=h['balance']+1), dict(average_cost=h['average_cost']+1e-8),
                           dict(market='tpex'), dict(date='2026-11-25')):
                self.assertFalse(reviewed('portfolios', dict(h, **change)))

    def test_only_fourteen_exact_official_balance_pairs(self):
        expected = [
            ('1808',3828,3037), ('1802',78255,78195), ('2303',106103,106089),
            ('2329',9732,9720), ('2409',226184,226144), ('2428',1748,1643),
            ('2441',6645,6631), ('6197',4523,4502), ('8215',2814,2799),
            ('3479',2775,2760), ('6021',487,467), ('6174',824,813),
            ('6290',9822,9799), ('8043',3028,3014)]
        tpex = {'3479','6021','6174','6290','8043'}
        rows = [dict(market='tpex' if code in tpex else 'twse',
                     date='2026-11-23' if code=='1808' else '2026-11-24',
                     code=code, prior=prior, yesterday=yesterday) for code,prior,yesterday in expected]
        self.assertCountEqual(self.record['balance_adjustments'], rows)
        self.assertNotIn('market_cap_observations', self.record)
        for row in rows:
            self.assertTrue(reviewed('balance_adjustments', row))
            for change in (dict(prior=row['prior']+1), dict(yesterday=row['yesterday']+1),
                           dict(code='9999'), dict(date='2026-11-25'),
                           dict(market='tpex' if row['market']=='twse' else 'twse')):
                self.assertFalse(reviewed('balance_adjustments', dict(row, **change)))
