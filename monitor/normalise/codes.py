"""ISO 3166 and ISO 639 code conversion.

TED publishes three-letter codes (`ESP`, `SPA`) where the registry, the geography
weights and the lexicons all use two-letter ones (`ES`, `es`). Other sources will
do the same or the opposite, so the conversion lives here once rather than in each
mapper.

This is standards data, not configuration: nothing here is a keyword, a threshold,
a country weight or a placeholder, so rule 6 does not apply. A code that is not in
these maps raises rather than passing through, because a country silently reaching
the geography lookup as `ESP` would score at the default weight and nobody would
notice.

Scope: the countries and languages the pilot's sources actually emit. It is not a
complete ISO table and is not meant to be. Add a row when a source produces a code
that raises; that failure is the prompt.
"""

from __future__ import annotations

# ISO 3166-1 alpha-3 to alpha-2. EU and EEA, the 13 West African pilot countries,
# Ukraine and the Western Balkans, plus the non-European buyers TED itself carries
# (EU delegations publish notices for countries well outside Europe).
COUNTRY_ALPHA3 = {
    # EU, EEA, UK, Switzerland
    "AUT": "AT",
    "BEL": "BE",
    "BGR": "BG",
    "CHE": "CH",
    "CYP": "CY",
    "CZE": "CZ",
    "DEU": "DE",
    "DNK": "DK",
    "ESP": "ES",
    "EST": "EE",
    "FIN": "FI",
    "FRA": "FR",
    "GBR": "GB",
    "GRC": "GR",
    "HRV": "HR",
    "HUN": "HU",
    "IRL": "IE",
    "ISL": "IS",
    "ITA": "IT",
    "LIE": "LI",
    "LTU": "LT",
    "LUX": "LU",
    "LVA": "LV",
    "MLT": "MT",
    "NLD": "NL",
    "NOR": "NO",
    "POL": "PL",
    "PRT": "PT",
    "ROU": "RO",
    "SVK": "SK",
    "SVN": "SI",
    "SWE": "SE",
    # West Africa, geography weight 1.0
    "BEN": "BJ",
    "BFA": "BF",
    "CIV": "CI",
    "GMB": "GM",
    "GHA": "GH",
    "LBR": "LR",
    "MLI": "ML",
    "MRT": "MR",
    "NER": "NE",
    "NGA": "NG",
    "SEN": "SN",
    "SLE": "SL",
    "TGO": "TG",
    # Ukraine and the Western Balkans, geography weight 0.8. Kosovo is XK.
    "UKR": "UA",
    "ALB": "AL",
    "BIH": "BA",
    "XKX": "XK",
    "MNE": "ME",
    "MKD": "MK",
    "SRB": "RS",
    # Others TED carries. EU delegations publish notices for countries well outside
    # Europe, so this is not the pilot's 47 countries. Checked against all 1,449
    # notices the query matched on 2026-09-11: 37 distinct buyer countries, all
    # covered.
    "ARG": "AR",
    "MWI": "MW",
    "ZMB": "ZM",
    "USA": "US",
    "CAN": "CA",
    "TUR": "TR",
    "MAR": "MA",
    "TUN": "TN",
    "EGY": "EG",
    "ZAF": "ZA",
    "IND": "IN",
    "AUS": "AU",
    "JPN": "JP",
    "ISR": "IL",
    "MDA": "MD",
    "GEO": "GE",
    "ARM": "AM",
    "AZE": "AZ",
}

# ISO 639-2/B to ISO 639-1. Only the lexicons' languages matter for filtering, but
# every language a source emits has to convert or the notice cannot be classified.
LANGUAGE_ALPHA3 = {
    "BUL": "bg",
    "CES": "cs",
    "DAN": "da",
    "DEU": "de",
    "ELL": "el",
    "ENG": "en",
    "EST": "et",
    "FIN": "fi",
    "FRA": "fr",
    "GLE": "ga",
    "HRV": "hr",
    "HUN": "hu",
    "ISL": "is",
    "ITA": "it",
    "LAV": "lv",
    "LIT": "lt",
    "MLT": "mt",
    "NLD": "nl",
    "NOR": "no",
    "POL": "pl",
    "POR": "pt",
    "RON": "ro",
    "SLK": "sk",
    "SLV": "sl",
    "SPA": "es",
    "SWE": "sv",
    "UKR": "uk",
    "SQI": "sq",
    "BOS": "bs",
    "MKD": "mk",
    "SRP": "sr",
    "ARA": "ar",
    "TUR": "tr",
    "RUS": "ru",
}


