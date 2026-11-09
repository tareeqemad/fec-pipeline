# Occupation categories

How a filed occupation gets its category. This file is the instruction the
model reads, word for word, so write it for a reader who has never seen the
data. Reviewed exceptions go in data/rules/occupation_category_overrides.csv;
the model's answers are kept in data/cache/occupation_categories.csv.

## Categories, each with typical filed texts

- ACCOUNTING / TAX: CPA, ACCOUNTANT, TAX ADVISOR, ACCOUNTING, FORENSIC ACCOUNTANT, CONTROLLER
- AGRICULTURE: FARMER, RANCHER, HORTICULTURIST, AGRICULTURE, FERTILIZER
- ARTS / ENTERTAINMENT: WRITER, ARTIST, DESIGNER, PRODUCER, SPORTS, INTERIOR DESIGNER
- BUSINESS / ENTREPRENEUR: OWNER, BUSINESS, BUSINESS OWNER, ENTREPRENEUR, BUSINESS MANAGER, BUSINESSMAN
- CONSTRUCTION / TRADES: ARCHITECT, CONTRACTOR, MAINTENANCE, CONSTRUCTION, HOME BUILDER, GENERAL CONTRACTOR
- CONSULTING: CONSULTANT, ADVISOR, CONSULTING, MANAGEMENT CONSULTANT, ART ADVISOR, INSURANCE CONSULTANT
- EDUCATION: PROFESSOR, LIBRARIAN, TEACHER, EDUCATOR, ACADEMIC, EDUCATION
- EXECUTIVE / C-SUITE: CEO, EXECUTIVE, PRESIDENT, CHAIRMAN, PARTNER, DIRECTOR
- FINANCE / INVESTMENT: INVESTOR, FINANCE, FINANCIAL ADVISOR, INVESTMENTS, INVESTMENT MANAGEMENT, VENTURE CAPITAL
- FOOD / HOSPITALITY: BAKERY, CATERER, RESTAURANTS, RESTAURATEUR, SERVER, CATER
- GOVERNMENT / MILITARY: EVP GOVERNMENT AFFAIRS, GOVERNMENT AFFAIRS, LOBBYIST, COUNCILMEMBER, GOVERNMENT RELATIONS, REGULATORY
- HOMEMAKER: HOMEMAKER, HOUSEWIFE, STAY AT HOME MOM, HOMEMAKER & HOMESCHOOL MOM, HOMEMAKER/ARTIST
- INSURANCE: INSURANCE, INSURANCE BROKER, CLAIMS, INSURANCE AGENT, INSURANCE SERVICES, PUBLIC ADJUSTER
- LEGAL: ATTORNEY, LAWYER, GENERAL COUNSEL, LAWYER ENTREPRENEUR, MEDIATOR, SENIOR COUNSEL
- MANAGEMENT: MANAGER, MANAGEMENT, SPECIAL PROGRAMS ADMINISTRATOR, OFFICE ADMIN, ADMINISTRATOR, PRODUCT MANAGER
- MEDICAL / HEALTHCARE: PHYSICIAN, MEDICAL DOCTOR, RADIOLOGIST, DENTIST, PSYCHOLOGIST, DOCTOR
- NONPROFIT / PHILANTHROPY: DEVELOPMENT, SOCIAL ACTIVIST, NONPROFIT, VOLUNTEER, PHILANTHROPIST, PHILANTHROPY
- NOT EMPLOYED: NOT EMPLOYED
- OTHER: ANALYST, TECHNOLOGIST, NOT DISCLOSED, FUNERAL DIRECTOR, EXECUTIVE COACH, INVESTIGATOR
- REAL ESTATE: REAL ESTATE, REAL ESTATE INVESTOR, REAL ESTATE DEVELOPER, REALTOR, REAL ESTATE DEVELOPMENT, PROPERTY MANAGER
- RELIGIOUS: RABBI, CANTOR, CLERGY, RABBI EMERITUS, PASTOR, CHAPLAIN
- RETIRED: RETIRED
- SALES / MARKETING: SALES, MARKETING, ADVERTISING, MEDIA, BUSINESS DEVELOPMENT, TALENT AGENT
- SCIENCE / RESEARCH: SCIENTIST, CHEMIST, SCIENCE, PHYSICIST, GEOLOGIST, HYDROGEOLOGIST
- SELF-EMPLOYED: SELF-EMPLOYED, PET SITTER, FREELANCER, INDEPENDENT CONTRACTOR, FREELANCE
- STUDENT: STUDENT, GRAD STUDENT
- TECHNOLOGY: ENGINEER, SOFTWARE ENGINEER, DEVELOPER, TECHNOLOGY, IT, SOFTWARE DEVELOPER
- TRANSPORTATION: PILOT, TRANSPORTATION, DRIVER, DRIVING, AIRLINE PILOT, MOVER

## Rules, in order; the first that applies wins

