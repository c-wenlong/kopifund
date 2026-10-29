#!/usr/bin/env python3
"""Cities, currencies and the country a card row was charged in.

Three parsers ask the same question of a merchant string: which country was
this spent in? YouTrip, Revolut and Wise all print the merchant and its city in
one field, so all three need the same tables and the same splitting rule. They
live here rather than in one of the parsers, so no parser has to import another
to reach them and no import can change what another parser sees.

    norm_city(s)                   the city fragment, uppercased and stripped
                                   of the mojibake pdftotext leaves behind
    city_country(frag)             the country a city fragment names, or None
    split_country(name, fx, ccy)   (merchant, country) from a merchant string
    load(path)                     the shipped tables plus a places.csv

The country slot is the last comma-separated fragment of the merchant string,
usually a city and often truncated. On an SGD-wallet row the charged foreign
currency is the reliable signal; a foreign wallet has no such signal, so
CITY_TABLE fills in, prefix-matched in both directions because the fragment is
cut off at an arbitrary length, and a wallet in a one-country currency falls
back to that country.

The shipped table holds capitals and major cities, plus the Singapore and
Malaysia places people here commonly pay in. The towns of one person's stays
and trips go in places.csv in the data folder (server/finance/paths.py), which is read
at the first lookup (never at import, so importing a parser neither fills the
tables nor checks the file) and appended after the shipped table:

    place,country,match
    SOMETOWN,NOR,prefix        appended to CITY_TABLE (the default)
    STW,NOR,exact              a fragment taken as it is, like CITY_COUNTRY

Order is what decides a prefix match, so a new city goes at the end of the
shipped table, or at the end of places.csv, rather than being inserted, and an
entry is never removed: a city that has dropped out of the statements still has
to resolve when an old month is re-parsed.
"""
import csv
import re
import sys
from pathlib import Path

from finance.paths import places_path

from .parse_statements import ALPHA2, US_STATES, two_letter_country

# the currency a card row was charged in, when the statement prints one
CCY_COUNTRY = {"AED": "ARE", "AUD": "AUS", "CAD": "CAN", "CHF": "CHE", "CNY": "CHN",
               "CZK": "CZE", "DKK": "DNK", "GBP": "GBR", "HKD": "HKG", "HUF": "HUN",
               "IDR": "IDN", "INR": "IND", "JPY": "JPN", "KRW": "KOR", "MYR": "MYS",
               "NOK": "NOR", "NZD": "NZL", "PHP": "PHL", "PLN": "POL", "SEK": "SWE",
               "THB": "THA", "TRY": "TUR", "TWD": "TWN", "USD": "USA", "VND": "VNM"}
# a wallet held in a one-country currency was mostly spent in that country, so
# an unrecognised city on one of those wallets falls back to it. USD is the
# exception: a USD wallet pays online merchants everywhere.
WALLET_COUNTRY = {ccy: cc for ccy, cc in CCY_COUNTRY.items() if ccy != "USD"}
# fragments that are not city names, or are cut so short that prefix matching
# would reach the wrong country
SHIPPED_CITY_COUNTRY = {"LONDO": "GBR", "LONDON": "GBR", "INTERNE": "SGP",
                        "INTERNET": "SGP", "SF": "USA"}
