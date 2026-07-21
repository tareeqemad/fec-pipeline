"""
config/occupation_rules.py — Occupation normalization, typo fixes, and category rules.

Maps raw FEC occupation strings → canonical forms.
Includes category regex rules (first match wins) and explicit overrides.
"""
import re

from fec.config.employers import EMPLOYER_NORMALIZE


OCCUPATION_NORMALIZE = {
    **EMPLOYER_NORMALIZE,

    # Homemaker — spacing/typo fixes only
    'HOMEMAKER': 'HOMEMAKER',
    'HOME MAKER': 'HOMEMAKER',       # spacing fix
    'HOME-MAKER': 'HOMEMAKER',       # punctuation fix
    'HOMEKEEPER': 'HOMEMAKER',       # typo
    'HOMEKEEPR': 'HOMEMAKER',        # typo
    'HOME': 'HOMEMAKER',             # truncated
    'AT HOME': 'HOMEMAKER',          # truncated
    'SAHM': 'STAY AT HOME MOM',     # abbreviation expansion
    'MOM': 'HOMEMAKER',             # common shorthand
    # HOUSEWIFE stays HOUSEWIFE — different word
    # STAY AT HOME MOM/DAD stay as-is — different from HOMEMAKER

    # Short abbreviation expansions (verified from FEC data)
    'ART': 'ARTIST',                # self-employed artists
    'BIZ': 'BUSINESS',              # business shorthand
    'CRE': 'COMMERCIAL REAL ESTATE', # real estate abbreviation

    # Self-employed typos that appear in occupation field
    'SELF EMPLOYED EMPLOYED': 'SELF-EMPLOYED',
    'SELF EMPLOYED': 'SELF-EMPLOYED',
    'SELFEMPLOYED': 'SELF-EMPLOYED',
    'SELF  EMPLOYED': 'SELF-EMPLOYED',
    'SLEF': 'SELF-EMPLOYED',
    'SWLF': 'SELF-EMPLOYED',        # W/E keyboard slip
    'SLEF EMPLOYED': 'SELF-EMPLOYED',
    'SEFL EMPLOYED': 'SELF-EMPLOYED',
    'SELF-EMPLOY': 'SELF-EMPLOYED',
    'SELF EMPLOY': 'SELF-EMPLOYED',

    # Legal — only typo/abbreviation fixes
    'ATTORNEY AT LAW': 'ATTORNEY',      # same thing
    'ATTORNEY-AT-LAW': 'ATTORNEY',      # same thing
    'ATTORNEY/LAWYER': 'ATTORNEY',      # combined → primary
    # LAWYER stays LAWYER — legally different from ATTORNEY
    # TRIAL LAWYER stays TRIAL LAWYER — specific type
    # TRIAL ATTORNEY stays TRIAL ATTORNEY — specific type

    # Medical — DR/MD handled in OCCUPATION_FIXES
    # DOCTOR stays DOCTOR — different word from PHYSICIAN
    # PHYSICIAN stays PHYSICIAN

    # Executive titles — typo fixes
    'CE0': 'CEO',          # zero instead of O
    'C.E.O.': 'CEO',      # punctuation
    'C.E.O': 'CEO',       # punctuation
    'CHIEF EXECUTIVE OFFICER': 'CEO',  # abbreviation is standard
    'CHIEF EXECUTIVE': 'CEO',
    'CDO': 'CHIEF DEVELOPMENT OFFICER',
    'CMO': 'CHIEF MARKETING OFFICER',
    'CIO': 'CHIEF INVESTMENT OFFICER',
    'CFP': 'CERTIFIED FINANCIAL PLANNER',
    'DDS': 'DENTIST',

    # Real estate — keep distinct
    'REALTOR': 'REALTOR',              # trademarked — stays
    'REAL ESTATE AGENT': 'REAL ESTATE AGENT',
    'REAL ESTATE BROKER': 'REAL ESTATE BROKER',  # different license

    # Business owner — only clear typos
    'BUSINESS OWNER': 'BUSINESS OWNER',
    'BUSINESSMAN': 'BUSINESSMAN',      # stays — different word
    'BUSINESSWOMAN': 'BUSINESSWOMAN',  # stays — different word
    # SMALL BUSINESS OWNER stays — more specific

    # Executive
    'EXEC': 'EXECUTIVE',               # abbreviation
    'EXEC.': 'EXECUTIVE',              # abbreviation with period
    'SENIOR EXECUTIVE': 'SENIOR EXECUTIVE',  # stays — more specific
    'CORPORATE EXECUTIVE': 'CORPORATE EXECUTIVE',  # stays — more specific

    # VP — expand abbreviations, keep full forms
    'VP': 'VICE PRESIDENT',            # abbreviation expansion
    'V.P.': 'VICE PRESIDENT',          # punctuation + expansion
    'VICE PRES': 'VICE PRESIDENT',     # abbreviation expansion
    'SVP': 'SENIOR VICE PRESIDENT',    # abbreviation expansion
    'EVP': 'EXECUTIVE VICE PRESIDENT', # abbreviation expansion
    # SENIOR VICE PRESIDENT stays
    # EXECUTIVE VICE PRESIDENT stays
    # VICE PRESIDENT stays

    # Chair — gender-neutral (keep all forms distinct)
    'CHAIRMAN': 'CHAIRMAN',
    'CHAIRWOMAN': 'CHAIRWOMAN',
    'CHAIRPERSON': 'CHAIRPERSON',
    'VICE CHAIRMAN': 'VICE CHAIRMAN',
    'EXECUTIVE CHAIRMAN': 'EXECUTIVE CHAIRMAN',
    'EXEC CHAIRMAN':  'EXECUTIVE CHAIRMAN',  # abbreviation — no period
    'EXEC. CHAIRMAN': 'EXECUTIVE CHAIRMAN',  # abbreviation — with period
    'CHAIRMAN OF THE BOARD': 'CHAIRMAN OF THE BOARD',

    # Consulting — typo fixes only
    'COMSULTANT': 'CONSULTANT',
    'CONSLTANT': 'CONSULTANT',
    # CONSULTING stays — it's a field, not a typo

    # Professor — keep qualifiers
    'ASSOC PROFESSOR': 'ASSOCIATE PROFESSOR',  # abbreviation expansion
    # ASSISTANT PROFESSOR stays
    # ASSOCIATE PROFESSOR stays
    # PROFESSOR EMERITUS stays

    # Medical typos (verified from real data)
    'PHYSICAN':  'PHYSICIAN',
    'PHYSICISN': 'PHYSICIAN',
    'PHYSICAIN': 'PHYSICIAN',

    # Real estate typos
    'REAL EATATE':  'REAL ESTATE',
    'REAL ESTSTE':  'REAL ESTATE',
    'REAL ESTAE':   'REAL ESTATE',
    'REALESTATE':   'REAL ESTATE',

    # Consulting typos
    'CONSJLTANT':  'CONSULTANT',
    'COMSUJTANT':  'CONSULTANT',
    'CONSUTLANT':  'CONSULTANT',
    'CONSUTANT':   'CONSULTANT',
    'CONSULTENT':  'CONSULTANT',
    # CONSULTANCY stays — it's a business form, not a typo

    # Legal typos
    'ATTORМEY':  'ATTORNEY',  # Cyrillic М
    'ATTONREY':  'ATTORNEY',
    'ATTORMEY':  'ATTORNEY',
    'ATTORENY':  'ATTORNEY',
    'ATORNEY':   'ATTORNEY',
    'ATTOREY':   'ATTORNEY',
    'AWYER':     'LAWYER',   # Typo for LAWYER

    # Management typos
    'MANAGMENT':  'MANAGEMENT',
    'MANAGMNT':   'MANAGEMENT',
    'MANGER':     'MANAGER',

    # Engineer typos (from real FEC data)
    'ENFINEER':    'ENGINEER',
    'ENGINEEER':   'ENGINEER',
    'ENGINER':     'ENGINEER',
    'ENGINEE':     'ENGINEER',
    'ENGIEER':     'ENGINEER',
    'ENNGINEER':   'ENGINEER',
    'ENGINEERR':   'ENGINEER',
    'ENGINNEER':   'ENGINEER',
    'ENGINEAR':    'ENGINEER',

    # Software engineer typos
    'SOFTWARE ENFINEER':     'SOFTWARE ENGINEER',
    'SOFTWARE ENGINEEER':    'SOFTWARE ENGINEER',
    'SOFTWARE ENGINER':      'SOFTWARE ENGINEER',
    'SOFTWARE ENGINEE':      'SOFTWARE ENGINEER',
    'SOFWARE ENGINEER':      'SOFTWARE ENGINEER',
    'SOFTWRE ENGINEER':      'SOFTWARE ENGINEER',
    'SOFTARE ENGINEER':      'SOFTWARE ENGINEER',
    'SFTWARE ENGINEER':      'SOFTWARE ENGINEER',
    'SOFTWEAR ENGINEER':     'SOFTWARE ENGINEER',
    'SOFTWARE ENGR':         'SOFTWARE ENGINEER',

    # Analyst typos
    'ANAYLST':     'ANALYST',
    'ANLYST':      'ANALYST',
    'ANLAYST':     'ANALYST',

    # Executive typos
    'EXECTUTIVE': 'EXECUTIVE',
    'EXECTUIVE':  'EXECUTIVE',
    'EXCECUTIVE': 'EXECUTIVE',
    'EXECUTIVRE': 'EXECUTIVE',

    # Medical typos (additional)
    'PHYSCIAN':    'PHYSICIAN',
    'PHYISICIAN':  'PHYSICIAN',
    'OPTHAMOLOGIST': 'OPHTHALMOLOGIST',   # typo → canonical (then MEDICAL regex catches it)

    # Wealth management typos
    'WEALTH MANAGMENT': 'WEALTH MANAGEMENT',

    # Company names that appear in occupation — keep for correct category only
    # (the text stays as-is, just ensures proper category assignment via OCCUPATION_FIXES)

    # ── Near-duplicate normalization ──
    # Singular/plural, -er/-ing, -or/-ion → canonical form
    'INVESTMENT BANKING':           'INVESTMENT BANKER',
    'VENTURE CAPITALIST':           'VENTURE CAPITAL',
    'PROPERTY MANAGEMENT':          'PROPERTY MANAGER',
    'REAL ESTATE INVESTING':        'REAL ESTATE INVESTOR',
    'REAL ESTATE INVESTMENTS':      'REAL ESTATE INVESTOR',
    'REAL ESTATE INVESTMENT':       'REAL ESTATE INVESTOR',
    'REAL ESTATE INVESTOR/BUSINESSPERSON': 'REAL ESTATE INVESTOR',
    'REAL ESTATE INVESTOR/DEVELOPER': 'REAL ESTATE INVESTOR/DEVELOPER',
    'REAL ESTATE PROPERTY MANAGER': 'REAL ESTATE MANAGEMENT',
    'REAL ESTATE PROPERTY MANAGEMENT': 'REAL ESTATE MANAGEMENT',
    'REAL ESTATE PROFESSIONAL':     'REAL ESTATE',
    'REAL ESTATE AND INVESTMENT':   'REAL ESTATE INVESTOR',
    'SELF-EMPLOYED CONSULTANT':     'CONSULTANT',

    # ── Typo fixes ──
    'SW EGIEER':                    'SOFTWARE ENGINEER',
    'EXECUIVE':                     'EXECUTIVE',
    'MANAGET':                      'MANAGER',
    'BUSIENSS':                     'BUSINESS',
    'HEALTH COCH':                  'HEALTH COACH',
    'VENTURE CSPITALIST':           'VENTURE CAPITAL',
    'MANAGING MENBER':              'MANAGING MEMBER',
    'APP DEVELOPMENT':              'APPLICATION DEVELOPER',
    'PROP MNGMT':                   'PROPERTY MANAGER',
    'PROJECT MGR':                  'PROJECT MANAGER',
    'CORP EXEC':                    'CORPORATE EXECUTIVE',
    'EXEC VP':                      'EXECUTIVE VICE PRESIDENT',
    'LUMBER BIZ':                   'LUMBER BUSINESS',

    # ── Auto-detected typos (Levenshtein=1 from common occupations) ──
    # RETIRED typos
    'RETITED':                      'RETIRED',
    'RETRIRED':                     'RETIRED',
    'RETIERED':                     'RETIRED',
    'RETRED':                       'RETIRED',
    'RETIRD':                       'RETIRED',
    # Truncated / mis-keyed job titles
    'MNAGER':                       'MANAGER',
    'PRESIDEN':                     'PRESIDENT',
    'JEWLER':                       'JEWELER',
    # ATTORNEY typos
    'ATTORNEYS':                    'ATTORNEY',
    'ATTORNEYH':                    'ATTORNEY',
    'ATRORNEY':                     'ATTORNEY',
    'ARTORNEY':                     'ATTORNEY',
    'ATRTORNEY':                    'ATTORNEY',
    'TTORNEY':                      'ATTORNEY',
    'ATTIRNEY':                     'ATTORNEY',
    'AATTORNEY':                    'ATTORNEY',
    'ATTORNEY`':                    'ATTORNEY',
    # REAL ESTATE typos
    'REA ESTATE':                   'REAL ESTATE',
    'REAL ESTARE':                  'REAL ESTATE',
    'REAL,ESTATE':                  'REAL ESTATE',
    'REAL ESTAT':                   'REAL ESTATE',
    'RAL ESTATE':                   'REAL ESTATE',
    'RESL ESTATE':                  'REAL ESTATE',
    'REQL ESTATE':                  'REAL ESTATE',
    'REAL ESATATE':                 'REAL ESTATE',
    'REAL RSTATE DEVELOPER':        'REAL ESTATE DEVELOPER',
    'REWAL ESTATE INVESTOR':        'REAL ESTATE INVESTOR',
    'REAL EATATE MANAGEMENT':       'REAL ESTATE MANAGEMENT',
    'REAL ESTATE/INVESTING':        'REAL ESTATE INVESTOR',
    'REAL ESTATE/OWNER':            'REAL ESTATE OWNER',
    # PHYSICIAN typos
    'OHYSICIAN':                    'PHYSICIAN',
    'PHYCICIAN':                    'PHYSICIAN',
    'PHYSICIA':                     'PHYSICIAN',
    'PHISICIAN':                    'PHYSICIAN',
    'PHYSICIQN':                    'PHYSICIAN',
    'PHYSISIAN':                    'PHYSICIAN',
    # EXECUTIVE typos
    'EXRCUTIVE':                    'EXECUTIVE',
    'EXERCUTIVE':                   'EXECUTIVE',
    'EXECUTICVE':                   'EXECUTIVE',
    'EXECUTIVEV':                   'EXECUTIVE',
    # LAWYER typos
    'LQWYER':                       'LAWYER',
    'LAQWYER':                      'LAWYER',
    # CONSULTANT typos
    'CONSULITANT':                  'CONSULTANT',
    'COSULTANT':                    'CONSULTANT',
    'FONSULTANT':                   'CONSULTANT',
    'CONULTING':                    'CONSULTING',
    # INVESTOR typos
    'INVESTORB':                    'INVESTOR',
    'INVESTORS':                    'INVESTOR',
    'INVESTO':                      'INVESTOR',
    'INVESTER':                     'INVESTOR',
    'INVESMENTS':                   'INVESTMENTS',
    # FINANCE typos
    'FINACE':                       'FINANCE',
    'GINANCE':                      'FINANCE',
    # MANAGER typos
    'MANAGERS':                     'MANAGER',
    'MZNAGER':                      'MANAGER',
    'PROPERTY MANGER':              'PROPERTY MANAGER',
    'PROJECT KANAGER':              'PROJECT MANAGER',
    'PORTFOLIO MANBAGER':           'PORTFOLIO MANAGER',
    'ASSET MANGER':                 'ASSET MANAGER',
    # SALES / MARKETING typos
    'SALEA':                        'SALES',
    'MARKERING':                    'MARKETING',
    'MARKETIBG':                    'MARKETING',
    'MARKRTING':                    'MARKETING',
    # OWNER typos
    'OWNERR':                       'OWNER',
    'OWNEE':                        'OWNER',
    'BUSINESS IWNER':               'BUSINESS OWNER',
    'BUSINESS OWNERR':              'BUSINESS OWNER',
    'SMALL BUSINESS OWNERS':        'SMALL BUSINESS OWNER',
    'SMALL BUSINESS OWNDER':        'SMALL BUSINESS OWNER',
    # FINANCIAL ADVISOR typos
    'FINANACIAL ADVISOR':           'FINANCIAL ADVISOR',
    'FINANCIAL ADVISORY':           'FINANCIAL ADVISOR',
    # CHAIRMAN typos
    'HAIRMAN':                      'CHAIRMAN',
    # PARTNER typos
    'PARTNET':                      'PARTNER',
    'PARATNER':                     'PARTNER',
    'MANAGIN PARTNER':              'MANAGING PARTNER',
    # PROFESSOR typos
    'PROESSOR':                     'PROFESSOR',
    'PROFESOR':                     'PROFESSOR',
    # PRINCIPAL typos
    'PRICIPAL':                     'PRINCIPAL',
    # DIRECTOR typos
    'DIRCTOR':                      'DIRECTOR',
    'DIRECTLOR':                    'DIRECTOR',
    'MANGAGING DIRECTOR':           'MANAGING DIRECTOR',
    'MANAGING DORECTOR':            'MANAGING DIRECTOR',
    'DEPUTY MANGING DIRECTOR':      'DEPUTY MANAGING DIRECTOR',
    'EXECUTIVE DIRECTORR':          'EXECUTIVE DIRECTOR',
    # BUSINESS typos
    'BUSINEES':                     'BUSINESS',
    'BUSYNESS':                     'BUSINESS',
    'BUSINESS MAN':                 'BUSINESSMAN',
    # PSYCHOLOGIST typos
    'PSYCOLOGIST':                  'PSYCHOLOGIST',
    'PSYCHOLOGISTS':                'PSYCHOLOGIST',
    'PSYCHOLOGIS':                  'PSYCHOLOGIST',
    # DEVELOPER typos
    'DEVEOPER':                     'DEVELOPER',
    'DEVELOPET':                    'DEVELOPER',
    # TEACHER typos
    'TEACYER':                      'TEACHER',
    # VENTURE CAPITAL typos
    'VENTURE CAPITOL':              'VENTURE CAPITAL',
    # REALTOR typos
    'RELTOR':                       'REALTOR',
    # ACCOUNTANT typos
    'ACCOYNTANT':                   'ACCOUNTANT',
    # ADVISOR typos
    'ADVOSOR':                      'ADVISOR',
    'DVISOR':                       'ADVISOR',
    'INVESTMENT ADVISER':           'INVESTMENT ADVISOR',
    'ART ADVISORY':                 'ART ADVISOR',
    'ART ADVISER':                  'ART ADVISOR',
    'TAX ADVISER':                  'TAX ADVISOR',
    # PRIVATE EQUITY typos
    'PRIVAT EQUITY':                'PRIVATE EQUITY',
    # ENTREPRENEUR typos
    'ENTREPRENUR':                  'ENTREPRENEUR',
    # CO-FOUNDER variants
    'COFOUNDER':                    'CO-FOUNDER',
    'CO FOUNDER':                   'CO-FOUNDER',
    # CONTRACTOR typos
    'CINTRACTOR':                   'CONTRACTOR',
    # CONSTRUCTION typos
    'CONSTRUCTIOM':                 'CONSTRUCTION',
    # WEALTH MANAGEMENT typos
    'WEALTH MANANGEMENT':           'WEALTH MANAGEMENT',
    # SOCIAL WORKER typos
    'SOCIAL WORKET':                'SOCIAL WORKER',
    # ADMINISTRATOR typos
    'ADMINISTRAOR':                 'ADMINISTRATOR',
    # OTHER typos
    'BUSINESS DEVELOPEMENT':        'BUSINESS DEVELOPMENT',
    'CHIEF DEVELOPMENT OFFIER':     'CHIEF DEVELOPMENT OFFICER',
    'TALENT AGWNT':                 'TALENT AGENT',
    'FUND RAISER':                  'FUNDRAISER',
    'RESTAURANTEUR':                'RESTAURATEUR',
    'AUTHOE':                       'AUTHOR',
    'MEECHANT':                     'MERCHANT',
    'CARDIOLODIST':                 'CARDIOLOGIST',
    'PIDIATRIST':                   'PODIATRIST',
    # Batch 2
    'ATROENEY':                     'ATTORNEY',
    'ATTOENY':                      'ATTORNEY',
    "ATT'Y":                        'ATTORNEY',
    'MARKEITNG':                    'MARKETING',
    'HESLTHCARE':                   'HEALTHCARE',
    'REAL EATSTE':                  'REAL ESTATE',
    'PRODUCT MANAGEENT':            'PRODUCT MANAGEMENT',
    'FINACIAL':                     'FINANCIAL',
    'AUTOMTIVE':                    'AUTOMOTIVE',
    'CLEANNG SERVICE':              'CLEANING SERVICE',
    'MANAY':                        'MANAGER',
    'CO PRES':                      'CO-PRESIDENT',
    'AQUISITIONS':                  'ACQUISITIONS',
    'JEWLERY':                      'JEWELRY',
    'PHYT':                         'PHYSICAL THERAPIST',
}


