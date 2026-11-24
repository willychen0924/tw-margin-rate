"""Recognize and rerun only the reviewed Pages artifact-lookup failure."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

REPO = 'willychen0924/tw-margin-rate'
API = f'https://api.github.com/repos/{REPO}'
WORKFLOW = '.github/workflows/pages.yml'
MISSING = 'Error: No artifacts named "github-pages" were found for this workflow run.'
NOT_READY = 'GitHub 部署失敗，不符合本次可自動重試的條件；未確認上線。'


def require_match(condition):
    if not condition:
        raise RuntimeError(NOT_READY)


def is_site_run(run, sha):
    return (run.get('head_sha') == sha and run.get('path') == WORKFLOW
            and run.get('head_branch') == 'main' and run.get('event') == 'push'
            and (run.get('repository') or {}).get('full_name') == REPO
            and (run.get('head_repository') or {}).get('full_name') == REPO)


def check_retry_evidence(get, run, sha):
    rid = run['id']
    require_match(is_site_run(run, sha) and type(rid) is int and rid > 0
                  and run.get('run_attempt') == 1 and run.get('status') == 'completed'
                  and run.get('conclusion') == 'failure')
    jobs = get(f'{API}/actions/runs/{rid}/attempts/1/jobs')
    require_match(jobs.get('total_count') == 1 and len(jobs.get('jobs', [])) == 1)
    job = jobs['jobs'][0]
    require_match(job.get('name') == 'deploy' and job.get('run_id') == rid
                  and job.get('head_sha') == sha and job.get('run_attempt') == 1
                  and job.get('conclusion') == 'failure' and type(job.get('id')) is int)
    steps = job.get('steps', [])
    require_match([s.get('name') for s in steps if s.get('conclusion') != 'success']
                  == ['Deploy to GitHub Pages']
                  and any(s.get('name') == 'Deploy to GitHub Pages' and
                          s.get('conclusion') == 'failure' for s in steps)
                  and any(s.get('name') == 'Run actions/upload-pages-artifact@v3' and
                          s.get('conclusion') == 'success' for s in steps))
    notes = get(f"{API}/check-runs/{job['id']}/annotations", params={'per_page': 100})
    require_match(isinstance(notes, list) and len(notes) < 100)
    errors = [a.get('message') or '' for a in notes if a.get('annotation_level') == 'failure']
    require_match(any(e.startswith(MISSING) for e in errors) and
                  all(e.startswith((MISSING, 'Fetching artifact metadata failed.')) for e in errors))


def request_rerun(sha, rid, directory, git):
    """Reserve before sending: even an uncertain response or app restart cannot submit twice."""
    require_match(re.fullmatch('[0-9a-f]{40}',sha) and type(rid) is int and rid>0)
    directory.mkdir(exist_ok=True)
    marker = directory / f'{sha}-{rid}.json'
    if marker.exists():
        return
    gh = next((p for p in (shutil.which('gh'), '/opt/homebrew/bin/gh', '/usr/local/bin/gh')
               if p and os.access(p, os.X_OK) and Path(p).is_file()), None)
    if not gh:
        raise RuntimeError('無法自動重試：此 Mac 未安裝 GitHub CLI。未確認上線。')
    if git('ls-remote', 'origin', 'refs/heads/main').split()[0] != sha:
        raise RuntimeError('遠端 main 已不是本次部署版本；不重試舊版本。')
    try:
        with marker.open('x', encoding='utf-8') as f:
            json.dump({'sha':sha, 'run_id':rid, 'requested_attempt':2}, f)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        return
    try:
        subprocess.run([gh, 'run', 'rerun', str(rid), '--repo', 'github.com/'+REPO],
                       env=dict(os.environ, GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1'),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=45)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('無法確認 GitHub 已接受重試，不再重送。請確認此 Mac 的 GitHub CLI 已登入且可重跑 Actions，再查看該工作狀態。') from None