SHIPPED_CITY_TABLE = (  # normalised city name -> ISO alpha-3; matched by prefix either way
    # Europe
    ("BERLIN", "DEU"), ("MUNCHEN", "DEU"), ("MUENCHEN", "DEU"), ("MUNICH", "DEU"),
    ("HAMBURG", "DEU"), ("FRANKFURT", "DEU"), ("KOLN", "DEU"), ("DUSSELDORF", "DEU"),
    ("STUTTGART", "DEU"),
    ("WIEN", "AUT"), ("VIENNA", "AUT"), ("SALZBURG", "AUT"),
    ("ZURICH", "CHE"), ("GENEVE", "CHE"), ("GENEVA", "CHE"), ("BASEL", "CHE"), ("BERN", "CHE"),
    ("PARIS", "FRA"), ("LYON", "FRA"), ("NICE", "FRA"),
    ("AMSTERDAM", "NLD"), ("ROTTERDAM", "NLD"), ("ANTWERPEN", "BEL"), ("LUXEMBOURG", "LUX"),
    ("DUBLIN", "IRL"),
    ("EDINBURGH", "GBR"), ("MANCHESTER", "GBR"), ("GLASGOW", "GBR"), ("LIVERPOOL", "GBR"),
    ("ROMA", "ITA"), ("ROME", "ITA"), ("MILANO", "ITA"), ("MILAN", "ITA"), ("VENEZIA", "ITA"),
    ("VENICE", "ITA"), ("FIRENZE", "ITA"), ("FLORENCE", "ITA"), ("NAPOLI", "ITA"),
    ("MADRID", "ESP"), ("BARCELONA", "ESP"), ("SEVILLA", "ESP"),
    ("LISBOA", "PRT"), ("LISBON", "PRT"), ("PORTO", "PRT"),
    ("ATHENS", "GRC"), ("ATHINA", "GRC"),
    ("PRAHA", "CZE"), ("PRAGUE", "CZE"), ("BUDAPEST", "HUN"), ("WARSZAWA", "POL"),
    ("WARSAW", "POL"), ("KRAKOW", "POL"), ("BRATISLAVA", "SVK"), ("LJUBLJANA", "SVN"),
    ("ZAGREB", "HRV"), ("BUCHAREST", "ROU"), ("SOFIA", "BGR"), ("BELGRADE", "SRB"),
    ("KYIV", "UKR"),
    ("STOCKHOLM", "SWE"), ("GOTEBORG", "SWE"), ("MALMO", "SWE"),
    ("OSLO", "NOR"), ("TRONDHEIM", "NOR"), ("AARHUS", "DNK"), ("ODENSE", "DNK"),
    ("HELSINKI", "FIN"), ("REYKJAVIK", "ISL"),
    ("TALLINN", "EST"), ("RIGA", "LVA"), ("VILNIUS", "LTU"),
    # the Americas
    ("SAN FRANCISCO", "USA"), ("SAN JOSE", "USA"), ("SAN DIEGO", "USA"), ("CHICAGO", "USA"),
    ("NEW YORK", "USA"), ("LOS ANGELES", "USA"), ("SEATTLE", "USA"), ("BOSTON", "USA"),
    ("WASHINGTON", "USA"), ("LAS VEGAS", "USA"), ("HONOLULU", "USA"), ("MIAMI", "USA"),
    ("ORLANDO", "USA"), ("AUSTIN", "USA"), ("DALLAS", "USA"), ("ATLANTA", "USA"),
    ("PHILADELPHIA", "USA"),
    ("TORONTO", "CAN"), ("VANCOUVER", "CAN"), ("MONTREAL", "CAN"),
    ("MEXICO CITY", "MEX"), ("CIUDAD DE MEXICO", "MEX"), ("CANCUN", "MEX"),
    ("SAO PAULO", "BRA"), ("RIO DE JANEIRO", "BRA"), ("BUENOS AIRES", "ARG"),
    ("SANTIAGO", "CHL"), ("LIMA", "PER"), ("BOGOTA", "COL"),
    # East Asia
    ("HANGZHOU", "CHN"), ("BEIJING", "CHN"), ("SHANGHAI", "CHN"), ("SHENZHEN", "CHN"),
    ("GUANGZHOU", "CHN"), ("CHENGDU", "CHN"), ("XIAMEN", "CHN"),
    ("HONG KONG", "HKG"), ("KOWLOON", "HKG"), ("MACAU", "MAC"), ("MACAO", "MAC"),
    ("TAIPEI", "TWN"), ("KAOHSIUNG", "TWN"),
    ("TOKYO", "JPN"), ("OSAKA", "JPN"), ("KYOTO", "JPN"), ("SAPPORO", "JPN"), ("FUKUOKA", "JPN"),
    ("NAGOYA", "JPN"), ("YOKOHAMA", "JPN"), ("NARITA", "JPN"),
    ("SEOUL", "KOR"), ("BUSAN", "KOR"), ("JEJU", "KOR"), ("INCHEON", "KOR"),
    # Malaysia, and the rest of Southeast Asia
    ("JOHOR BAHRU", "MYS"), ("JOHOR", "MYS"), ("PETALING JAYA", "MYS"), ("KUALA LUMPUR", "MYS"),
    ("ISKANDAR PUTERI", "MYS"), ("NUSAJAYA", "MYS"), ("SKUDAI", "MYS"), ("KULAI", "MYS"),
    ("MELAKA", "MYS"), ("PENANG", "MYS"), ("GEORGE TOWN", "MYS"), ("IPOH", "MYS"),
    ("GENTING", "MYS"), ("SHAH ALAM", "MYS"), ("SUBANG JAYA", "MYS"), ("PUTRAJAYA", "MYS"),
    ("KOTA KINABALU", "MYS"), ("KUCHING", "MYS"), ("LANGKAWI", "MYS"), ("DESARU", "MYS"),
    ("HA NOI", "VNM"), ("HANOI", "VNM"), ("HO CHI MINH", "VNM"), ("HCM", "VNM"), ("QUAN", "VNM"),
    ("DA NANG", "VNM"),
    ("DENPASAR", "IDN"), ("JAKARTA", "IDN"), ("BATAM", "IDN"), ("BINTAN", "IDN"),
    ("SURABAYA", "IDN"), ("YOGYAKARTA", "IDN"),
    ("BANGKOK", "THA"), ("PHUKET", "THA"), ("PATTAYA", "THA"), ("KRABI", "THA"),
    ("MANILA", "PHL"), ("MAKATI", "PHL"), ("CEBU", "PHL"),
    ("PHNOM PENH", "KHM"), ("SIEM REAP", "KHM"), ("YANGON", "MMR"),
    # South Asia and the Middle East
    ("NEW DELHI", "IND"), ("DELHI", "IND"), ("MUMBAI", "IND"), ("BENGALURU", "IND"),
    ("CHENNAI", "IND"), ("KOLKATA", "IND"),
    ("KATHMANDU", "NPL"), ("COLOMBO", "LKA"), ("DHAKA", "BGD"),
    ("DUBAI", "ARE"), ("ABU DHABI", "ARE"), ("DOHA", "QAT"), ("ISTANBUL", "TUR"),
    # Oceania and Africa
    ("BRISBANE", "AUS"), ("MELBOURNE", "AUS"), ("PERTH", "AUS"),
    ("ADELAIDE", "AUS"), ("CANBERRA", "AUS"), ("GOLD COAST", "AUS"),
    ("AUCKLAND", "NZL"), ("WELLINGTON", "NZL"), ("QUEENSTOWN", "NZL"), ("CHRISTCHURCH", "NZL"),
    ("CAPE TOWN", "ZAF"), ("CAIRO", "EGY"), ("NAIROBI", "KEN"),
)
# pdftotext turns the Nordic and German letters into these pairs
MOJIBAKE = [("Ã¼", "U"), ("Ã¶", "O"), ("Ã¤", "A"), ("Ã…", "A"), ("Ã–", "O"), ("Ã„", "A"), ("Ã", "O")]
ALPHA3 = set(ALPHA2.values()) | set(CCY_COUNTRY.values())

