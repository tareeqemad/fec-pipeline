"""US states and territories, and foreign cities that a state tells apart."""

from fec.vocabulary.tables import CONFIG_DIR, read_rows, words

US_STATES = words("us_states")


# Geocoding bounds: (lat_min, lat_max, lng_min, lng_max) per state.
_BOUNDS = ("lat_min", "lat_max", "lng_min", "lng_max")

US_STATE_BBOX = {
    row["state"]: tuple(float(row[bound]) for bound in _BOUNDS) for row in read_rows(CONFIG_DIR / "state_bounds.csv")
}


# Checked against state to avoid false positives (London OH, etc.)
FOREIGN_CITIES_NO_US_STATE = words("foreign_cities_no_us_state")

# Cities abroad and in the US: flagged on state mismatch.
AMBIGUOUS_CITIES = {
    "LONDON": {"OH", "KY", "KS", "AR", "TX"},
    "PARIS": {"TX", "TN", "KY", "IL", "ME", "AR"},
    "BERLIN": {"CT", "MD", "NH", "NJ", "WI", "PA"},
    "MOSCOW": {"ID", "PA", "TN", "TX"},
    "SYDNEY": {"FL", "MT", "NE"},
    "MELBOURNE": {"FL"},
    "TORONTO": {"OH"},
    "VANCOUVER": {"WA"},
}
