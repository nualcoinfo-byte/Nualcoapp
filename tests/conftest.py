from __future__ import annotations

import os

import pytest

os.environ["NUALCO_FORCE_SQLITE"] = "1"

import database as db  # noqa: E402


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.switch_to_sqlite()
    db.clear_session_actor()
    db.init_db()
    yield db
    db.ENGINE.dispose()
