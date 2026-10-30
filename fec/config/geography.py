"""US states and territories."""

from fec.config.tables import CONFIG_DIR, read_rows

US_STATES = frozenset({
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    'DC', 'PR', 'VI', 'GU', 'AS', 'MP',
})


# Geocoding bounds: (lat_min, lat_max, lng_min, lng_max) per state.
_BOUNDS = ("lat_min", "lat_max", "lng_min", "lng_max")
US_STATE_BBOX = {row["state"]: tuple(float(row[c]) for c in _BOUNDS)
                 for row in read_rows(CONFIG_DIR / "state_bounds.csv")}
