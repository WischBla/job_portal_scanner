"""LocationNormalizer - decides whether a job posting is a Swiss job.

This module is the single place in the codebase that is allowed to answer the
question "is this Switzerland?".  Neither the source adapters nor the frontend
may re-implement any part of it.

Decision model
--------------
The structured ``location`` field is the primary evidence.  Title and
description are only *secondary* evidence and are used exclusively when the
structured field is inconclusive (empty, or a region blanket term such as
"Europe" / "EMEA" / "DACH").  Secondary evidence must be an explicit
eligibility/residence statement about Switzerland - generic terms such as
"European", "DACH" or "German speaking" never establish Swiss eligibility.
"""

import re
import unicodedata

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# Country-level tokens that unambiguously mean Switzerland.
SWISS_COUNTRY_TOKENS = {
    'switzerland', 'swiss', 'schweiz', 'suisse', 'svizzera', 'svizra',
    'helvetia', 'confoederatio helvetica',
}
# Uppercase ISO-ish abbreviations, only accepted as *whole* tokens.
SWISS_COUNTRY_CODES = {'CH', 'CHE', 'SUI'}

# Canonical Swiss city -> accepted spelling variants.
SWISS_CITY_ALIASES = {
    'Zurich': ['zurich', 'zuerich', 'zurigo', 'zh-zurich'],
    'Geneva': ['geneva', 'geneve', 'genf', 'ginevra'],
    'Basel': ['basel', 'basle', 'bale', 'basilea', 'basel-stadt', 'basel stadt'],
    'Bern': ['bern', 'berne', 'berna'],
    'Lausanne': ['lausanne'],
    'Winterthur': ['winterthur'],
    'Luzern': ['luzern', 'lucerne', 'lucerna'],
    'St. Gallen': ['st gallen', 'st. gallen', 'sankt gallen', 'saint gallen', 'st.gallen', 'stgallen'],
    'Lugano': ['lugano'],
    'Biel': ['biel', 'bienne', 'biel/bienne'],
    'Thun': ['thun'],
    'Koeniz': ['koniz', 'koeniz'],
    'Fribourg': ['fribourg', 'friburgo'],
    'Schaffhausen': ['schaffhausen', 'schaffhouse'],
    'Chur': ['chur', 'coira'],
    'Neuchatel': ['neuchatel', 'neuenburg'],
    'Uster': ['uster'],
    'Sion': ['sion', 'sitten'],
    'Emmen': ['emmen'],
    'Zug': ['zug', 'zoug'],
    'Yverdon': ['yverdon', 'yverdon-les-bains'],
    'Duebendorf': ['dubendorf', 'duebendorf'],
    'Dietikon': ['dietikon'],
    'Montreux': ['montreux'],
    'Frauenfeld': ['frauenfeld'],
    'Wetzikon': ['wetzikon'],
    'Baar': ['baar'],
    'Kreuzlingen': ['kreuzlingen'],
    'Rapperswil': ['rapperswil', 'rapperswil-jona'],
    'Wil': ['wil sg', 'wil (sg)'],
    'Aarau': ['aarau'],
    'Allschwil': ['allschwil'],
    'Nyon': ['nyon'],
    'Vevey': ['vevey'],
    'Kloten': ['kloten'],
    'Opfikon': ['opfikon', 'glattbrugg'],
    'Wallisellen': ['wallisellen'],
    'Horgen': ['horgen'],
    'Olten': ['olten'],
    'Solothurn': ['solothurn', 'soleure'],
    'Burgdorf': ['burgdorf'],
    'Langenthal': ['langenthal'],
    'Interlaken': ['interlaken'],
    'Davos': ['davos'],
    'Zermatt': ['zermatt'],
    'Locarno': ['locarno'],
    'Bellinzona': ['bellinzona'],
    'Martigny': ['martigny'],
    'Morges': ['morges'],
    'Carouge': ['carouge'],
    'Lancy': ['lancy'],
    'Meyrin': ['meyrin'],
    'Bulle': ['bulle'],
    'Sierre': ['sierre'],
    'Delemont': ['delemont', 'delsberg'],
    'Herisau': ['herisau'],
    'Gossau': ['gossau'],
    'Einsiedeln': ['einsiedeln'],
    'Schwyz': ['schwyz'],
    'Altdorf': ['altdorf'],
    'Stans': ['stans'],
    'Sarnen': ['sarnen'],
    'Glarus': ['glarus'],
    'Liestal': ['liestal'],
    'Pratteln': ['pratteln'],
    'Muttenz': ['muttenz'],
    'Binningen': ['binningen'],
    'Riehen': ['riehen'],
    'Adliswil': ['adliswil'],
    'Thalwil': ['thalwil'],
    'Kuesnacht': ['kusnacht', 'kuesnacht'],
    'Zollikon': ['zollikon'],
    'Buelach': ['bulach', 'buelach'],
    'Regensdorf': ['regensdorf'],
    'Schlieren': ['schlieren'],
    'Wadenswil': ['wadenswil', 'waedenswil'],
    'Horw': ['horw'],
    'Kriens': ['kriens'],
    'Ebikon': ['ebikon'],
    'Rotkreuz': ['rotkreuz'],
    'Cham': ['cham zg', 'cham (zg)'],
    'Steinhausen': ['steinhausen'],
    'Sursee': ['sursee'],
    'Hochdorf': ['hochdorf'],
    'Vernier': ['vernier'],
    'Renens': ['renens'],
    'Onex': ['onex'],
    'Pully': ['pully'],
    'Monthey': ['monthey'],
    'Arbon': ['arbon'],
}

