"""Supabase client helper.

Separate from database.py, which talks to the same Supabase project's
Postgres database directly over DATABASE_URL (SQLAlchemy/psycopg2) -- that
is still the right way to read/write any table the app already uses.

This module is for Supabase-specific features raw Postgres access does not
cover: Storage (file buckets), Supabase Auth, Realtime, or the PostgREST
table API. Needs SUPABASE_URL and SUPABASE_KEY from Project Settings -> API
in the Supabase dashboard, set via environment variable,
.streamlit/secrets.toml, or .env.local -- same precedence and file as
DATABASE_URL in database.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from supabase import Client, create_client

ENV_FILE = Path(__file__).resolve().parent / ".env.local"

_client: Client | None = None
_client_loaded = False


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().strip('"').strip("'")
    return text or None


def _read_env_file(name: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name:
            return _clean(raw)
    return None


def supabase_credentials() -> tuple[str | None, str | None]:
    """(SUPABASE_URL, SUPABASE_KEY), or (None, None) if not configured."""
    url = _clean(os.environ.get("SUPABASE_URL"))
    key = _clean(os.environ.get("SUPABASE_KEY")) or _clean(
        os.environ.get("SUPABASE_ANON_KEY")
    )

    # Streamlit Cloud / local .streamlit/secrets.toml. Only touch Streamlit
    # if it is already loaded; importing it outside `streamlit run` can
    # hang the interpreter (see database.py's _database_url).
    if "streamlit" in sys.modules:
        try:
            import streamlit as st  # type: ignore

            secrets = st.secrets
            if "supabase" in secrets:
                block = secrets["supabase"]
                url = url or _clean(block.get("url"))
                key = (
                    key
                    or _clean(block.get("key"))
                    or _clean(block.get("anon_key"))
                )
            url = url or _clean(secrets.get("SUPABASE_URL"))
            key = (
                key
                or _clean(secrets.get("SUPABASE_KEY"))
                or _clean(secrets.get("SUPABASE_ANON_KEY"))
            )
        except Exception:
            pass

    url = url or _read_env_file("SUPABASE_URL")
    key = key or _read_env_file("SUPABASE_KEY") or _read_env_file("SUPABASE_ANON_KEY")
    return url, key


def get_supabase_client() -> Client | None:
    """Cached Supabase client, or None when SUPABASE_URL/SUPABASE_KEY are unset.

    For anything already stored in the app's own tables, use `database.py`
    (`import database as db`) instead -- it is already wired up and talks
    to the same database directly. Reach for this client only for Storage,
    Auth, Realtime, or PostgREST.
    """
    global _client, _client_loaded
    if _client_loaded:
        return _client
    url, key = supabase_credentials()
    if url and key:
        _client = create_client(url, key)
    _client_loaded = True
    return _client
