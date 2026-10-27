from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["LIFEOS_NO_SCHEDULER"] = "1"


def make_pdf(path: Path, text: str) -> Path:
    """Write a minimal one-page PDF whose text pypdf can extract."""
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh database and an approved Downloads folder with sample files."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(data))
    monkeypatch.setenv("LIFEOS_DB", str(data / "test.db"))
    from lifeos import llm, settings

    llm._status_cache.update(checked=0.0, url=None)
    settings.update({"ollama_url": "http://127.0.0.1:9", "planner": "auto"})

    home = tmp_path / "home"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    make_pdf(downloads / "invoice_march.pdf", "Invoice 2291. Payment due by March 30. Total amount 120 USD.")
    make_pdf(downloads / "js-testing-guide.pdf", "A practical guide to JavaScript testing with Jest and unit test design.")
    make_pdf(downloads / "lecture5.pdf", "Lecture 5 course notes for the semester exam in linear algebra.")
    make_pdf(downloads / "scan001.pdf", "Hello world")
    (downloads / "holiday.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
    (downloads / "notes.txt").write_text("Fix for the programming error: set PYTHONPATH before running pytest.")
    return {"tmp": tmp_path, "home": home, "downloads": downloads}


@pytest.fixture()
def approved(env):
    from lifeos import files, permissions

    folder = permissions.add_folder(str(env["downloads"]))
    files.index_folder(folder["id"], Path(folder["path"]))
    return env


@pytest.fixture()
def client(approved):
    from fastapi.testclient import TestClient

    from lifeos.main import app

    with TestClient(app, headers={"X-LifeOS-Client": "1"}) as c:
        yield c