# Canonical canton -> full-name variants.  Cantons are strong Swiss evidence.
SWISS_CANTON_ALIASES = {
    'Zurich': ['kanton zurich', 'kanton zuerich', 'canton of zurich', 'canton zurich'],
    'Bern': ['kanton bern', 'canton of bern', 'canton de berne'],
    'Luzern': ['kanton luzern', 'canton of lucerne'],
    'Uri': ['kanton uri', 'canton of uri'],
    'Schwyz': ['kanton schwyz', 'canton of schwyz'],
    'Obwalden': ['obwalden', 'obwald'],
    'Nidwalden': ['nidwalden', 'nidwald'],
    'Glarus': ['kanton glarus'],
    'Zug': ['kanton zug', 'canton of zug'],
    'Fribourg': ['kanton freiburg', 'canton de fribourg'],
    'Solothurn': ['kanton solothurn'],
    'Basel-Stadt': ['basel-stadt', 'basel stadt', 'basle-city'],
    'Basel-Landschaft': ['basel-landschaft', 'basel landschaft', 'baselland'],
    'Schaffhausen': ['kanton schaffhausen'],
    'Appenzell': ['appenzell', 'appenzell ausserrhoden', 'appenzell innerrhoden'],
    'St. Gallen': ['kanton st. gallen', 'kanton st gallen', 'canton of st. gallen'],
    'Graubuenden': ['graubunden', 'graubuenden', 'grisons', 'grigioni'],
    'Aargau': ['aargau', 'argovie'],
    'Thurgau': ['thurgau', 'thurgovie'],
    'Ticino': ['ticino', 'tessin'],
    'Vaud': ['vaud', 'waadt'],
    'Valais': ['valais', 'wallis'],
    'Neuchatel': ['kanton neuenburg', 'canton de neuchatel'],
    'Geneva': ['kanton genf', 'canton de geneve', 'canton of geneva'],
    'Jura': ['kanton jura', 'canton du jura'],
}

# Two-letter canton codes.  Deliberately treated as *corroborating evidence
# only* - "BE" is also Belgium, "FR" is also France, "AG" is a company suffix.
# They refine the region of an already-Swiss job; they never make one Swiss.
SWISS_CANTON_CODES = {
    'ZH': 'Zurich', 'BE': 'Bern', 'LU': 'Luzern', 'UR': 'Uri', 'SZ': 'Schwyz',
    'OW': 'Obwalden', 'NW': 'Nidwalden', 'GL': 'Glarus', 'ZG': 'Zug',
    'FR': 'Fribourg', 'SO': 'Solothurn', 'BS': 'Basel-Stadt', 'BL': 'Basel-Landschaft',
    'SH': 'Schaffhausen', 'AR': 'Appenzell', 'AI': 'Appenzell', 'SG': 'St. Gallen',
    'GR': 'Graubuenden', 'AG': 'Aargau', 'TG': 'Thurgau', 'TI': 'Ticino',
    'VD': 'Vaud', 'VS': 'Valais', 'NE': 'Neuchatel', 'GE': 'Geneva', 'JU': 'Jura',
}

