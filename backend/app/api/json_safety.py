"""
Recursively converts pandas/numpy types into plain JSON-safe Python types.
Used on every API response payload rather than relying on FastAPI's default
encoder, since pandas Timestamps, numpy scalars, and NaN values have all
caused real serialization surprises in ad-hoc testing during this project
and are worth handling explicitly rather than hoping the framework's
default behavior covers every case correctly.

Also strips any dict key starting with `_` (the project-wide convention for
in-process-only fields, e.g. `_record_uids` on a fingerprint) -- those are
internal plumbing between layers and were never meant to be exposed
externally.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def jsonify(obj):
    if isinstance(obj, dict):
        return {k: jsonify(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple, set)):
        return [jsonify(v) for v in obj]
    if isinstance(obj, pd.Timestamp):
        return None if pd.isna(obj) else obj.isoformat()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return None if math.isnan(value) else value
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj
