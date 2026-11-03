"""Background indexing must not lock out other writes (regression: 'database is locked' on Windows)."""
from __future__ import annotations

import threading
import time


def test_writes_proceed_while_a_folder_is_indexing(approved, monkeypatch):
    from lifeos import db, files, permissions, tasks

    big = approved["home"] / "Documents"
    big.mkdir()
    for i in range(30):
        (big / f"note{i}.txt").write_text(f"note {i}")
    folder = permissions.add_folder(str(big))

    real_extract = files.extract_text

    def slow_extract(path, *a, **k):
        time.sleep(0.05)  # stands in for parsing a large PDF
        return real_extract(path, *a, **k)

    monkeypatch.setattr(files, "extract_text", slow_extract)
    monkeypatch.setattr(db, "BUSY_TIMEOUT_S", 1, raising=False)

    indexer = threading.Thread(target=files.index_folder, args=(folder["id"], big))
    indexer.start()
    time.sleep(0.2)  # indexer is now mid-walk

    start = time.monotonic()
    other = approved["home"] / "Pictures"
    other.mkdir()
    permissions.add_folder(str(other))  # the write that failed in the field
    tasks.create("Written during indexing")
    elapsed = time.monotonic() - start

    indexer.join()
    assert elapsed < 0.5, f"writes waited {elapsed:.2f}s for the indexer"
    assert len(files.search("note", folder=str(big), limit=100)) == 30


def test_same_folder_is_not_indexed_twice_at_once(approved):
    from lifeos import files, permissions

    fid, root = permissions.approved_roots()[0]
    results = []
    threads = [threading.Thread(target=lambda: results.append(files.index_folder(fid, root))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3
    assert len(files.search("", folder=str(root), limit=100)) == 6