# Foreign country names (any language variant we are likely to meet).
FOREIGN_COUNTRY_ALIASES = {
    'Germany': ['germany', 'deutschland', 'allemagne', 'germania', 'german federal republic'],
    'Austria': ['austria', 'osterreich', 'oesterreich', 'autriche'],
    'France': ['france', 'frankreich', 'francia'],
    'Italy': ['italy', 'italien', 'italia', 'italie'],
    'Netherlands': ['netherlands', 'the netherlands', 'holland', 'nederland', 'niederlande', 'pays-bas'],
    'Belgium': ['belgium', 'belgien', 'belgique', 'belgie'],
    'Luxembourg': ['luxembourg', 'luxemburg'],
    'Liechtenstein': ['liechtenstein'],
    'United Kingdom': ['united kingdom', 'great britain', 'england', 'scotland', 'wales',
                       'northern ireland', 'grossbritannien', 'uk'],
    'Ireland': ['ireland', 'irland'],
    'Spain': ['spain', 'spanien', 'espana', 'espagne'],
    'Portugal': ['portugal'],
    'Poland': ['poland', 'polen', 'polska'],
    'Czechia': ['czechia', 'czech republic', 'tschechien'],
    'Slovakia': ['slovakia', 'slowakei'],
    'Slovenia': ['slovenia', 'slowenien'],
    'Croatia': ['croatia', 'kroatien'],
    'Hungary': ['hungary', 'ungarn'],
    'Romania': ['romania', 'rumanien', 'rumaenien'],
    'Bulgaria': ['bulgaria', 'bulgarien'],
    'Greece': ['greece', 'griechenland'],
    'Turkey': ['turkey', 'turkiye', 'turkei', 'tuerkei'],
    'Sweden': ['sweden', 'schweden', 'sverige'],
    'Norway': ['norway', 'norwegen'],
    'Denmark': ['denmark', 'danemark', 'daenemark'],
    'Finland': ['finland', 'finnland'],
    'Estonia': ['estonia', 'estland'],
    'Latvia': ['latvia', 'lettland'],
    'Lithuania': ['lithuania', 'litauen'],
    'Ukraine': ['ukraine'],
    'Serbia': ['serbia', 'serbien'],
    'United States': ['united states', 'usa', 'u.s.', 'u.s.a.', 'america'],
    'Canada': ['canada', 'kanada'],
    'Mexico': ['mexico'],
    'Brazil': ['brazil', 'brasilien', 'brasil'],
    'Argentina': ['argentina', 'argentinien'],
    'India': ['india', 'indien'],
    'Singapore': ['singapore', 'singapur'],
    'Japan': ['japan'],
    'China': ['china'],
    'South Korea': ['south korea', 'korea'],
    'Australia': ['australia', 'australien'],
    'New Zealand': ['new zealand', 'neuseeland'],
    'Israel': ['israel'],
    'United Arab Emirates': ['united arab emirates', 'uae', 'dubai', 'abu dhabi'],
    'South Africa': ['south africa', 'sudafrika', 'suedafrika'],
    'Egypt': ['egypt', 'agypten'],
    'Nigeria': ['nigeria'],
    'Kenya': ['kenya'],
    'Philippines': ['philippines', 'philippinen'],
    'Indonesia': ['indonesia'],
    'Vietnam': ['vietnam'],
    'Thailand': ['thailand'],
    'Pakistan': ['pakistan'],
    'Bangladesh': ['bangladesh'],
    'Colombia': ['colombia', 'kolumbien'],
    'Chile': ['chile'],
    'Peru': ['peru'],
    'Russia': ['russia', 'russland'],
    'Belarus': ['belarus'],
    'Moldova': ['moldova'],
    'Georgia': ['georgia', 'georgien'],
    'Armenia': ['armenia'],
    'Morocco': ['morocco', 'marokko'],
    'Tunisia': ['tunisia', 'tunesien'],
}

