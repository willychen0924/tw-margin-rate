"""Exact, human-reviewed observations; never broad anomaly bypasses."""
import json
from pathlib import Path

REVIEW_FILE = Path(__file__).resolve().parents[2] / 'data/reference/publication-review-20261109.json'

def reviewed(kind, value):
    if not REVIEW_FILE.exists():
        return False
    record = json.loads(REVIEW_FILE.read_text())
    return record.get('policy') == 'retain_finmind_inputs_and_existing_model_no_history_rewrite' and value in record.get(kind, [])

def revisions_reviewed(old, new):
    # Existing findings may not disappear or change. Each addition must exactly
    # match the market, date, old/new values and unresolved status reviewed.
    return all(row in new for row in old) and all(
        row in old or reviewed('market_cap_observations', row) for row in new
    )
