import urllib.request, re, json, csv, io, os
from datetime import datetime, timezone, timedelta

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

def translate_batch(texts, anthropic_key):
    """Traduit une liste de textes FR → EN + ES via l'API Claude."""
    if not texts or not anthropic_key:
        return [{"en":"","es":""} for _ in texts]
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "Traduis ces textes courts du français vers l'anglais et l'espagnol.\n"
        "Réponds UNIQUEMENT avec un tableau JSON valide, un objet par texte dans l'ordre.\n"
        'Format strict : [{"en":"...","es":"..."}]\n'
        "Textes :\n"
        + numbered
    )
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=20)
    data = json.loads(resp.read())
    raw = data["content"][0]["text"].strip()
    raw = raw.replace("```json","").replace("```","").strip()
    return json.loads(raw)

# ── 1. Météo ──────────────────────────────────────────────────────────────────
ms = json.loads(urllib.request.urlopen(
    urllib.request.Request(
        "https://meiliprod144.apsulis.fr/indexes/pdl-prod-fr/search",
        data=json.dumps({"q": "Ondres", "limit": 5}).encode(),
        headers={
            "Authorization": "Bearer cfd3463ba673bbd7fae56e71b74cd067d671ab37ef188fb62e5c7b35c698b9f6",
            "Content-Type": "application/json"
        },
        method="POST"
    ), timeout=10
).read())

commune = next((h for h in ms["hits"] if h["type"] == "commune" and h["title"] == "Ondres"), {})
plage   = next((h for h in ms["hits"] if h["type"] == "plages"), {})

om = json.loads(fetch(
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=43.5714&longitude=-1.4697"
    "&current=temperature_2m,wind_speed_10m,wind_direction_10m,uv_index,weather_code"
    "&daily=sunrise,sunset,uv_index_max"
    "&timezone=Europe%2FParis&forecast_days=1"
))

try:
    marine = json.loads(fetch(
        "https://marine-api.open-meteo.com/v1/marine"
        "?latitude=43.5714&longitude=-1.4697"
        "&current=wave_height,wave_period&timezone=Europe%2FParis"
    ))
    houle_m = str(round(marine["current"]["wave_height"], 2)) if marine["current"]["wave_height"] else ""
    houle_p = str(round(marine["current"]["wave_period"], 1)) if marine["current"]["wave_period"] else ""
except Exception:
    houle_m = ""; houle_p = ""

# Charger les coefs précédents comme fallback
coef = ""; coef_am = ""; coef_pm = ""; marees = []
try:
    import os, json as _json
    if os.path.exists("data.json"):
        _prev = _json.load(open("data.json"))
        coef    = _prev.get("maree_coef", "")
        coef_am = _prev.get("maree_coef_am", "")
        coef_pm = _prev.get("maree_coef_pm", "")
        marees  = _prev.get("marees", [])
except Exception:
    pass

try:
    html_page = fetch("https://www.plages-landes.info/ondres/")
    marees_raw = list(re.finditer(r"(Haute|Basse)</span>\s*<span[^>]*>(\d{2}h\d{2})</span>", html_page))
    if marees_raw:
        marees = [{"type": m.group(1), "heure": m.group(2)} for m in marees_raw[:4]]
    coef_m = re.search(r"am\s+(\d+)\s*/\s*pm\s+(\d+)", html_page, re.I)
    if coef_m:
        coef_am = coef_m.group(1).strip()
        coef_pm = coef_m.group(2).strip()
        coef = coef_am + " / " + coef_pm
except Exception as _e:
    import sys; print(f"Plages-landes error (coefs conservés): {_e}", file=sys.stderr)

_drap = plage.get("drapeau", "")
if _drap in ("vert", "jaune", "rouge"):  drapeau = _drap
elif plage.get("drapeau_vert"):          drapeau = "vert"
elif plage.get("drapeau_jaune"):         drapeau = "jaune"
elif plage.get("drapeau_rouge"):         drapeau = "rouge"
else:                                    drapeau = "nc"

surv_label = "Baignade non surveillee"
surv_h = plage.get("surveillance_h", {})
if isinstance(surv_h, dict) and surv_h.get("deb") and surv_h.get("fin"):
    surv_label = f"Surveillee {surv_h['deb']} - {surv_h['fin']}"