# Foreign cities.  Only needed to *reject* confidently; a missing city here
# simply means the job stays "unknown" rather than being wrongly accepted.
FOREIGN_CITY_ALIASES = {
    'Germany': ['bochum', 'duisburg', 'wuppertal', 'gelsenkirchen', 'monchengladbach', 'moenchengladbach', 'chemnitz', 'kiel', 'halle', 'magdeburg', 'krefeld', 'luebeck', 'lubeck', 'oberhausen', 'rostock', 'erfurt', 'jena', 'paderborn', 'siegen', 'heilbronn', 'boblingen', 'boeblingen', 'sindelfingen', 'ratingen', 'eschborn', 'unterfoehring', 'unterfohring', 'garching', 'friedrichshafen', 'berlin', 'munich', 'munchen', 'muenchen', 'frankfurt', 'hamburg', 'cologne',
                'koln', 'koeln', 'stuttgart', 'dusseldorf', 'duesseldorf', 'leipzig', 'dresden',
                'nuremberg', 'nurnberg', 'nuernberg', 'hannover', 'hanover', 'bremen', 'essen',
                'dortmund', 'bonn', 'karlsruhe', 'mannheim', 'mainz', 'wiesbaden', 'augsburg',
                'heidelberg', 'ulm', 'kassel', 'munster', 'muenster', 'aachen', 'bielefeld',
                'braunschweig', 'regensburg', 'wurzburg', 'wuerzburg', 'potsdam', 'darmstadt',
                'erlangen', 'freiburg im breisgau', 'walldorf', 'ingolstadt', 'wolfsburg'],
    'Austria': ['vienna', 'wien', 'graz', 'linz', 'salzburg', 'innsbruck', 'klagenfurt'],
    'France': ['annecy', 'rennes', 'nancy', 'metz', 'dijon', 'tours', 'orleans', 'angers', 'clermont-ferrand', 'aix-en-provence', 'sophia-antipolis', 'valbonne', 'paris', 'lyon', 'marseille', 'toulouse', 'lille', 'nice', 'bordeaux',
               'strasbourg', 'nantes', 'montpellier', 'grenoble', 'sophia antipolis'],
    'Italy': ['milan', 'milano', 'mailand', 'rome', 'roma', 'turin', 'torino', 'bologna',
              'florence', 'firenze', 'naples', 'napoli', 'venice', 'genoa'],
    'Netherlands': ['amsterdam', 'rotterdam', 'utrecht', 'eindhoven', 'the hague', 'den haag',
                    'groningen', 'delft', 'hilversum'],
    'Belgium': ['brussels', 'brussel', 'bruxelles', 'brussel', 'antwerp', 'antwerpen', 'ghent', 'leuven'],
    'United Kingdom': ['london', 'manchester', 'birmingham', 'edinburgh', 'glasgow', 'bristol',
                       'leeds', 'cambridge', 'oxford', 'reading', 'belfast'],
    'Ireland': ['dublin', 'cork', 'galway'],
    'Spain': ['madrid', 'barcelona', 'valencia', 'seville', 'malaga', 'bilbao', 'zaragoza'],
    'Portugal': ['lisbon', 'lisboa', 'porto', 'braga'],
    'Poland': ['warsaw', 'warszawa', 'warschau', 'krakow', 'cracow', 'wroclaw', 'gdansk', 'poznan', 'lodz'],
    'Czechia': ['prague', 'prag', 'praha', 'brno', 'ostrava'],
    'Hungary': ['budapest', 'debrecen'],
    'Romania': ['bucharest', 'bukarest', 'cluj', 'timisoara', 'iasi'],
    'Bulgaria': ['sofia', 'plovdiv', 'varna'],
    'Greece': ['athens', 'athen', 'thessaloniki'],
    'Sweden': ['stockholm', 'gothenburg', 'goteborg', 'malmo'],
    'Norway': ['oslo', 'bergen', 'trondheim'],
    'Denmark': ['copenhagen', 'kopenhagen', 'kobenhavn', 'aarhus'],
    'Finland': ['helsinki', 'espoo', 'tampere'],
    'Estonia': ['tallinn', 'tartu'],
    'Latvia': ['riga'],
    'Lithuania': ['vilnius', 'kaunas'],
    'Ukraine': ['kyiv', 'kiev', 'lviv'],
    'Serbia': ['belgrade', 'novi sad'],
    'Croatia': ['zagreb', 'split'],
    'Slovenia': ['ljubljana', 'maribor'],
    'Slovakia': ['bratislava', 'kosice'],
    'Turkey': ['istanbul', 'ankara', 'izmir'],
    'United States': ['san mateo', 'sunnyvale', 'santa clara', 'redmond', 'bellevue', 'cupertino', 'menlo park', 'redwood city', 'arlington', 'herndon', 'reston', 'irvine', 'san antonio', 'columbus', 'minneapolis', 'salt lake city', 'pittsburgh', 'charlotte', 'tampa', 'orlando', 'new york', 'san francisco', 'seattle', 'austin', 'boston', 'chicago',
                      'los angeles', 'denver', 'atlanta', 'miami', 'dallas', 'houston',
                      'san jose', 'palo alto', 'mountain view', 'washington dc', 'philadelphia',
                      'phoenix', 'portland', 'san diego', 'raleigh', 'nashville', 'detroit'],
    'Canada': ['toronto', 'vancouver', 'montreal', 'ottawa', 'calgary', 'waterloo'],
    'India': ['bangalore', 'bengaluru', 'mumbai', 'delhi', 'new delhi', 'hyderabad', 'pune',
              'chennai', 'gurgaon', 'gurugram', 'noida', 'kolkata'],
    'Singapore': ['singapore'],
    'Japan': ['tokyo', 'osaka', 'kyoto'],
    'China': ['shanghai', 'beijing', 'shenzhen', 'hong kong'],
    'South Korea': ['seoul'],
    'Australia': ['sydney', 'melbourne', 'brisbane', 'perth', 'canberra'],
    'New Zealand': ['auckland', 'wellington'],
    'Israel': ['herzliya', 'tel aviv', 'jerusalem', 'haifa'],
    'United Arab Emirates': ['dubai', 'abu dhabi'],
    'Brazil': ['belo horizonte', 'sao paulo', 'rio de janeiro'],
    'Mexico': ['monterrey', 'mexico city', 'guadalajara'],
    'Argentina': ['buenos aires'],
    'South Africa': ['cape town', 'johannesburg'],
    'Philippines': ['taguig', 'manila', 'cebu'],
    'Vietnam': ['ho chi minh city', 'hanoi', 'da nang'],
    'Malaysia': ['kuala lumpur', 'penang'],
    'Indonesia': ['jakarta'],
    'Thailand': ['bangkok'],
}