1. Status words: RETIRED, NOT EMPLOYED, STUDENT, HOMEMAKER, SELF-EMPLOYED each have their own category, only for a text that says just that.
2. REAL ESTATE whenever the text says real estate, realtor, property broker, property, developer, landlord, RE, commercial real estate, shopping center owner, or real estate investor / finance / capital / asset management / private equity.
3. Law and medicine beat rank: a legal role (attorney, lawyer, counsel, general counsel, chief legal officer, law partner, law professor, judge, paralegal) is LEGAL; a clinical or health-care role or health-care executive (physician, MD, nurse, dentist, therapist, social worker, provider, clinical director, hospital or nursing-home administrator, healthcare executive) is MEDICAL / HEALTHCARE. Accountant, CPA, bookkeeping and tax are ACCOUNTING / TAX. Actuary and underwriter are INSURANCE.
4. Consultants beat the field they consult in: consultant, advisor, adviser, advisory, strategist and coach are CONSULTING (INSURANCE CONSULTANT, HEALTHCARE CONSULTANT, MARKETING CONSULTANT, MEDIA STRATEGIST, EXECUTIVE COACH, ART CONSULTANT), with two exceptions: rule 2 or 3 applies (REAL ESTATE ADVISOR, LEGAL CONSULTANT, HEALTH COACH stay with their rule), or the advice is about money, which is FINANCE / INVESTMENT (FINANCIAL ADVISOR, INVESTMENT ADVISOR, REGISTERED INVESTMENT ADVISOR, WEALTH ADVISOR, FINANCIAL CONSULTANT, INVESTMENT CONSULTANT, INVESTOR/CONSULTANT).
5. Top rank beats everything else: CEO, CFO, COO, CIO, CTO, CMO, any chief ... officer, chief of staff, president, founder, co-founder, chairman, trustee, board member, vice president, VP, SVP, EVP, head of ..., global head, managing director, director, senior director, executive director, partner, managing partner, principal, and the word executive or exec used as a rank (CFO, PR EXECUTIVE, VP NATIONAL SALES, VP OF ENGINEERING, HEAD OF OPERATIONS, HEAD OF MARKETING, EVP GOVERNMENT AFFAIRS, LAB DIRECTOR, TV COMMERCIAL DIRECTOR, PROFESSOR/CHAIR, COMMISSIONER, FINANCE EXECUTIVE) are EXECUTIVE / C-SUITE. Exceptions: investment titles (chief investment officer, portfolio manager, fund manager, investor) are FINANCE / INVESTMENT; a compound where executive or director is not a rank is not one: EXECUTIVE ASSISTANT and EXECUTIVE SEARCH are MANAGEMENT and SALES / MARKETING, ACCOUNT EXECUTIVE and SALES EXECUTIVE are SALES / MARKETING, FUNERAL DIRECTOR is BUSINESS / ENTREPRENEUR, ART DIRECTOR and CREATIVE DIRECTOR are ARTS / ENTERTAINMENT.
6. FINANCE / INVESTMENT: investing, investor, shareholder, banking, mortgage banker or broker or loan originator, capital, funds, wealth, asset management, private equity, venture, trading, equity sales, economist, trust administration, FP&A, finance, healthcare finance.
7. BUSINESS / ENTREPRENEUR: owners and businesspeople without a legal, medical or real-estate word, whatever the trade: OWNER, BUSINESS OWNER, BUSINESSMAN, ENTREPRENEUR, MANUFACTURER, RETAILER, DISTRIBUTOR, JEWELER, JEWELRY, RECYCLING, OIL AND GAS, AUTOMOTIVE, SCRAP METAL, MUSIC BUSINESS, E-COMMERCE, MATCHMAKER, OWNER - INTERIOR DESIGNER, PRIVATE SCHOOL OWNER, NURSING HOME OWNER, BUSINESS MANAGER, PROFESSIONAL, OWNER LEGAL CANNABIS.
8. A field word beats manager, coordinator, administration or operations: SALES / MARKETING for sales, marketing, advertising, PR, communications, public affairs, media production, fundraising, fundraiser, recruiting, legal recruiter, executive search, customer success, business development, account management, talent management, travel agent, literary agent (SALES MANAGER, MARKETING MANAGER, ACCOUNT MANAGER, TALENT MANAGER, CUSTOMER SUCCESS MANAGER, SALES ADMINISTRATION); TECHNOLOGY for software, IT, engineer, engineering, data, systems, inventor, biotech, solar, and DEVELOPER alone, which here means software (ENGINEERING MANAGER, SOFTWARE MANAGEMENT, HEAD OF IT, PRINCIPAL SOLUTIONS ENGINEER, SOLAR DEVELOPER); FOOD / HOSPITALITY for restaurants, hotels, hospitality, chefs, caterers (HOTEL MANAGEMENT, HOTEL OPERATIONS, HOSPITALITY); EDUCATION for teacher, professor, lecturer, educator, tutor, school and college staff, intern, educational therapist, student custodian; GOVERNMENT / MILITARY for government employees, elected officials, military, police, diplomats, policy, landman; SCIENCE / RESEARCH for scientist, researcher, research analyst, lab, chemist, biologist, natural resources; NONPROFIT / PHILANTHROPY for nonprofit staff, philanthropy, community building, volunteer, charity; RELIGIOUS for clergy; ARTS / ENTERTAINMENT for writer, author, artist, musician, actor, designer, photographer, film, media, beauty, fashion, sports, fitness, tennis pro, pilates; TRANSPORTATION for trucking, shipping, airlines, logistics; CONSTRUCTION / TRADES for builders, contractors, electricians, plumbers, custodians; AGRICULTURE for farming.
9. MANAGEMENT: manager, administrator, coordinator, analyst, associate, assistant, clerk, planner, HR, operations, project and product management, with no field word (PRODUCT MANAGER, ANALYST, EXECUTIVE ASSISTANT).
10. OTHER for a text that names no work at all, and for a company or organisation name filed instead of a job (ACME LLC, CRESCENT REALTY ADVISORS, BERLINER FROZEN STORAGE): the pipeline moves those to the employer.

Answer with one category for every text, spelled exactly as listed above.