WMO = {0:"Ciel degage",1:"Peu nuageux",2:"Partiellement nuageux",3:"Couvert",
       45:"Brouillard",51:"Bruine legere",61:"Pluie legere",63:"Pluie moderee",80:"Averses",95:"Orage"}

cur = om["current"]; daily = om["daily"]
uv_val = cur["uv_index"]; uv_max = daily["uv_index_max"][0]
uv_display = uv_max if uv_val == 0 else uv_val

# ── METAR LFBZ — température air + vent live ─────────────────────────────────
metar_temp = ""
metar_vent_kmh = ""
metar_vent_deg = ""
try:
    metar_data = json.loads(fetch("https://aviationweather.gov/api/data/metar?ids=LFBZ&format=json"))
    if metar_data and isinstance(metar_data, list):
        m = metar_data[0]
        if m.get("temp") is not None:
            metar_temp = str(int(round(m["temp"])))
        if m.get("wspd") is not None:
            metar_vent_kmh = str(int(round(m["wspd"] * 1.852)))  # noeuds → km/h
        if m.get("wdir") is not None:
            metar_vent_deg = str(int(m["wdir"]))
except Exception as _me:
    import sys; print(f"METAR error: {_me}", file=sys.stderr)

meteo = {
    "updated":       datetime.now().strftime("%Y-%m-%dT%H:%M"),
    "temp_air":      metar_temp or commune.get("meteo_temp_air") or str(round(cur["temperature_2m"])),
    "meteo_picto":   commune.get("meteo_picto", ""),
    "meteo_label":   WMO.get(cur["weather_code"], ""),
    "temp_eau":      str(plage.get("temp_eau", "NC")) if plage.get("temp_eau") else "NC",
    # Vent : METAR LFBZ en priorité, fallback Open-Meteo
    "vent_kmh":      metar_vent_kmh or str(round(cur["wind_speed_10m"])),
    "vent_deg":      metar_vent_deg or str(round(cur["wind_direction_10m"])),
    "vent_cardinal": deg_to_cardinal(int(metar_vent_deg) if metar_vent_deg else round(cur["wind_direction_10m"])),
    "uv":            str(round(uv_display, 1)),
    "uv_label":      uv_label(uv_display),
    "lever":         daily["sunrise"][0][11:16],
    "coucher":       daily["sunset"][0][11:16],
    "houle_m":       houle_m,
    "houle_periode": houle_p,
    "marees":        marees,
    "maree_coef":    coef,
    "maree_coef_am": coef_am,
    "maree_coef_pm": coef_pm,
    "drapeau":       drapeau,
    "surv_label":    surv_label,
}

# ── 2. Animations depuis Google Sheets ───────────────────────────────────────
animations = []
anim_date = ""

try:
    paris_now = datetime.now(timezone(timedelta(hours=2)))
    sheet_name = paris_now.strftime("%d%m")
    anim_date  = paris_now.strftime("%d/%m")

    # Vérifier que la feuille existe
    html_sheets = fetch(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/htmlview")
    available = re.findall(r'{name:\s*"([^"]+)"', html_sheets)
    if sheet_name not in available:
        raise ValueError(f"Feuille {sheet_name} introuvable")

    sheet_url = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
                 f"/gviz/tq?tqx=out:csv&sheet={sheet_name}")
    sheet_data = fetch(sheet_url)
    reader = csv.reader(io.StringIO(sheet_data))
    rows = list(reader)

    # Parser robuste : chercher toutes les lignes avec format HH:MM
    for row in rows:
        if not row or not row[0].strip(): continue
        heure = row[0].strip()
        # Valider le format HH:MM
        import re as _re
        if not _re.match(r'^\d{1,2}:\d{2}$', heure): continue
        if not (len(row) > 2 and row[2].strip()): continue
        animations.append({
            "heure":   heure,
            "emoji":   row[1].strip() if len(row) > 1 else "",
            "fr":      row[2].strip(),
            "lieu":    row[3].strip() if len(row) > 3 else "",
            "en":      "", "es": "",
            "lieu_en": "", "lieu_es": "",
        })

    # Trier par heure (au cas où le Sheet ne serait pas dans l'ordre)
    animations.sort(key=lambda a: a["heure"])

    # ── 3. Traduction via API Claude ──────────────────────────────────────────
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if animations and anthropic_key:
        try:
            texts = []
            for a in animations:
                texts.append(a["fr"])
                if a["lieu"]:
                    texts.append(a["lieu"])
            translations = translate_batch(texts, anthropic_key)
            tidx = 0
            for a in animations:
                if tidx < len(translations):
                    a["en"] = translations[tidx].get("en", "")
                    a["es"] = translations[tidx].get("es", "")
                    tidx += 1
                if a["lieu"] and tidx < len(translations):
                    a["lieu_en"] = translations[tidx].get("en", "")
                    a["lieu_es"] = translations[tidx].get("es", "")
                    tidx += 1
        except Exception as te:
            import sys; print(f"Traduction error: {te}", file=sys.stderr)
            if a["lieu"] and tidx < len(translations):
                a["lieu_en"] = translations[tidx].get("en", "")
                a["lieu_es"] = translations[tidx].get("es", "")
                tidx += 1

