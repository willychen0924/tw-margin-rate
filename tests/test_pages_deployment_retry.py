import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pages_deployment as pages
import update_and_publish as runner

SHA='a'*40
RID=45462136500
REPO={'full_name':pages.REPO}
FIRST=dict(id=RID,head_sha=SHA,head_branch='main',event='push',repository=REPO,
           head_repository=REPO,path=pages.WORKFLOW,status='completed',conclusion='failure',run_attempt=1)
SUCCESS=dict(FIRST,conclusion='success',run_attempt=2)
OTHER=dict(SUCCESS,id=2,path='dynamic/pages/pages-build-deployment',event='dynamic',run_attempt=1)
JOB=dict(name='deploy',id=138687295743,run_id=RID,head_sha=SHA,run_attempt=1,conclusion='failure',
         steps=[dict(name='Run actions/upload-pages-artifact@v3',conclusion='success'),
                dict(name='Deploy to GitHub Pages',conclusion='failure')])
NOTES=[dict(annotation_level='failure',message=pages.MISSING+' Ensure artifacts are uploaded.'),
       dict(annotation_level='failure',message='Fetching artifact metadata failed. Please re-run later.')]


class PagesRetryTests(unittest.TestCase):
    def test_classify_exact_run_job_and_annotation(self):
        def check(run=FIRST,job=JOB,notes=NOTES):
            get=Mock(side_effect=[{'total_count':1,'jobs':[job]},notes])
            pages.check_retry_evidence(get,run,SHA)
        check()
        for change in (dict(path='other'),dict(event='pull_request'),dict(head_branch='dev'),
                       dict(head_sha='b'*40),dict(repository={}),dict(head_repository={}),
                       dict(run_attempt=2),dict(conclusion='cancelled'),dict(status='queued')):
            with self.subTest(change=change),self.assertRaises(RuntimeError): check(run=dict(FIRST,**change))
        for change in (dict(run_id=10),dict(head_sha='b'*40),dict(run_attempt=2),dict(steps=[])):
            with self.subTest(change=change),self.assertRaises(RuntimeError): check(job=dict(JOB,**change))
        job=copy.deepcopy(JOB);job['steps'][0]['conclusion']='failure'
        with self.assertRaises(RuntimeError): check(job=job)
        for notes in ([],[dict(annotation_level='warning',message=pages.MISSING)],
                      NOTES+[dict(annotation_level='failure',message='permission denied')]):
            with self.assertRaises(RuntimeError): check(notes=notes)

    def poll(self, sequence, directory, rerun, browser, body=b'new'):
        def get(session,method,url,**kwargs):
            self.assertEqual(method,'GET')
            if url.endswith('/actions/runs'): return {'workflow_runs':next(sequence)}
            if url.endswith('/jobs'): return {'total_count':1,'jobs':[JOB]}
            if url.endswith('/annotations'): return NOTES
            if url.endswith('/'+str(RID)): return FIRST
            self.fail('Unexpected GitHub request')
        def sleep(_):
            if (directory/'result.json').is_file():
                state=json.loads((directory/'result.json').read_text())
                self.assertLess(state['progress_percent'],100)
        html=directory/'index.html';html.write_bytes(b'new')
        with patch.object(runner,'request_json',side_effect=get),patch.object(pages,'request_rerun',rerun), \
             patch.object(runner.time,'sleep',side_effect=sleep),patch('browser_acceptance.verify',browser), \
             patch('requests.Session.get',return_value=Mock(status_code=200,content=body)):
            return runner.verify_public(SHA,html,{},directory,state=dict(stage='等待部署及公開驗收',status='執行中',started_at='test'))

    def test_retry_wait_for_peer_and_second_attempt_then_public_proof(self):
        seq=iter([[FIRST,dict(OTHER,status='in_progress',conclusion=None)],[FIRST,OTHER],
                  [FIRST,OTHER],[OTHER],[dict(SUCCESS,status='queued',conclusion=None),OTHER],[SUCCESS,OTHER]])
        rerun=Mock();browser=Mock(return_value={'ok':True})
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            def record(*args):
                self.assertFalse((out/'publication-verification.json').exists())
                browser.assert_not_called()
            rerun.side_effect=record
            result=self.poll(seq,out,rerun,browser)
            self.assertEqual(result['retry_run_id'],RID)
            self.assertEqual(result['runs'][0]['attempt'],2)
            self.assertTrue((out/'publication-verification.json').exists())
            rerun.assert_called_once();browser.assert_called_once()

    def test_second_failure_other_failure_or_failed_browser_never_complete(self):
        for outcome in ([dict(SUCCESS,conclusion='failure'),OTHER],[SUCCESS,dict(OTHER,conclusion='failure')],
                        [SUCCESS,OTHER]):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as d:
                rerun=Mock();browser=Mock(side_effect=RuntimeError('browser failure'))
                with self.assertRaises(RuntimeError): self.poll(iter([[FIRST,OTHER],outcome]),Path(d),rerun,browser)
                rerun.assert_called_once()
                self.assertFalse((Path(d)/'publication-verification.json').exists())

    def test_second_success_with_stale_html_or_missing_workflow_is_not_completion(self):
        for runlist,content in (([SUCCESS,OTHER],b'old'),([OTHER],b'new')):
            with self.subTest(runlist=runlist),tempfile.TemporaryDirectory() as d:
                rerun=Mock();browser=Mock()
                with patch.object(runner.time,'monotonic',side_effect=range(0,10000,100)),self.assertRaisesRegex(RuntimeError,'逾時'):
                    import itertools
                    self.poll(itertools.repeat(runlist),Path(d),rerun,browser,content)
                browser.assert_not_called();rerun.assert_not_called()

    def test_noninteractive_request_reserved_once_even_after_uncertain_response(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);gh=base/'gh';gh.touch();gh.chmod(0o700)
            remote=Mock(return_value=SHA+' refs/heads/main')
            with patch.object(pages.shutil,'which',return_value=str(gh)),patch.object(pages.subprocess,'run',side_effect=TimeoutExpired('gh',45)) as send:
                with self.assertRaisesRegex(RuntimeError,'不再重送'): pages.request_rerun(SHA,RID,base/'claims',remote)
                pages.request_rerun(SHA,RID,base/'claims',remote)
                send.assert_called_once()
                self.assertEqual(send.call_args.args[0],[str(gh),'run','rerun',str(RID),'--repo','github.com/'+pages.REPO])
            with patch.object(pages.shutil,'which',return_value=str(gh)),patch.object(pages.subprocess,'run') as send:
                with self.assertRaisesRegex(RuntimeError,'舊版本'):
                    pages.request_rerun(SHA,RID,base/'fresh',Mock(return_value='different refs/heads/main'))
                send.assert_not_called()
