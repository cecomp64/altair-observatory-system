from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# The tables robs touches, as Target Scheduler 5.9.6 creates them (schema
# version 23): its column names, casing and NOT NULL constraints.
CREATE_SCHEDULER_SCHEMA = """
CREATE TABLE `project` (
    `Id` INTEGER NOT NULL,
    `profileId` TEXT NOT NULL,
    `name` TEXT NOT NULL,
    `description` TEXT,
    `state` INTEGER,
    `priority` INTEGER,
    `createdate` INTEGER,
    `activedate` INTEGER,
    `inactivedate` INTEGER,
    `minimumtime` INTEGER,
    `minimumaltitude` REAL,
    `usecustomhorizon` INTEGER,
    `horizonoffset` REAL,
    `meridianwindow` INTEGER,
    `filterswitchfrequency` INTEGER,
    `ditherevery` INTEGER,
    `enablegrader` INTEGER, isMosaic INTEGER NOT NULL DEFAULT 0, flatsHandling INTEGER NOT NULL DEFAULT 0, maximumAltitude REAL DEFAULT 0, smartexposureorder INTEGER DEFAULT 0, guid TEXT,
    PRIMARY KEY(`id`)
);

CREATE TABLE `ruleweight` (
    `Id` INTEGER NOT NULL,
    `name` TEXT NOT NULL,
    `weight` REAL NOT NULL,
    `projectid` INTEGER,
    FOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),
    PRIMARY KEY(`Id`)
);

CREATE TABLE `target` (
    `Id` INTEGER NOT NULL,
    `name` TEXT NOT NULL,
    `active` INTEGER NOT NULL,
    `ra` REAL,
    `dec` REAL,
    `epochcode` INTEGER NOT NULL,
    `rotation` REAL,
    `roi` REAL,
    `projectid` INTEGER, unusedOEO TEXT, guid TEXT,
    FOREIGN KEY(`projectId`) REFERENCES `project`(`Id`),
    PRIMARY KEY(`id`)
);

CREATE TABLE `exposuretemplate` (
    `Id` INTEGER NOT NULL,
    `profileId` TEXT NOT NULL,
    `name` TEXT NOT NULL,
    `filtername` TEXT NOT NULL,
    `gain` INTEGER,
    `offset` INTEGER,
    `bin` INTEGER,
    `readoutmode` INTEGER,
    `twilightlevel` INTEGER,
    `moonavoidanceenabled` INTEGER,
    `moonavoidanceseparation` REAL,
    `moonavoidancewidth` INTEGER,
    `maximumhumidity` REAL, defaultexposure REAL DEFAULT 60, moonrelaxscale REAL DEFAULT 0, moonrelaxmaxaltitude REAL DEFAULT 5, moonrelaxminaltitude REAL DEFAULT -15, moondownenabled INTEGER DEFAULT 0, ditherevery INTEGER DEFAULT -1, minutesOffset INTEGER DEFAULT 0, guid TEXT,
    PRIMARY KEY(`Id`)
);

CREATE TABLE `exposureplan` (
    `Id` INTEGER NOT NULL,
    `profileId` TEXT NOT NULL,
    `exposure` REAL NOT NULL,
    `desired` INTEGER,
    `acquired` INTEGER,
    `accepted` INTEGER,
    `targetid` INTEGER,
    `exposureTemplateId` INTEGER, enabled INTEGER DEFAULT 1, guid TEXT,
    FOREIGN KEY(`targetId`) REFERENCES `target`(`Id`),
    FOREIGN KEY(`exposureTemplateId`) REFERENCES `exposuretemplate`(`Id`),
    PRIMARY KEY(`Id`)
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
scheduler_db_path: "{scheduler_db_path.as_posix()}"
subs_dir: "{subs_dir.as_posix()}"
nina_profile_id: "11111111-1111-1111-1111-111111111111"
"""
    )
    return config_path
