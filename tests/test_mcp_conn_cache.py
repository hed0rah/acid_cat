"""MCP read-connection cache: reuse across calls, eviction, and thread-safety
(connections are opened check_same_thread=False and used under _CACHE_LOCK)."""
import threading

import acidcat.mcp_server as M
from acidcat.core.catalogue import index as idx


def test_cached_conn_reuse_and_evict(tmp_path):
    db = str(tmp_path / "t.db")
    idx.open_db(db).close()
    with M.handlers._CACHE_LOCK:
        c1 = M.handlers._cached_conn(db)
        c2 = M.handlers._cached_conn(db)
    assert c1 is c2                         # same connection reused
    M.handlers._evict(db)
    with M.handlers._CACHE_LOCK:
        c3 = M.handlers._cached_conn(db)
    assert c3 is not c1                     # reopened after eviction
    M.handlers._evict()
    assert db not in M.handlers._CONN_CACHE


def test_cached_conn_thread_safe(tmp_path):
    db = str(tmp_path / "t.db")
    conn = idx.open_db(db)
    idx.upsert_sample(conn, {"path": "/x/a.wav", "format": "wav", "bpm": 120})
    conn.commit()
    conn.close()
    errors = []

    def work():
        try:
            for _ in range(50):
                with M.handlers._CACHE_LOCK:
                    M.handlers._cached_conn(db).execute(
                        "SELECT COUNT(*) FROM samples").fetchall()
        except Exception as e:              # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    M.handlers._evict()