OCCUPATION_FIXES = {
    # Management abbreviations
    'MGR':  ('MANAGER', 'MANAGEMENT'),
    'MGMT': ('MANAGEMENT', 'MANAGEMENT'),
    'MGT':  ('MANAGEMENT', 'MANAGEMENT'),
    'MGT.': ('MANAGEMENT', 'MANAGEMENT'),

    # Tech abbreviations
    'SW ENG':    ('SOFTWARE ENGINEER', 'TECHNOLOGY'),
    'SWE':       ('SOFTWARE ENGINEER', 'TECHNOLOGY'),
    'SDE':       ('SOFTWARE DEVELOPMENT ENGINEER', 'TECHNOLOGY'),
    'SE':        ('SOFTWARE ENGINEER', 'TECHNOLOGY'),
    'ENIGINEER': ('ENGINEER', 'TECHNOLOGY'),
    'ENGINNER':  ('ENGINEER', 'TECHNOLOGY'),
    'ENFINEER':  ('ENGINEER', 'TECHNOLOGY'),
    'DEV':       ('SOFTWARE DEVELOPER', 'TECHNOLOGY'),
    'TECH':      ('TECHNOLOGY', 'TECHNOLOGY'),

    # Real estate abbreviations
    'RE':         ('REAL ESTATE', 'REAL ESTATE'),
    'REALESTATE': ('REAL ESTATE', 'REAL ESTATE'),
    'REALSTATE':  ('REAL ESTATE', 'REAL ESTATE'),

    # Finance abbreviations
    'VC':      ('VENTURE CAPITAL', 'FINANCE / INVESTMENT'),
    'PE':      ('PRIVATE EQUITY', 'FINANCE / INVESTMENT'),
    'VENTURE': ('VENTURE CAPITAL', 'FINANCE / INVESTMENT'),
    'FIN':     ('FINANCE', 'FINANCE / INVESTMENT'),
    'RIA':     ('REGISTERED INVESTMENT ADVISOR', 'FINANCE / INVESTMENT'),
    'CFP':     ('CFP', 'FINANCE / INVESTMENT'),        # Certified Financial Planner — credential
    'FA':      ('FINANCIAL ADVISOR', 'FINANCE / INVESTMENT'),
    'CFA':     ('CFA', 'FINANCE / INVESTMENT'),        # Chartered Financial Analyst — credential
    'PWA':     ('PRIVATE WEALTH ADVISOR', 'FINANCE / INVESTMENT'),
    'M&A':     ('MERGERS & ACQUISITIONS', 'FINANCE / INVESTMENT'),

    # Medical abbreviations
    'MD':      ('MEDICAL DOCTOR', 'MEDICAL / HEALTHCARE'),
    'M.D.':    ('MEDICAL DOCTOR', 'MEDICAL / HEALTHCARE'),
    'M.D':     ('MEDICAL DOCTOR', 'MEDICAL / HEALTHCARE'),
    'M,D':     ('MEDICAL DOCTOR', 'MEDICAL / HEALTHCARE'),
    'DR':      ('DOCTOR', 'MEDICAL / HEALTHCARE'),
    'DR.':     ('DOCTOR', 'MEDICAL / HEALTHCARE'),
    'DOC':     ('DOCTOR', 'MEDICAL / HEALTHCARE'),
    'MED':     ('MEDICAL DOCTOR', 'MEDICAL / HEALTHCARE'),
    'DDS':     ('DDS', 'MEDICAL / HEALTHCARE'),   # Doctor of Dental Surgery — credential
    'RN':      ('REGISTERED NURSE', 'MEDICAL / HEALTHCARE'),
    'NP':      ('NURSE PRACTITIONER', 'MEDICAL / HEALTHCARE'),
    'PT':      ('PHYSICAL THERAPIST', 'MEDICAL / HEALTHCARE'),
    'RDH':     ('DENTAL HYGIENIST', 'MEDICAL / HEALTHCARE'),
    'SLP':     ('SPEECH LANGUAGE PATHOLOGIST', 'MEDICAL / HEALTHCARE'),
    'LMFT':    ('LMFT', 'MEDICAL / HEALTHCARE'),   # Licensed Marriage & Family Therapist — credential

    # Legal abbreviations
    'ATTY':    ('ATTORNEY', 'LEGAL'),
    'LAW':     ('LAWYER', 'LEGAL'),
    'ATT':     ('ATTORNEY', 'LEGAL'),

    # Executive abbreviations (already in OCCUPATION_NORMALIZE mostly, but ensure here)
    'CE0':     ('CEO', 'EXECUTIVE / C-SUITE'),
    'CAO':     ('CHIEF ADMINISTRATIVE OFFICER', 'EXECUTIVE / C-SUITE'),
    'CRO':     ('CHIEF REVENUE OFFICER', 'EXECUTIVE / C-SUITE'),
    'CCO':     ('CHIEF COMPLIANCE OFFICER', 'EXECUTIVE / C-SUITE'),
    'CPO':     ('CHIEF PRODUCT OFFICER', 'EXECUTIVE / C-SUITE'),
    'CO CEO':  ('CO-CEO', 'EXECUTIVE / C-SUITE'),
    'EXEC':    ('EXECUTIVE', 'EXECUTIVE / C-SUITE'),
    'EXC':     ('EXECUTIVE', 'EXECUTIVE / C-SUITE'),
    'PRES':    ('PRESIDENT', 'EXECUTIVE / C-SUITE'),
    'DIR':     ('DIRECTOR', 'EXECUTIVE / C-SUITE'),
    'NED':     ('NON-EXECUTIVE DIRECTOR', 'EXECUTIVE / C-SUITE'),
    'VICE PRES':  ('VICE PRESIDENT', 'EXECUTIVE / C-SUITE'),
    'VICE-PRES':  ('VICE PRESIDENT', 'EXECUTIVE / C-SUITE'),
    'V.P.':       ('VICE PRESIDENT', 'EXECUTIVE / C-SUITE'),
    'COB':        ('CHAIRMAN', 'EXECUTIVE / C-SUITE'),  # Chairman of the Board
    'SUPERMARKET EXEC': ('SUPERMARKET EXECUTIVE', 'EXECUTIVE / C-SUITE'),

    # Sales / Marketing
    'AE':      ('ACCOUNT EXECUTIVE', 'SALES / MARKETING'),
    'CSM':     ('CUSTOMER SUCCESS MANAGER', 'SALES / MARKETING'),
    'BDM':     ('BUSINESS DEVELOPMENT MANAGER', 'SALES / MARKETING'),
    'GSM':     ('GLOBAL SALES MANAGER', 'SALES / MARKETING'),
    'PR':      ('PUBLIC RELATIONS', 'SALES / MARKETING'),
    'MKTG':    ('MARKETING', 'SALES / MARKETING'),
    # Fix: regex catches EXECUTIVE in "ACCOUNT EXECUTIVE" as C-SUITE
    'ACCOUNT EXECUTIVE': ('ACCOUNT EXECUTIVE', 'SALES / MARKETING'),
    'ACCOUNT EXEC':      ('ACCOUNT EXECUTIVE', 'SALES / MARKETING'),

    # Management
    'ADMIN':   ('ADMINISTRATOR', 'MANAGEMENT'),

    # Finance / Investment
    'INV':     ('INVESTOR', 'FINANCE / INVESTMENT'),

    # Insurance
    'INS':     ('INSURANCE', 'INSURANCE'),

    # Business
    'BOSS':    ('BUSINESS OWNER', 'BUSINESS / ENTREPRENEUR'),

    # Education
    'TCHR':    ('TEACHER', 'EDUCATION'),
    'PROF':    ('PROFESSOR', 'EDUCATION'),
    # NIST — context-dependent, handled in enhancements.py

    # Government / Military
    'FSO':     ('FOREIGN SERVICE OFFICER', 'GOVERNMENT / MILITARY'),
    'MP':      ('MILITARY POLICE', 'GOVERNMENT / MILITARY'),
    'GR':      ('GOVERNMENT RELATIONS', 'GOVERNMENT / MILITARY'),

    # Other known
    'DBA':     ('DATABASE ADMINISTRATOR', 'TECHNOLOGY'),
    'HR':      ('HUMAN RESOURCES', 'MANAGEMENT'),
    'PM':      ('PROJECT MANAGER', 'MANAGEMENT'),
    'AR':      ('ACCOUNTS RECEIVABLE', 'ACCOUNTING / TAX'),
    'CTR':     ('CONTRACTOR', 'CONSTRUCTION / TRADES'),
    'ART':     ('ART', 'ARTS / ENTERTAINMENT'),
    'RABB':    ('RABBI', 'RELIGIOUS'),
    'GIG':     ('SELF-EMPLOYED', 'SELF-EMPLOYED'),
    'HW':      ('HOMEMAKER', 'HOMEMAKER'),
    'NON':     ('NONPROFIT', 'NONPROFIT / PHILANTHROPY'),
    'MC':      ('MANAGEMENT CONSULTANT', 'CONSULTING'),
    'AVP':     ('ASSISTANT VICE PRESIDENT', 'EXECUTIVE / C-SUITE'),
    'FOOD':    ('FOOD', 'FOOD / HOSPITALITY'),

    # Additional abbreviations (verified with employer context from real data)
    'GEN MGR': ('GENERAL MANAGER', 'MANAGEMENT'),
    'GENL MGR': ('GENERAL MANAGER', 'MANAGEMENT'),
    'INV MGR': ('INVESTMENT MANAGER', 'FINANCE / INVESTMENT'),
    'EXECTUTIVE': ('EXECUTIVE', 'EXECUTIVE / C-SUITE'),

    # Typo fixes (verified from real data)
    'DEISGNER':  ('DESIGNER', 'ARTS / ENTERTAINMENT'),
    'PHYSICAN':  ('PHYSICIAN', 'MEDICAL / HEALTHCARE'),
    'PHYSICISN': ('PHYSICIAN', 'MEDICAL / HEALTHCARE'),
    'PHYSICAIN': ('PHYSICIAN', 'MEDICAL / HEALTHCARE'),
    'ATTORМEY':  ('ATTORNEY', 'LEGAL'),   # Cyrillic М typo
    'ATTONREY':  ('ATTORNEY', 'LEGAL'),
    'ATTORMEY':  ('ATTORNEY', 'LEGAL'),
    'ATTORENY':  ('ATTORNEY', 'LEGAL'),
    'ATORNEY':   ('ATTORNEY', 'LEGAL'),
    'ATTOREY':   ('ATTORNEY', 'LEGAL'),
    'CONSJLTANT':('CONSULTANT', 'CONSULTING'),
    'COMSUJTANT':('CONSULTANT', 'CONSULTING'),
    'CONSUTLANT':('CONSULTANT', 'CONSULTING'),
    'COMSULTANT':('CONSULTANT', 'CONSULTING'),
    'CONSLTANT': ('CONSULTANT', 'CONSULTING'),
    'REAL EATATE':('REAL ESTATE', 'REAL ESTATE'),
    'REAL ESTSTE':('REAL ESTATE', 'REAL ESTATE'),
    'WEALTH MANAGMENT': ('WEALTH MANAGEMENT', 'FINANCE / INVESTMENT'),

    # "EMPLOYED" alone is not junk — send to AI with employer context.
    # DO NOT add here — leave as OTHER so AI classifier handles it.

    # Additional fixes discovered from data analysis
    'DRIVING':    ('DRIVING', 'TRANSPORTATION'),
    'MOVER':      ('MOVER', 'TRANSPORTATION'),
    'INVESTING':  ('INVESTING', 'FINANCE / INVESTMENT'),
    'MONEY':      ('FINANCE', 'FINANCE / INVESTMENT'),
    'COMPLIANCE': ('COMPLIANCE', 'LEGAL'),
    'OWNER3':     ('OWNER', 'BUSINESS / ENTREPRENEUR'),    # junk number removed
    'REL ESTATE': ('REAL ESTATE', 'REAL ESTATE'),
    'PILATES':    ('PILATES', 'FOOD / HOSPITALITY'),
    'FITNESS':    ('FITNESS', 'FOOD / HOSPITALITY'),
    'ORALSURGERON': ('ORAL SURGEON', 'MEDICAL / HEALTHCARE'),
    'PSYCHOANALYST': ('PSYCHOANALYST', 'MEDICAL / HEALTHCARE'),
    'PHARMA':     ('PHARMA', 'MEDICAL / HEALTHCARE'),
    'NURSING HOME': ('NURSING HOME', 'MEDICAL / HEALTHCARE'),
    'SHAREHOLDER': ('SHAREHOLDER', 'FINANCE / INVESTMENT'),
    'OFFICER':    ('OFFICER', 'EXECUTIVE / C-SUITE'),
    'DESIGN':     ('DESIGN', 'ARTS / ENTERTAINMENT'),
    'EDUCATION':  ('EDUCATION', 'EDUCATION'),
    'OFFICE':     ('OFFICE', 'MANAGEMENT'),
    'TRAINER':    ('TRAINER', 'EDUCATION'),
    'AGENT':      ('AGENT', 'SALES / MARKETING'),
    'TESTER':     ('TESTER', 'TECHNOLOGY'),
    'ASSOCIATE':  ('ASSOCIATE', 'MANAGEMENT'),
    'PROFESSIONAL': ('PROFESSIONAL', 'BUSINESS / ENTREPRENEUR'),

    # Additional typo fixes from deep analysis (round 2)
    'ATTOENEY':   ('ATTORNEY', 'LEGAL'),
    'ATTORNET':   ('ATTORNEY', 'LEGAL'),
    'LAWYET':     ('LAWYER', 'LEGAL'),
    'LAWER':      ('LAWYER', 'LEGAL'),
    'ATTORNE':    ('ATTORNEY', 'LEGAL'),
    'ATTORNWY':   ('ATTORNEY', 'LEGAL'),
    'ATTORNEU':   ('ATTORNEY', 'LEGAL'),
    'CHSIRMSN':   ('CHAIRMAN', 'EXECUTIVE / C-SUITE'),
    'PRINCIPLE':  ('PRINCIPAL', 'EXECUTIVE / C-SUITE'),
    'REAL':       ('REAL ESTATE', 'REAL ESTATE'),
    'BUS':        ('BUSINESS', 'BUSINESS / ENTREPRENEUR'),
    'HEDGE FUND': ('HEDGE FUND', 'FINANCE / INVESTMENT'),
    'MONEY MGMT': ('MONEY MANAGEMENT', 'FINANCE / INVESTMENT'),
    'INV MGT':    ('INVESTMENT MANAGEMENT', 'FINANCE / INVESTMENT'),
    'FUND MGR':   ('FUND MANAGER', 'FINANCE / INVESTMENT'),
    'CREDIT REPORTING': ('CREDIT REPORTING', 'FINANCE / INVESTMENT'),
    'MORTGAGE':   ('MORTGAGE', 'FINANCE / INVESTMENT'),
    'AUTO REPAIR': ('AUTO REPAIR', 'CONSTRUCTION / TRADES'),
    'RESTAURATEUR': ('RESTAURATEUR', 'FOOD / HOSPITALITY'),
    'JOURNALIST': ('JOURNALIST', 'ARTS / ENTERTAINMENT'),
    'BROADCASTING': ('BROADCASTING', 'ARTS / ENTERTAINMENT'),
    'MUSIC':      ('MUSIC', 'ARTS / ENTERTAINMENT'),
    'ACUPUNCTURIST': ('ACUPUNCTURIST', 'MEDICAL / HEALTHCARE'),
    'MEDICINE':   ('MEDICINE', 'MEDICAL / HEALTHCARE'),
    'ORTHOPAEDIC SURGERY': ('ORTHOPEDIC SURGEON', 'MEDICAL / HEALTHCARE'),  # typo fix
    'PSYCHOTHERAPY': ('PSYCHOTHERAPY', 'MEDICAL / HEALTHCARE'),
    'PSYCHOPHARMACOLOGIST': ('PSYCHOPHARMACOLOGIST', 'MEDICAL / HEALTHCARE'),
    'OPTOMETRY':  ('OPTOMETRY', 'MEDICAL / HEALTHCARE'),
    'NURSING HOMES': ('NURSING HOMES', 'MEDICAL / HEALTHCARE'),
    'SPEECH THERAPY': ('SPEECH THERAPY', 'MEDICAL / HEALTHCARE'),
    'LIFE SCIENCES': ('LIFE SCIENCES', 'SCIENCE / RESEARCH'),
    'RESEARCH':   ('RESEARCH', 'SCIENCE / RESEARCH'),
    'OIL AND GAS': ('OIL AND GAS', 'BUSINESS / ENTREPRENEUR'),
    'CARPET INDUSTRY': ('CARPET INDUSTRY', 'BUSINESS / ENTREPRENEUR'),
    'MATCHMAKER': ('MATCHMAKER', 'BUSINESS / ENTREPRENEUR'),
    'ENTREPENEUR': ('ENTREPRENEUR', 'BUSINESS / ENTREPRENEUR'),
    'PHILANTROPIST': ('PHILANTHROPIST', 'NONPROFIT / PHILANTHROPY'),
    'PUBLIC AFFAIRS': ('PUBLIC AFFAIRS', 'GOVERNMENT / MILITARY'),
    'LEGISLATIVE ADVOCATE': ('LEGISLATIVE ADVOCATE', 'GOVERNMENT / MILITARY'),
    'LAW ENFORCEMENT': ('LAW ENFORCEMENT', 'GOVERNMENT / MILITARY'),
    'POLICE DETECTIVE': ('POLICE DETECTIVE', 'GOVERNMENT / MILITARY'),
    'REAL ESTTE': ('REAL ESTATE', 'REAL ESTATE'),
    'REAL ESTATW': ('REAL ESTATE', 'REAL ESTATE'),
    'REAL EDTATE': ('REAL ESTATE', 'REAL ESTATE'),
    'REAL ESATE': ('REAL ESTATE', 'REAL ESTATE'),
    'REALESYATE': ('REAL ESTATE', 'REAL ESTATE'),
    'REAL ESSSSTATE': ('REAL ESTATE', 'REAL ESTATE'),
    'REALTOE':    ('REAL ESTATE AGENT', 'REAL ESTATE'),
    'LANDSCAPING': ('LANDSCAPING', 'CONSTRUCTION / TRADES'),
    'RE DEVELOPMNET': ('REAL ESTATE DEVELOPER', 'REAL ESTATE'),
    'DIGITAL':    ('DIGITAL', 'TECHNOLOGY'),

    # Junk / garbage → NOT DISCLOSED with NULL category (occupation_status set in occupations.py)
    'OCC':          ('NOT DISCLOSED', None),
    'XXX':          ('NOT DISCLOSED', None),
    'UGH':          ('NOT DISCLOSED', None),
    'SDSDS':        ('NOT DISCLOSED', None),
    'FDE':          ('NOT DISCLOSED', None),
    'ZZZ':          ('NOT DISCLOSED', None),
    'WHAT':         ('NOT DISCLOSED', None),
    'ABC':          ('NOT DISCLOSED', None),
    '3F':           ('NOT DISCLOSED', None),
    'NB':           ('NOT DISCLOSED', None),
    'JA.':          ('NOT DISCLOSED', None),
    'IF':           ('NOT DISCLOSED', None),
    'PO':           ('NOT DISCLOSED', None),
    'IC':           ('NOT DISCLOSED', None),
    'CX':           ('NOT DISCLOSED', None),
    'PC':           ('NOT DISCLOSED', None),
    'EBP':          ('NOT DISCLOSED', None),
    'RPD':          ('NOT DISCLOSED', None),
    '750,000':      ('NOT DISCLOSED', None),
    'PIMP':         ('NOT DISCLOSED', None),
    'SUPERHERO':    ('NOT DISCLOSED', None),
    'EATING':       ('NOT DISCLOSED', None),
    'HARD WORK':    ('NOT DISCLOSED', None),
    'LOADING...':   ('NOT DISCLOSED', None),
    'AN OCCUPATION':('NOT DISCLOSED', None),
    'TKHOME':       ('NOT DISCLOSED', None),
    'DONOR':        ('NOT DISCLOSED', None),   # not an occupation
    'WORKER':       ('OTHER', 'OTHER'),        # too vague for specific category but is employed
    'LABORER':      ('LABORER', 'TRADES / LABOR'),
    # 'EMPLOYED' — handled below at line ~734 as ('EMPLOYED', None)
    'OTHER':        ('NOT DISCLOSED', None),

    # ── Veterinary (missing — 100+ records) ──
    'VETERINARIAN':     ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),
    'VETERINARY':       ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),
    'VET':              ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),
    'VETERINARY DOCTOR': ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),
    'DVM':              ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),
    'VETERINARY SURGEON': ('VETERINARIAN', 'MEDICAL / HEALTHCARE'),

    # Explicit refusals → NOT DISCLOSED with NULL category
    'PRIVATE':      ('NOT DISCLOSED', None),
    'CONFIDENTIAL': ('NOT DISCLOSED', None),
    'MYOB':         ('NOT DISCLOSED', None),
    'TMI':          ('NOT DISCLOSED', None),
    'PREFER NOT TO ANSWER': ('NOT DISCLOSED', None),
    'DECLINED TO ANSWER':   ('NOT DISCLOSED', None),

    # ── Reclassifiable from OTHER (discovered in deep analysis v3) ──

    # Executive / C-Suite abbreviations
    'CFO':                  ('CFO', 'EXECUTIVE / C-SUITE'),
    'CHIEF FINANCIAL OFFICER': ('CFO', 'EXECUTIVE / C-SUITE'),
    'PR/EXEC':              ('PR EXECUTIVE', 'EXECUTIVE / C-SUITE'),
    'CSO':                  ('CHIEF STRATEGY OFFICER', 'EXECUTIVE / C-SUITE'),
    'CH. BD':               ('CHAIR OF THE BOARD', 'EXECUTIVE / C-SUITE'),
    'OFFICER IN COMPANY':   ('OFFICER', 'EXECUTIVE / C-SUITE'),
    'MARKET VP':            ('VP OF MARKETING', 'EXECUTIVE / C-SUITE'),
    'VP, RETAILER RELATIONS': ('VP RETAILER RELATIONS', 'EXECUTIVE / C-SUITE'),

    # Legal
    'GC':                   ('GENERAL COUNSEL', 'LEGAL'),
    'PARALEGAL':            ('PARALEGAL', 'LEGAL'),

    # Consulting
    'ART CONSULTATION':     ('ART CONSULTANT', 'CONSULTING'),

    # Arts / Entertainment
    'POKER BROADCASTER':    ('POKER BROADCASTER', 'ARTS / ENTERTAINMENT'),
    'RODEO CLOWN':          ('RODEO CLOWN', 'ARTS / ENTERTAINMENT'),
    'DRAG QUEEN':           ('DRAG QUEEN', 'ARTS / ENTERTAINMENT'),
    'ART DEALER':           ('ART DEALER', 'ARTS / ENTERTAINMENT'),
    'MASTER TOUR GUIDE':    ('MASTER TOUR GUIDE', 'ARTS / ENTERTAINMENT'),

    # Food / Hospitality
    'CASHIER':              ('CASHIER', 'FOOD / HOSPITALITY'),
    'BEER DISTRIBUTER':     ('BEVERAGE DISTRIBUTOR', 'FOOD / HOSPITALITY'),
    'NUTRITIONIST':         ('NUTRITIONIST', 'MEDICAL / HEALTHCARE'),
    'DIETITIAN':            ('DIETITIAN', 'MEDICAL / HEALTHCARE'),
    'BEHAVIORAL HEALTH':    ('BEHAVIORAL HEALTH', 'MEDICAL / HEALTHCARE'),

    # Government / Military
    'FOREIGN AFFAIRS OFFICER': ('FOREIGN AFFAIRS OFFICER', 'GOVERNMENT / MILITARY'),
    'SENATOR':              ('SENATOR', 'GOVERNMENT / MILITARY'),
    'PROGRAM OFFICER':      ('PROGRAM OFFICER', 'GOVERNMENT / MILITARY'),

    # Sales / Marketing
    'GIFT BUYER':           ('GIFT BUYER', 'SALES / MARKETING'),
    'LICENSING':            ('LICENSING', 'SALES / MARKETING'),
    'COMMUNITY ENGAGEMENT': ('COMMUNITY ENGAGEMENT', 'SALES / MARKETING'),

    # Education / Fitness
    'COACH':                ('COACH', 'EDUCATION'),
    'PERSONAL TRAINER':     ('PERSONAL TRAINER', 'MEDICAL / HEALTHCARE'),

    # Real estate (unusual but verified)
    'BOWLING PROPRIATOR':   ('BOWLING PROPRIETOR', 'BUSINESS / ENTREPRENEUR'),

    # Research
    'RD':                   ('RESEARCH & DEVELOPMENT', 'SCIENCE / RESEARCH'),

    # Nonprofit
    'OWNERS REPRESENTATIVE': ('OWNERS REPRESENTATIVE', 'MANAGEMENT'),

    # Single-character junk occupations → NOT DISCLOSED with NULL category
    'A':  ('NOT DISCLOSED', None),
    'E':  ('NOT DISCLOSED', None),
    'N':  ('NOT DISCLOSED', None),
    'R':  ('NOT DISCLOSED', None),
    'V':  ('NOT DISCLOSED', None),
    'XX': ('NOT DISCLOSED', None),

    # EMPLOYED without detail → treat as employed but unspecified
    'EMPLOYED': ('EMPLOYED', None),

    # ── Typo fixes discovered in deep audit v4 ──

    # Attorney typos
    'ATTTORNEY':            ('ATTORNEY', 'LEGAL'),
    'ATTNY':                ('ATTORNEY', 'LEGAL'),
    'ATTORRNEY':            ('ATTORNEY', 'LEGAL'),
    'ATTRONEY':             ('ATTORNEY', 'LEGAL'),
    'ATTORNY':              ('ATTORNEY', 'LEGAL'),

    # Other typos
    'INSIRANCE':            ('INSURANCE', 'INSURANCE'),
    'CHARIMAN':             ('CHAIRMAN', 'EXECUTIVE / C-SUITE'),
    'EXECITIVE':            ('EXECUTIVE', 'EXECUTIVE / C-SUITE'),
    'DESIGNSR':             ('DESIGNER', 'ARTS / ENTERTAINMENT'),
    'PSYCHOLOGIDT':         ('PSYCHOLOGIST', 'MEDICAL / HEALTHCARE'),
    'PSYCH':                ('PSYCHOLOGIST', 'MEDICAL / HEALTHCARE'),

    # Junk → NOT DISCLOSED with NULL category
    'IDK':                  ('NOT DISCLOSED', None),
    'BAGMAN':               ('NOT DISCLOSED', None),

    # Reclassifiable occupations
    'PROPRIETOR':           ('PROPRIETOR', 'BUSINESS / ENTREPRENEUR'),
    'REMODELING':           ('REMODELER', 'CONSTRUCTION / TRADES'),
    'ADMISSIONS':           ('ADMISSIONS', 'EDUCATION'),
    'LITERARY AGENT':       ('LITERARY AGENT', 'ARTS / ENTERTAINMENT'),
    'MERCHANDISER':         ('MERCHANDISER', 'SALES / MARKETING'),
    'THEATER PRODUCING':    ('THEATER PRODUCER', 'ARTS / ENTERTAINMENT'),
}


