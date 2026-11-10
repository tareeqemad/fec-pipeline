"""US states and territories."""

from fec.config.tables import CONFIG_DIR, read_rows, words

US_STATES = words("us_states")


# Geocoding bounds: (lat_min, lat_max, lng_min, lng_max) per state.
_BOUNDS = ("lat_min", "lat_max", "lng_min", "lng_max")
US_STATE_BBOX = {
    row["state"]: tuple(float(row[c]) for c in _BOUNDS) for row in read_rows(CONFIG_DIR / "state_bounds.csv")
}
