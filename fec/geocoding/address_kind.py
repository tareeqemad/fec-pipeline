"""What kind of address a filing gives: foreign, or a PO Box."""
import re

from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS

_US_ZIP_RE = re.compile(r"\d{5}(?:-?\d{4})?")


# postcodes that can never be a mangled US ZIP: Canada (M5V 3L9) and the UK (NW1 5DX)
_FOREIGN_POSTCODE_RE = re.compile(
    r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d"
    r"|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}"
)


_PO_BOX_RE = re.compile(r"^PO\s+BOX", re.IGNORECASE)


# true if the street starts with PO BOX
def is_po_box(street: str) -> bool:
    return bool(_PO_BOX_RE.match(street.strip()))


# true if the state or postcode indicates a non-US address
def is_foreign_address(state: str, zipcode: str) -> bool:
    """True for a non-US state or province (CUNDINAMARCA), or no state with a non-US postcode (NW1 5DX, 6744316); a US state with a malformed ZIP ('MA', '2138') stays US."""
    state = str(state or "").strip().upper()
    zipcode = str(zipcode or "").strip().upper()
    # old cache keys hold ZIPs read as floats ('10007.0', 'NAN'): still US ZIPs / no ZIP
    float_zip = re.fullmatch(r"(\d{3,5})\.0", zipcode)
    if zipcode in {"NAN", "NONE"}:
        zipcode = ""
    elif float_zip:
        zipcode = float_zip.group(1).zfill(5)
    if state:
        if state not in _STATE_BOUNDS:
            return True
        # a US state decides, unless the postcode is one no US ZIP typo can produce
        return bool(_FOREIGN_POSTCODE_RE.fullmatch(zipcode))
    # no state at all: only a US ZIP makes it a US address with the state missing
    return not _US_ZIP_RE.fullmatch(zipcode)


# is_foreign_address for a STREET|CITY|STATE|ZIP cache key
def is_foreign_key(key: str) -> bool:
    """is_foreign_address for a STREET|CITY|STATE|ZIP cache key."""
    parts = key.split("|")
    if len(parts) != 4:
        return False
    return is_foreign_address(parts[2], parts[3])