# Blanket region terms.  "Europe", "EU", "EMEA" and "DACH" are explicitly NOT
# Switzerland - a job carrying only one of these needs description evidence.
REGION_TERMS = {
    'europe', 'european', 'european union', 'eu', 'eea', 'efta', 'emea', 'dach',
    'dach region', 'd-a-ch', 'benelux', 'nordics', 'nordic', 'scandinavia',
    'central europe', 'eastern europe', 'western europe', 'southern europe',
    'northern europe', 'cee', 'apac', 'latam', 'americas', 'north america',
    'south america', 'middle east', 'africa', 'asia', 'asia pacific',
    'worldwide', 'world wide', 'global', 'globally', 'anywhere', 'international',
    'multiple locations', 'various locations', 'eu remote', 'remote europe',
    'europe remote', 'eu only', 'emea remote', 'remote emea', 'remote eu',
    'remote worldwide', 'remote global', 'remote anywhere', 'anywhere in the world',
    'germany or austria', 'uk or eu',
}

REMOTE_TERMS = [
    'remote', 'fully remote', 'work from home', 'home office', 'homeoffice',
    'telecommute', 'telearbeit', 'distributed', '100% remote', 'remote-first',
    'remote first', 'ortsunabhangig',
]
HYBRID_TERMS = [
    'hybrid', 'hybride', 'hybrid work', 'hybrid-model', 'hybrid model',
    'partially remote', 'teilweise remote', 'flexible office', '2 days in office',
    '3 days in office', 'days per week in the office', 'days in the office',
    'tage im buro', 'tage pro woche im buro',
]
ONSITE_TERMS = ['on-site', 'onsite', 'on site', 'office-based', 'vor ort', 'prasenz', 'in-office']

# Words that turn a mention of Switzerland in free text into real evidence that
# the *employer accepts a candidate based in Switzerland*.
ELIGIBILITY_CUES = [
    'reside', 'residing', 'residence', 'resident', 'located', 'location', 'based',
    'live', 'living', 'work from', 'work in', 'working from', 'hired', 'hiring',
    'employment', 'employed', 'employer of record', 'candidate', 'candidates',
    'applicant', 'applicants', 'eligible', 'eligibility', 'authorized', 'authorised',
    'authorization', 'authorisation', 'permit', 'right to work', 'relocate',
    'relocation', 'office', 'offices', 'hub', 'headquarter', 'headquarters',
    'entity', 'payroll', 'onsite', 'on-site', 'presence', 'timezone', 'time zone',
    'anstellung', 'wohnsitz', 'arbeitsort', 'standort', 'niederlassung', 'buro',
]
# Phrases that explicitly *exclude* Switzerland; they veto positive evidence.
NEGATIVE_PATTERNS = [
    r'not\s+(?:available|open|eligible|possible)[^.]{0,40}switzerland',
    r'(?:excluding|except|apart from|outside(?: of)?)\s+switzerland',
    r'switzerland[^.]{0,20}(?:not|cannot|can\'t|unfortunately)\s+(?:be|available|supported|considered)',
    r'no\s+(?:swiss|switzerland)\s+(?:entity|payroll|contracts?)',
]

