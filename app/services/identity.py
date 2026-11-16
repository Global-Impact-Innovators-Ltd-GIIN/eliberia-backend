"""Identity-document rules: which document a nationality may use and what its number looks like.

Passport formats are the published national formats where they are well known; other countries fall
back to the ICAO limit (6-9 letters and digits). The Liberian National ID pattern is a PLACEHOLDER
(8-14 letters/digits) until the National Identification Registry confirms the exact NIN format -
change it in one place below.
"""
import re

_RAW = ("AF:Afghanistan|AL:Albania|DZ:Algeria|AD:Andorra|AO:Angola|AG:Antigua and Barbuda|AR:Argentina|AM:Armenia|"
        "AU:Australia|AT:Austria|AZ:Azerbaijan|BS:Bahamas|BH:Bahrain|BD:Bangladesh|BB:Barbados|BY:Belarus|BE:Belgium|"
        "BZ:Belize|BJ:Benin|BT:Bhutan|BO:Bolivia|BA:Bosnia and Herzegovina|BW:Botswana|BR:Brazil|BN:Brunei|BG:Bulgaria|"
        "BF:Burkina Faso|BI:Burundi|CV:Cabo Verde|KH:Cambodia|CM:Cameroon|CA:Canada|CF:Central African Republic|TD:Chad|"
        "CL:Chile|CN:China|CO:Colombia|KM:Comoros|CG:Congo|CD:Congo (DRC)|CR:Costa Rica|CI:Cote d'Ivoire|HR:Croatia|"
        "CU:Cuba|CY:Cyprus|CZ:Czechia|DK:Denmark|DJ:Djibouti|DM:Dominica|DO:Dominican Republic|EC:Ecuador|EG:Egypt|"
        "SV:El Salvador|GQ:Equatorial Guinea|ER:Eritrea|EE:Estonia|SZ:Eswatini|ET:Ethiopia|FJ:Fiji|FI:Finland|FR:France|"
        "GA:Gabon|GM:Gambia|GE:Georgia|DE:Germany|GH:Ghana|GR:Greece|GD:Grenada|GT:Guatemala|GN:Guinea|GW:Guinea-Bissau|"
        "GY:Guyana|HT:Haiti|HN:Honduras|HU:Hungary|IS:Iceland|IN:India|ID:Indonesia|IR:Iran|IQ:Iraq|IE:Ireland|IL:Israel|"
        "IT:Italy|JM:Jamaica|JP:Japan|JO:Jordan|KZ:Kazakhstan|KE:Kenya|KI:Kiribati|KW:Kuwait|KG:Kyrgyzstan|LA:Laos|"
        "LV:Latvia|LB:Lebanon|LS:Lesotho|LR:Liberia|LY:Libya|LI:Liechtenstein|LT:Lithuania|LU:Luxembourg|MG:Madagascar|"
        "MW:Malawi|MY:Malaysia|MV:Maldives|ML:Mali|MT:Malta|MH:Marshall Islands|MR:Mauritania|MU:Mauritius|MX:Mexico|"
        "FM:Micronesia|MD:Moldova|MC:Monaco|MN:Mongolia|ME:Montenegro|MA:Morocco|MZ:Mozambique|MM:Myanmar|NA:Namibia|"
        "NR:Nauru|NP:Nepal|NL:Netherlands|NZ:New Zealand|NI:Nicaragua|NE:Niger|NG:Nigeria|KP:North Korea|"
        "MK:North Macedonia|NO:Norway|OM:Oman|PK:Pakistan|PW:Palau|PS:Palestine|PA:Panama|PG:Papua New Guinea|"
        "PY:Paraguay|PE:Peru|PH:Philippines|PL:Poland|PT:Portugal|QA:Qatar|RO:Romania|RU:Russia|RW:Rwanda|"
        "KN:Saint Kitts and Nevis|LC:Saint Lucia|VC:Saint Vincent and the Grenadines|WS:Samoa|SM:San Marino|"
        "ST:Sao Tome and Principe|SA:Saudi Arabia|SN:Senegal|RS:Serbia|SC:Seychelles|SL:Sierra Leone|SG:Singapore|"
        "SK:Slovakia|SI:Slovenia|SB:Solomon Islands|SO:Somalia|ZA:South Africa|KR:South Korea|SS:South Sudan|ES:Spain|"
        "LK:Sri Lanka|SD:Sudan|SR:Suriname|SE:Sweden|CH:Switzerland|SY:Syria|TW:Taiwan|TJ:Tajikistan|TZ:Tanzania|"
        "TH:Thailand|TL:Timor-Leste|TG:Togo|TO:Tonga|TT:Trinidad and Tobago|TN:Tunisia|TR:Turkiye|TM:Turkmenistan|"
        "TV:Tuvalu|UG:Uganda|UA:Ukraine|AE:United Arab Emirates|GB:United Kingdom|US:United States|UY:Uruguay|"
        "UZ:Uzbekistan|VU:Vanuatu|VA:Vatican City|VE:Venezuela|VN:Vietnam|YE:Yemen|ZM:Zambia|ZW:Zimbabwe")
