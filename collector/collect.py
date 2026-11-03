#!/usr/bin/env python3
"""World Now live data collector, run by GitHub Actions every 5 minutes.

usage: collect.py PREV_DIR OUT_DIR
Writes fast.json (5 min), mid.json (30 min), slow.json (6 h), weather.json (60 min).
Each part is refreshed only when it is older than its interval; a failed source keeps its previous data.
Standard library only. Ships (Open Waters) are deliberately NOT collected: personal use only.
"""
import csv, gzip, io, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

PREV, OUT = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
NOW = time.time()
UA = 'WorldNow-collector/1.0 (+https://github.com/ArmaniMachinena/worldnow-web)'
LOG = []


def log(*a):
    m = ' '.join(str(x) for x in a)
    LOG.append(m)
    print(m, flush=True)


def get(url, data=None, headers=None, timeout=40, raw=False):
    h = {'User-Agent': UA, 'Accept-Encoding': 'gzip'}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            b = gzip.decompress(b)
    return b if raw else b.decode('utf-8', 'replace')


def jget(url, **kw):
    return json.loads(get(url, **kw))


def load(name):
    for d in (OUT, PREV):
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                return json.load(open(p))
            except Exception:
                pass
    return {}


def save(name, obj):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), 'w') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def stale(part, key, minutes):
    return NOW - part.get('_at', {}).get(key, 0) > minutes * 60 - 30


def step(part, key, minutes, fn):
    """Run fn() if the key is stale; keep old data on failure."""
    if not stale(part, key, minutes):
        return
    try:
        t0 = time.time()
        fn(part)
        part.setdefault('_at', {})[key] = NOW
        log(f'ok   {key} ({time.time() - t0:.1f}s)')
    except Exception as e:
        log(f'FAIL {key}: {type(e).__name__}: {str(e)[:200]}')


# ---------------------------------------------------------------- helpers
COUNTRIES = json.load(open(os.path.join(HERE, 'countries.json')))
NAME2CC = {}
for cc, (n, o) in COUNTRIES.items():
    NAME2CC[n.lower()] = cc
    if o:
        NAME2CC[o.lower()] = cc
NAME2CC.update({
    'burma': 'MM', "cote d'ivoire": 'CI', "côte d'ivoire": 'CI', 'north korea': 'KP', 'south korea': 'KR', 'the bahamas': 'BS',
    'the gambia': 'GM', 'democratic republic of the congo': 'CD', 'republic of the congo': 'CG', 'republic of congo': 'CG',
    'czechia': 'CZ', 'turkey': 'TR', 'türkiye': 'TR', 'turkiye': 'TR', 'russia': 'RU', 'russian federation': 'RU', 'vatican city': 'VA',
    'eswatini': 'SZ', 'cabo verde': 'CV', 'timor-leste': 'TL', 'micronesia': 'FM', 'macau': 'MO', 'hong kong': 'HK',
    'the west bank': 'PS', 'mainland china': 'CN', 'china': 'CN', 'united kingdom': 'GB', 'kosovo': 'XK', 'the kyrgyz republic': 'KG',
    'laos': 'LA', 'syria': 'SY', 'iran': 'IR', 'vietnam': 'VN', 'venezuela': 'VE', 'bolivia': 'BO', 'tanzania': 'TZ',
    'moldova': 'MD', 'brunei': 'BN', 'united states': 'US', 'united arab emirates': 'AE', 'kingdom of saudi arabia': 'SA',
})


def code_for(raw):
    n = re.sub(r'\s*\(.*?\)', '', raw).strip().lower().replace('’', "'")
    if n in NAME2CC:
        return NAME2CC[n]
    n2 = n.replace('the ', '')
    return NAME2CC.get(n2)


