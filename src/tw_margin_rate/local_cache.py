"""Per-Mac cache of raw-data preparation only, never of portfolio calculations.

Every lookup rehashes source bytes (not just size/mtime), parameters and code.
Products are hashed too, atomically installed under a lock, then copied to a
private run directory. Corruption/missing products cause a rebuild.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_signature(paths):
    result=[]
    for path in sorted(map(Path, paths)):
        before=path.stat()
        value=sha256(path)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise RuntimeError('歷史原始檔案在讀取期間變動，停止而不使用快取')
        result.append([str(path.resolve()),value])
    return result


def cache_root():
    custom=os.environ.get('TW_MARGIN_CACHE_ROOT')
    return Path(custom).expanduser() if custom else Path.home()/'Library/Caches/tw-margin-rate/prepared-years'


def prepare_cached(paths, parameters, destination, producer, *, root=None, products=('data.parquet','index.json')):
    """producer(private_directory) creates data.parquet and index.json."""
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    if os.environ.get('TW_MARGIN_DISABLE_LOCAL_CACHE')=='1':
        producer(destination)
        return False
    resolve=lambda:list(map(Path,paths() if callable(paths) else paths))
    signature=source_signature(resolve())
    key=hashlib.sha256(json.dumps([sha256(Path(__file__)),products,parameters,signature],sort_keys=True).encode()).hexdigest()
    root=Path(root) if root is not None else cache_root()
    root.mkdir(parents=True,exist_ok=True)
    entry=root/key
    with (root/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            manifest=json.loads((entry/'manifest.json').read_text())
            hit=manifest['key']==key and all(sha256(entry/name)==manifest['products'][name] for name in products)
        except (OSError,ValueError,KeyError,TypeError):
            hit=False
        if not hit:
            with tempfile.TemporaryDirectory(prefix='prepare-',dir=root) as temporary:
                temp=Path(temporary)
                producer(temp)
                if source_signature(resolve())!=signature:
                    raise RuntimeError('歷史原始檔案在整理期間變動，停止而不保存快取')
                manifest={'key':key,'products':{name:sha256(temp/name) for name in products}}
                entry.mkdir(exist_ok=True)
                for name in products:
                    os.replace(temp/name,entry/name)
                (temp/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
                os.replace(temp/'manifest.json',entry/'manifest.json')
        for name in products:
            shutil.copyfile(entry/name,destination/name)
            if sha256(destination/name)!=manifest['products'][name]:
                raise RuntimeError('歷史整理快取複製驗證不符，停止')
        return hit


def cached_json(paths, parameters, producer):
    """Cache a verified source interpretation, not the market selection rules."""
    with tempfile.TemporaryDirectory(prefix='margin-source-map-') as directory:
        destination=Path(directory)
        def build(output):
            (output/'value.json').write_text(json.dumps(producer(),sort_keys=True,allow_nan=False))
        prepare_cached(paths,parameters,destination,build,root=cache_root()/'source-maps',products=('value.json',))
        return json.loads((destination/'value.json').read_text())