except Exception as e:
    import sys; print(f"Animations error: {e}", file=sys.stderr)
    # anim_date conserve sa valeur si elle avait été assignée

# ── 4. Transports SNCF ───────────────────────────────────────────────────────
transports = {"bayonne": [], "dax": []}
try:
    import base64 as b64
    SNCF_KEY = "bf3caae1-0cae-4911-877c-501736a4643a"
    STOP_ID  = "stop_area:SNCF:87673319"
    auth_b64 = b64.b64encode(f"{SNCF_KEY}:".encode()).decode()
    sncf_url = f"https://api.sncf.com/v1/coverage/sncf/stop_areas/{STOP_ID}/departures?count=30"
    sncf_req = urllib.request.Request(sncf_url, headers={
        "Authorization": f"Basic {auth_b64}",
        "Accept": "application/json"
    })
    sncf_data = json.loads(urllib.request.urlopen(sncf_req, timeout=10).read())
    from datetime import datetime as _dt
    for d in sncf_data.get("departures", []):
        info = d.get("display_informations", {})
        stop = d.get("stop_date_time", {})
        direction = info.get("direction", "")
        dt_str = stop.get("departure_date_time", "")
        if not dt_str: continue
        hhmm = _dt.strptime(dt_str, "%Y%m%dT%H%M%S").strftime("%H:%M")
        if "Dax" in direction and len(transports["dax"]) < 3:
            transports["dax"].append(hhmm)
        elif ("Hendaye" in direction or "Bayonne" in direction) and len(transports["bayonne"]) < 3:
            transports["bayonne"].append(hhmm)
        if len(transports["dax"]) >= 3 and len(transports["bayonne"]) >= 3:
            break
    # Trier et garder seulement les 3 prochains
    from datetime import datetime as _dt2
    _now_hm = _dt2.now(timezone(timedelta(hours=2))).strftime("%H:%M")
    for _k in ["bayonne", "dax"]:
        _sorted = sorted(set(transports[_k]))
        _upcoming = [t for t in _sorted if t >= _now_hm]
        transports[_k] = _upcoming[:3] if _upcoming else _sorted[-3:]
except Exception as te:
    import sys; print(f"Transport error: {te}", file=sys.stderr)


