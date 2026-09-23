"""Street-level geocodes checked by hand: the point is right although it lies outside the filed ZIP.

In each of these the filed ZIP is the typo, not the point. The street-inside-ZIP
check (fec/geocoding/pipeline.py) cannot prove that on its own, so these keys keep
their cached street point and are never looked up again. Add a key here only after
checking that the street exists where the point is and not in the filed ZIP; say
how in the note. Never research a donor's home address to fill this list.
"""

REVIEWED_ZIP_TYPO_KEYS: dict[str, str] = {
    # the 2026-09-23 audit verdict (finding 8) lists these as ZIP typos with correct coordinates
    "791 HWY 77 N|WAXAHACHIE|TX|75164": "audit: the point is Waxahachie; 75164 is Josephine, 89 km away",
    "3750 S DIXIE HWY|MIAMI|FL|33154": "audit: S Dixie Hwy runs through Coconut Grove (33133), not Bal Harbour (33154)",
    "4 CHESTNUT DR|NEW YORK|NY|10112": "audit: the point is Great Neck 11021; 10112 is one Rockefeller Center building",
    # street names that exist in one place only, or the city's own street grid
    "201 ROUSE BLVD|PHILADELPHIA|PA|19122": "Rouse Blvd exists only in the Navy Yard (19112)",
    "1875 CENTURY PARK EAST||CA|91605": "Century Park East exists only in Century City (90067); 91605 is North Hollywood",
    "740 SW 19TH AVE|MIAMI|FL|33174": "Miami street grid: 740 SW 19th Ave is Little Havana (33135); 33174 lies 13 km west",
    "3135 HUTTON DR|BEVERLY HILLS|CA|90212": "Hutton Dr is in the 90210 hills; the same filing with 90210 has this point",
}


def is_reviewed_zip_typo(key: str) -> bool:
    """True for a cache key whose outside-ZIP street point was checked by hand and is right."""
    return key in REVIEWED_ZIP_TYPO_KEYS
