"""Occupations, roles, sectors and titles that are not real employer names."""
import re

from fec.config.constants import (
    REFUSAL_EMPLOYERS,
    RETIRED_TYPO_EMPLOYERS,
    SKIP_EMPLOYERS,
)

# Occupation words in the employer field — probably self-employed in that profession.
OCCUPATION_AS_EMPLOYER = frozenset({
    'ATTORNEY', 'LAWYER', 'DOCTOR', 'PHYSICIAN', 'DENTIST',
    'ACCOUNTANT', 'CONSULTANT', 'ARCHITECT', 'ENGINEER',
    'PROFESSOR', 'TEACHER', 'NURSE', 'PSYCHOLOGIST',
    'THERAPIST', 'SURGEON', 'RADIOLOGIST', 'DERMATOLOGIST',
    'REALTOR', 'PHARMACY OWNER', 'CARDIOLOGIST',
    'CPA', 'DESIGNER',
})


# Role/title words in the employer field. Bare-title filers put the real
# company in the OCCUPATION field ('CHAIRMAN' + occ 'KIMCO'); AD0 (the
# cross-record swap net) recognises titles listed here and recovers the
# company, and AD falls back to SELF-EMPLOYED when none can be corroborated.
ROLE_AS_EMPLOYER = frozenset({
    'OWNER', 'CEO', 'PRESIDENT', 'PRESIDENT CEO', 'VICE PRESIDENT', 'VP',
    'FOUNDER', 'PRINCIPAL',
    'SENIOR DIRECTOR', 'MANAGING DIRECTOR', 'MANAGING PARTNER',
    'PARTNER',
    # self-employment descriptors — genuinely mean "works for themselves"
    'BUSINESS OWNER', 'BUSINESS OWNER/OPERATOR', 'ENTREPRENEUR',
    'BUSINESSMAN', 'BUSINESSWOMAN', 'INVESTOR', 'PRIVATE INVESTOR',
    'CHAIRMAN', 'COO', 'FREELANCE', 'FREELANCER',
    'INDEPENDENT', 'INDEPENDENT CONTRACTOR', 'OWN BUSINESS',
    'PRIVATE PRACTICE', 'MONEY MANAGER', 'SHAREHOLDER',
    'MENTAL HEALTH PROFESSIONAL', 'PROJECT MANAGEMENT CONSULTANT',
    'PROFESSIONAL FINANCIAL ANALYST', 'EXEC DIRECTOR', 'EXEC',
    'SCHOOL COUNSELOR', 'DRIVING', 'LANDLORD',
    # truncated at the FEC's 38-char limit
    'INDEPENDENT CONTRACTOR; AFFILIATED WIT',
    'STUDENT CUSTODIAN', 'FOUNDER AND MANAGING PRINCIPAL', 'DEPUTY CEO',
    'INTERIOR ARCHITECT', 'BUSINESS DEVELOPMENT', 'FOUNDING ATTORNEY',
    'MORTGAGE LOAN ORIGINATOR', 'MANAGING MEMBER & CO-FOUNDER',
    'INSURANCE BROKER', 'FINANCIAL ADVISOR', 'INVESTMENT ADVISOR',
    # "SELF" with the trade in parentheses - the parenthetical is the occupation,
    # so the whole string means self-employed, not a company called SELF
    'SELF (LANDSCAPE DESIGNER)',
    # describes what the filer does, not who they work for; expanding the
    # abbreviation would have invented a company called Independent Investment
    # Manager, so it belongs here instead
    'INDEPENDENT INVESTMENT MNGR',
})