CATEGORY_RULES = [
    # --- High-priority status categories (override profession) ---
    ('RETIRED',        r'^RETIRED$'),
    ('NOT EMPLOYED',   r'^NOT EMPLOYED$'),
    ('STUDENT',        r'^STUDENT$'),
    ('HOMEMAKER',      r'^HOMEMAKER$|^HOUSEWIFE$|^STAY.AT.HOME|^MOM$|^SAHM$'),
    ('SELF-EMPLOYED',  r'^SELF-EMPLOYED$'),

    # --- Professional categories ---
    # ⚠ ORDER MATTERS — first match wins. More specific before broader.

    ('LEGAL',
     r'ATTORNEY|LAWYER|JUDGE|PARALEGAL|LEGAL|COUNSEL|SOLICITOR|JURIS'
     r'|ESQUIRE|\bESQ\b|ARBITRATOR|MEDIATOR|LAW CLERK'),

    ('MEDICAL / HEALTHCARE',
     # Physicians & specialists
     r'PHYSICIAN|DOCTOR|SURGEON|DENTIST|DENTAL|NURSE|PHARMACIST|PSYCHOLOGIST'
     r'|THERAPIST|MEDICAL|HEALTHCARE|HOSPITAL|EMT|PARAMEDIC'
     r'|RADIOLOG|PEDIATRIC|OPHTHALMOLOG|ANESTHESIOLOG|PODIATRI'
     r'|ORTHODONTI|PERIODONTI|DERMATOLOG|VETERINAR|UROLOG|CARDIOLOG'
     r'|ENDODONTI|OPTOMETRI|PSYCHIATRI|NEUROLOG|ONCOLOG|PATHOLOG'
     r'|RHEUMATOLOG|GASTROENTEROLOG|PULMONOLOG|HEMATOLOG|GYNECOLOG|OB.?GYN'
     r'|OBSTETRICI|NEONATOLOG|GERIATRIC|PALLIATIVE|PSYCHOANALYS'
     # Allied health
     r'|SOCIAL WORK|CLINICAL SOCIAL|HEALTH CARE|HOME CARE|HEALTH COACH'
     r'|BEHAVIOR ANALYS|SPEECH PATHOLOG|SPEECH LANGUAGE'
     r'|PHYSICAL THERAP|OCCUPATIONAL THERAP'
     r'|PROVIDER|PRACTITIONER'),

    ('FINANCE / INVESTMENT',
     r'FINANCE|FINANCIAL|INVEST\w*|BANKER|BANKING|PORTFOLIO'
     r'|PRIVATE EQUITY|VENTURE CAPITAL|SECURITIES|TRADER|BROKER|TREASURER'
     r'|TRADING|WEALTH MANAG|TRUST ADMIN|TRUST ADMINISTRATION'
     r'|CREDIT ANALYS|COMMODITIES|LENDING|\bLENDER\b|FIXED INCOME'
     r'|FAMILY OFFICE|INV MGR|ACTUAR|\bANALYST\b'),

    ('REAL ESTATE',
     r'REAL ESTATE|PROPERTY|REALTY|REALTOR|LANDLORD|REAL ESTSTE'
     r'|REAL EATATE|REAL RSTATE|HOUSING DEVELOP|MORTGAGE LOAN'
     r'|LAND DEVELOPER|HOME DEVELOPER|HOTEL DEVELOPER|R/E DEVELOP'
     r'|\bBUILDER/DEVELOPER\b|\bBUILDER DEVELOPER\b'),

    ('EXECUTIVE / C-SUITE',
     r'CEO|COO|CTO|CIO|CMO|CDO|CFO|\bCHROO?\b|EXECUTIVE|PRESIDENT|\bCHAIR(?:MAN|WOMAN|PERSON)?\b'
     r'|VICE PRESIDENT|DIRECTOR|PARTNER|FOUNDER|CHIEF'
     r'|PRINCIPAL|\bSENIOR VP\b|\bCO-CHAIRMAN\b|CHAIRMAN EMERITUS'
     r'|MANAGING MEMBER|MANAGING DIRECTOR|MANAGING PARTNER'
     r'|HEAD OF|BOARD MEMBER|COMMISSIONER'),

    ('BUSINESS / ENTREPRENEUR',
     r'BUSINESS OWNER|ENTREPRENEUR|\bOWNER\b|BUSINESS|SMALL BUSINESS|FRANCHISE'
     r'|MERCHANT|MANUFACTURER|MANUFACTURING|\bWHOLESAL|\bRETAIL\b'
     r'|AUTO DEALER|CAR DEALER|AUTOMOTIVE DEALER|AUTOMOTIVE'
     r'|IMPORT.?EXPORT|JEWEL|RECYCLING|SCRAP METAL|E-COMMERCE'
     r'|HANDBAG|DISTRIBUTOR'),

    ('TECHNOLOGY',
     r'SOFTWARE|ENGINEER|PROGRAMMER|DEVELOPER|TECHNOLOGY|TECH'
     r'|\bIT\b|DATA SCIEN|DATA ANALY|COMPUTER|CYBER|NETWORK'
     r'|SYSTEM|DEVOPS|CLOUD|\bUX\b|\bUI\b|WIRELESS|INVENTOR'),

    ('ACCOUNTING / TAX',
     r'\bCPA\b|ACCOUNTANT|ACCOUNTING|\bTAX\b|AUDITOR|BOOKKEEPER'
     r'|CONTROLLER|COMPTROLLER'),

    ('EDUCATION',
     r'TEACHER|PROFESSOR|EDUCATOR|INSTRUCTOR|TUTOR|LIBRARIAN'
     r'|SCHOOL|UNIVERSITY|COLLEGE|FACULTY|LECTURER|ACADEMIC'
     r'|EDUCATIONAL SPECIALIST|HIGHER EDUCATION|\bINTERN\b'),

    ('CONSULTING',
     r'CONSULTANT|CONSULTING|ADVISOR|ADVISORY|ADVISER|STRATEGIST'),

    ('SALES / MARKETING',
     r'SALES|MARKETING|ADVERTISING|PUBLIC RELATION'
     r'|\bBRAND\b|MEDIA|COMMUNICATIONS|PUBLICIST'
     r'|TRAVEL AGENT|RECRUITER|TALENT AGENT|FUNDRAIS'
     r'|PLANNED GIVING|CORPORATE OUTREACH|CORPORATE RELATIONS'
     r'|CUSTOMER SUCCESS|MARKET RESEARCH'),

    ('INSURANCE',
     r'INSURANCE'),

    ('RELIGIOUS',
     r'RABBI|PASTOR|CLERGY|MINISTER|PRIEST|IMAM|CHAPLAIN|CHURCH|CANTOR'),

    ('GOVERNMENT / MILITARY',
     r'GOVERNMENT|MILITARY|ARMY|NAVY|AIR FORCE|FEDERAL'
     r'|STATE EMPLOY|CITY EMPLOY|POLITICAL|POLITICIAN|LOBBYIST|POLICY'
     r'|INVESTIGATOR|REGULATORY|COURT RECEIVER'),

    ('ARTS / ENTERTAINMENT',
     r'ARTIST|WRITER|AUTHOR|MUSICIAN|ACTOR|PRODUCER|DESIGNER'
     r'|PHOTOGRAPHER|ENTERTAINMENT|FILMMAKER|PUBLISHER|PUBLISHING'
     r'|EDITOR|EVENT PLANNER|EVENT PRODUCTION|OPERA SINGER'
     r'|INTERIOR DESIGN|INTERIOR ARCHITECT|CREATIVE|FASHION'
     r'|BEAUTY|SPORTS|PHOTOGRAPHY|\bDJ\b|ARCHITECTURE|\bARCHITECT\b'),

    ('SCIENCE / RESEARCH',
     r'SCIENTIST|RESEARCHER|CHEMIST|PHYSICIST|BIOLOGIST'
     r'|ECONOMIST|LABORATORY|GEOLOGIST|HYDROGEOLOG|ARCHAEOLOG'
     r'|\bSCIENCE\b'),

    ('MANAGEMENT',
     r'MANAGER|MANAGEMENT|SUPERVISOR|ADMINISTRATOR|OPERATIONS'
     r'|ADMIN|ADMINISTRATION|SECRETARY|ORGANIZER|OFFICE ADMIN'
     r'|\bASSISTANT\b|ASSOCIATE STAFF'),

    ('CONSTRUCTION / TRADES',
     r'CONSTRUCT|CONTRACTOR|PLUMB|ELECTRICIAN|CARPENTER'
     r'|HVAC|ROOFING|WELDING|MECHANIC|BUILDER|MAINTENANCE'),

    ('AGRICULTURE',
     r'FARM|RANCH|AGRICULTURE|AGRI'),

    ('TRANSPORTATION',
     r'PILOT|DRIV\w+|TRUCKING|SHIPPING|LOGISTICS|TRANSPORT|AVIATION|MOVER'),

    ('FOOD / HOSPITALITY',
     r'CHEF|COOK|RESTAURANT|HOTEL|BAKERY|CATER|SERVER'
     r'|WAITER|HOSPITALITY|FOOD SERVICE|BARISTA'),

    ('NONPROFIT / PHILANTHROPY',
     r'VOLUNTEER|PHILANTHROPIST|NONPROFIT|NON-PROFIT|NON PROFIT'
     r'|FOUNDATION|CHARITY|NGO|PHILANTHROPY|SOCIAL ACTIVIST'),

]

