#!/usr/bin/env python3
"""Fail-closed, zero-model daily publication. All reports are local and secret-free."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tw_margin_rate.paths import discover_stock_data, assert_archive_ready, latest_complete_stock_info
from tw_margin_rate.publishing import assert_official_origin
from tw_margin_rate.revisions import install_outputs
from official_daily_audit import audit, require, request_json
import requests

PUBLIC='https://willychen0924.github.io/tw-margin-rate/'
OUTPUTS=['data/processed/margin-maintenance-history.json','docs/index.html','index.html',
         'data/cache/market-margin-money-history.json','data/cache/market-cap-history.json',
         'data/reference/market-cap-revisions.json']

def git(*args):
    env=os.environ.copy();env.update(GIT_TERMINAL_PROMPT='0',GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=20')
    p=subprocess.run(['git',*args],cwd=ROOT,env=env,text=True,capture_output=True,timeout=90)
    if p.returncode:
        raise RuntimeError(f'Git {args[0]} 失敗，請確認網路、遠端及此 Mac 的 SSH 設定（不自動變更憑證）')
    return p.stdout.strip()

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def expected_day(now, holidays):
    """Conservative 18:00 TW cutoff. Unknown calendar year is a stop, not a guess."""
    parsed={}
    for row in holidays:
        text=row['Date'];d=date(int(text[:3])+1911,int(text[3:5]),int(text[5:7]))
        parsed[d]=row
    day=now.date() if now.hour>=18 else now.date()-timedelta(days=1)
    require(any(d.year==day.year for d in parsed),'官方休市表未涵蓋所需年份')
    for _ in range(30):
        row=parsed.get(day)
        explicitly_open=row and ('開始交易' in row['Name'] or '最後交易' in row['Name'])
        closed=row and not explicitly_open
        if explicitly_open or (day.weekday()<5 and not closed): return day.isoformat()
        day-=timedelta(days=1)
    raise RuntimeError('無法判定最新完整交易日')

def check_archive(stock, target):
    import pandas as pd
    latest=assert_archive_ready(stock,'2001-01-05')
    for kind in ('chips_margin','prices'):
        p=stock/f'raw/{kind}/{target[:4]}/{target}.parquet'
        require(p.exists(),f'{kind} 尚無 {target} 資料，請完成同步／抓取後重試')
        frame=pd.read_parquet(p)
        require(len(frame)>1000 and set(frame.date.astype(str))=={target}, f'{kind} 日期／筆數不完整')
    info=pd.read_parquet(latest_complete_stock_info(stock/'raw/stock_info'))
    require(str(info.date.max())>=target,'股票主檔有效日期落後')
    return latest

def input_signature(stock):
    # Detect locally visible iCloud changes during the run; not a remote-Mac lock.
    return {str(p.relative_to(stock)):(p.stat().st_size,p.stat().st_mtime_ns)
            for kind in ('chips_margin','prices','stock_info')
            for p in (stock/f'raw/{kind}').glob('*/*.parquet')}

def find_node():
    import playwright
    candidates=[str(Path(playwright.__file__).parent/'driver/node'),shutil.which('node'),'/opt/homebrew/bin/node','/usr/local/bin/node']
    node=next((p for p in candidates if p and Path(p).is_file()),None)
    require(node is not None,'缺少 Node.js，請安裝後重試（用於網站日期刻度測試）')
    return node

@contextlib.contextmanager
def preview(directory):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(directory)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/index.html'
    finally: server.shutdown();server.server_close();thread.join()

def verify_public(sha, html, history, out, timeout=900):
    from browser_acceptance import verify
    deadline=time.monotonic()+timeout
    session=requests.Session()
    while time.monotonic()<deadline:
        try:
            runs=request_json(session,'GET','https://api.github.com/repos/willychen0924/tw-margin-rate/actions/runs',params={'head_sha':sha,'per_page':20})
            matching=[r for r in runs.get('workflow_runs',[]) if r['head_sha']==sha]
            bad=[r for r in matching if r['status']=='completed' and r['conclusion'] not in ('success','skipped')]
            require(not bad,'GitHub 部署失敗，已推送但未確認上線')
            response=session.get(PUBLIC,params={'verify':sha,'t':time.time()},timeout=30)
            if matching and all(r['status']=='completed' and r['conclusion'] in ('success','skipped') for r in matching) and response.status_code==200 and response.content==html.read_bytes():
                result=verify(PUBLIC+'?verify='+sha,history,out/'public-browser')
                proof={'sha':sha,'http':200,'html_sha256':digest(html),'runs':[{'id':r['id'],'conclusion':r['conclusion']} for r in matching],'browser':result}
                dump(out/'publication-verification.json',proof)
                return proof
        except requests.RequestException:
            pass
        print('等待 GitHub Pages 部署與公開內容一致…',flush=True)
        time.sleep(15)
    raise RuntimeError('部署等待逾時；已推送的版本可能稍後上線，尚不能視為發布成功。請查看報告與 GitHub Actions')

def progress_percent(state):
    # Milestones, not elapsed-time estimates. Failure never means completion.
    if state.get('status') in ('發布成功','已是最新，公開驗證通過'):
        return 100
    return {'啟動':0,'前置檢查':5,'候選重算與測試':15,
            '官方獨立驗算與公司行動':50,'本機網站驗收':75,
            '安裝、提交及推送':85,'等待部署及公開驗收':90,
            '公開網站驗收':95}.get(state.get('stage'),0)

def write_report(out,state):
    state['progress_percent']=progress_percent(state)
    state['progress_basis']='階段進度，非剩餘時間估算；公開驗收通過才達 100%'
    dump(out/'result.json',state)
    lines=['更新並發布結果',f"狀態：{state['status']}",f"階段：{state['stage']}",f"時間：{state['started_at']}",f"資料日期：{state.get('target','未確認')}",f"版本：{state.get('commit','未提交')}",f"已推送：{state.get('pushed',False)}",'',state.get('message',''),'',f'證據目錄：{out}',f'網站：{PUBLIC}', '玩股網：依使用者 2026-11-07 指示，不作日常查核或發布門檻。', '本流程不呼叫任何 AI 模型。']
    if state.get('candidate'): lines += [f"計算與官方查核證據：{state['candidate']}"]
    lines += [f"詳細執行紀錄：{out / 'execution.log'}"]
    for market,row in state.get('latest',{}).items():
        lines += [f"{'上市' if market=='twse' else '櫃買'}：維持率 {row['maintenance']}%；指數 {row['index']}；融資餘額 {row['financed_amount']} 億；市值比 {row['margin_market_cap_ratio']}%"]
    (out/'報告.txt').write_text('\n'.join(lines),encoding='utf-8')

def execute(out,state,verify_only=False):
    import update_margin_rate as updater
    from browser_acceptance import verify
    state['stage']='前置檢查';write_report(out,state)
    require(not sys.flags.optimize,'不能停用 Python 斷言檢查；請移除 PYTHONOPTIMIZE')
    remote=git('remote','get-url','origin');assert_official_origin(remote)
    require(remote.startswith('git@'),'無人發布需使用已設定的 SSH origin')
    require(not git('diff','--name-only','--diff-filter=U'),'有 Git 合併衝突')
    head=git('rev-parse','HEAD')
    remote_head=git('ls-remote','origin','refs/heads/main').split()[0]
    if not verify_only:
        require(not git('status','--porcelain'),'有未提交變更；為保護您的修改，停止自動發布')
        require(head==remote_head,'本地與遠端版本不同；停止以免發布未審查的提交')
    require(not list((ROOT/'data/tmp').glob('margin-update-*/install-backup/in-progress.json')),'發現未完成安裝，請先復原')
    processes=subprocess.run(['/bin/ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout
    competing=[line for line in processes.splitlines() if any(f'scripts/{name}.py' in line for name in ('build_margin_maintenance_history','update_margin_rate','fetch_margin_money_history','fetch_market_cap_history')) and not any(shell in line for shell in (' -c ', 'exec_command'))]
    require(not competing,'發現本機另有資料計算程序，停止避免競合')
    for p in ROOT.rglob('*'):
        if 'data/tmp' in str(p.relative_to(ROOT)) or '.git/' in str(p.relative_to(ROOT)): continue
        require('conflicted copy' not in p.name.lower() and '衝突的拷貝' not in p.name,f'發現同步衝突：{p.name}')
    stock=discover_stock_data()
    holidays=request_json(requests.Session(),'GET','https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule')
    dump(out/'official-holidays.json',holidays)
    target=expected_day(datetime.now(ZoneInfo('Asia/Taipei')),holidays)
    state['target']=target
    dump(out/'archive-check.json',check_archive(stock,target))
    source_before=input_signature(stock)
    protected={p:digest(ROOT/p) for p in OUTPUTS}
    node=find_node()
    state['stage']='候選重算與測試';write_report(out,state)
    print('正在完整重算，正式檔案尚未更動…',flush=True)
    config=json.loads((ROOT/'config/project.json').read_text())
    args=argparse.Namespace(end=target,stock_data=stock,warmup_start='2001-01-05',display_start='2017-07-03',twse_market_cap_start=config['market_cap_starts']['twse'],tpex_market_cap_start=config['market_cap_starts']['tpex'],no_finmind_fetch=True,refresh_reference=False,allow_history_rewrite=False,publish=False,candidate_only=True)
    candidate=updater.main(args);state['candidate']=str(candidate)
    payload=json.loads((candidate/'history.json').read_text())
    state['latest']={market:rows[-1] for market,rows in payload['markets'].items()}
    require(payload['metadata']['end']==target,'計算未到最新完整交易日')
    prior=json.loads((candidate/'previous-history.json').read_text())['metadata']['end']
    expected=[];day=date.fromisoformat(prior)+timedelta(days=1)
    while day<=date.fromisoformat(target):
        if expected_day(datetime.combine(day,datetime.min.time()).replace(hour=18),holidays)==day.isoformat(): expected.append(day.isoformat())
        day+=timedelta(days=1)
    for market in ('twse','tpex'):
        require([r['date'] for r in payload['markets'][market] if r['date']>prior]==expected,'新增期間缺少應有交易日；不可跨過缺漏日發布')
    old_ledger=json.loads((ROOT/'data/reference/market-cap-revisions.json').read_text())
    new_ledger=json.loads((candidate/'market-cap-revisions.json').read_text())
    require(old_ledger['changes']==new_ledger['changes'],'出現新的歷史市值修訂，已保存候選，須先查證')
    state['stage']='官方獨立驗算與公司行動';write_report(out,state)
    print('核對官方逐股資料、獨立成本、公司行動與變動來源…',flush=True)
    audit(candidate,stock)
    subprocess.run([node,'--test','tests/chart_logic.test.cjs'],cwd=ROOT,check=True,timeout=120)
    state['stage']='本機網站驗收';write_report(out,state)
    print('測試桌面、手機、圖線記憶與提示框…',flush=True)
    with preview(candidate) as url: verify(url,payload,out/'local-browser')
    require(source_before==input_signature(stock),'計算期間原始資料變動；請同步完成後重試')
    require(all(digest(ROOT/p)==h for p,h in protected.items()),'正式檔案於執行中變動，停止')
    require(git('rev-parse','HEAD')==head,'執行中 Git HEAD 改變')
    if verify_only:
        state.update(status='驗證通過（未發布）',message='完整候選、官方獨立驗算及本機瀏覽器通過；未改正式輸出、未提交、未推送。')
        return
    require(not git('status','--porcelain'),'驗收期間工作樹出現其他修改')
    require(git('ls-remote','origin','refs/heads/main').split()[0]==remote_head,'遠端在執行期間更新，停止')
    same=payload['markets']==json.loads((ROOT/OUTPUTS[0]).read_text())['markets'] and (candidate/'index.html').read_bytes()==(ROOT/'docs/index.html').read_bytes()
    if same:
        state['stage']='公開網站驗收';state['commit']=head
        # Tool-only commits need not deploy HTML. Verify the last commit that
        # actually changed the site, plus byte equality to the current candidate.
        site_sha=git('log','-1','--format=%H','--','docs/index.html','index.html','.github/workflows/pages.yml')
        require(bool(site_sha),'找不到網站版本提交')
        state['site_commit']=site_sha
        write_report(out,state)
        verify_public(site_sha,ROOT/'docs/index.html',payload,out)
        state.update(status='已是最新，公開驗證通過',message='資料沒有新增或變更；未產生重複提交。')
        return
    state['stage']='安裝、提交及推送';write_report(out,state)
    print('所有發布前檢查通過，正在提交與推送…',flush=True)
    pairs=list(zip([candidate/n for n in ('history.json','index.html','root-index.html','market-margin-money-history.json','market-cap-history.json','market-cap-revisions.json')],[ROOT/p for p in OUTPUTS]))
    install_outputs(pairs,candidate/'install-backup')
    # Only the explicit output allowlist is ever staged. Source/user files are not included.
    git('add','--',*OUTPUTS)
    staged=set(git('diff','--cached','--name-only').splitlines())
    require(staged<=set(OUTPUTS),'暫存區出現非預期檔案，停止提交')
    git('commit','-m',f'Update margin data through {target} (verified automatic publication)')
    sha=git('rev-parse','HEAD');state['commit']=sha
    state['push_attempted']=True;write_report(out,state)
    git('push','origin','HEAD:main');state['pushed']=True
    state['stage']='等待部署及公開驗收';write_report(out,state)
    verify_public(sha,ROOT/'docs/index.html',payload,out)
    state.update(status='發布成功',message='官方驗算、歷史、測試、本機與公開瀏覽器全部通過。')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    parent=ROOT/'data/tmp';parent.mkdir(parents=True,exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='publish-',dir=parent))
    state={'status':'執行中','stage':'啟動','started_at':datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),'pushed':False}
    print(f'REPORT:{out / "報告.txt"}',flush=True)
    code=0
    with (out/'execution.log').open('w',encoding='utf-8') as log:
        # All verbose subprocess details stay in the report; only progress goes to GUI.
        saved=os.dup(1);saved_err=os.dup(2)
        try:
            sys.stdout.flush();os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
            with (parent/'update.lock').open('a+') as lock:
                try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: raise RuntimeError('本機已有更新程序，請勿同時執行') from None
                execute(out,state,args.verify_only)
        except BaseException as exc:
            code=1
            # Avoid exception URLs/headers/token echoes in user-visible reports.
            state['status']='部署未完成' if state.get('push_attempted') or state.get('pushed') else '已停止'
            state['message']=str(exc) if isinstance(exc,RuntimeError) else f'{type(exc).__name__}：步驟未通過，請檢查本次報告；未略過檢查。'
        finally:
            sys.stdout.flush();sys.stderr.flush();os.dup2(saved,1);os.dup2(saved_err,2);os.close(saved);os.close(saved_err)
            write_report(out,state)
    print(f"RESULT:{state['status']}",flush=True)
    print(f'REPORT:{out / "報告.txt"}',flush=True)
    return code

if __name__=='__main__': sys.exit(main())
