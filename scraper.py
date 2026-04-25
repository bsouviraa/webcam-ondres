import urllib.request, re, json, csv, io
from datetime import datetime

SHEET_ID = "1O-vrZ_qSRbsjsNAuIc5YS7Aw4qulp3AG"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")

def deg_to_cardinal(deg):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
    return dirs[round(int(deg) / 22.5) % 16]

def uv_label(val):
    v = float(val)
    if v < 3: return "Faible"
    if v < 6: return "Modere"
    if v < 8: return "Eleve"
    if v < 11: return "Tres eleve"
    return "Extreme"

# 1. Meilisearch
ms = json.loads(urllib.request.urlopen(
    urllib.request.Request(
        "https://meiliprod111.apsulis.fr/indexes/pdl-fr/search",
        data=json.dumps({"q": "Ondres", "limit": 5}).encode(),
        headers={
            "Authorization": "Bearer a1608a72d5e5343758e2636b1a48bc94c96cb40d57327c03fe54bcdb4a4c4490",
            "Content-Type": "application/json"
        },
        method="POST"
    ), timeout=10
).read())

commune = next((h for h in ms["hits"] if h["type"] == "commune" and h["title"] == "Ondres"), {})
plage   = next((h for h in ms["hits"] if h["type"] == "plages"), {})

# 2. Open-Meteo
om = json.loads(fetch(
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=43.5714&longitude=-1.4697"
    "&current=temperature_2m,wind_speed_10m,wind_direction_10m,uv_index,weather_code"
    "&daily=sunrise,sunset,uv_index_max"
    "&timezone=Europe%2FParis&forecast_days=1"
))

# 3. Marine (avec fallback)
try:
    marine = json.loads(fetch(
        "https://marine-api.open-meteo.com/v1/marine"
        "?latitude=43.5714&longitude=-1.4697"
        "&current=wave_height,wave_period"
        "&timezone=Europe%2FParis"
    ))
    houle_m = str(round(marine["current"]["wave_height"], 2)) if marine["current"]["wave_height"] else ""
    houle_p = str(round(marine["current"]["wave_period"], 1)) if marine["current"]["wave_period"] else ""
except Exception:
    houle_m = ""
    houle_p = ""

# 4. Plages-landes (marees, drapeau)
html = fetch("https://www.plages-landes.info/ondres/")
marees_raw = list(re.finditer(r"(Haute|Basse)</span>\s*<span[^>]*>(\d{2}h\d{2})</span>", html))
marees = [{"type": m.group(1), "heure": m.group(2)} for m in marees_raw[:4]]
coef_m = re.search(r"COEF\.?&nbsp;\s*<span[^>]*>([\d\s/]+)</span>", html)
coef = coef_m.group(1).strip() if coef_m else ""

if plage.get("drapeau_vert"):    drapeau = "vert"
elif plage.get("drapeau_jaune"): drapeau = "jaune"
elif plage.get("drapeau_rouge"): drapeau = "rouge"
else:                            drapeau = "nc"

surv_label = "Baignade non surveillee"
surv_h = plage.get("surveillance_h", {})
if isinstance(surv_h, dict) and surv_h.get("deb") and surv_h.get("fin"):
    surv_label = f"Surveillee {surv_h['deb']} - {surv_h['fin']}"

WMO = {0:"Ciel degage",1:"Peu nuageux",2:"Partiellement nuageux",3:"Couvert",
       45:"Brouillard",51:"Bruine legere",53:"Bruine moderee",61:"Pluie legere",
       63:"Pluie moderee",65:"Pluie forte",80:"Averses",95:"Orage"}

cur = om["current"]
daily = om["daily"]
uv_val = cur["uv_index"]
uv_max = daily["uv_index_max"][0]
uv_display = uv_max if uv_val == 0 else uv_val

meteo = {
    "updated":       datetime.now().strftime("%Y-%m-%dT%H:%M"),
    "temp_air":      commune.get("meteo_temp_air") or str(round(cur["temperature_2m"])),
    "meteo_picto":   commune.get("meteo_picto", ""),
    "meteo_label":   WMO.get(cur["weather_code"], ""),
    "temp_eau":      plage.get("temp_eau", "NC"),
    "vent_kmh":      str(round(cur["wind_speed_10m"])),
    "vent_deg":      str(round(cur["wind_direction_10m"])),
    "vent_cardinal": deg_to_cardinal(round(cur["wind_direction_10m"])),
    "uv":            str(round(uv_display, 1)),
    "uv_label":      uv_label(uv_display),
    "lever":         daily["sunrise"][0][11:16],
    "coucher":       daily["sunset"][0][11:16],
    "houle_m":       houle_m,
    "houle_periode": houle_p,
    "marees":        marees,
    "maree_coef":    coef,
    "drapeau":       drapeau,
    "surv_label":    surv_label,
}

# 5. Google Sheets animations
animations = []
try:
    sheet_url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        "/gviz/tq?tqx=out:csv&sheet=Programme%20du%20jour"
    )
    sheet_data = fetch(sheet_url)
    reader = csv.reader(io.StringIO(sheet_data))
    rows = list(reader)
    for row in rows[4:]:
        if not row or not row[0].strip() or ":" not in row[0]: continue
        heure = row[0].strip()
        if len(heure) > 6: continue
        anim = {
            "heure": heure,
            "emoji": row[1].strip() if len(row) > 1 else "",
            "fr":    row[2].strip() if len(row) > 2 else "",
            "en":    row[3].strip() if len(row) > 3 else "",
            "es":    row[4].strip() if len(row) > 4 else "",
            "lieu":  row[5].strip() if len(row) > 5 else "",
        }
        if anim["fr"]:
            animations.append(anim)
except Exception:
    pass

print(json.dumps({"meteo": meteo, "animations": animations}, ensure_ascii=False, indent=2))
