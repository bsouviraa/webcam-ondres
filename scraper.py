import urllib.request, re, json, sys
from datetime import datetime

def fetch(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    })
    return urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')

def deg_to_cardinal(deg):
    dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSO','SO','OSO','O','ONO','NO','NNO']
    return dirs[round(int(deg) / 22.5) % 16]

def uv_label(val):
    v = float(val)
    if v < 3: return 'Faible'
    if v < 6: return 'Modéré'
    if v < 8: return 'Élevé'
    if v < 11: return 'Très élevé'
    return 'Extrême'

# 1. Meilisearch → temp air + picto météo + temp eau
ms = json.loads(urllib.request.urlopen(
    urllib.request.Request(
        "https://meiliprod111.apsulis.fr/indexes/pdl-fr/search",
        data=json.dumps({"q": "Ondres", "limit": 5}).encode(),
        headers={
            "Authorization": "Bearer a1608a72d5e5343758e2636b1a48bc94c96cb40d57327c03fe54bcdb4a4c4490",
            "Content-Type": "application/json"
        },
        method='POST'
    ), timeout=10
).read())

commune = next((h for h in ms['hits'] if h['type'] == 'commune' and h['title'] == 'Ondres'), {})
plage = next((h for h in ms['hits'] if h['type'] == 'plages'), {})

# 2. Open-Meteo → vent, direction, UV, lever/coucher
om = json.loads(fetch(
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=43.5714&longitude=-1.4697"
    "&current=temperature_2m,wind_speed_10m,wind_direction_10m,uv_index,weather_code,is_day"
    "&daily=sunrise,sunset,uv_index_max"
    "&timezone=Europe%2FParis&forecast_days=1"
))

# 3. Open-Meteo Marine → houle
marine = json.loads(fetch(
    "https://marine-api.open-meteo.com/v1/marine"
    "?latitude=43.5714&longitude=-1.4697"
    "&current=wave_height,wave_direction,wave_period"
    "&timezone=Europe%2FParis"
))

# 4. Scraper marées depuis plages-landes.info
html = fetch("https://www.plages-landes.info/ondres/")

marees_raw = list(re.finditer(
    r'(Haute|Basse)</span>\s*<span[^>]*>(\d{2}h\d{2})</span>', html
))
marees = [{"type": m.group(1), "heure": m.group(2)} for m in marees_raw[:4]]

coef = (re.search(r'COEF\.?&nbsp;\s*<span[^>]*>([\d\s/]+)</span>', html) or ['',''])[1]
if hasattr(coef, 'group'): coef = coef.group(1).strip()

# Drapeau depuis Meilisearch (fiable, mis à jour par les nageurs sauveteurs)
if plage.get('drapeau_vert'):
    drapeau = 'vert'
elif plage.get('drapeau_jaune'):
    drapeau = 'jaune'
elif plage.get('drapeau_rouge'):
    drapeau = 'rouge'
elif plage.get('etat_surveillance') == False:
    drapeau = 'nc'  # hors saison / non surveillé
else:
    drapeau = ''
# Label surveillance
surv_label = 'Baignade non surveillée'
surv_h = plage.get('surveillance_h', {})
if isinstance(surv_h, dict) and surv_h.get('deb') and surv_h.get('fin'):
    surv_label = f"Surveillée {surv_h['deb']} — {surv_h['fin']}"
elif plage.get('etat_surveillance'):
    surv_label = 'Baignade surveillée' 

# WMO code → label
WMO = {0:'Ciel dégagé',1:'Peu nuageux',2:'Partiellement nuageux',3:'Couvert',
       45:'Brouillard',48:'Brouillard givrant',51:'Bruine légère',53:'Bruine modérée',
       55:'Bruine dense',61:'Pluie légère',63:'Pluie modérée',65:'Pluie forte',
       71:'Neige légère',73:'Neige modérée',75:'Neige forte',
       80:'Averses légères',81:'Averses modérées',82:'Averses violentes',
       95:'Orage',96:'Orage avec grêle',99:'Orage violent avec grêle'}

cur = om['current']
daily = om['daily']
uv_val = cur['uv_index']
uv_max = daily['uv_index_max'][0]
# Si UV = 0 (nuit), utiliser le max du jour
uv_display = uv_max if uv_val == 0 else uv_val

data = {
    "updated": datetime.now().strftime("%Y-%m-%dT%H:%M"),
    "temp_air": commune.get('meteo_temp_air') or str(round(cur['temperature_2m'])),
    "meteo_picto": commune.get('meteo_picto', ''),
    "meteo_label": WMO.get(cur['weather_code'], ''),
    "temp_eau": plage.get('temp_eau', 'NC'),
    "vent_kmh": str(round(cur['wind_speed_10m'])),
    "vent_deg": str(round(cur['wind_direction_10m'])),
    "vent_cardinal": deg_to_cardinal(round(cur['wind_direction_10m'])),
    "uv": str(round(uv_display, 1)),
    "uv_label": uv_label(uv_display),
    "lever": daily['sunrise'][0][11:16],
    "coucher": daily['sunset'][0][11:16],
    "houle_m": str(round(marine['current']['wave_height'], 2)),
    "houle_periode": str(round(marine['current']['wave_period'], 1)),
    "marees": marees,
    "maree_coef": coef.strip() if isinstance(coef, str) else '—',
    "drapeau": drapeau,
    "surv_label": surv_label,
}

print(json.dumps(data, ensure_ascii=False, indent=2))