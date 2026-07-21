"""
config/geography.py — US states and territories.
"""

US_STATES = frozenset({
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    # Territories
    'DC', 'PR', 'VI', 'GU', 'AS', 'MP',
})

STATE_NAMES = {
    'AL': 'Alabama',       'AK': 'Alaska',        'AZ': 'Arizona',
    'AR': 'Arkansas',      'CA': 'California',     'CO': 'Colorado',
    'CT': 'Connecticut',   'DE': 'Delaware',       'FL': 'Florida',
    'GA': 'Georgia',       'HI': 'Hawaii',         'ID': 'Idaho',
    'IL': 'Illinois',      'IN': 'Indiana',        'IA': 'Iowa',
    'KS': 'Kansas',        'KY': 'Kentucky',       'LA': 'Louisiana',
    'ME': 'Maine',         'MD': 'Maryland',       'MA': 'Massachusetts',
    'MI': 'Michigan',      'MN': 'Minnesota',      'MS': 'Mississippi',
    'MO': 'Missouri',      'MT': 'Montana',        'NE': 'Nebraska',
    'NV': 'Nevada',        'NH': 'New Hampshire',  'NJ': 'New Jersey',
    'NM': 'New Mexico',    'NY': 'New York',       'NC': 'North Carolina',
    'ND': 'North Dakota',  'OH': 'Ohio',           'OK': 'Oklahoma',
    'OR': 'Oregon',        'PA': 'Pennsylvania',   'RI': 'Rhode Island',
    'SC': 'South Carolina','SD': 'South Dakota',   'TN': 'Tennessee',
    'TX': 'Texas',         'UT': 'Utah',           'VT': 'Vermont',
    'VA': 'Virginia',      'WA': 'Washington',     'WV': 'West Virginia',
    'WI': 'Wisconsin',     'WY': 'Wyoming',
    'DC': 'District of Columbia',
    'PR': 'Puerto Rico',   'VI': 'US Virgin Islands',
    'GU': 'Guam',          'AS': 'American Samoa',
    'MP': 'Northern Mariana Islands',
}


# Per-state lat/lon bounding boxes (lat_min, lat_max, lng_min, lng_max).
# Single source of truth shared by geocoding result-validation
# (fec/geocoding/pipeline.py) and the address-review report
# (fec/cleaning/address_review.py).
US_STATE_BBOX = {
    'AL': (30.1, 35.0, -88.5, -84.9), 'AK': (51.2, 71.4, -180.0, -129.9),
    'AZ': (31.3, 37.0, -114.8, -109.0), 'AR': (33.0, 36.5, -94.6, -89.6),
    'CA': (32.5, 42.0, -124.5, -114.1), 'CO': (36.9, 41.0, -109.1, -102.0),
    'CT': (40.9, 42.1, -73.7, -71.8), 'DE': (38.4, 39.8, -75.8, -75.0),
    'FL': (24.4, 31.0, -87.6, -80.0), 'GA': (30.4, 35.0, -85.6, -80.8),
    'HI': (18.9, 22.2, -160.3, -154.8), 'ID': (41.9, 49.0, -117.2, -111.0),
    'IL': (36.9, 42.5, -91.5, -87.0), 'IN': (37.8, 41.8, -88.1, -84.8),
    'IA': (40.4, 43.5, -96.6, -90.1), 'KS': (36.9, 40.0, -102.1, -94.6),
    'KY': (36.5, 39.1, -89.6, -81.9), 'LA': (28.9, 33.0, -94.0, -88.8),
    'ME': (43.0, 47.5, -71.1, -66.9), 'MD': (37.9, 39.7, -79.5, -75.0),
    'MA': (41.2, 42.9, -73.5, -69.9), 'MI': (41.7, 48.3, -90.4, -82.4),
    'MN': (43.5, 49.4, -97.2, -89.5), 'MS': (30.2, 35.0, -91.7, -88.1),
    'MO': (35.9, 40.6, -95.8, -89.1), 'MT': (44.4, 49.0, -116.1, -104.0),
    'NE': (40.0, 43.0, -104.1, -95.3), 'NV': (35.0, 42.0, -120.0, -114.0),
    'NH': (42.7, 45.3, -72.6, -70.7), 'NJ': (38.9, 41.4, -75.6, -73.9),
    'NM': (31.3, 37.0, -109.1, -103.0), 'NY': (40.5, 45.0, -79.8, -71.9),
    'NC': (33.8, 36.6, -84.3, -75.5), 'ND': (45.9, 49.0, -104.1, -96.6),
    'OH': (38.4, 42.0, -84.8, -80.5), 'OK': (33.6, 37.0, -103.0, -94.4),
    'OR': (41.9, 46.3, -124.6, -116.5), 'PA': (39.7, 42.3, -80.5, -74.7),
    'RI': (41.1, 42.0, -71.9, -71.1), 'SC': (32.0, 35.2, -83.4, -78.5),
    'SD': (42.5, 46.0, -104.1, -96.4), 'TN': (35.0, 36.7, -90.3, -81.6),
    'TX': (25.8, 36.5, -106.7, -93.5), 'UT': (37.0, 42.0, -114.1, -109.0),
    'VT': (42.7, 45.0, -73.4, -71.5), 'VA': (36.5, 39.5, -83.7, -75.2),
    'WA': (45.5, 49.0, -124.8, -116.9), 'WV': (37.2, 40.6, -82.6, -77.7),
    'WI': (42.5, 47.1, -92.9, -86.8), 'WY': (41.0, 45.0, -111.1, -104.1),
    'DC': (38.8, 39.0, -77.1, -76.9), 'PR': (17.9, 18.5, -67.3, -65.6),
    'VI': (17.6, 18.4, -65.1, -64.6), 'GU': (13.2, 13.7, 144.6, 145.0),
    'AS': (-14.4, -14.2, -170.8, -170.5),
}
