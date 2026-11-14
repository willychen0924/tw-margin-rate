import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tw_margin_rate import publication_reviews as reviews
from tw_margin_rate.publication_reviews import REVIEW_FILE, MARKET_CAP_REVIEW_FILE, reviewed, revisions_reviewed

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

    def test_november_14_approval_is_one_exact_unresolved_observation(self):
        record=json.loads(MARKET_CAP_REVIEW_FILE.read_text())
        row={'accepted':1429753.02,'date':'2026-11-13','difference':53.24,
             'market':'twse','observed':1429806.26,'status':'pending_verification'}
        self.assertEqual(record['market_cap_observations'], [row])
        self.assertTrue(reviewed('market_cap_observations',row))
        for key,value in [('accepted',1429753.03),('date','2026-11-12'),('difference',53.25),
                          ('market','tpex'),('observed',1429806.27),('status','accepted'),('extra',True)]:
            with self.subTest(key=key):
                changed=dict(row);changed[key]=value
                self.assertFalse(reviewed('market_cap_observations',changed))
                self.assertFalse(revisions_reviewed([], [changed]))
        for key in row:
            missing=dict(row);missing.pop(key)
            self.assertFalse(reviewed('market_cap_observations',missing))

    def test_new_approval_preserves_old_findings_and_rejects_other_additions(self):
        old=self.record['market_cap_observations']
        row=json.loads(MARKET_CAP_REVIEW_FILE.read_text())['market_cap_observations'][0]
        self.assertTrue(revisions_reviewed(old, old+[row]))
        self.assertFalse(revisions_reviewed(old, [row]))
        self.assertFalse(revisions_reviewed(old+[row], old))
        other=dict(row);other['date']='2026-11-16'
        self.assertFalse(revisions_reviewed(old, old+[row,other]))

    def test_approval_never_installs_observed_cap_or_allows_history_rewrite(self):
        from tw_margin_rate.revisions import retain_published_market_caps
        from validate_margin_outputs import compare_rows
        row=json.loads(MARKET_CAP_REVIEW_FILE.read_text())['market_cap_observations'][0]
        published={'metadata':{'end':row['date']},'markets':{m:[{
            'date':row['date'],'market_cap':row['accepted'] if m=='twse' else 95770.97,
            'maintenance':150.38,'financed_amount':5365.99,'margin_market_cap_ratio':0.3753,
        }] for m in ('twse','tpex')}}
        prior=copy.deepcopy(published)
        refreshed=copy.deepcopy(prior)
        refreshed['metadata'].update(complete=True,validation_passed=True)
        refreshed['validation']={}
        refreshed['markets']['twse'][0]['market_cap']=row['observed']
        accepted,ledger=retain_published_market_caps(published,prior,refreshed)
        self.assertEqual(ledger['changes'],[row])
        self.assertTrue(revisions_reviewed([],ledger['changes']))
        self.assertEqual(accepted['markets'],published['markets'])
        for key in ('market_cap','maintenance','financed_amount','margin_market_cap_ratio'):
            changed=copy.deepcopy(accepted)
            changed['markets']['twse'][0][key]+=1
            with self.subTest(key=key), self.assertRaises(AssertionError):
                compare_rows(changed,published,row['date'])

    def test_market_cap_approval_cannot_authorize_other_scopes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json'
            record=json.loads(MARKET_CAP_REVIEW_FILE.read_text())
            row={'unreviewed':'must not be accepted'}
            for kind in ('company_actions','balance_adjustments','portfolios','authorization'):
                record[kind]=[row]
            path.write_text(json.dumps(record))
            with patch.object(reviews,'MARKET_CAP_REVIEW_FILE',path):
                for kind in ('company_actions','balance_adjustments','portfolios','authorization'):
                    self.assertFalse(reviewed(kind,row))

    def test_missing_wrong_policy_or_broken_approval_cannot_pass(self):
        row=json.loads(MARKET_CAP_REVIEW_FILE.read_text())['market_cap_observations'][0]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json'
            with patch.object(reviews,'MARKET_CAP_REVIEW_FILE',path):
                self.assertFalse(reviewed('market_cap_observations',row))
                path.write_text(json.dumps({'policy':'allow_history_rewrite','market_cap_observations':[row]}))
                self.assertFalse(reviewed('market_cap_observations',row))
                path.write_text('{broken')
                with self.assertRaises(json.JSONDecodeError): reviewed('market_cap_observations',row)
