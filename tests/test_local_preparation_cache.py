import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tw_margin_rate.local_cache import prepare_cached, cached_json
from tw_margin_rate.timing import measured

class PreparationCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.source=self.base/'source';self.source.write_bytes(b'one')
        self.root=self.base/'cache';self.dest=self.base/'run';self.calls=0
    def produce(self,out):
        self.calls+=1
        (out/'data.parquet').write_bytes(self.source.read_bytes())
        (out/'index.json').write_text('{"index":123.4}')
    def prepare(self,parameters=None,producer=None):
        return prepare_cached([self.source],parameters or {'version':1},self.dest,producer or self.produce,root=self.root)
    def test_hit_returns_private_verified_copy(self):
        self.assertFalse(self.prepare());self.assertTrue(self.prepare());self.assertEqual(self.calls,1)
        (self.dest/'data.parquet').write_bytes(b'local corruption')
        self.assertTrue(self.prepare());self.assertEqual((self.dest/'data.parquet').read_bytes(),b'one')
    def test_same_size_same_mtime_edit_invalidates(self):
        self.prepare();s=self.source.stat();self.source.write_bytes(b'two');os.utime(self.source,ns=(s.st_atime_ns,s.st_mtime_ns))
        self.assertFalse(self.prepare());self.assertEqual(self.calls,2)
    def test_parameters_invalidate(self):
        self.prepare();self.assertFalse(self.prepare({'version':2}));self.assertEqual(self.calls,2)
    def test_corrupt_and_missing_cache_rebuild(self):
        self.prepare();entry=next(self.root.glob('*/manifest.json')).parent
        (entry/'data.parquet').write_bytes(b'bad');self.assertFalse(self.prepare())
        (entry/'index.json').unlink();self.assertFalse(self.prepare());self.assertEqual(self.calls,3)
    def test_source_mutation_during_build_never_installs(self):
        def bad(out):self.produce(out);self.source.write_bytes(b'two')
        with self.assertRaisesRegex(RuntimeError,'變動'):self.prepare(producer=bad)
        self.assertFalse(list(self.root.glob('*/manifest.json')))
    def test_partial_producer_failure_never_installs(self):
        def bad(out):(out/'data.parquet').write_bytes(b'partial');raise RuntimeError('failed')
        with self.assertRaises(RuntimeError):self.prepare(producer=bad)
        self.assertFalse(list(self.root.glob('*/manifest.json')))
    def test_new_source_during_build_is_detected(self):
        other=self.base/'new-source'
        def files():return [self.source]+([other] if other.exists() else [])
        def bad(out):self.produce(out);other.write_bytes(b'new')
        with self.assertRaisesRegex(RuntimeError,'變動'):
            prepare_cached(files,{},self.dest,bad,root=self.root)
        self.assertFalse(list(self.root.glob('*/manifest.json')))
    def test_file_set_changes_invalidate(self):
        self.prepare();other=self.base/'other';other.write_bytes(b'new')
        self.assertFalse(prepare_cached([self.source,other],{'version':1},self.dest,self.produce,root=self.root))
    def test_disabled_cache_does_not_reuse(self):
        with patch.dict(os.environ,{'TW_MARGIN_DISABLE_LOCAL_CACHE':'1'}):
            self.assertFalse(self.prepare());self.assertFalse(self.prepare());self.assertEqual(self.calls,2)
    def test_timing_records_failure_and_propagates(self):
        out=self.base/'timing.jsonl'
        with patch.dict(os.environ,{'TW_MARGIN_TIMING_FILE':str(out)}),self.assertRaises(ValueError):
            with measured('fixture'):raise ValueError('secret not recorded')
        row=json.loads(out.read_text());self.assertEqual(row['status'],'failed');self.assertGreaterEqual(row['seconds'],0)
        self.assertNotIn('secret',out.read_text())
    def test_source_map_checks_inputs_and_rules(self):
        calls=[]
        def parse():calls.append(1);return {'1234':self.source.read_text()}
        with patch.dict(os.environ,{'TW_MARGIN_CACHE_ROOT':str(self.root)}):
            self.assertEqual(cached_json([self.source],{'rule':1},parse),{'1234':'one'})
            self.assertEqual(cached_json([self.source],{'rule':1},parse),{'1234':'one'})
            self.assertEqual(len(calls),1)
            cached_json([self.source],{'rule':2},parse);self.assertEqual(len(calls),2)
            self.source.write_bytes(b'two')
            self.assertEqual(cached_json([self.source],{'rule':2},parse),{'1234':'two'})
    def test_source_map_conflict_is_not_cached(self):
        def conflict():raise ValueError('market conflict')
        with patch.dict(os.environ,{'TW_MARGIN_CACHE_ROOT':str(self.root)}),self.assertRaises(ValueError):
            cached_json([self.source],{},conflict)
        self.assertFalse(list(self.root.glob('**/manifest.json')))
