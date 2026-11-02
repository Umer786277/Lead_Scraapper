import subprocess, sys, textwrap

import locks

HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, r"E:\\Projects\\lead_scraper")
    import locks
    lk = locks.BrowserFileLock()
    assert lk.acquire(blocking=False), "holder failed to lock"
    print("held", flush=True)
    time.sleep(4)
    lk.release()
    print("released", flush=True)
""")

p = subprocess.Popen(
    [sys.executable, "-c", HOLDER],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
line = p.stdout.readline()  # "held"
assert line.strip() == "held", f"subprocess did not acquire: {line!r}"

lk = locks.BrowserFileLock()
try:
    got = lk.acquire(blocking=False)
    assert not got, "main process acquired a lock the child holds — cross-process lock broken!"
    print("ok  cross-process: non-blocking acquire correctly denied while child holds")
    print("ok  acquired after child releases")
    out, err = p.communicate(timeout=15)
    assert (out.strip() or "").splitlines() == ["released"], f"child output: {out} {err}"
    assert lk.acquire(blocking=False), "could not lock after child release"
    with lk:
        assert lk.acquire(blocking=False), "reentrant acquire in same thread failed"
        lk.release()
    print("ok  reentrant within same thread")
    lk.release()
except AssertionError as e:
    p.kill()
    raise
print("LOCK CHECKS PASSED")