EVIDENCE_WINDOW = 90  # characters around a Swiss token searched for a cue word


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def fold(value):
    """Lowercase + strip diacritics so 'Zürich' and 'Zuerich' compare equal."""
    text = str(value or '')
    text = text.replace('ß', 'ss').replace('ẞ', 'ss')
    # German transliteration must happen before the diacritics are dropped,
    # otherwise 'ü' would become 'u' and 'Zürich'/'Zuerich' would diverge.
    text = (text.replace('ü', 'ue').replace('Ü', 'Ue')
                .replace('ö', 'oe').replace('Ö', 'Oe')
                .replace('ä', 'ae').replace('Ä', 'Ae'))
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    # Both spellings collapse onto the same key: ue -> u, oe -> o, ae -> a.
    text = text.replace('ue', 'u').replace('oe', 'o').replace('ae', 'a')
    return re.sub(r'\s+', ' ', text).strip()


def _token_key(value):
    """Fold and drop punctuation - used for whole-string comparisons."""
    return re.sub(r'[^a-z0-9 ]+', ' ', fold(value)).strip()


def _build_lookup(alias_map):
    out = {}
    for canonical, aliases in alias_map.items():
        for alias in aliases:
            out[_token_key(alias)] = canonical
    return out


def _build_city_lookup(alias_map):
    """alias -> (display city name, country) so a foreign city also yields its country."""
    out = {}
    for country, cities in alias_map.items():
        for alias in cities:
            out[_token_key(alias)] = (alias.title(), country)
    return out


_SWISS_CITY_LOOKUP = _build_lookup(SWISS_CITY_ALIASES)
_SWISS_CANTON_LOOKUP = _build_lookup(SWISS_CANTON_ALIASES)
_FOREIGN_COUNTRY_LOOKUP = _build_lookup(FOREIGN_COUNTRY_ALIASES)
_FOREIGN_CITY_LOOKUP = _build_city_lookup(FOREIGN_CITY_ALIASES)
_SWISS_COUNTRY_KEYS = {_token_key(t) for t in SWISS_COUNTRY_TOKENS}
_REGION_KEYS = {_token_key(t) for t in REGION_TERMS}

# Regex that finds any Swiss token inside free text (country names + cities).
_SWISS_FREETEXT_RE = re.compile(
    r'\b(' + '|'.join(sorted(
        {re.escape(k) for k in _SWISS_COUNTRY_KEYS} |
        {re.escape(k) for k in _SWISS_CITY_LOOKUP} |
        {re.escape(k) for k in _SWISS_CANTON_LOOKUP},
        key=len, reverse=True)) + r')\b'
)
_CUE_RE = re.compile(r'\b(' + '|'.join(re.escape(_token_key(c)) for c in ELIGIBILITY_CUES) + r')')
_NEGATIVE_RES = [re.compile(p) for p in NEGATIVE_PATTERNS]


def split_location(raw):
    """Split a raw location string into comparable fragments."""
    text = str(raw or '')
    text = re.sub(r'\b(?:or|and|und|oder|ou)\b', ',', text, flags=re.IGNORECASE)
    parts = re.split(r'[,;/|()\[\]\n\t]+|\s+-\s+|–|•', text)
    return [p.strip() for p in parts if p and p.strip()]