def ts_of(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def rss(url, source=None, limit=30):
    """Generic RSS/RDF/Atom reader -> [{t,u,ts,s}] (ts in seconds)."""
    root = ET.fromstring(get(url, raw=True))
    out = []
    for it in root.iter():
        tag = it.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        d = {}
        for ch in it:
            k = ch.tag.split('}')[-1]
            if k == 'link' and not (ch.text or '').strip():
                d.setdefault('link', ch.get('href', ''))
            else:
                d.setdefault(k, (ch.text or '').strip())
        t = d.get('title', '')
        ts = ts_of(d.get('pubDate') or d.get('date') or d.get('published') or d.get('updated'))
        if not t or not ts:
            continue
        src = source or d.get('source') or ''
        if not source and ' - ' in t and d.get('source'):
            t = t.rsplit(' - ', 1)[0]
        out.append({'t': t, 'u': d.get('link', ''), 'ts': int(ts), 's': src})
        if len(out) >= limit:
            break
    return out


def gnews(q, limit=30):
    return 'https://news.google.com/rss/search?q=' + urllib.parse.quote(q) + '&hl=en-US&gl=US&ceid=US:en'


# ---------------------------------------------------------------- fast (5 min)
def opensky_token():
    cid, sec = os.environ.get('OPENSKY_ID'), os.environ.get('OPENSKY_SECRET')
    if not cid or not sec:
        return None
    body = urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': cid, 'client_secret': sec}).encode()
    r = json.loads(get('https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token', data=body,
                       headers={'Content-Type': 'application/x-www-form-urlencoded'}))
    return r['access_token']


def flights(p):
    tok = opensky_token()
    h = {'Authorization': 'Bearer ' + tok} if tok else {}
    d = jget('https://opensky-network.org/api/states/all', headers=h, timeout=60)
    out = []
    for s in d.get('states') or []:
        lon, lat = s[5], s[6]
        if lat is None or lon is None or s[8]:
            continue
        alt = s[7] if s[7] is not None else s[13]
        out.append([round(lat, 4), round(lon, 4), round(s[10] or 0, 1), round(alt or 0), round(s[9] or 0, 1),
                    (s[1] or '').strip(), s[2] or '', s[0]])
    if len(out) < 500:
        raise RuntimeError(f'only {len(out)} aircraft')
    p['flights'] = out
    p['flightsAt'] = d.get('time') or int(NOW)
    p['flightsAuth'] = bool(tok)


def quakes(p):
    d = jget('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson')
    out = []
    for f in d['features']:
        q = f['properties']
        out.append({'id': f['id'], 'm': q.get('mag'), 'p': q.get('place') or '', 't': q.get('time'), 'u': q.get('url'),
                    'ts': q.get('tsunami') or 0, 'a': q.get('alert'), 'fe': q.get('felt'), 'x': f['geometry']['coordinates']})
    out = [q for q in out if q['m'] is not None]
    p['quakes'] = out
    p['quakesGen'] = d['metadata']['generated']
    p['quakesRef'] = max(q['t'] for q in out) if out else int(NOW * 1000)


CITIES = json.load(open(os.path.join(HERE, 'israel_cities.json')))


def sirens(p):
    hist = jget('https://api.tzevaadom.co.il/alerts-history', headers={'Referer': 'https://www.tzevaadom.co.il/'})
    old = p.get('alerts') or []
    seen = {(a[0], a[3]) for a in old}
    new = []
    for g in hist:
        gid = g.get('id')
        for a in g.get('alerts') or []:
            if a.get('isDrill'):
                continue
            for name in a.get('cities') or []:
                c = CITIES.get(name)
                if not c:
                    continue
                row = [int(a.get('time') or 0), c[0], c[1], c[2], int(a.get('threat', 8)), gid]
                if (row[0], row[3]) not in seen:
                    seen.add((row[0], row[3]))
                    new.append(row)
    cut = NOW - 120 * 86400
    p['alerts'] = sorted([a for a in old + new if a[0] > cut], key=lambda a: -a[0])
    log(f'     sirens +{len(new)} -> {len(p["alerts"])}')


def origins(p):
    p['recent'] = jget('https://under-fire.org/data/processed/recent_alerts.json')


RADAR_FEEDS = [
    ('BBC', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('Reuters', gnews('site:reuters.com world')),
    ('AP', gnews('site:apnews.com world')),
    ('CNN', gnews('site:cnn.com world')),
    ('Al Jazeera', 'https://www.aljazeera.com/xml/rss/all.xml'),
    ('Euronews', 'https://www.euronews.com/rss?level=theme&name=news'),
    ('NYT', 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'),
    ('WaPo', 'https://feeds.washingtonpost.com/rss/world'),
    ('Guardian', 'https://www.theguardian.com/world/rss'),
    ('DW', 'https://rss.dw.com/rdf/rss-en-world'),
]


def radar(p):
    out, ok = [], 0
    for name, url in RADAR_FEEDS:
        try:
            items = rss(url, source=name, limit=40)
            items = [i for i in items if NOW - i['ts'] < 36 * 3600]
            items.sort(key=lambda i: -i['ts'])
            out += [{'s': name, 't': i['t'], 'u': i['u'], 'ts': i['ts']} for i in items[:12]]
            ok += 1
        except Exception as e:
            log(f'     radar {name}: {type(e).__name__}')
    if ok < 5:
        raise RuntimeError(f'only {ok} outlets')
    p['radar'] = out


def nkwind(p):
    # Wind over Pickaxe Mt. (Natanz) for the experimental TEST NUCLEAR page.
    p['nkwind'] = jget('https://api.open-meteo.com/v1/forecast?latitude=33.68&longitude=51.94&current=wind_speed_10m,wind_direction_10m'
                       '&hourly=wind_speed_850hPa,wind_direction_850hPa,wind_speed_500hPa,wind_direction_500hPa&forecast_days=3&timezone=UTC')


# ---------------------------------------------------------------- mid (30 min)
def gdacs(p):
    to = datetime.now(timezone.utc).date()
    fr = to - timedelta(days=7)
    url = ('https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=EQ;TC;FL;VO;DR;WF'
           f'&fromDate={fr}&toDate={to}&alertlevel=Green;Orange;Red')
    d = jget(url)
    out = []
    for f in d.get('features') or []:
        q, g = f['properties'], f['geometry']['coordinates']
        out.append({'src': 'GDACS', 'cat': q.get('eventtype'), 'lon': g[0], 'lat': g[1], 'title': q.get('name') or q.get('description') or '',
                    'lvl': q.get('alertlevel'), 'cty': q.get('country') or '', 'fr': q.get('fromdate'), 'to': q.get('todate'),
                    'sev': (q.get('severitydata') or {}).get('severitytext', ''), 'url': (q.get('url') or {}).get('report', ''),
                    'cur': str(q.get('iscurrent', '')).lower()})
    p['gdacs'] = out


def eonet(p):
    d = jget('https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30&limit=300')
    out = []
    for e in d.get('events') or []:
        geo = (e.get('geometry') or [])
        if not geo:
            continue
        g = geo[-1]
        c = g.get('coordinates')
        if g.get('type') != 'Point' or not c:
            continue
        mag = f"{g['magnitudeValue']:g} {g.get('magnitudeUnit') or ''}".strip() if g.get('magnitudeValue') is not None else None
        if mag:
            mag = mag.replace('acres', 'acres')
        out.append({'cat': (e.get('categories') or [{}])[0].get('id', ''), 'lat': c[1], 'lon': c[0], 'date': (g.get('date') or '')[:10],
                    'mag': mag, 'title': e.get('title', '')})
    p['events'] = out


def fires(p):
    key = os.environ.get('FIRMS_KEY')
    if key:
        url = f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_NOAA20_NRT/world/1'
    else:
        url = 'https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv'
    rows = list(csv.DictReader(io.StringIO(get(url, timeout=90))))
    out = []
    for r in rows:
        try:
            t = (r.get('acq_time') or '0').zfill(4)
            conf = r.get('confidence', '')
            conf = {'l': 30, 'n': 60, 'h': 90}.get(conf, conf)
            out.append([round(float(r['latitude']), 3), round(float(r['longitude']), 3), round(float(r.get('frp') or 0), 1),
                        int(float(conf or 0)), f"{r['acq_date']} {t[:2]}:{t[2:]}", r.get('daynight', '')])
        except Exception:
            continue
    if len(out) < 200:
        raise RuntimeError(f'only {len(out)} fires')
    # How many of the last 7 days each spot was hot: 3+ days = steady source (gas flare, plant), 1 = new.
    hist = load('firehist.json').get('cells', {})
    cut = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y%m%d')
    cell = lambda la, lo: f'{round(la / 0.02)}:{round(lo / 0.02)}'
    for f in out:
        d = f[4][:10].replace('-', '')
        k = cell(f[0], f[1]); days = set(hist.get(k, [])); days.add(d); hist[k] = sorted(days)
    hist = {k: [d for d in v if d >= cut] for k, v in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    def seen(la, lo):
        a, b = round(la / 0.02), round(lo / 0.02); ds = set()
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                ds.update(hist.get(f'{a + i}:{b + j}', []))
        return len(ds)
    for f in out:
        f.append(seen(f[0], f[1]))
    save('firehist.json', {'cells': hist, 'gen': int(NOW)})
    p['fires'] = out
    p['firesSrc'] = 'VIIRS NOAA-20' if key else 'MODIS'


WARWORDS = re.compile(r'strike|attack|drone|missile|rocket|explos|blast|shell|bomb|airstrike|tanker|vessel|ship|war|troops|army|military|killed|clash|fighting|offensive|raid|artillery')
GDELT_ROOTS = {'18': 'assault', '19': 'fight', '20': 'mass violence'}


def _slug_title(u):
    path = urllib.parse.urlparse(u).path.rstrip('/').split('/')
    seg = max(path, key=len) if path else ''
    seg = re.sub(r'\.(html?|php|aspx?)$', '', seg)
    seg = re.sub(r'[-_]+', ' ', seg)
    seg = re.sub(r'\b\d{5,}\b', '', seg).strip()
    return seg[:1].upper() + seg[1:] if len(seg) > 12 else ''


def incidents(p):
    """Geolocated violent events (CAMEO 18-20: assaults, fights, mass violence) from the GDELT 2.0 event export,
    every 15-minute file since the last run, kept for 24 h and grouped by place."""
    import zipfile
    raw = load('incidents_hist2.json')
    events, last = raw.get('events', []), raw.get('last', '')
    now = datetime.now(timezone.utc)
    t = now - timedelta(minutes=now.minute % 15 + 15, seconds=now.second, microseconds=now.microsecond)
    stamps = []
    for k in range(8):
        st = (t - timedelta(minutes=15 * k)).strftime('%Y%m%d%H%M%S')
        if st <= last:
            break
        stamps.append(st)
    got = 0
    for st in reversed(stamps):
        try:
            b = get(f'http://data.gdeltproject.org/gdeltv2/{st}.export.CSV.zip', raw=True, timeout=40)
        except Exception as e:
            log(f'     gdelt {st}: {type(e).__name__}'); continue
        z = zipfile.ZipFile(io.BytesIO(b))
        for line in z.read(z.namelist()[0]).decode('utf-8', 'replace').splitlines():
            c = line.split('\t')
            if len(c) < 61 or c[28] not in GDELT_ROOTS or not c[56] or not c[57]:
                continue
            # armed-conflict codes only (bombings, military force, small arms, artillery, air strikes, ceasefire breaches,
            # assassinations, mass violence), city-level places only, and a war-like word in the article address
            if not c[26].startswith(('183', '186', '190', '193', '194', '195', '196', '20')) or c[51] not in ('3', '4'):
                continue
            if not WARWORDS.search(c[60].lower()):
                continue
            try:
                events.append([int(datetime.strptime(c[59], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc).timestamp()),
                               round(float(c[56]), 2), round(float(c[57]), 2), c[52][:80], c[28], int(c[32] or 1), c[60]])
            except Exception:
                continue
        last = max(last, st); got += 1
    cut = NOW - 24 * 3600
    events = [e for e in events if e[0] > cut]
    save('incidents_hist2.json', {'events': events, 'last': last})
    groups = {}
    for e in events:
        k = (round(e[1], 1), round(e[2], 1))
        g = groups.setdefault(k, {'lat': e[1], 'lon': e[2], 'place': e[3], 'n': 0, 'src': 0, 'urls': [], 't': 0, 'codes': set()})
        g['n'] += 1; g['src'] += e[5]; g['t'] = max(g['t'], e[0]); g['codes'].add(e[4])
        if e[6] not in g['urls'] and len(g['urls']) < 4:
            g['urls'].append(e[6])
    out = []
    for g in groups.values():
        if g['src'] < 3:
            continue
        out.append([g['lat'], g['lon'], g['place'], g['n'], [[_slug_title(u), u] for u in g['urls']], g['t'], g['src'], sorted(g['codes'])])
    out.sort(key=lambda x: -x[6])
    log(f'     incidents: {got} files, {len(events)} events, {len(out)} places')
    if not out and not events:
        raise RuntimeError('no incidents')
    p['incidents'] = out[:300]


def kznews(p):
    out = []
    for url, src in [('https://www.inform.kz/rss/eng.xml', 'Kazinform'),
                     (gnews('site:akorda.kz OR site:primeminister.kz OR site:gov.kz OR site:nationalbank.kz OR site:stat.gov.kz'), None)]:
        try:
            out += rss(url, source=src, limit=80)
        except Exception as e:
            log(f'     kznews {src}: {type(e).__name__}')
    seen, res = set(), []
    for i in sorted(out, key=lambda i: -i['ts']):
        k = i['t'].lower()[:70]
        if k in seen or NOW - i['ts'] > 14 * 86400:
            continue
        seen.add(k)
        res.append({'t': i['t'], 'ts': i['ts'] * 1000, 'u': i['u'], 's': i['s'] or 'News'})
    if not res:
        raise RuntimeError('no news')
    p['news'] = res[:160]


# ---------------------------------------------------------------- weather (60 min)
WMO_PTS = json.load(open(os.path.join(HERE, 'weather_points.json')))


def weather(p):
    ccs = list(WMO_PTS)
    res = {}
    for i in range(0, len(ccs), 100):
        part = ccs[i:i + 100]
        q = {
            'latitude': ','.join(str(WMO_PTS[c][0]) for c in part),
            'longitude': ','.join(str(WMO_PTS[c][1]) for c in part),
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl,is_day',
            'daily': 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum',
            'timezone': 'auto', 'wind_speed_unit': 'ms', 'forecast_days': 7,
        }
        d = jget('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode(q), timeout=60)
        if isinstance(d, dict):
            d = [d]
        for cc, w in zip(part, d):
            c, dl = w['current'], w['daily']
            res[cc] = {'tz': w.get('timezone'), 't': c['time'],
                       'c': [c['temperature_2m'], c['apparent_temperature'], c['relative_humidity_2m'], c['weather_code'],
                             c['wind_speed_10m'], c['wind_direction_10m'], c["pressure_msl"], c['is_day']],
                       'd': [dl['time'], dl['weather_code'], dl['temperature_2m_max'], dl['temperature_2m_min'], dl['precipitation_sum']]}
    p['weather'] = res
    p['weatherAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')


# ---------------------------------------------------------------- slow (6 h)
SAT_GROUPS = [('stations', 'station'), ('gnss', 'gnss'), ('weather', 'weather')]


def tle_list(text):
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        if lines[i + 1].startswith('1 ') and lines[i + 2].startswith('2 '):
            out.append((lines[i].strip(), lines[i + 1], lines[i + 2]))
    return out


def sats(p):
    base = 'https://celestrak.org/NORAD/elements/gp.php?FORMAT=tle&GROUP='
    tag = {}
    for g, name in SAT_GROUPS:
        for n, l1, l2 in tle_list(get(base + g)):
            tag[l1[2:7]] = name
    out = []
    for n, l1, l2 in tle_list(get(base + 'active', timeout=90)):
        nid = l1[2:7]
        grp = tag.get(nid) or ('starlink' if n.startswith('STARLINK') else 'oneweb' if n.startswith('ONEWEB')
                               else 'iridium' if n.startswith('IRIDIUM') else 'other')
        out.append([n, grp, l1, l2])
    if len(out) < 5000:
        raise RuntimeError(f'only {len(out)} satellites')
    p['sats'] = out
    iss = next((s for s in out if s[0].startswith('ISS (ZARYA)')), None)
    if iss:
        p['tle'] = [iss[2], iss[3]]
    p['satEpochNote'] = 'CelesTrak'


def risk(p):
    r = p.get('risk') or {'adv': {}, 'who': [], 'outages': {}}
    adv = {}
    for a in jget('https://cadataapi.state.gov/api/TravelAdvisories'):
        title = a.get('Title') or ''
        m = re.search(r'\s*-\s*Level\s*(\d)', title)
        if not m:
            continue
        cc = code_for(title[:m.start()])
        if not cc:
            continue
        lv = int(m.group(1))
        if cc in adv and adv[cc][0] >= lv:
            continue
        text = ':'.join(title.split(':')[1:]).strip()
        adv[cc] = [lv, text, a.get('Link') or '', (a.get('Updated') or '')[:10]]
    if len(adv) > 100:
        r['adv'] = adv
        r['advAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        d = jget('https://www.who.int/api/news/diseaseoutbreaknews?sf_culture=en&$orderby=PublicationDateAndTime%20desc&$top=40')
        who = []
        for w in d.get('value') or []:
            title = w.get('OverrideTitle') or w.get('Title') or ''
            pub = w.get('PublicationDate') or ''
            t = ts_of(pub)
            if not title or (t and NOW - t > 90 * 86400):
                continue
            ccs = [c for part in title.split(' - ')[1:] for piece in re.split(r',| and ', part) if (c := code_for(piece))]
            path = w.get('ItemDefaultUrl') or ''
            url = path if path.startswith('http') else 'https://www.who.int/emergencies/disease-outbreak-news/item' + path
            if ccs:
                who.append({'t': title, 'd': pub[:10], 'u': url, 'cc': ccs[0], 'also': ccs[1:]})
        r['who'] = who
        r['whoAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    except Exception as e:
        log(f'     who: {type(e).__name__}')
    try:
        until = int(NOW)
        d = jget(f'https://api.ioda.inetintel.cc.gatech.edu/v2/outages/summary?from={until - 7 * 86400}&until={until}&entityType=country')
        r['outages'] = {row['entity']['code']: [row.get('event_cnt', 0), round((row.get('scores') or {}).get('overall', 0))]
                        for row in d.get('data') or [] if row.get('entity', {}).get('code')}
        r['iodaWindow'] = 'last 7 days'
    except Exception as e:
        log(f'     ioda: {type(e).__name__}')
    p['risk'] = r


def rates(p):
    d = jget('https://open.er-api.com/v6/latest/KZT')
    p['rates'] = d['rates']
    p['ratesUpd'] = d.get('time_last_update_utc', '')
    try:
        root = ET.fromstring(get('https://nationalbank.kz/rss/rates_all.xml', raw=True))
        nbk, date = {}, ''
        for it in root.iter('item'):
            code = (it.findtext('title') or '').strip()
            val = float((it.findtext('description') or '0').replace(',', '.'))
            q = float((it.findtext('quant') or '1').replace(',', '.') or 1)
            if code and val:
                nbk[code] = round(val / q, 6)
            date = (it.findtext('pubDate') or date).strip()
        if nbk:
            p['nbk'], p['nbkDate'] = nbk, date
    except Exception as e:
        log(f'     nbk: {type(e).__name__}')


# ---------------------------------------------------------------- cameras (24 h)
def cams(p):
    # Public camera index from OSIRIS (open source, MIT; its documented API needs no key).
    d = jget('https://osirisai.live/api/cctv?region=all', timeout=180)
    out = []
    for c in d.get('cameras') or []:
        try:
            lat, lng = float(c['lat']), float(c['lng'])
        except Exception:
            continue
        feed, stream = c.get('feed_url') or '', c.get('stream_url') or ''
        if not feed and not stream:
            continue
        out.append([round(lat, 4), round(lng, 4), c.get('name') or '', c.get('city') or '', c.get('country') or '',
                    feed, stream, c.get('stream_type') or '', c.get('external_url') or '', c.get('source') or ''])
    if len(out) < 500:
        raise RuntimeError(f'only {len(out)} cameras')
    p['cams'] = out
    p['camsSources'] = d.get('sources') or {}


# ---------------------------------------------------------------- run
def main():
    fast, mid, slow, wx, cm = load('fast.json'), load('mid.json'), load('slow.json'), load('weather.json'), load('cams.json')
    step(fast, 'flights', 5 if os.environ.get('OPENSKY_ID') else 20, flights)
    step(fast, 'quakes', 5, quakes)
    step(fast, 'sirens', 5, sirens)
    step(fast, 'origins', 15, origins)
    step(fast, 'radar', 10, radar)
    step(fast, 'nkwind', 30, nkwind)
    step(mid, 'gdacs', 30, gdacs)
    step(mid, 'eonet', 60, eonet)
    step(mid, 'fires', 30, fires)
    step(mid, 'kznews', 30, kznews)
    step(mid, 'incidents', 30, incidents)
    step(wx, 'weather', 60, weather)
    step(slow, 'sats', 360, sats)
    step(slow, 'risk', 180, risk)
    step(slow, 'rates', 180, rates)
    step(cm, 'cams', 1440, cams)
    for name, part in (('fast.json', fast), ('mid.json', mid), ('slow.json', slow), ('weather.json', wx), ('cams.json', cm)):
        part['gen'] = int(NOW)
        save(name, part)
    save('status.json', {'gen': int(NOW), 'log': LOG, 'at': {k: v for part in (fast, mid, slow, wx, cm) for k, v in part.get('_at', {}).items()}})


if __name__ == '__main__':
    main()
