import copy
import json
import unittest
from tw_margin_rate.publication_reviews import REVIEW_FILE, reviewed, revisions_reviewed

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.record=json.loads(REVIEW_FILE.read_text())

    def test_review_matches_only_exact_facts(self):
        for kind in ('company_actions','balance_adjustments','portfolios','market_cap_observations'):
            item=self.record[kind][0]
            self.assertTrue(reviewed(kind,item))
            changed=copy.deepcopy(item);changed['unexpected']='new'
            self.assertFalse(reviewed(kind,changed))
        for key,value in [('date','2026-11-10'),('prior',628),('yesterday',587),('code','1234'),('market','twse')]:
            row=dict(self.record['balance_adjustments'][0]);row[key]=value
            self.assertFalse(reviewed('balance_adjustments',row))

    def test_revisions_cannot_disappear_change_or_expand(self):
        row=self.record['market_cap_observations'][0]
        self.assertTrue(revisions_reviewed([], [row]))
        self.assertTrue(revisions_reviewed([row], [row]))
        self.assertFalse(revisions_reviewed([row], []))
        for key,value in [('date','2026-11-09'),('observed',1299989),('status','accepted')]:
            other=dict(row);other[key]=value
            self.assertFalse(revisions_reviewed([], [other]))
            self.assertFalse(revisions_reviewed([row], [other]))

    def test_input_failures_still_stop_reviewed_balance(self):
        import official_daily_audit as audit
        from unittest.mock import patch
        row={'date':'2026-11-09','market':'tpex','missing_local':[], 'margin_mismatches':['4207'], 'valid_close_mismatches':[], 'identity_errors':[], 'cost_mismatches':[]}
        with patch.object(audit,'DAYS',['2026-11-09']), self.assertRaises(RuntimeError):
            audit.validate_report({'days':[row,row]})
