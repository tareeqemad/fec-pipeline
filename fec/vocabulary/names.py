"""Missing-value words and the name patterns read while cleaning contributor names."""

import re

from fec.vocabulary.tables import words

# FEC junk values that all mean "missing"
MISSING_VALUES = words("missing_values")
# Individual names in FEC "LASTNAME, F..." form.
INDIV_NAME_RE = re.compile(r"^[A-Z][\w\s.'\-()`;]*,\s*[A-Z]")
# Trailing junk on committee names, but not legal suffixes.
COMM_TAIL_RE = re.compile(r",\s+(?!INC|LLC|LLP|CORP|JR|SR|PA\s*$)[A-Z][A-Z.\s]{0,30}$")