# the tables every lookup reads: the shipped ones plus the data folder's
# places.csv. load() refills these same objects, so a module that imported
# them by name sees the change.
CITY_TABLE = []
CITY_COUNTRY = {}


def norm_city(s):
    for a, b in MOJIBAKE:
        s = s.replace(a, b)
    s = re.sub(r"[^A-Z \-]", "", s.upper())
    return re.sub(r"\s+", " ", s).strip()


# Every ISO 3166-1 alpha-3 code: a country a places.csv names must be one.
ISO_ALPHA3 = frozenset("""
    AFG ALA ALB DZA ASM AND AGO AIA ATA ATG ARG ARM ABW AUS AUT AZE BHS BHR
    BGD BRB BLR BEL BLZ BEN BMU BTN BOL BES BIH BWA BVT BRA IOT BRN BGR BFA
    BDI CPV KHM CMR CAN CYM CAF TCD CHL CHN CXR CCK COL COM COG COD COK CRI
    CIV HRV CUB CUW CYP CZE DNK DJI DMA DOM ECU EGY SLV GNQ ERI EST SWZ ETH
    FLK FRO FJI FIN FRA GUF PYF ATF GAB GMB GEO DEU GHA GIB GRC GRL GRD GLP
    GUM GTM GGY GIN GNB GUY HTI HMD VAT HND HKG HUN ISL IND IDN IRN IRQ IRL
    IMN ISR ITA JAM JPN JEY JOR KAZ KEN KIR PRK KOR KWT KGZ LAO LVA LBN LSO
    LBR LBY LIE LTU LUX MAC MDG MWI MYS MDV MLI MLT MHL MTQ MRT MUS MYT MEX
    FSM MDA MCO MNG MNE MSR MAR MOZ MMR NAM NRU NPL NLD NCL NZL NIC NER NGA
    NIU NFK MKD MNP NOR OMN PAK PLW PSE PAN PNG PRY PER PHL PCN POL PRT PRI
    QAT REU ROU RUS RWA BLM SHN KNA LCA MAF SPM VCT WSM SMR STP SAU SEN SRB
    SYC SLE SGP SXM SVK SVN SLB SOM ZAF SGS SSD ESP LKA SDN SUR SJM SWE CHE
    SYR TWN TJK TZA THA TLS TGO TKL TON TTO TUN TUR TKM TCA TUV UGA UKR ARE
    GBR USA UMI URY UZB VUT VEN VNM VGB VIR WLF ESH YEM ZMB ZWE
""".split())


