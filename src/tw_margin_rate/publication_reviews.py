"""Exact, human-reviewed observations; never broad anomaly bypasses."""
import json
from pathlib import Path

REVIEW_FILE = Path(__file__).resolve().parents[2] / 'data/reference/publication-review-20261109.json'
MARKET_CAP_REVIEW_FILE = REVIEW_FILE.with_name('publication-review-20261114.json')

def reviewed(kind, value):
    # Explicit files and scopes: a market-cap-only approval must never authorize
    # company actions, balances or portfolio costs, nor load unreviewed files.
    sources = (
        (REVIEW_FILE, ('company_actions', 'balance_adjustments', 'portfolios', 'market_cap_observations')),
        (MARKET_CAP_REVIEW_FILE, ('market_cap_observations',)),
    )
    for path, kinds in sources:
        if kind not in kinds or not path.exists():
            continue
        record = json.loads(path.read_text())
        if record.get('policy') == 'retain_finmind_inputs_and_existing_model_no_history_rewrite' and value in record.get(kind, []):
            return True
    return False

def revisions_reviewed(old, new):
    # Existing findings may not disappear or change. Each addition must exactly
    # match the market, date, old/new values and unresolved status reviewed.
    return all(row in new for row in old) and all(
        row in old or reviewed('market_cap_observations', row) for row in new
    )
