"""Independent scalar audit; configured by the zero-model publication runner."""
import concurrent.futures, hashlib, json, math, sys
from pathlib import Path
import pandas as pd
import requests
import time
from decimal import Decimal, ROUND_HALF_UP

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'src')]
import build_margin_maintenance_history as builder
from fetch_margin_money_history import TpexCertificateAdapter

OUT = None
HISTORY = None
DAYS = []
SEED = None
STOCK_DATA = None
MONEY = None
CAP = None

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def request_json(session, method, url, **kwargs):
    for attempt in range(3):
        try:
            response = session.request(method, url, timeout=(15, 45), **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise RuntimeError(f"官方來源暫時無法讀取：{url}") from None
            time.sleep(2 * (attempt + 1))
FORMAL = ['data/processed/margin-maintenance-history.json', 'docs/index.html', 'index.html', 'data/cache/market-margin-money-history.json', 'data/cache/market-cap-history.json']
def hashes():
    return {f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in FORMAL}
def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
def ordinary(code):
    return len(code)==4 and code.isdigit() and not code.startswith(('0','91'))
def number(value):
    try:
        n=float(str(value).replace(',',''))
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None

def capture():
    before=hashes()
    captured={}
    def profile(frame,event,arg):
        if event=='return' and frame.f_code.co_name=='process_day':
            day=frame.f_locals.get('day','')
            if day>=SEED:
                captured[day]=frame.f_locals['margin'].copy()
            if day==SEED:
                save(OUT/'seed-state.json', {'date':day, 'cost':dict(frame.f_locals['average_cost']), 'price':dict(frame.f_locals['last_close'])})
    saved_argv = sys.argv[:]
    sys.argv=[sys.argv[0],'--end',DAYS[-1],'--no-finmind-fetch', '--stock-data', str(STOCK_DATA), '--margin-money-history', str(MONEY), '--market-cap-history', str(CAP)]
    args=builder.parse_args()
    sys.argv = saved_argv
    args.stock_data=STOCK_DATA
    sys.setprofile(profile)
    try: payload=builder.build_history(args)
    finally: sys.setprofile(None)
    for day,frame in captured.items():
        frame.to_json(OUT/f'portfolio-{day}.json', orient='records', indent=2)
    save(OUT/'rebuilt-history.json',payload)
    save(OUT/'full-rebuild-check.json',{'same_entire_published_history':payload['markets']==HISTORY['markets'],'formal_before':before,'formal_after':hashes(),'days':DAYS,'seed':SEED})
    print('Full rebuild exact:',payload['markets']==HISTORY['markets'],flush=True)

def fetch():
    base=OUT/'official';base.mkdir(exist_ok=True)
    jobs=[(market,kind,day) for day in DAYS for market in ('twse','tpex') for kind in ('margin','quotes')]
    def get(job):
        market,kind,day=job
        session=requests.Session()
        session.headers.update({'User-Agent':'tw-margin-rate-independent-audit/1.0','Accept':'application/json'})
        if market=='twse':
            endpoint='MI_MARGN' if kind=='margin' else 'MI_INDEX'
            url='https://www.twse.com.tw/exchangeReport/'+endpoint
            params={'date':day.replace('-',''),'response':'json'}
            params.update({'selectType':'ALL'} if kind=='margin' else {'type':'ALLBUT0999'})
            payload=request_json(session, 'GET', url, params=params)
        else:
            session.mount('https://www.tpex.org.tw',TpexCertificateAdapter())
            url='https://www.tpex.org.tw/www/zh-tw/'+('margin/balance' if kind=='margin' else 'afterTrading/dailyQuotes')
            payload=request_json(session, 'POST', url, data={'date':day.replace('-','/'),'response':'json'})
        assert payload.get('date')==day.replace('-',''),(job,payload.get('date'),payload.get('stat'))
        assert payload.get('tables'),job
        target=base/f'{market}-{kind}-{day}.json'
        save(target,payload)
        print('Official saved',market,kind,day,flush=True)
        return {'market':market,'kind':kind,'date':day,'url':url,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        sources=list(pool.map(get,jobs))
    save(OUT/'source-manifest.json',sources)

def check():
    seed=json.loads((OUT/'seed-state.json').read_text())
    costs=dict(seed['cost']);prices=dict(seed['price'])
    previous=pd.read_json(OUT/f'portfolio-{SEED}.json',dtype={'stock_id':str}).set_index('stock_id')
    previous_q=previous.MarginPurchaseTodayBalance.to_dict()

    result={'window':[DAYS[0],DAYS[-1]],'days':[],'scope':'Official margin, effective closes, scalar daily costs, market universe and published aggregates; seed from a fresh 2001 full replay.', 'formal_hashes':hashes()}
    for day in DAYS:
        local_m=pd.read_parquet(STOCK_DATA/f'raw/chips_margin/{day[:4]}/{day}.parquet');local_m.stock_id=local_m.stock_id.astype(str);local_m=local_m.set_index('stock_id')
        local_p=pd.read_parquet(STOCK_DATA/f'raw/prices/{day[:4]}/{day}.parquet');local_p.stock_id=local_p.stock_id.astype(str);local_p=local_p.set_index('stock_id')
        fresh=pd.read_json(OUT/f'portfolio-{day}.json',dtype={'stock_id':str}).set_index('stock_id')
        official_frames=[]
        for market in ('twse','tpex'):
            mp=json.loads((OUT/f'official/{market}-margin-{day}.json').read_text())
            qp=json.loads((OUT/f'official/{market}-quotes-{day}.json').read_text())
            mt=mp['tables'][1 if market=='twse' else 0]
            close_field='收盤價' if market=='twse' else '收盤'
            qt=next(t for t in qp['tables'] if close_field in (t.get('fields') or []))
            close_index=qt['fields'].index(close_field)
            quotes={str(r[0]):number(r[close_index]) for r in qt['data']}
            rows={str(r[0]):r for r in mt['data'] if ordinary(str(r[0]))}
            mismatches=[];close_diff=[];missing=[];carry=[];identities=[];discontinuities=[];calculated=[]
            mapping={'MarginPurchaseBuy':2 if market=='twse' else 3,'MarginPurchaseSell':3 if market=='twse' else 4,'MarginPurchaseCashRepayment':4 if market=='twse' else 5,'MarginPurchaseYesterdayBalance':5 if market=='twse' else 2,'MarginPurchaseTodayBalance':6}
            for code,row in rows.items():
                values={field:number(row[i]) for field,i in mapping.items()}
                if code not in local_m.index:
                    missing.append(code)
                else:
                    for field,value in values.items():
                        if value!=float(local_m.loc[code,field]):mismatches.append([code,field,value,float(local_m.loc[code,field])])
                raw_close=quotes.get(code)
                if raw_close is not None and raw_close>0:
                    if code not in local_p.index or raw_close!=float(local_p.loc[code,'close']):close_diff.append([code,raw_close,None if code not in local_p.index else float(local_p.loc[code,'close'])])
                    close=raw_close;prices[code]=close
                else:
                    close=prices.get(code);carry.append(code)
                today=values['MarginPurchaseTodayBalance'];yesterday=values['MarginPurchaseYesterdayBalance'];buy=values['MarginPurchaseBuy'];sell=values['MarginPurchaseSell'];repay=values['MarginPurchaseCashRepayment']
                if yesterday+buy-sell-repay!=today:identities.append([code,yesterday+buy-sell-repay,today])
                if code in previous_q and yesterday!=previous_q[code]:discontinuities.append([code,previous_q[code],yesterday])
                oldcost=costs.get(code,close)
                if today<=0:
                    costs.pop(code,None)
                    newcost=close
                elif close is None or close<=0:
                    continue
                else:
                    newcost=(max(yesterday-sell-repay,0)*oldcost+buy*close)/today
                    costs[code]=newcost
                if close is not None:
                    calculated.append({'stock_id':code,'market':market,'close':close,'balance':today,'cost':newcost,'value':close*today,'debt':newcost*today*.60})
            active=[r for r in calculated if r['balance']>0]
            local_active=fresh[(fresh.market==market)&(fresh.MarginPurchaseTodayBalance>0)]
            universe_diff={'official_only':sorted(set(r['stock_id'] for r in active)-set(local_active.index)),'local_only':sorted(set(local_active.index)-set(r['stock_id'] for r in active))}
            value=math.fsum(r['value'] for r in active);debt=math.fsum(r['debt'] for r in active)
            published=next(r for r in HISTORY['markets'][market] if r['date']==day)
            cost_diff=[{'code':r['stock_id'],'official_scalar_cost':r['cost'],'full_replay_cost':float(fresh.loc[r['stock_id'],'average_cost'])} for r in active if r['stock_id'] in fresh.index and abs(r['cost']-float(fresh.loc[r['stock_id'],'average_cost']))>1e-8]
            rate=value/debt*100
            if market=='twse':
                money=number(next(r for r in mp['tables'][0]['data'] if r[0]=='融資金額(仟元)')[5])/1e5
                official_index=number(next(r for t in qp['tables'] if t.get('fields') and t['fields'][0]=='指數' for r in t['data'] if r[0]=='發行量加權股價指數')[1])
            else:
                money=number(next(r for r in mt['summary'] if r[1]=='融資金(仟元)')[6])/1e5
                official_index=None
            obs=None
            etf_value=math.fsum(number(r[6])*quotes.get(r[0],0) for r in mt['data'] if r[0].startswith('0') and number(r[6])>0 and quotes.get(r[0]) is not None)
            result['days'].append({'date':day,'market':market,'official_rows':len(rows),'positive_stocks':len(active),'missing_local':missing,'margin_mismatches':mismatches,'valid_close_mismatches':close_diff,'carried_codes':carry,'identity_errors':identities,'discontinuities':discontinuities,'universe_diff':universe_diff,'cost_mismatches':cost_diff,'independent_rate':rate,'published_rate':published['maintenance'],'same_rounded_rate':round(rate,2)==published['maintenance'],'same_balance':int(sum(r['balance'] for r in active))==published['financed_balance'],'official_money':money,'published_money':published['financed_amount'],'same_money':round(money,2)==published['financed_amount'],'official_index':official_index,'published_index':published['index'],'ratio_arithmetic_correct':round(published['financed_amount']/published['market_cap']*100,4)==published['margin_market_cap_ratio'],'wantgoo_excluded':obs,'gap_pp':None if obs is None else round(published['maintenance']-obs,2),'value_yi':value/1e5,'debt_yi':debt/1e5,'diagnostic_money_including_etf_rate':(value+etf_value)/(money*1e5)*100})
            official_frames.extend(calculated)
        previous_q={r['stock_id']:r['balance'] for r in official_frames}
        save(OUT/f'official-scalar-portfolio-{day}.json',official_frames)
    save(OUT/'independent-report.json',result)
    print(json.dumps([{k:r[k] for k in ('date','market','independent_rate','published_rate','same_rounded_rate','same_balance','same_money','gap_pp','discontinuities','margin_mismatches','valid_close_mismatches','universe_diff','cost_mismatches')} for r in result['days']],ensure_ascii=False,indent=2))

def fetch_caps():
    from fetch_market_cap_history import TPEX_DAILY_MARKET_VALUE_URL
    session=requests.Session();session.mount('https://www.tpex.org.tw',TpexCertificateAdapter())
    for day in DAYS:
        p=request_json(session, 'POST', TPEX_DAILY_MARKET_VALUE_URL, data={'date':day.replace('-','/'),'response':'json'})
        assert p.get('date')==day.replace('-','')
        save(OUT/f'official/tpex-market-value-{day}.json',p)
        print(day,p['tables'][0]['fields'],p['tables'][0]['data'][:1],flush=True)


def validate_report(result):
    require(len(result['days']) == len(DAYS)*2, '官方驗算日數不足')
    for row in result['days']:
        label = f"{row['date']} {row['market']}"
        for key in ('missing_local', 'margin_mismatches', 'valid_close_mismatches', 'identity_errors', 'cost_mismatches'):
            require(not row[key], f'{label} {key} 不通過，詳見 independent-report.json')
        require(not any(row['universe_diff'].values()), f'{label} 股票集合不一致')
        for key in ('same_rounded_rate', 'same_balance', 'same_money', 'ratio_arithmetic_correct'):
            require(row[key], f'{label} {key} 不通過')
        if row['market']=='twse':
            require(row['official_index']==row['published_index'], f'{label} 官方指數不符')
        # Small officially corroborated administrative adjustments are logged;
        # a large unexplained share discontinuity requires human investigation.
        for code, prior, yesterday in row['discontinuities']:
            require(abs(yesterday-prior)<=10 and abs(yesterday-prior)<=max(10, prior*.01),
                    f'{label} {code} 跨日餘額異常，停止而不推定拆股')


def check_actions():
    session=requests.Session()
    session.mount('https://www.tpex.org.tw', TpexCertificateAdapter())
    tw=request_json(session,'GET','https://www.twse.com.tw/exchangeReport/TWTAUU',
        params={'startDate':DAYS[0].replace('-',''),'endDate':DAYS[-1].replace('-',''),'response':'json'})
    tp=request_json(session,'POST','https://www.tpex.org.tw/www/zh-tw/bulletin/decap',data={'response':'json'})
    save(OUT/'official/twse-capital-reductions.json',tw)
    save(OUT/'official/tpex-capital-reductions.json',tp)
    require(tw.get('stat')=='OK' or '沒有符合條件' in tw.get('stat',''), 'TWSE 公司行動回應不明')
    require(bool(tp.get('tables')) and any(t.get('data') for t in tp['tables']), 'TPEx 公司行動表缺失')
    events=[]
    for r in tw.get('data',[]):
        events.append({'market':'twse','row':r})
    compact={str(int(d[:4])-1911)+d[5:7]+d[8:10] for d in DAYS}
    for table in tp['tables']:
        require('恢復買賣日期' in table.get('fields',[]), 'TPEx 公司行動欄位改變')
        i=table['fields'].index('恢復買賣日期')
        events.extend({'market':'tpex','row':r} for r in table.get('data',[]) if r[i] in compact)
    save(OUT/'company-action-review.json',{'events_in_window':events,'policy':'Any reduction resumption requires review; no automatic formula changes.'})
    require(not events, '本次有減資恢復交易事件，須人工查核；未套用或忽略事件')


def audit(candidate, stock_data):
    global OUT,HISTORY,DAYS,SEED,STOCK_DATA,MONEY,CAP
    OUT=candidate/'official-check';OUT.mkdir(exist_ok=True)
    STOCK_DATA=stock_data; MONEY=candidate/'market-margin-money-history.json';CAP=candidate/'market-cap-history.json'
    HISTORY=json.loads((candidate/'history.json').read_text())
    previous=json.loads((candidate/'previous-history.json').read_text())
    cutoff=previous['metadata']['end']
    all_days=[r['date'] for r in HISTORY['markets']['twse']]
    DAYS=[d for d in all_days if d>cutoff] or [all_days[-1]]
    SEED=all_days[all_days.index(DAYS[0])-1]
    capture()
    rebuild=json.loads((OUT/'full-rebuild-check.json').read_text())
    require(rebuild['same_entire_published_history'], '第二次全量重算與候選不同')
    require(rebuild['formal_before']==rebuild['formal_after'], '驗算時正式檔案遭改動')
    fetch();fetch_caps();check_actions();check()
    report=json.loads((OUT/'independent-report.json').read_text())
    validate_report(report)
    movements=[]
    for previous_day,day in zip([SEED]+DAYS,DAYS):
        a=pd.read_json(OUT/f'portfolio-{previous_day}.json',dtype={'stock_id':str}).set_index('stock_id')
        b=pd.read_json(OUT/f'portfolio-{day}.json',dtype={'stock_id':str}).set_index('stock_id')
        for market in ('twse','tpex'):
            aa=a[a.market==market];bb=b[b.market==market]
            v0,d0=aa.market_value.sum(),aa.estimated_debt.sum()
            v1,d1=bb.market_value.sum(),bb.estimated_debt.sum()
            diff=bb[['market_value','estimated_debt']].subtract(aa[['market_value','estimated_debt']],fill_value=0)
            contributions=100*(diff.market_value-v0/d0*diff.estimated_debt)/d1
            delta=100*(v1/d1-v0/d0)
            require(abs(contributions.sum()-delta)<1e-8,'變動歸因未能重現維持率')
            movements.append({'date':day,'market':market,'value_change_pct':(v1/v0-1)*100,'debt_change_pct':(d1/d0-1)*100,'rate_change_pp':delta,'largest_negative':contributions.nsmallest(5).to_dict(),'largest_positive':contributions.nlargest(5).to_dict()})
        payload=json.loads((OUT/f'official/tpex-market-value-{day}.json').read_text())
        table=payload['tables'][0];i=table['fields'].index('市值(佰萬元)')
        total=float((sum(Decimal(r[i].replace(',','')) for r in table['data'])/100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        formal=next(r for r in HISTORY['markets']['tpex'] if r['date']==day)
        require(total==formal['market_cap'],f'{day} TPEx 官方市值加總不符')
    save(OUT/'movement-analysis.json',movements)
    save(OUT/'all-official-source-hashes.json',[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((OUT/'official').glob('*.json'))])
    return report
