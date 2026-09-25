from altair.config import HubResolve
from altair.hub.config_sync import HubConfig
from altair.hub.resolver import resolve

from conftest import TELESCOPE, TRAIN, hub_config_payload

CONFIG = HubConfig.from_payload(hub_config_payload())
RULES = HubResolve()


def r(**kw):
    args = {"object_header": None, "ra": None, "dec": None, "telescope": TELESCOPE, "optical_train": TRAIN, "config": CONFIG, "rules": RULES}
    return resolve(**{**args, **kw})


def test_header_token_wins():
    assert r(object_header="#34 M31", ra=200.0, dec=-30.0) == (34, "header_token")


def test_header_token_for_target_without_train_matches_on_the_telescope():
    assert r(object_header="#35 M33") == (35, "header_token")


def test_header_token_for_a_draft_or_foreign_target_is_ignored():
    assert r(object_header="#36 Draft thing").target_id is None
    assert r(object_header="#99 Nowhere").target_id is None


def test_name_needs_nearby_coordinates():
    assert r(object_header="Andromeda Galaxy", ra=10.7, dec=41.3) == (34, "name")
    assert r(object_header="m 31", ra=10.7, dec=41.3) == (34, "name")
    # Right name, pointing miles away: not linked by name (and nothing near by coordinates).
    assert r(object_header="M31", ra=150.0, dec=10.0).source == "unlinked"


def test_coordinates_only_when_exactly_one_target_on_this_train_is_near():
    assert r(object_header="Some field", ra=10.9, dec=41.0) == (34, "coords")
    # M33 has no train set, so coordinates alone don't link it (rule 3 is per train).
    assert r(object_header="", ra=23.46, dec=30.66).source == "unlinked"


def test_rules_can_be_switched_off():
    rules = HubResolve(by_header_token=False, by_name=False, by_coordinates=False)
    assert r(object_header="#34 M31", ra=10.7, dec=41.3, rules=rules).source == "unlinked"
