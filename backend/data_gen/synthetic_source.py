"""
Generates clean, realistic weather-station observations, matching the
NOAA ISD schema fields used throughout the RCA framework doc
(CelsiusTemperatureQuantity, RelativeHumidityNumber, station_id, etc).

Two source systems are simulated:
  - NOAA_ISD : the long-standing legacy feed (most stations, humidity as 0-100 %)
  - API_v3   : a newer vendor feed onboarded partway through the time range
               (humidity natively as a 0-1 fraction) -- this mirrors the
               framework doc's own worked example exactly, so the eventual
               defect we inject in defect_injector.py has a natural story.

Swap `fetch_real_noaa()` in for `generate_synthetic()` if/when you have
network access to NOAA's public ISD/GSOD endpoints -- the downstream
pipeline code doesn't care which one produced the DataFrame, as long as
the column contract below is respected.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

STATIONS = [
    # station_id, country, region, source_system, onboarded_on (None = present from day 1)
    ("725300-94846", "US", "Illinois", "NOAA_ISD", None),
    ("722950-23174", "US", "California", "NOAA_ISD", None),
    ("725030-14732", "US", "New York", "NOAA_ISD", None),
    ("999999-99999", "US", "Texas", "NOAA_ISD", None),
    ("037720-99999", "GB", "England", "NOAA_ISD", None),
    ("068800-99999", "FR", "Ile-de-France", "NOAA_ISD", None),
    ("761220-00999", "IN", "Karnataka", "API_v3", "onboard_day_20"),
    ("432950-00999", "IN", "Maharashtra", "API_v3", "onboard_day_20"),
    ("484550-00999", "TH", "Bangkok", "API_v3", "onboard_day_20"),
]

RNG = np.random.default_rng(42)


def generate_synthetic(start_date: datetime, num_days: int = 60) -> pd.DataFrame:
    rows = []
    for station_id, country, region, source_system, onboard_flag in STATIONS:
        onboard_day = 20 if onboard_flag == "onboard_day_20" else 0
        base_temp = RNG.uniform(10, 30)
        base_humidity_pct = RNG.uniform(40, 80)  # true physical value, always 0-100 %

        for day in range(num_days):
            if day < onboard_day:
                continue  # station doesn't exist in the feed yet
            for hour in (0, 6, 12, 18):
                obs_time = start_date + timedelta(days=day, hours=hour)
                temp = base_temp + 6 * np.sin(hour / 24 * 2 * np.pi) + RNG.normal(0, 1.2)
                humidity_pct = float(np.clip(base_humidity_pct + RNG.normal(0, 5), 5, 100))

                # API_v3 natively reports humidity as a 0-1 fraction (source quirk,
                # NOT yet a defect -- it's a legitimate format difference that the
                # warehouse transform is responsible for normalizing correctly).
                raw_humidity = humidity_pct / 100.0 if source_system == "API_v3" else humidity_pct

                rows.append(
                    dict(
                        record_uid=f"{station_id}-{obs_time.isoformat()}",
                        station_id=station_id,
                        source_system=source_system,
                        country=country,
                        region=region,
                        observed_at=obs_time,
                        batch_id=f"batch_{day:03d}_{hour:02d}",
                        load_type="incremental" if day > 0 else "full",
                        celsius_temperature=round(temp, 2),
                        relative_humidity_raw=round(raw_humidity, 4),  # pre-normalization
                        relative_humidity_true_pct=round(humidity_pct, 2),  # ground truth
                        wind_speed=round(float(RNG.uniform(0, 15)), 2),
                        sea_level_pressure=round(float(RNG.normal(1013, 5)), 2),
                        weather_description=RNG.choice(
                            ["clear sky", "few clouds", "scattered clouds", "light rain", "overcast"]
                        ),
                    )
                )
    return pd.DataFrame(rows)


def fetch_real_noaa(*args, **kwargs):
    """
    Placeholder for a real NOAA ISD/GSOD pull, e.g. via
    https://www.ncei.noaa.gov/access/services/data/v1 or the Integrated
    Surface Database. Not callable from this sandbox (network egress is
    restricted to package registries), but on your own machine this is a
    straightforward `requests.get(...)` -> DataFrame with the same
    column contract as `generate_synthetic()` above, so the rest of the
    pipeline requires zero changes.
    """
    raise NotImplementedError(
        "Run this from your local machine with network access to NOAA's API, "
        "or use generate_synthetic() as the default data source."
    )
