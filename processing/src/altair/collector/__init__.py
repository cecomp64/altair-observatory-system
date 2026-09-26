"""The collector (SPEC §7.3): pulls finished frames from each rig's NINA
share, verifies them with a double read, writes them into the NAS, and hands
them to ingest; then closes nights with a collection manifest."""
