"""Column/table names for NINA's Target Scheduler plugin database.

Target Scheduler (the NINA plugin by Ghilios) stores its state in a
SQLite database with (approximately) this shape:

    project (Id, profileId, name, description, state, priority,
             minimumTime, minimumAltitude, useCustomHorizon,
             horizonOffset, filterSwitchFrequency, ditherEvery,
             enableGrader, isMosaic, createDate, activeDate, inactiveDate)
    target (Id, projectId, name, ra, dec, rotation, roi, enabled)
    exposureplan (Id, targetId, exposureTemplateId, desired, accepted)
    exposuretemplate (Id, profileId, name, filterName, defaultExposure, ...)

**This is best-effort** and reconstructed from the plugin's public
schema as of the time this worker was written — Target Scheduler is a
third-party plugin that changes across releases, and we don't have a
live install to verify column names/casing against. Before relying on
`scheduler_db.py` in production:

1. Open your installed plugin's `schedulerdb.sqlite` with any SQLite
   browser and compare its `project`/`target`/`exposureplan`/
   `exposuretemplate` tables against the constants below.
2. Update the constants (not the calling code) to match.
3. Run `robs check-schema --telescope <slug>` (see cli.py), which just
   verifies every table/column named here actually exists and fails
   loudly with a clear message if not, rather than corrupting NINA's
   database.
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
    "create_date": "createDate",
}

# Used only in ts_project_mode: per_hub_project (§8.3). If your plugin
# version lacks them, check-schema says so and the worker falls back to
# a single managed project (§13 #3).
PROJECT_OPTIONAL_COLUMNS = {
    "minimum_altitude": "minimumAltitude",
}

# Target Scheduler project states (best effort): 1 = active, 2 = inactive.
PROJECT_STATE_ACTIVE = 1
PROJECT_STATE_INACTIVE = 2

TARGET_TABLE = "target"
TARGET_COLUMNS = {
    "id": "Id",
    "project_id": "projectId",
    "name": "name",
    "ra": "ra",
    "dec": "dec",
    "enabled": "enabled",
}

EXPOSURE_TEMPLATE_TABLE = "exposuretemplate"
EXPOSURE_TEMPLATE_COLUMNS = {
    "id": "Id",
    "profile_id": "profileId",
    "name": "name",
    "filter_name": "filterName",
    "default_exposure": "defaultExposure",
}

EXPOSURE_PLAN_TABLE = "exposureplan"
EXPOSURE_PLAN_COLUMNS = {
    "id": "Id",
    "target_id": "targetId",
    "exposure_template_id": "exposureTemplateId",
    "desired": "desired",
    "accepted": "accepted",
}

# A dedicated project holds every target this worker manages, so it's
# easy to tell "queue-managed" targets apart from anything an operator
# schedules by hand directly in NINA.
MANAGED_PROJECT_NAME = "Remote Observatory Queue"

ALL_TABLES = {
    PROJECT_TABLE: PROJECT_COLUMNS,
    TARGET_TABLE: TARGET_COLUMNS,
    EXPOSURE_TEMPLATE_TABLE: EXPOSURE_TEMPLATE_COLUMNS,
    EXPOSURE_PLAN_TABLE: EXPOSURE_PLAN_COLUMNS,
}
