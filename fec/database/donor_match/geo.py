"""Post-geocode canonicalization: collapse street forms that map to one physical point."""

import math
from collections import defaultdict

import pandas as pd

_EARTH_RADIUS_M = 6371000.0


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def canonicalize_donor_addresses_geo(df: pd.DataFrame, radius_m: float = 50.0) -> int:
    """Collapse per-donor street_1 forms whose precise geocodes sit within radius_m to the most common form (coords aligned; coarse geocode levels skipped); returns rows rewritten."""
    needed = {"latitude", "longitude", "donor_key", "contributor_street_1"}
    if not needed <= set(df.columns):
        return 0
    ind = df["entity_type"] == "INDIVIDUAL" if "entity_type" in df.columns \
        else pd.Series(True, index=df.index)
    if not ind.any():
        return 0

    st_col = df.columns.get_loc("contributor_street_1")
    lat_col = df.columns.get_loc("latitude")
    lng_col = df.columns.get_loc("longitude")
    level_col = df.columns.get_loc("geocode_level") if "geocode_level" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        pts = []  # (row_i, street, lat, lng)
        for i in idx:
            s = df.iat[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            if level_col is not None:
                lvl = str(df.iat[i, level_col]).lower()
                if any(t in lvl for t in ("city", "zip", "centroid", "state")):
                    continue
            try:
                lat, lng = float(df.iat[i, lat_col]), float(df.iat[i, lng_col])
            except (TypeError, ValueError):
                continue
            if math.isnan(lat) or math.isnan(lng):
                continue
            pts.append((i, s.strip(), lat, lng))
        if len(pts) < 2:
            continue

        parent = list(range(len(pts)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                if _haversine_m(pts[a][2], pts[a][3], pts[b][2], pts[b][3]) <= radius_m:
                    parent[find(a)] = find(b)

        clusters = defaultdict(list)
        for k in range(len(pts)):
            clusters[find(k)].append(k)

        for members in clusters.values():
            forms = [pts[m][1] for m in members]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=forms.count)
            rep = next(m for m in members if pts[m][1] == canon)
            rlat, rlng = pts[rep][2], pts[rep][3]
            for m in members:
                i = pts[m][0]
                if df.iat[i, st_col].strip() != canon:
                    df.iat[i, st_col] = canon
                    df.iat[i, lat_col] = rlat
                    df.iat[i, lng_col] = rlng
                    changed += 1
    return changed
