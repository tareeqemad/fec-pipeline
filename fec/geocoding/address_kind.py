"""Classify addresses as foreign or PO Box."""
import re

from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS

# a US ZIP5 or ZIP+4
_US_ZIP_RE = re.compile(r"\d{5}(?:-?\d{4})?")


# Canadian and UK postcodes, never mangled ZIPs
_FOREIGN_POSTCODE_RE = re.compile(
    r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d"
    r"|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}"
)


# a leading "PO BOX" marker
_PO_BOX_RE = re.compile(r"^PO\s+BOX", re.IGNORECASE)


# true if street starts with PO BOX
def is_po_box(street: str) -> bool:
    return bool(_PO_BOX_RE.match(street.strip()))


# true if state or postcode is non-US
def is_foreign_address(state: str, zipcode: str) -> bool:
    state = str(state or "").strip().upper()
    zipcode = str(zipcode or "").strip().upper()
    # old cache keys hold float ZIPs ('10007.0')
    float_zip = re.fullmatch(r"(\d{3,5})\.0", zipcode)
    if zipcode in {"NAN", "NONE"}:
        zipcode = ""
    elif float_zip:
        zipcode = float_zip.group(1).zfill(5)
    if state:
        if state not in _STATE_BOUNDS:
            return True
        # US state wins unless postcode is foreign-only
        return bool(_FOREIGN_POSTCODE_RE.fullmatch(zipcode))
    # no state: only US ZIPs mean US
    return not _US_ZIP_RE.fullmatch(zipcode)


# is_foreign_address for a STREET|CITY|STATE|ZIP key
def is_foreign_key(key: str) -> bool:
    parts = key.split("|")
    if len(parts) != 4:
        return False
    return is_foreign_address(parts[2], parts[3])
