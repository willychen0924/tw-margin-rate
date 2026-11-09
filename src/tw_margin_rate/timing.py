"""Append-only, non-sensitive timings shared by publisher child processes."""
import contextlib
import functools
import json
import os
import time

@contextlib.contextmanager
def measured(label):
    started=time.perf_counter()
    status='ok'
    try:
        yield
    except BaseException:
        status='failed'
        raise
    finally:
        path=os.environ.get('TW_MARGIN_TIMING_FILE')
        if path:
            row={'step':label,'seconds':round(time.perf_counter()-started,4),'pid':os.getpid(),'status':status}
            # A single append syscall per line; no command arguments or secrets.
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:os.write(fd,(json.dumps(row,ensure_ascii=False)+'\n').encode())
            finally:os.close(fd)

def timed(label):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args,**kwargs):
            with measured(label):return fn(*args,**kwargs)
        return wrapper
    return decorate
