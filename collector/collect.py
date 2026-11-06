#!/usr/bin/env python3
"""World Now live data collector, run by GitHub Actions every 5 minutes.

usage: collect.py PREV_DIR OUT_DIR
Writes fast.json (5 min), mid.json (30 min), slow.json (6 h), weather.json (60 min).
Each part is refreshed only when it is older than its interval; a failed source keeps its previous data.
Standard library only. Ships (Open Waters) are deliberately NOT collected: personal use only.
"""
import csv, gzip, io, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
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
    try:
        ap_activity(d.get('states') or [], d.get('time') or int(NOW))
    except Exception as e:
        log(f'     airports activity: {type(e).__name__}: {e}')
    try:
        ap_board(d.get('states') or [], p)
    except Exception as e:
        log(f'     airports board: {type(e).__name__}: {e}')
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


EQCA_BOX = (34, 56, 46, 96)  # lat min/max, lon min/max: Central Asia + Xinjiang + Caspian


def _hav(a, b, c, d):
    import math
    f1, f2 = math.radians(a), math.radians(c)
    x = math.sin((f2 - f1) / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def eqca(p):
    """Central Asia earthquakes from several agencies, merged into one event list."""
    import datetime as _dt
    la0, la1, lo0, lo1 = EQCA_BOX
    st = _dt.datetime.utcfromtimestamp(NOW - 7 * 86400).strftime('%Y-%m-%dT%H:%M:%S')
    rec, ok = [], {}
    def inbox(la, lo): return la0 <= la <= la1 and lo0 <= lo <= lo1
    try:
        d = jget(f'https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime={st}&minlatitude={la0}&maxlatitude={la1}&minlongitude={lo0}&maxlongitude={lo1}&minmagnitude=2')
        for f in d['features']:
            q, c = f['properties'], f['geometry']['coordinates']
            if q.get('mag') is None: continue
            rec.append(['usgs', q['time'], c[1], c[0], c[2], q['mag'], q.get('magType') or '', q.get('place') or '', (q.get('net') or '').upper(), q.get('url') or ''])
        ok['usgs'] = len(d['features'])
    except Exception as e:
        log('eqca usgs', e)
    try:
        d = jget(f'https://www.seismicportal.eu/fdsnws/event/1/query?format=json&start={st}&minlat={la0}&maxlat={la1}&minlon={lo0}&maxlon={lo1}&minmag=2&limit=500')
        for f in d['features']:
            q = f['properties']
            t = int(_dt.datetime.fromisoformat(q['time'].replace('Z', '+00:00')).timestamp() * 1000)
            rec.append(['emsc', t, q['lat'], q['lon'], q.get('depth'), q.get('mag'), q.get('magtype') or '', q.get('flynn_region') or '', q.get('auth') or '', 'https://www.seismicportal.eu/eventdetails.html?unid=' + q.get('unid', '')])
        ok['emsc'] = len(d['features'])
    except Exception as e:
        log('eqca emsc', e)
    try:
        t = get(f'https://geofon.gfz.de/fdsnws/event/1/query?format=text&starttime={st}&minlat={la0}&maxlat={la1}&minlon={lo0}&maxlon={lo1}&minmag=2')
        n = 0
        for ln in t.splitlines():
            if not ln or ln.startswith('#'): continue
            c = ln.split('|')
            if len(c) < 13: continue
            tt = int(_dt.datetime.fromisoformat(c[1][:19]).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
            rec.append(['gfz', tt, float(c[2]), float(c[3]), float(c[4] or 0), float(c[10]), c[9], c[12], c[5], 'https://geofon.gfz.de/eqinfo/event.php?id=' + c[0]]); n += 1
        ok['gfz'] = n
    except Exception as e:
        log('eqca gfz', e)
    try:
        d = jget('https://api.wolfx.jp/cenc_eqlist.json')
        n = 0
        for k, q in d.items():
            if not k.startswith('No'): continue
            la, lo = float(q['latitude']), float(q['longitude'])
            if not inbox(la, lo): continue
            tt = int(_dt.datetime.strptime(q['time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=_dt.timezone(_dt.timedelta(hours=8))).timestamp() * 1000)
            rec.append(['cenc', tt, la, lo, float(q.get('depth') or 0), float(q['magnitude']), 'M', q.get('placeName') or '', 'CENC', 'https://news.ceic.ac.cn/']); n += 1
        ok['cenc'] = n
    except Exception as e:
        log('eqca cenc', e)
    rec = [r for r in rec if r[5] is not None and inbox(r[2], r[3]) and r[1] >= (NOW - 7 * 86400) * 1000]
    rec.sort(key=lambda r: r[1])
    ev = []
    for r in rec:
        hit = None
        for e in ev[-40:]:
            if abs(e['t'] - r[1]) <= 90000 and _hav(e['la'], e['lo'], r[2], r[3]) <= 150 and r[0] not in e['s']:
                hit = e; break
        if not hit:
            hit = {'t': r[1], 'la': r[2], 'lo': r[3], 'd': r[4], 's': {}}
            ev.append(hit)
        hit['s'][r[0]] = [r[5], r[6], r[8], r[7], r[9], r[1], round(r[2], 3), round(r[3], 3), r[4]]
    for e in ev:
        pref = next(k for k in ('usgs', 'emsc', 'gfz', 'cenc') if k in e['s'])
        x = e['s'][pref]; e['t'], e['la'], e['lo'], e['d'] = x[5], x[6], x[7], x[8]
        e['m'] = round(sorted(v[0] for v in e['s'].values())[len(e['s']) // 2], 1)
        e['p'] = (e['s'].get('emsc') or e['s'].get('usgs') or x)[3]
    ev.sort(key=lambda e: -e['t'])
    p['eqca'] = ev[:400]
    p['eqcaSrc'] = ok
    p['eqcaAt'] = int(NOW * 1000)


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


_LAND = None


def LAND():
    """Land polygons as (bbox, rings) for a quick point-in-polygon test; cached between runs in land50.json."""
    global _LAND
    if _LAND is not None:
        return _LAND
    gj = load('land50.json')
    if not gj:
        gj = jget('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson', timeout=90)
        save('land50.json', gj)
    polys = []
    for f in gj['features']:
        g = f['geometry']
        for poly in (g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]):
            ring = poly[0]
            xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
            polys.append(((min(xs), min(ys), max(xs), max(ys)), ring))
    _LAND = polys
    return polys


def on_land(polys, x, y):
    for (x0, y0, x1, y1), ring in polys:
        if x < x0 or x > x1 or y < y0 or y > y1:
            continue
        inside, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]; xj, yj = ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
        if inside:
            return True
    return False


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
    # at sea? (Natural Earth 1:50m land; a point on the coastline may count either way)
    try:
        land = LAND()
        for f in out:
            f.append(0 if on_land(land, f[1], f[0]) else 1)
    except Exception as e:
        log(f'     land mask: {type(e).__name__}')
    save('firehist.json', {'cells': hist, 'gen': int(NOW)})
    p['fires'] = out
    p['firesSrc'] = 'VIIRS NOAA-20' if key else 'MODIS'


WARWORDS = re.compile(r'\b(strikes?|attack\w*|drones?|missiles?|rockets?|explosi\w*|blasts?|shell(ing|ed)|bomb\w*|airstrikes?|tankers?|vessels?|war|troops|army|military|killed|clash\w*|fighting|offensive|raids?|artillery|gunmen|insurgents?|militants?|jihadists?|terror\w*)\b')
GDELT_ROOTS = {'14': 'protest', '18': 'assault', '19': 'fight', '20': 'mass violence'}
RIOTWORDS = re.compile(r'\b(riots?|rioters?|rioting|unrest|clash\w*|tear ?gas|looting|looters?|barricades?|protests?|protesters?|demonstrat\w*|police)\b')


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
            if c[51] not in ('3', '4'):
                continue
            u = c[60].lower().replace('_', ' ').replace('-', ' ')
            if c[28] == '14':
                # riots and violent protests (CAMEO 145) or protests the article itself calls clashes/riots/unrest
                if not (c[26].startswith('145') or (c[26].startswith('14') and RIOTWORDS.search(u) and re.search(r'riot|clash|tear ?gas|unrest|loot|barricade', u))):
                    continue
            elif not c[26].startswith(('183', '186', '190', '193', '194', '195', '196', '20')) or not WARWORDS.search(u):
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
        g = groups.setdefault(k, {'lat': e[1], 'lon': e[2], 'place': e[3], 'n': 0, 'src': 0, 'urls': [], 't': 0, 'codes': set(), 'dom': set()})
        g['dom'].add(re.sub(r'^www\.', '', urllib.parse.urlparse(e[6]).netloc.lower()))
        g['n'] += 1; g['src'] += e[5]; g['t'] = max(g['t'], e[0]); g['codes'].add(e[4])
        if e[6] not in g['urls'] and len(g['urls']) < 4:
            g['urls'].append(e[6])
    out = []
    for g in groups.values():
        if g['src'] < 3:
            continue
        out.append([g['lat'], g['lon'], g['place'], g['n'], [[_slug_title(u), u] for u in g['urls']], g['t'], g['src'], sorted(g['codes']), len(g['dom'])])
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
SAT_GROUPS = [('military', 'military'), ('resource', 'earth'), ('planet', 'earth'), ('science', 'science'), ('geo', 'geo'), ('stations', 'station'), ('gnss', 'gnss'), ('weather', 'weather')]


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
        try:
            for n, l1, l2 in tle_list(get(base + g)):
                tag[l1[2:7]] = name
        except Exception as e:
            log(f'     sats {g}: {type(e).__name__}')
    out = []
    for n, l1, l2 in tle_list(get(base + 'active', timeout=90)):
        nid = l1[2:7]
        grp = ('starlink' if n.startswith('STARLINK') else 'oneweb' if n.startswith('ONEWEB') else 'kuiper' if n.startswith('KUIPER')
               else 'qianfan' if n.startswith(('QIANFAN', 'THOUSAIL', 'G60')) else 'iridium' if n.startswith('IRIDIUM') else None) or tag.get(nid) or 'other'
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



# ---------------------------------------------------------------- airports and sea ports
# Places (daily): OurAirports (public domain) large airports + medium ones with scheduled service;
# NGA World Port Index (US public domain) large and medium harbours; airline names and contacts from
# OpenFlights (ODbL) and Wikidata (CC0).
def places(p):
    rows = list(csv.DictReader(io.StringIO(get('https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv', timeout=90))))
    ap = []
    for r in rows:
        big = r['type'] == 'large_airport'
        if not (big or (r['type'] == 'medium_airport' and r['scheduled_service'] == 'yes' and r['iata_code'])):
            continue
        try:
            ap.append([r['iata_code'], r['icao_code'] or r['ident'], r['name'], r['municipality'], r['iso_country'],
                       round(float(r['latitude_deg']), 4), round(float(r['longitude_deg']), 4), 'L' if big else 'M',
                       r['home_link'], r['wikipedia_link']])
        except Exception:
            continue
    if len(ap) < 2000:
        raise RuntimeError(f'only {len(ap)} airports')
    p['airports'] = ap
    # sea ports
    try:
        txt = get('https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv', timeout=90)
        pr = []
        for r in csv.DictReader(io.StringIO(txt.lstrip('\ufeff'))):
            size = (r.get('Harbor Size') or '').strip()
            if size not in ('Large', 'Medium'):
                continue
            try:
                pr.append([r.get('Main Port Name', '').strip(), (r.get('UN/LOCODE') or '').replace(' ', ''), r.get('Country Code', '').strip(),
                           round(float(r['Latitude']), 4), round(float(r['Longitude']), 4), size[0], (r.get('Harbor Type') or '').strip()])
            except Exception:
                continue
        if len(pr) > 200:
            p['ports'] = pr
        log(f'     ports: {len(pr)}')
    except Exception as e:
        log(f'     ports WPI: {type(e).__name__}: {str(e)[:120]}')
    if not p.get('ports'):
        # fallback: Natural Earth 1:10m ports (public domain), with port websites
        d = jget('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_ports.geojson', timeout=60)
        p['ports'] = [[f['properties'].get('name') or '', '', '', round(f['geometry']['coordinates'][1], 4), round(f['geometry']['coordinates'][0], 4),
                       'L' if (f['properties'].get('scalerank') or 9) <= 5 else 'M', '', f['properties'].get('website') or '']
                      for f in d['features'] if f.get('geometry')]
        log(f'     ports: {len(p["ports"])} (Natural Earth)')
    # airlines: ICAO code -> [name, IATA, callsign, country, web, phone, email]
    al, byid = {}, {}
    for r in csv.reader(io.StringIO(get('https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat'))):
        if len(r) >= 8 and r[7] == 'Y' and re.fullmatch(r'[A-Z]{3}', r[4] or ''):
            al[r[4]] = [r[1], r[3] if r[3] not in ('\\N', '-') else '', r[5] if r[5] != '\\N' else '', r[6], '', '', '']
            byid[r[0]] = r[4]
    try:
        i2k = {a[0]: a[1] for a in ap if a[0]}
        rt = {}
        for r in csv.reader(io.StringIO(get('https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat', timeout=90))):
            if len(r) < 6 or r[1] not in byid:
                continue
            for code in (r[2], r[4]):
                k = i2k.get(code)
                if k:
                    rt.setdefault(k, set()).add(byid[r[1]])
        p['apRoutes'] = {k: sorted(v) for k, v in rt.items()}
        log(f'     airline routes: {len(rt)} airports')
    except Exception as e:
        log(f'     routes: {type(e).__name__}: {str(e)[:120]}')
    try:
        q = ('SELECT ?icao ?name ?web ?phone ?mail WHERE { ?a wdt:P230 ?icao . OPTIONAL { ?a wdt:P856 ?web } '
             'OPTIONAL { ?a wdt:P1329 ?phone } OPTIONAL { ?a wdt:P968 ?mail } '
             'OPTIONAL { ?a rdfs:label ?name FILTER(lang(?name) = "en") } FILTER NOT EXISTS { ?a wdt:P576 ?end } }')
        d = jget('https://query.wikidata.org/sparql?format=json&query=' + urllib.parse.quote(q), timeout=120,
                 headers={'Accept': 'application/sparql-results+json'})
        n = 0
        for b in d['results']['bindings']:
            c = b['icao']['value'].strip().upper()
            if not re.fullmatch(r'[A-Z]{3}', c):
                continue
            row = al.setdefault(c, [b.get('name', {}).get('value', c), '', '', '', '', '', ''])
            if not row[4] and 'web' in b: row[4] = b['web']['value']; n += 1
            if not row[5] and 'phone' in b: row[5] = b['phone']['value']
            if not row[6] and 'mail' in b: row[6] = b['mail']['value'].replace('mailto:', '')
        log(f'     airlines: {len(al)}, {n} with a website (Wikidata)')
    except Exception as e:
        log(f'     airlines wikidata: {type(e).__name__}: {str(e)[:120]}')
    p['airlines'] = al


_AP = None


def AP():
    """Airports as (code, lat, lon) plus a 1-degree grid for quick lookups."""
    global _AP
    if _AP is None:
        ap = load('places.json').get('airports') or []
        grid = {}
        for a in ap:
            grid.setdefault((int(a[5] // 1), int(a[6] // 1)), []).append(a)
        _AP = (ap, grid)
    return _AP


def ap_near(grid, lat, lon, km=20):
    best, bd = None, km
    ci, cj = int(lat // 1), int(lon // 1)
    for i in (ci - 1, ci, ci + 1):
        for j in (cj - 1, cj, cj + 1):
            for a in grid.get((i, j), ()):
                dy = (lat - a[5]) * 111.2; dx = (lon - a[6]) * 111.2 * max(0.05, abs(__import__('math').cos(lat * 0.01745)))
                d = (dx * dx + dy * dy) ** .5
                if d < bd:
                    best, bd = a, d
    return best


def ap_activity(states, t):
    """Aircraft on the ground or below 2.5 km within 20 km of an airport, per snapshot; airline codes from callsigns."""
    ap, grid = AP()
    if not ap:
        return
    cnt, air = {}, {}
    for s in states:
        lon, lat = s[5], s[6]
        if lat is None or lon is None:
            continue
        alt = s[7] if s[7] is not None else s[13]
        if not s[8] and (alt is None or alt > 2500):
            continue
        a = ap_near(grid, lat, lon)
        if not a:
            continue
        k = a[1]; cnt[k] = cnt.get(k, 0) + 1
        cs = (s[1] or '').strip()
        if re.fullmatch(r'[A-Z]{3}\d[0-9A-Z]*', cs):
            air.setdefault(k, set()).add(cs[:3])
    h = load('aphist.json')
    hour = str(int(t // 3600))
    snaps = h.get('snaps', {}); snaps[hour] = snaps.get(hour, 0) + 1
    counts = h.get('counts', {})
    for k, n in cnt.items():
        counts.setdefault(k, {})
        counts[k][hour] = counts[k].get(hour, 0) + n
    keep = int(t // 3600) - 7 * 24
    snaps = {x: v for x, v in snaps.items() if int(x) >= keep}
    counts = {k: {x: v for x, v in c.items() if int(x) >= keep} for k, c in counts.items()}
    airl = h.get('air', {})
    day = str(int(t // 86400))
    for k, codes in air.items():
        airl.setdefault(k, {})
        for c in codes:
            airl[k][c] = day
    kd = int(t // 86400) - 7
    airl = {k: {c: d for c, d in v.items() if int(d) >= kd} for k, v in airl.items()}
    save('aphist.json', {'snaps': snaps, 'counts': {k: c for k, c in counts.items() if c}, 'air': {k: v for k, v in airl.items() if v}})


def route_of(cache, cs, budget):
    r = cache.get(cs)
    if r and NOW - r[2] < 5 * 86400:
        return r
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    try:
        d = jget(f'https://api.adsbdb.com/v0/callsign/{cs}', timeout=8)
        fr = (d.get('response') or {}).get('flightroute') if isinstance(d.get('response'), dict) else None
        r = [fr['origin']['icao_code'], fr['destination']['icao_code'], NOW] if fr else [None, None, NOW]
    except urllib.error.HTTPError as e:
        r = [None, None, NOW] if e.code == 404 else None
    except Exception:
        r = None
    if r:
        cache[cs] = r
    return r


def ap_board(states, p):
    """Departures and arrivals right now: aircraft within 150 km of an airport, below 6 km or on the ground,
    whose route (adsbdb) starts or ends there. Rows: [callsign, other airport ICAO, D|A, alt m, km, on_ground]."""
    import math
    ap, grid = AP()
    if not ap:
        return
    cache = load('routes_cache.json')
    cache = {k: v for k, v in cache.items() if NOW - v[2] < 6 * 86400}
    budget = [int(os.environ.get('ROUTE_BUDGET', '250'))]
    byk = {a[1]: a for a in ap}
    near = []
    for st in states:
        lon, lat, cs = st[5], st[6], (st[1] or '').strip()
        if lat is None or lon is None or not re.fullmatch(r'[A-Z]{3}\d[0-9A-Z]*', cs):
            continue
        alt = st[7] if st[7] is not None else st[13]
        if not st[8] and (alt is None or alt > 6000):
            continue
        near.append((0 if st[8] else (alt or 0), cs, lat, lon, bool(st[8]), alt or 0))
    near.sort()
    board = {}
    for _, cs, lat, lon, ground, alt in near:
        r = route_of(cache, cs, budget)
        if not r or not r[0]:
            continue
        for k, other, dirn in ((r[0], r[1], 'D'), (r[1], r[0], 'A')):
            a = byk.get(k)
            if not a:
                continue
            dy = (lat - a[5]) * 111.2; dx = (lon - a[6]) * 111.2 * max(0.05, abs(math.cos(lat * 0.01745)))
            km = (dx * dx + dy * dy) ** .5
            if km <= 150:
                board.setdefault(k, []).append([cs, other, dirn, round(alt), round(km), 1 if ground else 0])
    save('routes_cache.json', cache)
    p['apboard'] = {k: sorted(v, key=lambda x: x[4])[:40] for k, v in board.items()}
    p['apboardAt'] = int(NOW)
    log(f'     board: {len(board)} airports, {sum(len(v) for v in board.values())} flights, {250 - budget[0]} route lookups')


def apstat(p):
    """Traffic light per airport: G = traffic as usual, Y = much less than usual or a hazard/incident/FAA delay nearby,
    R = usually busy but no aircraft seen for 2+ hours, or closed by the FAA. No light when ADS-B coverage is too thin."""
    import math
    ap, grid = AP()
    h = load('aphist.json')
    snaps, counts = h.get('snaps', {}), h.get('counts', {})
    if not ap or not snaps:
        raise RuntimeError('no airport history yet')
    nowh = int(NOW // 3600)
    def avg(c, hours):
        n = sum(snaps.get(str(x), 0) for x in hours)
        return (sum(c.get(str(x), 0) for x in hours) / n) if n else None
    recent_h = [nowh, nowh - 1, nowh - 2]
    base_h = [x for x in range(nowh - 7 * 24, nowh - 3)]
    same_h = [nowh - 24 * d + o for d in range(1, 8) for o in (-1, 0, 1)]
    # hazards near airports: incidents (24 h), big quakes, GDACS orange/red
    mid, fast = load('mid.json'), load('fast.json')
    haz = []
    for x in mid.get('incidents') or []:
        haz.append((x[0], x[1], 40, 'incident'))
    for q in fast.get('quakes') or []:
        if (q.get('m') or 0) >= 5.5 and NOW * 1000 - (q.get('t') or 0) < 48 * 3600e3:
            haz.append((q['x'][1], q['x'][0], 150, 'quake'))
    for g in mid.get('gdacs') or []:
        try:
            if str(g.get('lvl') or '').lower() in ('orange', 'red') and g.get('lat') is not None:
                haz.append((g['lat'], g['lon'], 150, 'disaster'))
        except Exception:
            pass
    faa = p.get('faaRaw') or {}
    out = {}
    for a in ap:
        k = a[1]; c = counts.get(k, {})
        rec, base, same = avg(c, recent_h), avg(c, base_h), avg(c, same_h)
        ref = same if same is not None and sum(snaps.get(str(x), 0) for x in same_h) >= 6 else base
        why = []
        st = ''
        nrec = sum(snaps.get(str(x), 0) for x in recent_h)
        if rec is not None and rec > 0:
            st = 'G'
            if ref is not None and ref >= 2 and rec < ref * 0.3 and nrec >= 2:
                st = 'Y'; why.append('low')
        elif rec == 0 and ref is not None and ref >= 1.5 and nrec >= 2:
            st = 'R'; why.append('quiet')
        for la, lo, km, kind in haz:
            if abs(la - a[5]) < 2 and abs(lo - a[6]) < 2:
                d = 6371 * math.acos(min(1, math.sin(math.radians(la)) * math.sin(math.radians(a[5])) +
                                          math.cos(math.radians(la)) * math.cos(math.radians(a[5])) * math.cos(math.radians(lo - a[6]))))
                if d <= km and kind not in why:
                    why.append(kind)
                    if st in ('', 'G'):
                        st = 'Y'
        f = faa.get(a[0])
        if f:
            why.append('faa:' + f[1][:80])
            st = 'R' if f[0] == 'closed' else ('Y' if st != 'R' else st)
        if st or why:
            out[k] = [st, round(rec, 1) if rec is not None else None, round(ref, 1) if ref is not None else None, why]
    airl = h.get('air', {})
    p['apstat'] = out
    p['apair'] = {k: sorted(v)[:24] for k, v in airl.items()}


def faa(p):
    """US airport delays and closures from the FAA NAS status feed."""
    x = ET.fromstring(get('https://nasstatus.faa.gov/api/airport-status-information', timeout=30))
    res = {}
    for e in x.iter():
        tag = e.tag.split('}')[-1]
        if tag in ('Ground_Delay', 'Ground_Stop', 'Delay', 'Airport', 'Program'):
            arpt = (e.findtext('ARPT') or e.findtext('ARPT_ID') or '').strip()
            if not arpt:
                continue
            reason = (e.findtext('Reason') or e.findtext('REASON') or '').strip()
            res[arpt] = ['delay', f'{tag.replace("_", " ")}: {reason}'.strip(': ')]
    for e in x.iter():
        if e.tag.split('}')[-1] == 'Airport_Closure_List':
            for a in e:
                arpt = (a.findtext('ARPT') or '').strip()
                if arpt:
                    res[arpt] = ['closed', 'Closed: ' + (a.findtext('Reason') or '').strip()]
    p['faaRaw'] = res


# ---------------------------------------------------------------- situation monitor sources
def outages(p):
    """Internet outages by country over the last 24 h (IODA, Georgia Tech; open API)."""
    until = int(NOW); frm = until - 24 * 3600
    d = jget(f'https://api.ioda.inetintel.cc.gatech.edu/v2/outages/summary?from={frm}&until={until}&entityType=country', timeout=60)
    out = {}
    for r in d.get('data') or []:
        ent = r.get('entity') or {}
        cc = (ent.get('code') or '').upper()
        sc = (r.get('scores') or {}).get('overall') or 0
        if cc and sc:
            out[cc] = [round(sc), r.get('event_cnt') or 0]
    alerts = {}
    try:
        a = jget(f'https://api.ioda.inetintel.cc.gatech.edu/v2/outages/alerts?from={until - 6 * 3600}&until={until}&entityType=country', timeout=60)
        for r in a.get('data') or []:
            cc = ((r.get('entity') or {}).get('code') or '').upper()
            if cc and r.get('level') in ('critical', 'warning'):
                alerts[cc] = 'critical' if r.get('level') == 'critical' or alerts.get(cc) == 'critical' else 'warning'
    except Exception as e:
        log(f'     ioda alerts: {type(e).__name__}')
    p['outages'] = out
    p['outagesNow'] = alerts
    log(f'     outages: {len(out)} countries, {len(alerts)} with alerts in 6 h')


def gpsjam(p):
    """GPS interference from aircraft navigation accuracy (gpsjam.org daily H3 cells)."""
    import h3
    rows, day = None, None
    for back in (1, 2, 3):
        day = (datetime.now(timezone.utc) - timedelta(days=back)).strftime('%Y-%m-%d')
        try:
            rows = list(csv.DictReader(io.StringIO(get(f'https://gpsjam.org/data/{day}-h3_4.csv', timeout=60))))
            break
        except Exception:
            rows = None
    if not rows:
        raise RuntimeError('no gpsjam data')
    out = []
    for r in rows:
        try:
            good, bad = int(r['count_good_aircraft']), int(r['count_bad_aircraft'])
        except Exception:
            continue
        tot = good + bad
        if tot < 3:
            continue
        ratio = bad / tot
        if ratio < 0.02:
            continue
        la, lo = h3.cell_to_latlng(r['hex'])
        out.append([round(la, 3), round(lo, 3), round(ratio, 3), tot])
    out.sort(key=lambda x: -x[2])
    p['gpsjam'] = out[:4000]
    p['gpsjamDay'] = day
    log(f'     gpsjam {day}: {len(out)} cells with interference')


def mil(p):
    """Military aircraft with ADS-B (adsb.lol, open data, no key)."""
    d = jget('https://api.adsb.lol/v2/mil', timeout=40)
    out = []
    for a in d.get('ac') or []:
        if a.get('lat') is None or a.get('lon') is None:
            continue
        alt = a.get('alt_baro')
        out.append([round(a['lat'], 3), round(a['lon'], 3), round(a.get('track') or 0), alt if isinstance(alt, (int, float)) else 0,
                    round(a.get('gs') or 0), (a.get('flight') or '').strip(), a.get('t') or '', a.get('r') or '', a.get('hex') or ''])
    p['mil'] = out
    p['milAt'] = int(NOW)


# ---------------------------------------------------------------- live TV and markets
STREAMS = [('aje', 'UCNye-wNBqNL5ZzHSJj3l8Bg'), ('aja', 'UCfiwzLy-8yKzIbsmZTzxDgw'), ('sky', 'UCoMdktPbSTixAyNGwb-UYkQ'), ('dw', 'UCknLrEdhRCp1aegoMqRaCZg'),
           ('f24', 'UCQfwfsi5VrQ8yKZ-UWmAEFg'), ('eun', 'UCSrZ3UV4jOidv8ppoVuvW9Q'), ('nbc', 'UCeY0bbntWzzVIaj2z3QigXg'), ('abc', 'UCBi2mrWuNuyYy4gbM6fW3Mg'),
           ('cbs', 'UC8p1vwvWtl6T73JiExfWs1g'), ('fox', 'UCJg9wBPyKMNA5sRDnvzmkdg'), ('blm', 'UCIALMKvObZNtJ6AmdCLP7Lg'), ('trt', 'UC7fWeaHhqgM4Ry-RMpM2YYw'),
           ('wion', 'UC_gUM8rL-Lrg6O3adPW9K1g'), ('cna', 'UC83jt4dlz1Gjl58fzQrrHZg'), ('abcau', 'UCVgO39Bk5sMo66-6o6Spn6Q'), ('nhk', 'UCSPEjw8F2nQDtmUKPFNF7_A'),
           ('i24', 'UCvHDpsWKADrDia0c99X37vg'), ('reu', 'UChqUTb7kYRX8-EiaN3XFrSQ'), ('ap', 'UC52X5wxOL_s5yw0dQk7NtgA'), ('kz24', 'UCnAFKvDuqBGkIfV8Vn0J_CQ')]


def streams(p):
    """Current live video of each news channel (YouTube channel /live page), so the site can embed it."""
    out, ok = {}, 0
    for key, ch in STREAMS:
        try:
            h = get(f'https://www.youtube.com/channel/{ch}/live', headers={'Accept-Language': 'en-US,en;q=0.8', 'Cookie': 'CONSENT=YES+1'}, timeout=20)
            m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', h)
            live = bool(m) and '"isUpcoming":true' not in h[:200000]
            out[key] = m.group(1) if live else ''
            ok += 1 if live else 0
        except Exception as e:
            out[key] = ''
            log(f'     stream {key}: {type(e).__name__}')
    if not ok:
        raise RuntimeError('no live streams found')
    p['streams'] = out
    log(f'     streams: {ok}/{len(STREAMS)} live')


MARKETS = [('idx', 'S&P 500', '^GSPC'), ('idx', 'Dow Jones', '^DJI'), ('idx', 'Nasdaq', '^IXIC'), ('idx', 'FTSE 100', '^FTSE'), ('idx', 'DAX', '^GDAXI'),
           ('idx', 'CAC 40', '^FCHI'), ('idx', 'Nikkei 225', '^N225'), ('idx', 'Hang Seng', '^HSI'), ('idx', 'Shanghai Composite', '000001.SS'), ('idx', 'Sensex', '^BSESN'),
           ('idx', 'VIX', '^VIX'),
           ('cmd', 'Brent', 'BZ=F'), ('cmd', 'WTI', 'CL=F'), ('cmd', 'Natural gas', 'NG=F'), ('cmd', 'Gold', 'GC=F'), ('cmd', 'Silver', 'SI=F'), ('cmd', 'Copper', 'HG=F'), ('cmd', 'Wheat', 'ZW=F'),
           ('fx', 'USD/KZT', 'KZT=X'), ('fx', 'EUR/USD', 'EURUSD=X'), ('fx', 'USD/RUB', 'RUB=X'), ('fx', 'USD/CNY', 'CNY=X'), ('fx', 'USD/TRY', 'TRY=X'), ('fx', 'Dollar index', 'DX-Y.NYB'),
           ('rate', 'US 10-year yield', '^TNX'),
           ('cry', 'Bitcoin', 'BTC-USD'), ('cry', 'Ethereum', 'ETH-USD')]


def markets(p):
    """Key world markets: last price, change vs previous close, 5-day closes (Yahoo Finance chart endpoint)."""
    out = []
    for grp, name, sym in MARKETS:
        try:
            d = jget(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d', timeout=20)
            r = d['chart']['result'][0]; meta = r['meta']
            price = meta.get('regularMarketPrice'); prev = meta.get('chartPreviousClose') or meta.get('previousClose')
            closes = [c for c in (r.get('indicators', {}).get('quote', [{}])[0].get('close') or []) if c is not None]
            if len(closes) >= 2:
                prev = closes[-2] if abs(closes[-1] - price) / max(1e-9, price) < 0.02 else closes[-1]
            chg = (price - prev) / prev * 100 if price and prev else None
            out.append([grp, name, sym, round(price, 4), round(chg, 2) if chg is not None else None, [round(c, 4) for c in closes[-6:]], meta.get('regularMarketTime') or 0])
        except Exception as e:
            log(f'     market {sym}: {type(e).__name__}')
    if len(out) < 10:
        raise RuntimeError(f'only {len(out)} markets')
    p['markets'] = out
    p['marketsAt'] = int(NOW)


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
    fast, mid, slow, wx, cm, pl = load('fast.json'), load('mid.json'), load('slow.json'), load('weather.json'), load('cams.json'), load('places.json')
    if not pl.get('ports') or not pl.get('airlines') or 'apRoutes' not in pl:
        pl.get('_at', {}).pop('places', None)
    step(pl, 'places', 1440, places)
    if pl.get('airports'):
        save('places.json', pl)
    step(fast, 'flights', 5 if os.environ.get('OPENSKY_ID') else 20, flights)
    step(fast, 'quakes', 5, quakes)
    step(fast, 'eqca', 5, eqca)
    step(fast, 'sirens', 5, sirens)
    step(fast, 'origins', 15, origins)
    step(fast, 'radar', 10, radar)
    step(mid, 'gdacs', 30, gdacs)
    step(mid, 'eonet', 60, eonet)
    step(mid, 'fires', 30, fires)
    step(mid, 'kznews', 30, kznews)
    step(mid, 'incidents', 30, incidents)
    step(wx, 'weather', 60, weather)
    if 'kuiper' not in json.dumps([x[1] for x in slow.get('sats', [])[:1]]) and not slow.get('satGroupsV2'):
        slow.get('_at', {}).pop('sats', None); slow['satGroupsV2'] = 1
    step(slow, 'sats', 360, sats)
    step(slow, 'risk', 180, risk)
    step(slow, 'rates', 180, rates)
    step(cm, 'cams', 1440, cams)
    step(fast, 'faa', 10, faa)
    step(fast, 'mil', 5, mil)
    step(fast, 'markets', 10, markets)
    step(mid, 'streams', 30, streams)
    step(mid, 'outages', 30, outages)
    step(slow, 'gpsjam', 360, gpsjam)
    step(fast, 'apstat', 10, apstat)
    for name, part in (('fast.json', fast), ('mid.json', mid), ('slow.json', slow), ('weather.json', wx), ('cams.json', cm), ('places.json', pl)):
        part['gen'] = int(NOW)
        save(name, part)
    save('status.json', {'gen': int(NOW), 'log': LOG, 'at': {k: v for part in (fast, mid, slow, wx, cm, pl) for k, v in part.get('_at', {}).items()}})


if __name__ == '__main__':
    main()