# Pre-compile for performance
CATEGORY_PATTERNS = [(cat, re.compile(pat)) for cat, pat in CATEGORY_RULES]

# ── Explicit category overrides ──
# These occupations are matched to the WRONG category by the regex rules above
# (because a broader pattern fires first). Override with the correct category.
CATEGORY_OVERRIDES = {
    # ── TECHNOLOGY ──
    # "DEVELOPER" alone → TECHNOLOGY (in FEC data, bare "DEVELOPER" is usually software)
    "SOFTWARE DEVELOPER":           "TECHNOLOGY",
    "SOFTWARE EXECUTIVE":           "TECHNOLOGY",
    "SOFTWARE RELEASE ANALYST":     "TECHNOLOGY",
    "SOFTWARE DEVELOPMENT MANAGER": "TECHNOLOGY",
    "QUANT DEVELOPER":              "TECHNOLOGY",
    "DEVELOPER":                    "TECHNOLOGY",
    "DEVELOPER/DESIGNER":           "TECHNOLOGY",
    "DATABASE DEVELOPER":           "TECHNOLOGY",
    "DATABASE ANALYTICS":           "TECHNOLOGY",

    # ── CONSTRUCTION / TRADES ──
    # "CONTRACTOR" caught by EXECUTIVE regex (has "C" prefix patterns)
    "CONTRACTOR":                   "CONSTRUCTION / TRADES",
    "GENERAL CONTRACTOR":           "CONSTRUCTION / TRADES",
    "ELECTRICAL CONTRACTOR":        "CONSTRUCTION / TRADES",
    "ROOFING CONTRACTOR":           "CONSTRUCTION / TRADES",
    "FLOORING CONTRACTOR":          "CONSTRUCTION / TRADES",
    "FLOORING":                     "CONSTRUCTION / TRADES",
    "ARCHITECT":                    "CONSTRUCTION / TRADES",
    "INTERIOR ARCHITECT":           "CONSTRUCTION / TRADES",

    # ── MEDICAL / HEALTHCARE ──
    # "CHIROPRACTOR" caught by EXECUTIVE regex
    "CHIROPRACTOR":                 "MEDICAL / HEALTHCARE",

    # ── FINANCE / INVESTMENT ──
    "PRIVATE WEALTH ADVISOR":       "FINANCE / INVESTMENT",
    "FUND MANAGER":                 "FINANCE / INVESTMENT",
    "MONEY MANAGEMENT":             "FINANCE / INVESTMENT",
    "MONEY MANAGER":                "FINANCE / INVESTMENT",
    "ECONOMIST":                    "FINANCE / INVESTMENT",
    "CFP":                          "FINANCE / INVESTMENT",  # Certified Financial Planner

    # ── SALES / MARKETING ──
    "BUSINESS DEVELOPMENT MANAGER": "SALES / MARKETING",
    "BUSINESS DEVELOPMENT":         "SALES / MARKETING",
    "HEAD OF BUSINESS DEVELOPMENT": "SALES / MARKETING",
    "ACCOUNT EXECUTIVE":            "SALES / MARKETING",

    # ── MANAGEMENT ──
    "HUMAN RESOURCES":              "MANAGEMENT",
    "CONSTRUCTION MANAGER":         "MANAGEMENT",

    # ── EXECUTIVE / C-SUITE ──
    "VP OF MARKETING":              "EXECUTIVE / C-SUITE",
    "CHIEF DEVELOPMENT OFFICER":    "EXECUTIVE / C-SUITE",

    # ── LEGAL ──
    # "MEDIATION" contains "MEDIA" matching SALES/MARKETING regex
    "MEDIATION":                    "LEGAL",
    "MEDIATOR":                     "LEGAL",
    "FAMILY MEDIATION":             "LEGAL",
    "FAMILY MEDIATOR":              "LEGAL",

    # ── FOOD / HOSPITALITY ──
    "RESTAURATEUR":                 "FOOD / HOSPITALITY",

    # ── EDUCATION ──
    "EDUCATION":                    "EDUCATION",

    # ── REAL ESTATE ──
    # These DEVELOP* entries that ARE real estate (not caught by updated regex)
    "OWNER/DEVELOPER":              "REAL ESTATE",
    "PROPERTY DEVELOPMENT":         "REAL ESTATE",
    "SOLAR DEVELOPER":              "CONSTRUCTION / TRADES",
    "AFFORDABLE HOUSING DEVELOPER": "REAL ESTATE",

    # ── MEDICAL / HEALTHCARE (missed by regex) ──
    "NEPHROLOGIST":                 "MEDICAL / HEALTHCARE",
    "PHARMACY SERVICES":            "MEDICAL / HEALTHCARE",
    "PHARMACY":                     "MEDICAL / HEALTHCARE",
    "PHARMACIST CONSULTANT":        "MEDICAL / HEALTHCARE",
    "ORAL SURGEON":                 "MEDICAL / HEALTHCARE",
    "ALLERGIST":                    "MEDICAL / HEALTHCARE",
    "INTERNIST":                    "MEDICAL / HEALTHCARE",

    # ── GOVERNMENT / MILITARY (missed by regex) ──
    "COUNCILMEMBER":                "GOVERNMENT / MILITARY",
    "CITY COUNCIL MEMBER":          "GOVERNMENT / MILITARY",
    "COUNCIL MEMBER":               "GOVERNMENT / MILITARY",
    "ELECTED OFFICIAL":             "GOVERNMENT / MILITARY",
    "PUBLIC OFFICIAL":              "GOVERNMENT / MILITARY",
    "MAYOR":                        "GOVERNMENT / MILITARY",
    "ALDERMAN":                     "GOVERNMENT / MILITARY",

    # ── SALES / MARKETING ──
    "PROPANE MARKETER":             "SALES / MARKETING",
    "IMPORTER":                     "SALES / MARKETING",

    # ── REAL ESTATE (development variants) ──
    "COMMERCIAL REAL ESTATE DEVELOPMENT": "REAL ESTATE",
    "REAL ESTATE DEVELOPMENT AND MANAGEMENT": "REAL ESTATE",
    "HOUSING DEVELOPMENT":          "REAL ESTATE",
    "HOMELESS HOUSING":             "REAL ESTATE",
    "COMMUNITY BUILDING":           "REAL ESTATE",

    # ── EXECUTIVE / C-SUITE (typos and variants) ──
    "CORP EXEC":                    "EXECUTIVE / C-SUITE",
    "EXEC VP":                      "EXECUTIVE / C-SUITE",
    "EXECUIVE":                     "EXECUTIVE / C-SUITE",
    "CORPORATE OFFICER":            "EXECUTIVE / C-SUITE",
    "SENIOR ASSOCIATE":             "EXECUTIVE / C-SUITE",
    "MANAGING MENBER":              "EXECUTIVE / C-SUITE",
    "CORPORATE FIDUCIARY":          "EXECUTIVE / C-SUITE",

    # ── CONSULTING (missed by regex) ──
    "CONSULTANCY":                  "CONSULTING",

    # ── ARTS / ENTERTAINMENT ──
    "ART GALLERIST":                "ARTS / ENTERTAINMENT",
    "ENTERTAINER":                  "ARTS / ENTERTAINMENT",
    "DECORATOR":                    "ARTS / ENTERTAINMENT",
    "CORPORATE STORYTELLER":        "ARTS / ENTERTAINMENT",
    "TOUR GUIDE":                   "ARTS / ENTERTAINMENT",
    "SUSTAINABLE DESIGN":           "ARTS / ENTERTAINMENT",
    "MEETING PLANNER":              "ARTS / ENTERTAINMENT",
    "INTERNATIONAL PARTY PLANNER":  "ARTS / ENTERTAINMENT",

    # ── BUSINESS / ENTREPRENEUR ──
    "AUTOMOBILE DEALER":            "BUSINESS / ENTREPRENEUR",
    "RETAILER":                     "BUSINESS / ENTREPRENEUR",
    "LUMBER BIZ":                   "BUSINESS / ENTREPRENEUR",
    "BUSIENSS":                     "BUSINESS / ENTREPRENEUR",
    "INTERNATIONAL COMMERCE":       "BUSINESS / ENTREPRENEUR",
    "APARTMENTS":                   "REAL ESTATE",
    "HOMEBUILDING":                 "CONSTRUCTION / TRADES",

    # ── EDUCATION ──
    "DEAN":                         "EDUCATION",
    "TEACHING":                     "EDUCATION",
    "STUDENT CUSTODIAN":            "EDUCATION",

    # ── MEDICAL / HEALTHCARE ──
    "REGISTERED DIETITIAN":         "MEDICAL / HEALTHCARE",
    "HOLISTIC NUTRITIONIST":        "MEDICAL / HEALTHCARE",
    "HEALTH COCH":                  "MEDICAL / HEALTHCARE",
    "CAREGIVER":                    "MEDICAL / HEALTHCARE",
    "CLINICAL DEVELOPMENT":         "MEDICAL / HEALTHCARE",

    # ── MANAGEMENT ──
    "MANAGET":                      "MANAGEMENT",
    "PROJECT MGR":                  "MANAGEMENT",
    "ACCOUNT CLERK":                "MANAGEMENT",
    "OFFICE HELP":                  "MANAGEMENT",
    "PROP MNGMT":                   "MANAGEMENT",
    "RGM LEAD":                     "MANAGEMENT",

    # ── TECHNOLOGY ──
    "SW EGIEER":                    "TECHNOLOGY",
    "APP DEVELOPMENT":              "TECHNOLOGY",

    # ── FINANCE / INVESTMENT ──
    "GROWTH EQUITY":                "FINANCE / INVESTMENT",
    "VENTURE CSPITALIST":           "FINANCE / INVESTMENT",
    "UNDERWRITING":                 "FINANCE / INVESTMENT",

    # ── SALES / MARKETING ──
    "MARKETER":                     "SALES / MARKETING",

    # ── GOVERNMENT / MILITARY ──
    "PUBLIC ADJUSTER":              "GOVERNMENT / MILITARY",
    "PROGRAM SPECIALIST":           "GOVERNMENT / MILITARY",
    "COMP CLAIMS REFEREE":          "GOVERNMENT / MILITARY",
    "INFORMATION RESEARCH SPECIALIST": "GOVERNMENT / MILITARY",
    "JUDICIAL RECEIVER":            "LEGAL",

    # ── NONPROFIT ──
    "FREELANCER":                   "SELF-EMPLOYED",
    "PET SITTER":                   "SELF-EMPLOYED",

    # ── Batch 2: remaining OTHER cleanup ──

    # EXECUTIVE / C-SUITE
    "DEPUTY MANAGING DIRECTOR":     "EXECUTIVE / C-SUITE",
    "CO PRES":                      "EXECUTIVE / C-SUITE",
    "FOOD MFG EXEC":                "EXECUTIVE / C-SUITE",
    "CH BD":                        "EXECUTIVE / C-SUITE",
    "EXEC RECRUITING/BD":           "EXECUTIVE / C-SUITE",
    "SVP, MUSIC & CELEBRITY TALENT": "EXECUTIVE / C-SUITE",
    "FIN MGMT":                     "FINANCE / INVESTMENT",
    "FIN ADV":                      "FINANCE / INVESTMENT",
    "FINACIAL":                     "FINANCE / INVESTMENT",
    "PRIVATE WEALTH":               "FINANCE / INVESTMENT",
    "PRIVATE CREDIT ALLOCATOR":     "FINANCE / INVESTMENT",
    "RESTRUCTURING":                "FINANCE / INVESTMENT",
    "AQUISITIONS":                  "FINANCE / INVESTMENT",
    "FUNDING":                      "FINANCE / INVESTMENT",

    # LEGAL
    "ATROENEY":                     "LEGAL",
    "ATTOENY":                      "LEGAL",
    "ATT'Y":                        "LEGAL",
    "PROSECUTOR":                   "LEGAL",
    "FIDUCIARY":                    "LEGAL",

    # MEDICAL / HEALTHCARE
    "NUCLEAR HYDROLOGIST":          "SCIENCE / RESEARCH",
    "BIOSTATISTICIAN":              "SCIENCE / RESEARCH",
    "PSYCHOMETRIST":                "MEDICAL / HEALTHCARE",
    "LONG TERM CARE AND REHABILITATION FACI": "MEDICAL / HEALTHCARE",
    "SENIOR LIVING":                "MEDICAL / HEALTHCARE",
    "MENTAL HEALTH":                "MEDICAL / HEALTHCARE",
    "CLINICAL TRIALS":              "MEDICAL / HEALTHCARE",
    "HESLTHCARE":                   "MEDICAL / HEALTHCARE",
    "PHYT":                         "MEDICAL / HEALTHCARE",

    # INSURANCE
    "INSURANCE BROKER":             "INSURANCE",
    "INSURANCE":                    "INSURANCE",
    "COMPLEX CLAIMS SPECIALIST":    "INSURANCE",
    "ADJUSTER":                     "INSURANCE",

    # REAL ESTATE
    "REAL ESTATE":                  "REAL ESTATE",
    "REAL EATSTE":                  "REAL ESTATE",
    "REAL ESTATE DEVELOPMENT":      "REAL ESTATE",
    "CRE APPRAISER":               "REAL ESTATE",
    "APPRAISER":                    "REAL ESTATE",
    "MARINA OPERATOR":              "REAL ESTATE",
    "SELF STG MGMT & DVLPMT":      "REAL ESTATE",
    "HOME IMPROVEMENT":             "CONSTRUCTION / TRADES",

    # SALES / MARKETING
    "MARKEITNG":                    "SALES / MARKETING",
    "BRANDING":                     "SALES / MARKETING",
    "BRANDED MERCH":                "SALES / MARKETING",
    "MERCH AND PROMOTIONAL PRODUCTS": "SALES / MARKETING",
    "HEADHUNTER":                   "SALES / MARKETING",
    "CUSTOMER SERVICE":             "SALES / MARKETING",
    "LOAN OFFICER":                 "SALES / MARKETING",
    "AUCTIONEER":                   "SALES / MARKETING",

    # EDUCATION
    "INSTRUCTIONAL COACH":          "EDUCATION",
    "LANG ARTS SPECIALIST":         "EDUCATION",
    "BASKETBALL COACH":             "EDUCATION",
    "GRAD STUDENT":                 "STUDENT",
    "SENIOR FELLOW":                "EDUCATION",

    # MANAGEMENT
    "PRODUCT MANAGEENT":            "MANAGEMENT",
    "PROGRAM ASSOCIATE":            "MANAGEMENT",
    "OPS":                          "MANAGEMENT",
    "PACKAGE HANDLER":              "MANAGEMENT",
    "CLERK":                        "MANAGEMENT",
    "OFFICE STAFF":                 "MANAGEMENT",
    "OPERATOR":                     "MANAGEMENT",
    "EMPLOYEE":                     "MANAGEMENT",
    "MANAY":                        "MANAGEMENT",
    "MD LATAM":                     "MANAGEMENT",

    # GOVERNMENT / MILITARY
    "CONGRESSIONAL CANDIDATE":      "GOVERNMENT / MILITARY",
    "STATE REP":                    "GOVERNMENT / MILITARY",
    "JP #5":                        "GOVERNMENT / MILITARY",
    "GOV'T RELATIONS":              "GOVERNMENT / MILITARY",
    "LANDMAN":                      "GOVERNMENT / MILITARY",

    # ARTS / ENTERTAINMENT
    "HAIR STYLIST":                 "ARTS / ENTERTAINMENT",
    "FLORIST":                      "ARTS / ENTERTAINMENT",
    "TAILOR":                       "ARTS / ENTERTAINMENT",
    "VIDEO":                        "ARTS / ENTERTAINMENT",
    "BROADCAST PRODUCTION":         "ARTS / ENTERTAINMENT",
    "FIELD PROD":                   "ARTS / ENTERTAINMENT",
    "PRODUCT DESIGN":               "ARTS / ENTERTAINMENT",
    "MULTI FAMILY DESIGN":          "ARTS / ENTERTAINMENT",
    "YOGA FITNESS":                 "ARTS / ENTERTAINMENT",
    "LIFE COACH":                   "CONSULTING",
    "MARRIAGE COACH":               "CONSULTING",
    "FACILITATOR":                  "CONSULTING",

    # BUSINESS / ENTREPRENEUR
    "AUTO":                         "BUSINESS / ENTREPRENEUR",
    "AUTOMTIVE":                    "BUSINESS / ENTREPRENEUR",
    "IMPORT":                       "BUSINESS / ENTREPRENEUR",
    "SCRAP":                        "BUSINESS / ENTREPRENEUR",
    "PAPER CONVERTER":              "BUSINESS / ENTREPRENEUR",
    "APPAREL":                      "BUSINESS / ENTREPRENEUR",
    "JEWLERY":                      "BUSINESS / ENTREPRENEUR",
    "CLEANNG SERVICE":              "BUSINESS / ENTREPRENEUR",
    "ENERGY":                       "BUSINESS / ENTREPRENEUR",
    "POWER":                        "BUSINESS / ENTREPRENEUR",
    "OIL & GAS":                    "BUSINESS / ENTREPRENEUR",
    "DIGITAL TRANSFORMATION":       "TECHNOLOGY",

    # FOOD / HOSPITALITY
    "ISRAEL TOUR OPERATOR":         "FOOD / HOSPITALITY",

    # NONPROFIT
    "FUND-RAISING":                 "NONPROFIT / PHILANTHROPY",
    "JEWISH PROFESSIONAL":          "NONPROFIT / PHILANTHROPY",
    "DIR OF PROGRAMMING":           "NONPROFIT / PHILANTHROPY",
    "DIRECT OF CURRICULUM DEVELOPMENT": "EDUCATION",

    # SCIENCE / RESEARCH
    "MEMBER OF RESEARCH STAFF":     "SCIENCE / RESEARCH",
    "PSYCHOLOGY":                   "SCIENCE / RESEARCH",

    # ── Batch 3: final stragglers ──
    "DEVELOPMENT":                  "NONPROFIT / PHILANTHROPY",  # fundraising context (Milken School, FDD, NDI)
    "INTERNATIONAL DEVELOPMENT":    "NONPROFIT / PHILANTHROPY",
    "VP OF DEVELOPMENT":            "NONPROFIT / PHILANTHROPY",
    "INVESTMENT MANAGEMENT":        "FINANCE / INVESTMENT",
    "MANAGING PARTNER":             "EXECUTIVE / C-SUITE",
    "OWNER":                        "BUSINESS / ENTREPRENEUR",
    "ENTREPRENEUR":                 "BUSINESS / ENTREPRENEUR",
    "ATTORNEY":                     "LEGAL",
    "LAWYER":                       "LEGAL",
    "EMPLOYED":                     "OTHER",  # keep as OTHER, no info
    "SELF-EMPLOYED":                "SELF-EMPLOYED",
    "REG. REP":                     "FINANCE / INVESTMENT",
    "GP":                           "FINANCE / INVESTMENT",  # General Partner
    "SALE":                         "SALES / MARKETING",
    "ACCOUNT":                      "ACCOUNTING / TAX",
    "MFG":                          "BUSINESS / ENTREPRENEUR",
    "VENTURER":                     "FINANCE / INVESTMENT",
    "HEALTH":                       "MEDICAL / HEALTHCARE",
    "PROGRAM":                      "MANAGEMENT",
    "ASSOC":                        "MANAGEMENT",
    "REGISTERED DIETITIAN, HEALTH AND WELLN": "MEDICAL / HEALTHCARE",
    "HOLISTIC WEALTH AND PHILANTHROPIC COAC": "CONSULTING",
}

