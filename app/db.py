from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine):
    # expire_on_commit=True (the default) so that objects re-read their
    # attributes -- including the `attempts` relationship -- after each
    # commit. The scheduler repeatedly commits and re-inspects the same
    # event within one session (claim -> attempt -> retry/terminal), so a
    # stale cached collection would hide attempts made after the first read.
    return sessionmaker(bind=engine)
