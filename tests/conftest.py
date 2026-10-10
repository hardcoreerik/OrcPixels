"""Shared test setup: a fixed sample place and time zone, so Live Data tests don't depend on
the machine's settings or zone. The sample location is downtown Portland, OR (a public city
centre matching the recorded API fixtures), not anyone's home."""
import pytest

from orcpixels import place as PLACE

SAMPLE = {"home_lat": 45.5152, "home_lon": -122.6784, "home_label": "PDX", "timezone": "us-pacific",
          "livedata_alerts_label": "OREGON", "livedata_quake_label": "PNW", "livedata_nws_station": "KPDX",
          "livedata_alerts_area": "OR", "livedata_tide_station": "9435380", "livedata_tide_label": "NEWPORT",
          "livedata_usgs_site": "14211720", "livedata_river_label": "WILLAMETTE",
          "livedata_github_user": "example-user"}


@pytest.fixture(autouse=True)
def sample_place(monkeypatch):
    monkeypatch.setattr(PLACE, "OVERRIDE", dict(SAMPLE))
    monkeypatch.setenv("ORCPIXELS_TZ", "us-pacific")
    yield


SAMPLE_SETTINGS = {"orcsdr_url": "http://192.0.2.10", "livedata_ft8_port": "COM9", "livedata_ft8_home_grid": "CN85"}


@pytest.fixture(autouse=True)
def sample_settings(monkeypatch):
    """Device settings a configured install would have (TEST-NET address, fake port)."""
    from orcpixels.sources import livefeeds as LF
    orig = LF._setting
    monkeypatch.setattr(LF, "_setting", lambda k: orig(k) or SAMPLE_SETTINGS.get(k))
    yield