class LocationVerdict(dict):
    """Plain dict so the result serialises straight into JSON / SQLite."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


# --------------------------------------------------------------------------
# Fragment classification
# --------------------------------------------------------------------------

def _classify_fragment(fragment):
    """Return (kind, canonical) for a single location fragment."""
    key = _token_key(fragment)
    if not key:
        return ('empty', None)
    raw_upper = re.sub(r'[^A-Za-z]', '', str(fragment)).upper()

    if key in _SWISS_COUNTRY_KEYS:
        return ('swiss_country', 'Switzerland')
    if raw_upper in SWISS_COUNTRY_CODES and len(str(fragment).strip()) <= 4:
        return ('swiss_country', 'Switzerland')
    if key in _SWISS_CITY_LOOKUP:
        return ('swiss_city', _SWISS_CITY_LOOKUP[key])
    if key in _SWISS_CANTON_LOOKUP:
        return ('swiss_canton', _SWISS_CANTON_LOOKUP[key])
    if key in _FOREIGN_COUNTRY_LOOKUP:
        return ('foreign_country', _FOREIGN_COUNTRY_LOOKUP[key])
    if key in _FOREIGN_CITY_LOOKUP:
        return ('foreign_city', _FOREIGN_CITY_LOOKUP[key])  # (city, country)
    if key in _REGION_KEYS:
        return ('region', key)
    if raw_upper in SWISS_CANTON_CODES and len(str(fragment).strip()) <= 3:
        # Corroborating only - "BE"/"FR"/"AG" are far too ambiguous alone.
        return ('canton_code', SWISS_CANTON_CODES[raw_upper])
    if any(_token_key(term) == key for term in REMOTE_TERMS):
        return ('remote', key)

    # Multi-word fragment: look for embedded known names ("Greater Zurich Area").
    for lookup, kind in ((_SWISS_CITY_LOOKUP, 'swiss_city'),
                         (_SWISS_CANTON_LOOKUP, 'swiss_canton'),
                         (_FOREIGN_CITY_LOOKUP, 'foreign_city'),
                         (_FOREIGN_COUNTRY_LOOKUP, 'foreign_country')):
        for alias, canonical in lookup.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', key):
                return (kind, canonical)
    # 'Greater Zurich Area' style strings can still be region-flavoured; Swiss
    # tokens were already checked above, so fall through to the region scan.
    for swiss in _SWISS_COUNTRY_KEYS:
        if re.search(r'\b' + re.escape(swiss) + r'\b', key):
            return ('swiss_country', 'Switzerland')
    for region in _REGION_KEYS:
        if re.search(r'\b' + re.escape(region) + r'\b', key):
            return ('region', region)
    return ('unknown', str(fragment).strip())


# --------------------------------------------------------------------------
# Free-text (secondary) evidence
# --------------------------------------------------------------------------

def find_switzerland_evidence(text):
    """Look for an explicit statement that Switzerland is acceptable.

    Returns (found: bool, quote: str).  Generic terms such as "European",
    "DACH" or "German speaking" never match because only real Swiss tokens are
    searched for, and the token still has to sit next to an eligibility cue.
    """
    folded = _token_key(text)
    if not folded:
        return (False, '')
    for pattern in _NEGATIVE_RES:
        if pattern.search(folded):
            return (False, '')
    for match in _SWISS_FREETEXT_RE.finditer(folded):
        start = max(0, match.start() - EVIDENCE_WINDOW)
        end = min(len(folded), match.end() + EVIDENCE_WINDOW)
        window = folded[start:end]
        if _CUE_RE.search(window):
            quote = re.sub(r'\s+', ' ', window).strip()
            return (True, quote[:220])
    return (False, '')


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

class LocationNormalizer:
    """Turns raw source location data into a structured, decidable verdict."""

    def normalize(self, raw_location, title='', description='', remote_hint=None):
        fragments = split_location(raw_location)
        classified = [_classify_fragment(f) for f in fragments]
        kinds = [k for k, _ in classified]

        swiss_cities = [v for k, v in classified if k == 'swiss_city']
        swiss_cantons = [v for k, v in classified if k == 'swiss_canton']
        canton_codes = [v for k, v in classified if k == 'canton_code']
        foreign_countries = [v for k, v in classified if k == 'foreign_country']
        foreign_city_pairs = [v for k, v in classified if k == 'foreign_city']
        foreign_cities = [c for c, _ in foreign_city_pairs]
        foreign_countries = foreign_countries + [
            c for _, c in foreign_city_pairs if c not in foreign_countries]
        regions = [v for k, v in classified if k == 'region']
        has_swiss_country = 'swiss_country' in kinds

        has_city = bool([k for k in kinds if k in ('swiss_city', 'foreign_city')])
        model = self._work_model(raw_location, title, description, remote_hint, has_city)

        swiss_signal = bool(swiss_cities or swiss_cantons or has_swiss_country)
        # A canton code counts only when something else already points to
        # Switzerland, or when it sits next to an otherwise unknown town.
        if not swiss_signal and canton_codes and not (foreign_countries or foreign_cities or regions):
            swiss_signal = bool([k for k in kinds if k == 'unknown'])

        country = None
        confidence = 'none'
        eligible = False
        reason = ''
        evidence = ''

        if swiss_signal:
            country = 'Switzerland'
            eligible = True
            confidence = 'high'
            reason = 'Structured location names a Swiss city, canton or the country itself.'
            if foreign_countries or foreign_cities:
                # "Switzerland or Germany" - Switzerland is one of the options.
                reason = 'Structured location lists Switzerland next to other countries.'
        elif foreign_countries or foreign_cities:
            country = (foreign_countries or [None])[0]
            confidence = 'high'
            reason = 'Structured location names {0}, which is not Switzerland.'.format(country or 'a foreign country')
        elif regions:
            # Blanket region - needs explicit evidence from the free text.
            pretty = ', '.join(sorted({r.upper() if len(r) <= 4 else r.title() for r in regions}))
            found, quote = find_switzerland_evidence('{0}\n{1}'.format(title, description))
            if found:
                country = 'Switzerland'
                eligible = True
                confidence = 'medium'
                evidence = quote
                reason = 'Location "{0}" is a region, but the description states Switzerland explicitly.'.format(pretty)
            else:
                confidence = 'high'
                reason = ('Location "{0}" is a blanket region. '
                          'Region wording alone does not include Switzerland.'.format(pretty))
        else:
            # Empty or unrecognised structured location.
            found, quote = find_switzerland_evidence('{0}\n{1}'.format(title, description))
            if found:
                country = 'Switzerland'
                eligible = True
                confidence = 'medium'
                evidence = quote
                reason = 'No usable structured location, but the text names Switzerland explicitly.'
            else:
                confidence = 'low'
                reason = ('Location "{0}" could not be resolved and the text contains no '
                          'Switzerland statement.'.format(str(raw_location or '').strip() or 'unknown'))

        region = None
        if swiss_cantons:
            region = swiss_cantons[0]
        elif canton_codes and eligible:
            region = canton_codes[0]
        elif swiss_cities:
            region = None

        # A multi-country posting ("Baden, Aargau, Switzerland | Krakow, Poland")
        # is eligible because of its Swiss option, so the city shown must be the
        # Swiss one.  When no Swiss city was recognised it is left empty and the
        # UI falls back to the raw location - naming the foreign city instead
        # would tell the reader the job is somewhere it is not.
        city = (swiss_cities or [None])[0]
        if city is None and not eligible:
            city = (foreign_cities or [None])[0]

        return LocationVerdict(
            raw_location=str(raw_location or '').strip(),
            normalized_country=country,
            normalized_city=city,
            normalized_region=region,
            is_remote=model['is_remote'],
            is_hybrid=model['is_hybrid'],
            is_onsite=model['is_onsite'],
            work_model=model['work_model'],
            office_days=model['office_days'],
            switzerland_eligible=eligible,
            location_confidence=confidence,
            reason=reason,
            evidence=evidence,
            regions=regions,
            foreign_countries=foreign_countries,
            foreign_cities=foreign_cities,
            swiss_cities=swiss_cities,
        )

    # -- work model ------------------------------------------------------
    def _work_model(self, raw_location, title, description, remote_hint, has_city=False):
        head = _token_key('{0} {1}'.format(title, raw_location))
        body = _token_key(description)[:6000]
        blob = '{0} {1}'.format(head, body)

        is_hybrid = any(_token_key(t) in blob for t in HYBRID_TERMS)
        is_remote = bool(remote_hint) or any(
            re.search(r'\b' + re.escape(_token_key(t)) + r'\b', head) for t in REMOTE_TERMS)
        if not is_remote:
            is_remote = any(_token_key(t) in body for t in
                            ['fully remote', '100% remote', 'remote-first', 'remote first', 'remote role',
                             'remote position', 'work from home'])
        is_onsite = any(_token_key(t) in blob for t in ONSITE_TERMS)

        office_days = None
        match = re.search(r'(\d)\s*(?:-|to|bis)?\s*(\d)?\s*(?:days?|tage?)\s*(?:per week|a week|pro woche|/week)?'
                          r'[^.]{0,30}(?:office|buro|onsite|on-site|vor ort)', blob)
        if not match:
            match = re.search(r'(?:office|buro|onsite|on-site|vor ort)[^.]{0,30}?(\d)\s*(?:-|to|bis)?\s*(\d)?\s*(?:days?|tage?)', blob)
        if match:
            try:
                nums = [int(g) for g in match.groups() if g]
                office_days = max(nums) if nums else None
            except ValueError:
                office_days = None

        if is_hybrid:
            work_model = 'Hybrid'
        elif is_remote:
            work_model = 'Remote'
        elif is_onsite or has_city:
            work_model = 'Onsite'
        else:
            work_model = 'Unknown'
        if work_model == 'Hybrid':
            is_remote = is_remote or False
        return {'is_remote': is_remote, 'is_hybrid': is_hybrid, 'is_onsite': is_onsite,
                'work_model': work_model, 'office_days': office_days}

def canonical_location_name(value):
    """Map a user-entered preferred location onto its canonical Swiss name."""
    key = _token_key(value)
    if key in _SWISS_CITY_LOOKUP:
        return _SWISS_CITY_LOOKUP[key]
    if key in _SWISS_CANTON_LOOKUP:
        return _SWISS_CANTON_LOOKUP[key]
    if key in _SWISS_COUNTRY_KEYS:
        return 'Switzerland'
    return str(value or '').strip()
