"""Column/table names for NINA's Target Scheduler plugin database.

Target Scheduler (the NINA plugin by Tom Palmer) stores its state in a
SQLite database, `schedulerdb.sqlite`. The names and defaults below were
checked against Target Scheduler 5.9.6 (`PRAGMA user_version` 23) and the
rows its own UI writes:

    project (Id, profileId, name, description, state, priority, createdate,
             minimumtime, minimumaltitude, usecustomhorizon, horizonoffset,
             meridianwindow, filterswitchfrequency, ditherevery, enablegrader,
             isMosaic, flatsHandling, maximumAltitude, smartexposureorder, guid)
    ruleweight (Id, name, weight, projectid)
    target (Id, name, active, ra, dec, epochcode, rotation, roi, projectid, guid)
    exposureplan (Id, profileId, exposure, desired, acquired, accepted,
                  targetid, exposureTemplateId, enabled, guid)
    exposuretemplate (Id, profileId, name, filtername, gain, offset, bin,
                      readoutmode, twilightlevel, moonavoidance..., defaultexposure,
                      ditherevery, guid)

Target Scheduler is a third-party plugin and its schema changes across
releases. After installing or upgrading it:

1. Run `robs check-schema --config <file>`, which verifies every table and
   column named here exists (ignoring case, as SQLite does) and fails loudly
   if not, rather than corrupting NINA's database.
2. If it fails, compare the plugin's tables against the constants below and
   update the constants (not the calling code) to match.

The plugin's docs allow direct edits, also while NINA runs: it picks them up
the next time its planning engine runs
(https://tcpalmer.github.io/nina-scheduler/adv-topics/database.html).
"""

from __future__ import annotations

PROJECT_TABLE = "project"
PROJECT_COLUMNS = {
    "id": "Id",
    "profile_id": "profileId",
    "name": "name",
    "description": "description",
    "state": "state",
    "priority": "priority",
    "create_date": "createdate",
    "guid": "guid",
}

# Used only in ts_project_mode: per_hub_project (§8.3). If your plugin
# version lacks them, check-schema says so and the worker falls back to
# a single managed project (§13 #3).
PROJECT_OPTIONAL_COLUMNS = {
    "minimum_altitude": "minimumaltitude",
}

# Target Scheduler project states: 0 = draft, 1 = active, 2 = inactive, 3 = closed.
PROJECT_STATE_ACTIVE = 1
PROJECT_STATE_INACTIVE = 2

# Target Scheduler project priorities: 0 = low, 1 = normal, 2 = high.
PROJECT_PRIORITY_LOW = 0
PROJECT_PRIORITY_NORMAL = 1
PROJECT_PRIORITY_HIGH = 2

# What the plugin's UI writes for a new project. The plugin reads these
# columns into non-nullable fields, so a project row needs all of them.
PROJECT_DEFAULTS = {
    "minimumtime": 30,
    "usecustomhorizon": 0,
    "horizonoffset": 0,
    "meridianwindow": 0,
    "filterswitchfrequency": 0,
    "ditherevery": 0,
    "enablegrader": 1,
}

# Every project has one row per scoring rule; these are the plugin's defaults.
RULE_WEIGHT_TABLE = "ruleweight"
RULE_WEIGHT_COLUMNS = {
    "id": "Id",
    "name": "name",
    "weight": "weight",
    "project_id": "projectid",
}
DEFAULT_RULE_WEIGHTS = {
    "Meridian Flip Penalty": 0,
    "Meridian Window Priority": 75,
    "Mosaic Completion": 0,
    "Percent Complete": 50,
    "Project Priority": 50,
    "Setting Soonest": 50,
    "Smart Exposure Order": 0,
    "Target Switch Penalty": 67,
}

TARGET_TABLE = "target"
TARGET_COLUMNS = {
    "id": "Id",
    "project_id": "projectid",
    "name": "name",
    "ra": "ra",  # hours, not degrees
    "dec": "dec",
    "active": "active",
    "epoch_code": "epochcode",
    "rotation": "rotation",
    "roi": "roi",
    "guid": "guid",
}
TARGET_EPOCH_J2000 = 2
TARGET_DEFAULT_ROI = 100

EXPOSURE_TEMPLATE_TABLE = "exposuretemplate"
EXPOSURE_TEMPLATE_COLUMNS = {
    "id": "Id",
    "profile_id": "profileId",
    "name": "name",
    "filter_name": "filtername",
    "default_exposure": "defaultexposure",
    "guid": "guid",
}

# What the plugin's UI writes for a new exposure template (-1 = the camera's
# own setting, or "use the project's" for ditherevery; moon avoidance off).
EXPOSURE_TEMPLATE_DEFAULTS = {
    "gain": -1,
    "offset": -1,
    "bin": 1,
    "readoutmode": -1,
    "twilightlevel": 0,
    "moonavoidanceenabled": 0,
    "moonavoidanceseparation": 60,
    "moonavoidancewidth": 7,
    "maximumhumidity": 0,
    "ditherevery": -1,
}

EXPOSURE_PLAN_TABLE = "exposureplan"
EXPOSURE_PLAN_COLUMNS = {
    "id": "Id",
    "profile_id": "profileId",
    "target_id": "targetid",
    "exposure_template_id": "exposureTemplateId",
    "exposure": "exposure",  # seconds; -1 = the template's default exposure
    "desired": "desired",
    "acquired": "acquired",
    "accepted": "accepted",
    "enabled": "enabled",
    "guid": "guid",
}

# A dedicated project holds every target this worker manages, so it's
# easy to tell "queue-managed" targets apart from anything an operator
# schedules by hand directly in NINA.
MANAGED_PROJECT_NAME = "Remote Observatory Queue"

ALL_TABLES = {
    PROJECT_TABLE: {**PROJECT_COLUMNS, **{name: name for name in PROJECT_DEFAULTS}},
    RULE_WEIGHT_TABLE: RULE_WEIGHT_COLUMNS,
    TARGET_TABLE: TARGET_COLUMNS,
    EXPOSURE_TEMPLATE_TABLE: {**EXPOSURE_TEMPLATE_COLUMNS, **{name: name for name in EXPOSURE_TEMPLATE_DEFAULTS}},
    EXPOSURE_PLAN_TABLE: EXPOSURE_PLAN_COLUMNS,
}