def country_alpha2(code: str) -> str:
    """'ESP' -> 'ES'. A two-letter code passes through. Anything else raises."""
    value = code.strip().upper()
    if len(value) == 2:
        return value
    if value not in COUNTRY_ALPHA3:
        raise ValueError(f"unknown country code {code!r}; add it to COUNTRY_ALPHA3 in monitor/normalise/codes.py")
    return COUNTRY_ALPHA3[value]


def language_alpha2(code: str) -> str:
    """'SPA' -> 'es'. A two-letter code passes through lowercased. Anything else raises."""
    value = code.strip().upper()
    if len(value) == 2:
        return value.lower()
    if value not in LANGUAGE_ALPHA3:
        raise ValueError(f"unknown language code {code!r}; add it to LANGUAGE_ALPHA3 in monitor/normalise/codes.py")
    return LANGUAGE_ALPHA3[value]


# ISO 3166-1 alpha-2 to the country's English short name, for appendix E's Shipping
# Country column. The candidates table carries the two-letter code because every
# lookup in the pipeline keys on it; the record builder needs the name because the
# CRM's field is a picklist of names and an import that writes `GH` matches nothing.
#
# Scope is the same as COUNTRY_ALPHA3's values, plus the two-letter codes the
# registry uses directly. Same rule as above: a code that is not here raises.
COUNTRY_NAMES = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "ES": "Spain",
    "EE": "Estonia",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IS": "Iceland",
    "IT": "Italy",
    "LI": "Liechtenstein",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MT": "Malta",
    "NL": "Netherlands",
    "NO": "Norway",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "SE": "Sweden",
    # West Africa
    "BJ": "Benin",
    "BF": "Burkina Faso",
    "CI": "Côte d'Ivoire",
    "GM": "Gambia",
    "GH": "Ghana",
    "LR": "Liberia",
    "ML": "Mali",
    "MR": "Mauritania",
    "NE": "Niger",
    "NG": "Nigeria",
    "SN": "Senegal",
    "SL": "Sierra Leone",
    "TG": "Togo",
    # Ukraine and the Western Balkans
    "UA": "Ukraine",
    "AL": "Albania",
    "BA": "Bosnia and Herzegovina",
    "XK": "Kosovo",
    "ME": "Montenegro",
    "MK": "North Macedonia",
    "RS": "Serbia",
    # Others the sources carry
    "AR": "Argentina",
    "MW": "Malawi",
    "ZM": "Zambia",
    "US": "United States",
    "CA": "Canada",
    "TR": "Türkiye",
    "MA": "Morocco",
    "TN": "Tunisia",
    "EG": "Egypt",
    "ZA": "South Africa",
    "IN": "India",
    "AU": "Australia",
    "JP": "Japan",
    "IL": "Israel",
    "MD": "Moldova",
    "GE": "Georgia",
    "AM": "Armenia",
    "AZ": "Azerbaijan",
    # Not a country: TED's own notices carry EU where the buyer is an EU body, and
    # a donor source covering many countries carries `multi`. Both reach the record
    # builder and neither is a Shipping Country a person would key by hand.
    "EU": "European Union",
}


def country_name(code: str) -> str:
    """'GH' -> 'Ghana'. Anything unknown raises rather than exporting a bare code."""
    value = country_alpha2(code)
    if value not in COUNTRY_NAMES:
        raise ValueError(f"unknown country code {code!r}; add it to COUNTRY_NAMES in monitor/normalise/codes.py")
    return COUNTRY_NAMES[value]