VALID_CATEGORIES = frozenset(
    [cat for cat, _ in CATEGORY_RULES]
    + [
        'OTHER',
        'POLITICAL COMMITTEE', 'CONGRESSIONAL CAMPAIGN',
        'SENATE CAMPAIGN', 'POLITICAL ACTION COMMITTEE',
        'PARTY ORGANIZATION',
        # ORGANIZATION entities (banks, trusts, businesses) are given this
        # category by the entity classifier, not by CATEGORY_RULES — so the
        # gate has to list it, otherwise every run reports it as an invalid
        # category while the cleaner is deliberately writing it.
        'ORGANIZATION',
    ]
)


# Canonical occupation forms — collapse variant spellings/abbreviations of the
# SAME job title to one form (applied by enhancements.normalize_occupation_canonical).
# Data, not logic — lives here with the other occupation lookups.
OCCUPATION_CANONICAL = {
    # ━━━━ MEDICAL ━━━━
    'ORTHOPAEDIC SURGEON': 'ORTHOPEDIC SURGEON',
    'ORTHO SURGEON':       'ORTHOPEDIC SURGEON',
    'PODIATRIC PHYSICIAN': 'PODIATRIST',
    'PODIATRISY':          'PODIATRIST',
    'DENTISTY':            'DENTIST',
    'PHARMACETICALS':      'PHARMACIST',
    'PSYCHOTHERAPISTS':    'PSYCHOTHERAPIST',
    'CARDIOLOGISTS':       'CARDIOLOGIST',
    'RETINA SURGEONS':     'RETINA SURGEON',
    'SCHOOLPSYCHOLOGIST':  'SCHOOL PSYCHOLOGIST',
    # ━━━━ LEGAL ━━━━
    'ATTY':             'ATTORNEY',
    'ATTORNEY-AT-LAW':  'ATTORNEY',
    'ATTORNEY AT LAW':  'ATTORNEY',
    # ━━━━ FINANCE ━━━━
    'FINANCIAL ADVISER':    'FINANCIAL ADVISOR',
    'FINANCIAL ADVISOY':    'FINANCIAL ADVISOR',
    'FINANCIAL ADVISIR':    'FINANCIAL ADVISOR',
    'FINANCIAL ADV':        'FINANCIAL ADVISOR',
    'FINANCIAL SREVICES':   'FINANCIAL SERVICES',
    'INVESTMENT ADVISORS':  'INVESTMENT ADVISOR',
    # ━━━━ ACCOUNTING ━━━━
    'CERTIFIED PUBLIC ACCOUNTANT':  'CPA',
    'CERT. PUBLIC ACCOUNTANT':      'CPA',
    'CERIFIED PUBLIC ACCOUNTANT':   'CPA',
    'CERTIFIED PUBIC ACCOUNTANT':   'CPA',
    # ━━━━ ENGINEERING ━━━━
    'SW ENGINEER':      'SOFTWARE ENGINEER',
    'SYSTEM ENGINEER':  'SYSTEMS ENGINEER',
    # ━━━━ REAL ESTATE ━━━━
    'RE DEVELOPER':     'REAL ESTATE DEVELOPER',
    'RE DEVELOPMENT':   'REAL ESTATE DEVELOPMENT',
    'RE FINANCE':       'REAL ESTATE FINANCE',
    'REAL ESTATE DEVELOPRR':    'REAL ESTATE DEVELOPER',
    'REAL ESTATE DECELOPET':    'REAL ESTATE DEVELOPER',
    'REAL ESTATE DRVELOPER':    'REAL ESTATE DEVELOPER',
    'REAL ESTATE DEVOLOPMENT':  'REAL ESTATE DEVELOPMENT',
    'REAL ESTATE INVESTER':     'REAL ESTATE INVESTOR',
    'REAL ESTATE INVEATOR':     'REAL ESTATE INVESTOR',
    'REAL ESTATE BROKET':       'REAL ESTATE BROKER',
    'REAL ESTATE DEV':          'REAL ESTATE DEVELOPER',
    'REAL ESTATE EXEC':         'REAL ESTATE EXECUTIVE',
    'REAL ESTATE MGMT':         'REAL ESTATE MANAGEMENT',
    'REAL ESTATE MGR':          'REAL ESTATE MANAGER',
    'COMERCIAL REAL ESTATE EXECUTIVE':   'COMMERCIAL REAL ESTATE EXECUTIVE',
    'COMMERICAL REAL ESTATE':            'COMMERCIAL REAL ESTATE',
    'COMMERCAIL REAL ESTATE EXECUTIVE':  'COMMERCIAL REAL ESTATE EXECUTIVE',
    'COMMERICAL REAL ESTATE LANDLORD':   'COMMERCIAL REAL ESTATE',
    # ━━━━ EXEC → EXECUTIVE ━━━━
    'BUSINESS EXEC':    'BUSINESS EXECUTIVE',
    'INSURANCE EXEC':   'INSURANCE EXECUTIVE',
    'FINANCIAL MGMT':   'FINANCIAL MANAGEMENT',
    # ━━━━ C-SUITE ━━━━
    'C.E.O.': 'CEO',   'C.E.O': 'CEO',
    'CHIEF EXECUTIVE OFFICER': 'CEO',
    'CHIEF EXECUTIVE': 'CEO',
    'C.F.O.': 'CFO',   'C.F.O': 'CFO',
    'CHIEF FINANCIAL OFFICER': 'CFO',
    'C.O.O.': 'COO',
    'CHIEF OPERATING OFFICER': 'COO',
    'CHIEF TECHNOLOGY OFFICER': 'CTO',
    'CHIEF TECHNICAL OFFICER': 'CTO',
    # ━━━━ VP ━━━━
    'VP': 'VICE PRESIDENT',
    'V.P.': 'VICE PRESIDENT',
    'VICE PRES': 'VICE PRESIDENT',
    'VICE-PRESIDENT': 'VICE PRESIDENT',
    'SVP': 'SENIOR VICE PRESIDENT',
    'EVP': 'EXECUTIVE VICE PRESIDENT',
    # ━━━━ OTHER ━━━━
    'ENTREPRENUER':     'ENTREPRENEUR',
    'DORECTOR':         'DIRECTOR',
    'PROJECTMANAGER':   'PROJECT MANAGER',
    'HOME MAKER':       'HOMEMAKER',
    'HOME-MAKER':       'HOMEMAKER',
    'NON-PROFIT':       'NONPROFIT',
    'NON PROFIT':       'NONPROFIT',
    'MGMT CONSULTANT':  'MANAGEMENT CONSULTANT',
}

