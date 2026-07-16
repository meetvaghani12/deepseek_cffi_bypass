"""Tests for the PoW pre-solve pool — pure logic, no browser."""
import sys
import os
import time
import json
import base64
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.network.session import PoWPool, _pow_expire_at_ms


def _token(expire_at_ms):
    """Build a base64 PoW token carrying an expiry, like a real solution."""
    return base64.b64encode(json.dumps({
        "algorithm": "DeepSeekHashV1", "answer": 123, "expire_at": expire_at_ms,
    }).encode()).decode()


def test_expire_at_decoded():
    tok = _token(1783791951338)
    assert _pow_expire_at_ms(tok) == 1783791951338


def test_expire_at_bad_token_returns_none():
    assert _pow_expire_at_ms("not-base64!!") is None


def test_pool_serves_prefilled_token_instantly():
    future = int((time.time() + 300) * 1000)  # 5 min out
    solved = []
    def solve():
        t = _token(future); solved.append(t); return t
    pool = PoWPool(solve_fn=solve, lock=threading.Lock(), target_size=2)
    # give the refiller a moment to fill
    for _ in range(50):
        if pool._count_valid() >= 2:
            break
        time.sleep(0.02)
    tok = pool.take()
    assert tok is not None
    assert _pow_expire_at_ms(tok) == future
    pool.stop()


def test_pool_skips_expired_tokens():
    past = int((time.time() - 10) * 1000)  # already expired
    pool = PoWPool(solve_fn=lambda: None, lock=threading.Lock(), target_size=0)
    pool.put(_token(past))
    # take() must not return an expired token
    assert pool.take() is None
    pool.stop()


def test_pool_empty_returns_none_for_inline_solve():
    pool = PoWPool(solve_fn=lambda: None, lock=threading.Lock(), target_size=0)
    assert pool.take() is None  # caller then solves inline
    pool.stop()


def test_pool_refills_after_take():
    future = int((time.time() + 300) * 1000)
    calls = {"n": 0}
    def solve():
        calls["n"] += 1
        return _token(future)
    pool = PoWPool(solve_fn=solve, lock=threading.Lock(), target_size=2)
    time.sleep(0.2)
    first = calls["n"]
    assert first >= 2, f"should have pre-solved to target, got {first}"
    pool.take()
    time.sleep(0.2)
    assert calls["n"] > first, "should refill after a token is taken"
    pool.stop()


def test_solve_runs_under_lock():
    """The pool must hold the shared browser lock while solving."""
    lock = threading.Lock()
    held = []
    future = int((time.time() + 300) * 1000)
    def solve():
        held.append(lock.locked())
        return _token(future)
    pool = PoWPool(solve_fn=solve, lock=lock, target_size=1)
    time.sleep(0.2)
    pool.stop()
    assert held and all(held), "solve_fn must run while the lock is held"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try: t(); print(f"  PASS  {t.__name__}"); passed += 1
        except Exception: print(f"  FAIL  {t.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