def read_places(path):
    """(prefix entries, exact entries) from a places.csv; nothing when the
    file is absent. A bad line stops the parser rather than being skipped,
    since a skipped town would quietly lose its rows' country."""
    path = Path(path)
    if not path.is_file():
        return [], {}
    prefix, exact, errors = [], {}, []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not {"place", "country"} <= set(reader.fieldnames or ()):
            sys.exit(f"{path}: the header must name place and country (and optionally match)")
        for n, row in enumerate(reader, start=2):
            place = (row.get("place") or "").strip().upper()
            country = (row.get("country") or "").strip().upper()
            match = (row.get("match") or "").strip().lower() or "prefix"
            if not place or norm_city(place) != place:
                errors.append(f"  line {n}: place {row.get('place')!r} must be letters A to Z, "
                              "spaces and hyphens")
            elif country not in ISO_ALPHA3:
                errors.append(f"  line {n}: country {row.get('country')!r} is not an ISO alpha-3 code")
            elif match not in ("prefix", "exact"):
                errors.append(f"  line {n}: match {row.get('match')!r} is neither prefix nor exact")
            elif match == "exact":
                exact[place] = country
            else:
                prefix.append((place, country))
    if errors:
        sys.exit(f"{path}: {len(errors)} bad line(s)\n" + "\n".join(errors))
    return prefix, exact


def load(path):
    """Set the tables to the shipped ones plus the places.csv at `path`
    (None for the shipped tables alone), in place."""
    global _loaded
    prefix, exact = read_places(path) if path is not None else ([], {})
    _loaded = True
    CITY_TABLE[:] = list(SHIPPED_CITY_TABLE) + prefix
    CITY_COUNTRY.clear()
    CITY_COUNTRY.update(SHIPPED_CITY_COUNTRY)
    CITY_COUNTRY.update(exact)


_loaded = False


def _ensure_loaded():
    """Read the data folder's places.csv the first time a place is looked up,
    not at import: importing a parser never fails on a personal file, and a
    caller (a test) that has already called load() keeps its tables."""
    if not _loaded:
        load(places_path())


def city_country(frag):
    _ensure_loaded()
    f = norm_city(frag)
    if len(f) < 3:
        return None
    for city, cc in CITY_TABLE:
        if city.startswith(f) or (len(city) >= 4 and f.startswith(city)):
            return cc
    return None


def split_country(name, fx_ccy, wallet):
    """Split the trailing city fragment off a merchant string when it is
    recognisable as a country; otherwise leave the string whole."""
    _ensure_loaded()
    name = name.strip().rstrip(",").strip()
    if "," not in name:
        if name == "MRT/BUS Total Fare":
            return name, "SGP"
        return name, WALLET_COUNTRY.get(wallet)
    head, _, tail = name.rpartition(",")
    tail_u = tail.strip().upper()
    cc = None
    if tail_u and "SINGAPORE".startswith(tail_u.split()[0].rstrip(".")) and len(tail_u) >= 1:
        cc = "SGP"
    elif tail_u in ("SGP", "SG"):
        cc = "SGP"
    elif fx_ccy in CCY_COUNTRY:
        cc = CCY_COUNTRY[fx_ccy]
    elif tail_u in CITY_COUNTRY:
        cc = CITY_COUNTRY[tail_u]
    elif tail_u in ALPHA3:
        cc = tail_u
    elif len(tail_u) == 2 and (tail_u in ALPHA2 or tail_u in US_STATES):
        cc = two_letter_country(tail_u, head.rpartition(",")[2])
    elif (c := city_country(tail_u)):
        cc = c
    if cc is None:
        return name, WALLET_COUNTRY.get(wallet)
    head = head.strip().rstrip(",").strip()
    return head or name, cc