# ── 5. Bus Txik Txak (lignes 23, 29, I-fêtes) ────────────────────────────────
bus = {"plage": [], "ondres_bourg": [], "st_martin": [], "bayonne": [], "fetes_bayonne": []}
try:
    import zipfile, io as _io
    _gtfs_data = urllib.request.urlopen(
        urllib.request.Request(
            "https://www.data.gouv.fr/api/1/datasets/r/011b5a77-604b-4e12-bf8a-c944164acdd6",
            headers={"User-Agent": "Mozilla/5.0"}
        ), timeout=20
    ).read()
    _zf = zipfile.ZipFile(_io.BytesIO(_gtfs_data))
    _now = datetime.now(timezone(timedelta(hours=2))).strftime('%H:%M')

    def _first(j):
        ts = re.findall(r'<DepartureTime>(\d{2}:\d{2}:\d{2})</DepartureTime>', j)
        return ts[0][:5] if ts else None
    def _last(j):
        ts = re.findall(r'<DepartureTime>(\d{2}:\d{2}:\d{2})</DepartureTime>', j)
        return ts[-1][:5] if ts else None
    def _dest(j):
        m = re.search(r'<Name>([^<]+)</Name>', j)
        return m.group(1) if m else ''
    def _next3(times):
        s = sorted(set(filter(None, times)))
        upcoming = [t for t in s if t >= _now]
        if upcoming:
            return upcoming[:3]
        # Plus de passage aujourd'hui → 3 premiers de demain, préfixés d'un indicateur
        return [t + "+1" for t in s[:3]]

    # ── Ligne 23 ──────────────────────────────────────────────────────────────
    _j23 = re.findall(r'<ServiceJourney [^>]*>([\s\S]*?)</ServiceJourney>',
        _zf.read('CA_PAYS_BASQUE_offre_Bus_TXIKTXAK_23_23.xml').decode('utf-8', errors='ignore'))

    _plage23, _bourg23, _bayonne23 = [], [], []
    for j in _j23:
        d = _dest(j)
        if 'Ondres Oc' in d:     _plage23.append(_last(j))
        elif 'Ondres Cap' in d:  _bourg23.append(_last(j))
        elif 'Mendiburua' in d:  _bayonne23.append(_first(j))

    # ── Ligne 29 ──────────────────────────────────────────────────────────────
    _j29 = re.findall(r'<ServiceJourney [^>]*>([\s\S]*?)</ServiceJourney>',
        _zf.read('CA_PAYS_BASQUE_offre_Bus_TXIKTXAK_29_29.xml').decode('utf-8', errors='ignore'))

    _plage29, _bourg29, _stmartin29 = [], [], []
    for j in _j29:
        d = _dest(j)
        if 'Capranie' in d:     _bourg29.append(_last(j))
        elif 'Ambroise' in d:   _stmartin29.append(_first(j))

    # ── Ligne I (fêtes de Bayonne) ────────────────────────────────────────────
    _ji = re.findall(r'<ServiceJourney [^>]*>([\s\S]*?)</ServiceJourney>',
        _zf.read('CA_PAYS_BASQUE_offre_Bus_TXIKTXAK_I_I.xml').decode('utf-8', errors='ignore'))

    _fetes_bayonne = []
    for j in _ji:
        if '124262F8A44A0503333643937617DA58' in j:
            _fetes_bayonne.append(_first(j))

    # ── Résultat ──────────────────────────────────────────────────────────────
    bus["plage"]          = _next3(_plage23)
    bus["ondres_bourg"]   = _next3(_bourg23 + _bourg29)
    bus["st_martin"]      = _next3(_stmartin29)
    bus["bayonne"]        = _next3(_bayonne23)
    # Fêtes de Bayonne : uniquement pendant la période du calendrier NeTEx (ligne I)
    try:
        _cal = _zf.read('CA_PAYS_BASQUE_calendriers.xml').decode('utf-8', errors='ignore')
        _per = re.findall(r'OperatingPeriod:I_FB[^"]*"[\s\S]{0,300}?<FromDate>(\d{4}-\d{2}-\d{2})[\s\S]{0,120}?<ToDate>(\d{4}-\d{2}-\d{2})', _cal)
        _today = datetime.now(timezone(timedelta(hours=2))).strftime('%Y-%m-%d')
        if not any(f <= _today <= t for f, t in _per):
            _fetes_bayonne = []
    except Exception as _fe:
        import sys; print(f"Fetes cal error: {_fe}", file=sys.stderr)

    bus["fetes_bayonne"]  = _next3(_fetes_bayonne) if _fetes_bayonne else []

except Exception as _be:
    import sys; print(f"Bus error: {_be}", file=sys.stderr)

print(json.dumps({"meteo": meteo, "animations": animations, "anim_date": anim_date, "transports": transports, "bus": bus},
                 ensure_ascii=False, indent=2))