# Industry/sector words a donor wrote instead of a company. They name an
# INDUSTRY or a KIND of workplace (unlike OWNER/INVESTOR they don't imply
# self-employment) — the employer is NULLED, occupation kept. A filer who
# swapped the fields (occ is a real company) gets a swap-back instead.
SECTOR_AS_EMPLOYER = frozenset({
    'HEALTHCARE', 'HEALTH CARE', 'REAL ESTATE', 'REAL-ESTATE',
    'FINANCE', 'FINANCIAL', 'FINANCIAL SERVICES', 'INSURANCE',
    'TECHNOLOGY', 'EDUCATION', 'MANUFACTURING', 'CONSTRUCTION',
    'HOSPITALITY', 'RETAIL', 'AGRICULTURE', 'BANKING', 'ENERGY',
    'PHARMACEUTICALS', 'PHARMACEUTICAL', 'ENTERTAINMENT', 'MEDIA',
    'TELECOMMUNICATIONS', 'AUTOMOTIVE', 'AEROSPACE', 'LOGISTICS',
    'ENGINEERING', 'BIOTECHNOLOGY', 'BIOTECH', 'ACCOUNTING',
    'ADVERTISING', 'NONPROFIT', 'NON-PROFIT', 'GOVERNMENT',
    # EXECUTIVE is a title, not an industry: the sector safety net swaps it
    # back when the occupation box holds the company (safety_nets/employer.py)
    'EXECUTIVE', 'MANAGEMENT', 'SALES', 'MARKETING',
    # bare institution-type words: a KIND of workplace, not a workplace
    'HOSPITAL', 'LAW FIRM', 'LAW OFFICE', 'UNIVERSITY', 'SCHOOL',
    'BANK', 'CENTER', 'HOSPITALITY/TOURISM',
    'TRANSPORTATION', 'SOFTWARE', 'HEDGE FUND', 'INVESTMENT BANK',
    'INSURANCE COMPANY', 'CONSTRUCTION CO', 'CONSTRUCTION CO.', 'RE FIRM', 'REALESTATE',
    # PM&R = physical medicine & rehabilitation (medical specialty); all three
    # keyed spellings must be here or the donor's other filings re-fill the null
    'MAGAZINE', 'MEDICAL', 'PM&R', 'PMR', 'PM AND R', 'NON PROFIT', 'PUBLIC SECTOR', 'E COMMERCE',
    # names the line of work, same as REAL ESTATE above; it had survived as a
    # "company" for a $615K donor whose occupation is simply RETIRED
    'PROPERTY MANAGEMENT',
    'COLLEGE', 'A BANK', 'CDN PROVIDER', 'STARTUP', 'STEALTH STARTUP',
    # government: names the sector, not the agency
    'STATE EMPLOYEE', 'FEDERAL EMPLOYEE', 'LOCAL GOVERNMENT',
    # a kind of workplace / line of work, like HEDGE FUND and LAW OFFICE:
    # OFFICE (occ MANAGER), FAMILY OFFICE (occ INVESTOR / PORTFOLIO MANAGER /
    # ASSOCIATE), REAL ESTATE SALES (occ REALTOR)
    'OFFICE', 'FAMILY OFFICE', 'REAL ESTATE SALES',
})


# Job titles that appear as employer (swap candidates).
JOB_TITLE_AS_EMPLOYER = frozenset({
    'REAL ESTATE', 'INVESTMENT MANAGEMENT', 'INSURANCE',
    'CONSULTING', 'FINANCE', 'MARKETING', 'MANAGEMENT',
    'CONSTRUCTION', 'EDUCATION',
})


# Self-employed occupation words found in the employer field.
SELF_EMPLOYED_OCC_AS_EMP = frozenset({
    'DESIGNER', 'CATER', 'CATERER', 'WRITER', 'ARTIST', 'SALES',
    'REAL ESTATE', 'PERSONAL', 'BUSINESS', 'FARMING', 'TRADER',
    # filed both ways by one donor (SELF EMPLOYED / TRUST DEED INVESTMENTS and
    # TRUST DEED INVESTMENTS / SELF-EMPLOYED): a line of work, not a firm
    'TRUST DEED INVESTMENTS',
})


# Legal suffixes (LLC, INC, CORP, LTD...) stripped from employer names for matching.
LEGAL_SUFFIX_RE = re.compile(
    r',?\s*\b'  # word boundary — don't strip inside words
    r'(LLC|LLP|L\.L\.C\.?|L\.L\.P\.?'
    r'|INC\.?|CORP\.?|LTD\.?'
    r'|LP|PLC'
    r'|P\.?C\.?|P\.?A\.?|P\.?L\.?L\.?C\.?)'
    r'\s*\.?\s*$',
    re.IGNORECASE,
)


