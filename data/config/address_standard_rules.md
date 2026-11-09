# Address standardization

How a filed US mailing address becomes the one form the database keeps. This
file is the instruction the model reads, word for word. Reviewed exceptions
stay in data/rules/address_rules.csv; the model's answers are kept in
data/cache/addresses.csv.

You receive one address per line as filed by a donor on an FEC form:
`street_1 | street_2 | city | state | zip`. Return the same address in the
standard form below. Change spelling, abbreviation, casing, punctuation and
the placement of the unit; never change the meaning, never add a part that
was not filed, never guess a house number, street, city or ZIP that is not
there. When a field is empty and nothing in the other fields belongs in it,
leave it empty.

## street_1
1. Uppercase, single spaces, no periods or trailing commas. Keep hyphens in
   house numbers (153-01) and in names.
2. Abbreviate the street type at the end of the name as USPS Publication 28
   does: STREET ST, AVENUE AVE, ROAD RD, BOULEVARD BLVD, DRIVE DR, LANE LN,
   COURT CT, CIRCLE CIR, PLACE PL, PARKWAY PKWY, HIGHWAY HWY, TERRACE TER,
   TURNPIKE TPKE, EXPRESSWAY EXPY, SQUARE SQ, TRAIL TRL, CROSSING XING,
   JUNCTION JCT, MOUNT MT, PLAZA PLZ, POINT PT, BEND BND, ROUTE RTE, ALLEY ALY,
   EXTENSION EXT, FREEWAY FWY. Leave HEIGHTS, HILLS, CENTER, LAKE, PARK,
   WAY, LOOP, RUN, PIKE and other words as they are. A type word inside the
   name is part of the name and stays (EVERGREEN POINT RD keeps POINT,
   TERRACE OAKS DR keeps TERRACE, AVENUE OF THE STARS and AVENUE R keep
   AVENUE, OLD COURT RD keeps COURT).
3. Directions: NORTH N, SOUTH S, EAST E, WEST W, NORTHEAST NE, NORTHWEST NW,
   SOUTHEAST SE, SOUTHWEST SW (123 EAST 13TH STREET is 123 E 13TH ST). They
   stay where the filer put them: before the name (2450 W WESLEY RD) or
   after the type as a quadrant (4545 CONNECTICUT AVE NW, 2204 70TH AVE SE,
   125 OCEAN DR W stay so). A direction that is the name itself stays whole
   (100 WEST AVE, 5 NORTH ST, 545 WEST END AVE).
4. Ordinals: 13TH, 2ND; spelled ordinals become numbers (FIFTH AVE is 5TH AVE).
5. PO boxes: PO BOX 4570, whatever was written (P.O. BOX, P O BOX, POST OFFICE BOX, BOX).
6. Remove C/O, ATTN and the name after them, and anything that is not part
   of a street address (a company name, a phone number, an email, a repeat
   of the address after a comma, a city or ZIP written at the end). If the
   whole field is such text, or is a bare number with no street, return it
   empty. A city or ZIP found at the end of street_1 moves to its own field
   when that field is empty or plainly wrong (20440 PCH MALIBU 90265 filed
   under WASHINGTON DC 20440: city MALIBU, zip 90265).
7. A unit at the end of street_1 (apartment, suite, floor, building, room,
   #, PH, PMB, or a bare apartment id after the street type such as 872
   MADISON AVENUE 5A) moves to street_2 in the form of rule 8. A floor alone
   in street_1 is a unit too. Fix a dropped space: E8TH ST is E 8TH ST.

## street_2
8. The unit in USPS form, designator then id with one space: APT 13K,
   STE 205, UNIT 4, FL 10, BLDG C, RM 12, PH 2, PMB 118, # 420 (a bare
   number or letter after #, NO or nothing keeps the # form: "#1606" is
   "# 1606", "1312" alone is "# 1312"). APARTMENT is APT, SUITE is STE,
   FLOOR, 9TH FL and 18TH FLOOR are FL 9 and FL 18, NUMBER and NO are #.
   A hyphen inside the id stays (STE 2-287, UNIT 17-E, # 5-413); a hyphen
   between designator and id goes (STE-C #258 is STE C-258); a # after a
   designator goes (UNIT #15180 is UNIT 15180). UNIT stays UNIT.
   A bare apartment id taken from street_1 (5A) is APT 5A. Nothing else goes here: a city, a
   state, a second street name or a company name in street_2 is dropped
   unless it belongs in street_1 (a street_2 that is the real street while
   street_1 is a building name: swap them).

## city
9. The USPS city name in full, uppercase: SAINT LOUIS not ST LOUIS, FORT
   WORTH not FT WORTH, MOUNT KISCO not MT KISCO, PALM BEACH GARDENS not PBG,
   NEW YORK not NYC or NY, LOS ANGELES not LA. Fix misspellings (BROOKKYN is
   BROOKLYN). Remove a state code or ZIP written after the city. A
   neighbourhood that USPS accepts as a city (BROOKLYN, HOLLYWOOD, ENCINO)
   stays as written.

## state
10. The two-letter code, uppercase; a spelled state becomes its code.

## zip
11. The first five digits, keeping a leading zero (06032). Drop a ZIP+4
    tail. Keep the ZIP as filed otherwise; do not correct it to match the
    city or the state.

## foreign addresses
12. An address outside the United States is returned exactly as filed, in
    every field.

Return every address, in the order given, echoing the input line exactly in
`filed`.
