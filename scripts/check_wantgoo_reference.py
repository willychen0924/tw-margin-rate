"""Compare dated, browser-observed WantGoo values; never calibrate or treat stale data as current."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compare(history, observations):
    latest = history['metadata']['end']
    result = {'latest': latest, 'reference_only': True, 'markets': {}}
    for market in ('twse', 'tpex'):
        ours = {r['date']: r['maintenance'] for r in history['markets'][market]}
        theirs = {r['date']: r['maintenance'] for r in observations['markets'][market]}
        rows = [{'date': day, 'ours': ours[day], 'wantgoo': theirs[day],
                 'gap_pp': round(ours[day]-theirs[day], 2)} for day in sorted(ours.keys() & theirs.keys())]
        result['markets'][market] = {'status': 'same_date_available' if latest in theirs else 'needs_browser_check',
                                    'source': observations['sources'][market], 'rows': rows}
        if latest in theirs and len(rows)>1 and rows[-1]['date']==latest:
            a,b=rows[-2:]
            result['markets'][market]['latest_change'] = {
                'ours_pp': round(b['ours']-a['ours'],2),
                'wantgoo_pp': round(b['wantgoo']-a['wantgoo'],2),
                'opposite_direction': (b['ours']-a['ours'])*(b['wantgoo']-a['wantgoo'])<0}
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--history', type=Path, default=ROOT/'data/processed/margin-maintenance-history.json')
    parser.add_argument('--observations', type=Path, default=ROOT/'data/reference/wantgoo-observations.json')
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    report=compare(json.loads(args.history.read_text()),json.loads(args.observations.read_text()))
    encoded=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)
