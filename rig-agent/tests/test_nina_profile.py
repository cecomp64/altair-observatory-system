from __future__ import annotations

import logging
from pathlib import Path

from robs import nina_profile
from robs.config import TelescopeConfig

PROFILE_ID = "11111111-1111-1111-1111-111111111111"
# The shape NINA writes: the filter wheel's filters in their own XML namespace.
PROFILE_XML = """<Profile xmlns="http://schemas.datacontract.org/2004/07/NINA.Profile">
<FilterWheelSettings><FilterWheelFilters xmlns:a="http://schemas.datacontract.org/2004/07/NINA.Core.Model.Equipment">
<a:FilterInfo><a:FlatWizardFilterSettings><a:Gain>-1</a:Gain></a:FlatWizardFilterSettings><a:_name>Red</a:_name><a:_position>0</a:_position></a:FilterInfo>
<a:FilterInfo><a:_name>Ha</a:_name><a:_position>1</a:_position></a:FilterInfo>
</FilterWheelFilters><Id>ASCOM.Simulator.FilterWheel</Id></FilterWheelSettings>
<Id>11111111-1111-1111-1111-111111111111</Id><Name>Test rig</Name></Profile>"""


def config_in(nina_dir: Path, profile_id: str | None = PROFILE_ID) -> TelescopeConfig:
    return TelescopeConfig(slug="test-scope", api_base_url=None, api_key=None, hub_enabled=False,
                           scheduler_db_path=nina_dir / "SchedulerPlugin" / "schedulerdb.sqlite", subs_dir=nina_dir / "subs",
                           nina_profile_id=profile_id)


def write_profile(nina_dir: Path, xml: str = PROFILE_XML) -> Path:
    (nina_dir / "Profiles").mkdir(parents=True)
    path = nina_dir / "Profiles" / f"{PROFILE_ID}.profile"
    path.write_text(xml, encoding="utf-8")
    return path


def targets(*filters: str) -> list[dict]:
    return [{"id": 1, "exposure_plans": [{"filter": name} for name in filters]}]


def test_finds_the_profile_beside_the_scheduler_plugin_folder(tmp_path):
    path = write_profile(tmp_path)

    assert nina_profile.profile_path(config_in(tmp_path)) == path
    assert nina_profile.profile_path(config_in(tmp_path, "22222222-2222-2222-2222-222222222222")) is None
    assert nina_profile.profile_path(config_in(tmp_path, None)) is None


def test_reads_the_filter_wheels_filter_names(tmp_path):
    assert nina_profile.filter_names(write_profile(tmp_path)) == {"Red", "Ha"}


def test_missing_filters_are_the_plan_filters_the_wheel_lacks(tmp_path):
    write_profile(tmp_path)

    assert nina_profile.missing_filters(config_in(tmp_path), targets("Ha", "OIII", "Luminance", "Red")) == ["Luminance", "OIII"]
    assert nina_profile.missing_filters(config_in(tmp_path), targets("Ha")) == []


def test_nothing_is_reported_missing_without_a_readable_profile(tmp_path, caplog):
    assert nina_profile.missing_filters(config_in(tmp_path), targets("Ha")) == []  # no profile

    write_profile(tmp_path, "<Profile")
    with caplog.at_level(logging.WARNING):
        assert nina_profile.missing_filters(config_in(tmp_path), targets("Ha")) == []
    assert "Couldn't read NINA profile" in caplog.text
