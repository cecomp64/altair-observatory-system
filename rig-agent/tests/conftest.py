from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

CREATE_SCHEDULER_SCHEMA = """
CREATE TABLE project (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    profileId TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    state INTEGER,
    priority INTEGER,
    createDate INTEGER
);

CREATE TABLE target (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    projectId INTEGER NOT NULL,
    name TEXT NOT NULL,
    ra REAL,
    dec REAL,
    enabled INTEGER
);

CREATE TABLE exposuretemplate (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    profileId TEXT NOT NULL,
    name TEXT,
    filterName TEXT NOT NULL,
    defaultExposure INTEGER
);

CREATE TABLE exposureplan (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    targetId INTEGER NOT NULL,
    exposureTemplateId INTEGER NOT NULL,
    desired INTEGER,
    accepted INTEGER
);
"""


@pytest.fixture
def scheduler_db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "schedulerdb.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(CREATE_SCHEDULER_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def telescope_config_yaml(tmp_path: Path, scheduler_db_path: Path) -> Path:
    subs_dir = tmp_path / "subs"
    subs_dir.mkdir()
    config_path = tmp_path / "telescope.yml"
    config_path.write_text(
        f"""
slug: test-scope
api_base_url: "https://example.test"
api_key: "test-token"
scheduler_db_path: "{scheduler_db_path}"
subs_dir: "{subs_dir}"
s3_bucket: "test-bucket"
s3_prefix: "test-scope"
nina_profile_id: "11111111-1111-1111-1111-111111111111"
"""
    )
    return config_path