COUNTRIES = dict(item.split(":", 1) for item in _RAW.split("|"))

NATIONAL_ID, PASSPORT = "National ID", "Passport"
DOC_TYPES = (NATIONAL_ID, PASSPORT)
HOME = "LR"

GENERIC_PASSPORT = (r"[A-Z0-9]{6,9}", "6 to 9 letters and digits", "AB1234567")
# country -> (pattern, plain-language format, example)
PASSPORT_RULES = {
    "LR": (r"[A-Z]{1,2}\d{6,7}", "1 or 2 letters followed by 6 or 7 digits", "PP0123456"),
    "US": (r"(\d{9}|[A-Z]\d{8})", "9 digits, or 1 letter followed by 8 digits", "123456789"),
    "GB": (r"\d{9}", "9 digits", "123456789"),
    "CA": (r"([A-Z]{2}\d{6}|[A-Z]\d{6}[A-Z]{2})", "2 letters and 6 digits", "AB123456"),
    "NG": (r"[A-Z]\d{8}", "1 letter followed by 8 digits", "A12345678"),
    "GH": (r"[A-Z]\d{7}", "1 letter followed by 7 digits", "G1234567"),
    "ZA": (r"[A-Z]\d{8}", "1 letter followed by 8 digits", "A12345678"),
    "IN": (r"[A-Z]\d{7}", "1 letter followed by 7 digits", "A1234567"),
    "CN": (r"[A-Z]{1,2}\d{7,8}", "1 or 2 letters followed by 7 or 8 digits", "E12345678"),
    "FR": (r"\d{2}[A-Z]{2}\d{5}", "2 digits, 2 letters, 5 digits", "12AB34567"),
    "DE": (r"[CFGHJKLMNPRTVWXYZ0-9]{9}", "9 characters (digits and the letters CFGHJKLMNPRTVWXYZ)", "C01X00T47"),
    "KE": (r"[A-Z]{1,2}\d{6,8}", "1 or 2 letters followed by 6 to 8 digits", "AK1234567"),
}
NATIONAL_ID_RULES = {
    "LR": (r"[A-Z0-9]{8,14}", "8 to 14 letters and digits, exactly as printed on the card", "LR12345678"),
}


def clean(number):
    return re.sub(r"[\s\-./]", "", str(number or "")).upper()


def rule_for(country, doc_type):
    if doc_type == NATIONAL_ID:
        return NATIONAL_ID_RULES.get(country)
    return PASSPORT_RULES.get(country, GENERIC_PASSPORT)


def validate(country, doc_type, number):
    """Return (clean_number, None) or (None, error message)."""
    if country not in COUNTRIES:
        return None, "Choose a nationality from the list."
    if doc_type not in DOC_TYPES:
        return None, "Choose the type of identity document."
    name = COUNTRIES[country]
    if doc_type == NATIONAL_ID and country != HOME:
        return None, f"A Liberian National ID can only be used by Liberian nationals. {name} nationals must use a passport."
    pattern, plain, example = rule_for(country, doc_type)
    value = clean(number)
    if not value:
        return None, f"Enter the {doc_type.lower()} number."
    if not re.fullmatch(pattern, value):
        article = "A" if doc_type == NATIONAL_ID else f"A {name}"
        return None, f"{article} {doc_type.lower()} number is {plain} (for example {example})."
    return value, None


def catalog():
    """Everything the form needs to guide and check input before it reaches the server."""
    return {
        "home": HOME,
        "doc_types": list(DOC_TYPES),
        "countries": [{"code": c, "name": n} for c, n in sorted(COUNTRIES.items(), key=lambda x: x[1])],
        "passport": {c: {"pattern": p, "format": f, "example": e} for c, (p, f, e) in PASSPORT_RULES.items()},
        "passport_default": dict(zip(("pattern", "format", "example"), GENERIC_PASSPORT)),
        "national_id": {c: {"pattern": p, "format": f, "example": e} for c, (p, f, e) in NATIONAL_ID_RULES.items()},
    }