# Bare professions / job titles / lines of business written in an employer
# box. Each was found as a previous_employer (copied verbatim from a donor's
# older filing) and checked: none names a company. They are NOT added to
# OCCUPATION_AS_EMPLOYER / ROLE_AS_EMPLOYER / SECTOR_AS_EMPLOYER because those
# sets also drive the current-employer swap nets (ADMINISTRATOR and
# INVESTMENT MANAGEMENT are swapped back against a company in the occupation
# box, and nulling them first would lose that company). Listing them in
# NOT_REAL_EMPLOYER only answers "is this a company?" - no.
OCCUPATION_TITLE_EMPLOYERS = frozenset({
    # professions
    'MEDICAL DOCTOR', 'CHIROPRACTOR', 'OPHTHALMOLOGIST', 'PERIODONTIST',
    'PSYCHOTHERAPIST', 'SPEECH PATHOLOGIST', 'SCIENTIST', 'WRITER',
    'ARTIST', 'PRODUCER', 'OPERA SINGER', 'PHOTOGRAPHER',
    'INTERIOR DESIGNER', 'PERSONAL TRAINER', 'BOOKKEEPER', 'FARMER',
    'CONTRACTOR', 'BUILDER', 'LAND DEVELOPER', 'REAL ESTATE BROKER',
    'PROPERTY OWNER', 'FUNDRAISER', 'MANAGEMENT CONSULTANT',
    'FINANCIAL CONSULTANT',
    # professions cached from a retiree's older FEC filing (each is the whole
    # cached employer; no filer ever used one as a current employer)
    'BUILDING CONSULTANT', 'PLANNING CONSULTANT', 'COMPUTER CONSULTANT',
    'MUSEUM EDUCATION CONSULTANT', 'BOND BROKER', 'SOFTWARE DESIGNER',
    'SCRAP DEALER', 'ANTIQUE DEALER', 'RESIDENTIAL REAL ESTATE APPRAISER',
    'BROADCASTER', 'COMMUNITY ACTIVIST', 'COMMUNITY VOLUNTEER LEADER',
    'MUSEUM FOUNDER', 'ORTHOPAEDIC SURGEON', 'ORTHOPEDIC SURGEON',
    # the profession left after a RETIRED marker in the filed text
    # ("RETIRED JUDGE", "RETIRED ORAL SURGEON", ...): the donor's old job
    'JUDGE', 'JUDGE MEDIATOR', 'FAMILY PHYSICIAN', 'ORAL SURGEON',
    'GENERAL CONTRACTOR', 'ASSISTANT DEAN', 'JEWISH EDUCATOR (DIRECTOR)',
    'MILITARY AND BUSINESS OWNER',
    # job titles
    'ADMINISTRATOR', 'EXECUTIVE DIRECTOR', 'SENIOR MANAGING DIRECTOR',
    'BUSINESS EXECUTIVE', 'BOARD OF DIRECTORS', 'ADMIN',
    # lines of business, not a firm (like FINANCIAL SERVICES below)
    'INVESTMENTS', 'INVESTMENT MANAGEMENT', 'PARTNERSHIPS',
    'SEMICONDUCTOR SECTOR', 'IT SERVICES',
})


# The shared answer to "is this string a company?" — resolve skips AI lookup
# for these, the loader keeps them out of the employers table, and the
# previous_employer contract clears them. Unions the skip/refusal sets so
# there is no second list to keep in sync. Lives in config (data, not logic)
# because cleaning, resolve AND database all need it.
NOT_REAL_EMPLOYER = frozenset({
    "NO",
    "SELF",
    "SELF EMPL.", "SELF EMPL", "SELF-EMP", "SELF EMP",
    "UNKNOWN",
    "MR AND MRS", "MR. AND MRS.", "DR AND MS", "DR. AND MS", "DR. AND MS.",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "DISABLED",
    # status / housewife variants
    "RETIREE", "RETIEED", "RETIRE", "HOUSEWIFE", "HOUSWIFE", "HOUSE WIFE",
    # occupations / job titles — these belong in occupation, not employer
    "PHYSICIAN", "ATTORNEY", "CONSULTANT", "INVESTOR", "PRIVATE INVESTOR",
    "BUSINESSMAN", "BUSINESSWOMAN", "ENTREPRENEUR", "PHILANTHROPIST", "EXECUTIVE",
    # industries / sectors written instead of an employer
    "REAL ESTATE", "HEALTHCARE", "HEALTH CARE", "FINANCE", "FINANCIAL SERVICES",
}) | SKIP_EMPLOYERS | REFUSAL_EMPLOYERS | RETIRED_TYPO_EMPLOYERS | OCCUPATION_TITLE_EMPLOYERS
