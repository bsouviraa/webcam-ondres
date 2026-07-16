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

# ── METAR LFBZ — température air + vent live (2 tentatives + secours NOAA) ───
metar_temp = ""
metar_vent_kmh = ""
metar_vent_deg = ""
try:
    metar_data = None
    for _att in range(2):
        try:
            _mreq = urllib.request.Request(
                "https://aviationweather.gov/api/data/metar?ids=LFBZ&format=json",
                headers={"User-Agent": "Mozilla/5.0"})
            metar_data = json.loads(urllib.request.urlopen(_mreq, timeout=30).read())
            break
        except Exception:
            metar_data = None
    if metar_data and isinstance(metar_data, list):
        m = metar_data[0]
        if m.get("temp") is not None:
            metar_temp = str(int(round(m["temp"])))
        if isinstance(m.get("wspd"), (int, float)):
            metar_vent_kmh = str(int(round(m["wspd"] * 1.852)))  # noeuds → km/h
        if isinstance(m.get("wdir"), (int, float)):
            metar_vent_deg = str(int(m["wdir"]))  # "VRB" (vent variable) → ignoré
    if not metar_temp:
        # Secours : METAR brut NOAA (ex: "LFBZ 052030Z AUTO 24004KT CAVOK 23/18 Q1021")
        _raw = urllib.request.urlopen(urllib.request.Request(
            "https://tgftp.nws.noaa.gov/data/observations/metar/stations/LFBZ.TXT",
            headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8", errors="ignore")
        _t = re.search(r"\s(M?\d{2})/(M?\d{2})\b", _raw)
        if _t:
            metar_temp = str(int(_t.group(1).replace("M", "-")))
        _w = re.search(r"\s(\d{3})(\d{2,3})KT", _raw)
        if _w:
            metar_vent_deg = str(int(_w.group(1)))
            metar_vent_kmh = str(int(round(int(_w.group(2)) * 1.852)))
except Exception as _me:
    import sys; print(f"METAR error: {_me}", file=sys.stderr)

meteo = {
    "updated":       datetime.now().strftime("%Y-%m-%dT%H:%M"),
    "temp_air":      metar_temp or str(round(cur["temperature_2m"])),
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

# ── 2. Animations depuis le fichier Drive (jour + lendemain) ─────────────────
animations = []
animations_demain = []
anim_date = ""
anim_date_demain = ""

try:
    paris_now = datetime.now(timezone(timedelta(hours=2)))
    demain    = paris_now + timedelta(days=1)
    anim_date        = paris_now.strftime("%d/%m")
    anim_date_demain = demain.strftime("%d/%m")

    # Export xlsx brut (anti-cache, formats sales OK) — un seul téléchargement
    import zipfile as _zip
    _xlsx = urllib.request.urlopen(urllib.request.Request(
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx",
        headers={"User-Agent": "Mozilla/5.0"}), timeout=25).read()
    _zf = _zip.ZipFile(io.BytesIO(_xlsx))
    _wb   = _zf.read('xl/workbook.xml').decode('utf-8', errors='ignore')
    _rels = _zf.read('xl/_rels/workbook.xml.rels').decode('utf-8', errors='ignore')
    _shared = []
    try:
        _ss = _zf.read('xl/sharedStrings.xml').decode('utf-8', errors='ignore')
        for _si in re.findall(r'<si>([\s\S]*?)</si>', _ss):
            _shared.append(''.join(re.findall(r'<t[^>]*>([^<]*)</t>', _si)))
    except KeyError:
        pass

    def _norm_heure(h):
        # Heure Excel numérique (0.3958 → 09:30) ou texte sale ("15h:00" → "15:00")
        try:
            _f = float(h)
            if 0 < _f < 1.5:
                _tot = round(_f * 24 * 60)
                return "{:02d}:{:02d}".format((_tot // 60) % 24, _tot % 60)
        except (ValueError, TypeError):
            pass
        _m = re.search(r"(\d{1,2})\D{0,2}(\d{2})", str(h))
        return "{:02d}:{}".format(int(_m.group(1)), _m.group(2)) if _m else None

    def _parse_onglet(sheet_name):
        """Retourne les animations d'un onglet JJMM, [] si l'onglet n'existe pas."""
        _sm = re.search(r'<sheet name="' + re.escape(sheet_name) + r'"[^>]*r:id="(rId\d+)"', _wb)
        if not _sm:
            return []
        _tm = re.search(r'<Relationship Id="' + _sm.group(1) + r'"[^>]*Target="([^"]+)"', _rels)
        _target = _tm.group(1).lstrip('/')
        if not _target.startswith('xl/'):
            _target = 'xl/' + _target
        _sheet_xml = _zf.read(_target).decode('utf-8', errors='ignore')
        _cells = {}
        for _cm in re.finditer(r'<c ([^>]*?)(?:/>|>([\s\S]*?)</c>)', _sheet_xml):
            _attrs, _inner = _cm.group(1), _cm.group(2) or ''
            _rm = re.search(r'r="([A-Z]+)(\d+)"', _attrs)
            _vm = re.search(r'<v>([^<]*)</v>', _inner)
            if not _rm or not _vm:
                continue
            _v = _shared[int(_vm.group(1))] if 't="s"' in _attrs and int(_vm.group(1)) < len(_shared) else _vm.group(1)
            _cells.setdefault(int(_rm.group(2)), {})[_rm.group(1)] = _v

        # Détection du format via l'en-tête (ancien 4 col. ou nouveau 6 col.)
        _col_en, _col_es, _col_lieu = None, None, 'D'
        for _rn in sorted(_cells.keys()):
            _row = _cells[_rn]
            if 'heure' in str(_row.get('A', '')).lower():
                for _c, _val in _row.items():
                    _vl = str(_val).lower()
                    if 'english' in _vl:  _col_en = _c
                    elif 'espa' in _vl:   _col_es = _c
                    elif 'lieu' in _vl:   _col_lieu = _c
                break

        _out = []
        for _rn in sorted(_cells.keys()):
            _row = _cells[_rn]
            _heure = _norm_heure(_row.get('A', ''))
            _fr = (_row.get('C') or '').strip()
            if not _heure or not _fr:
                continue
            if 'exemple' in _fr.lower():
                continue
            _out.append({
                "heure":   _heure,
                "emoji":   (_row.get('B') or '').strip(),
                "fr":      _fr,
                "en":      (_row.get(_col_en) or '').strip() if _col_en else "",
                "es":      (_row.get(_col_es) or '').strip() if _col_es else "",
                "lieu":    (_row.get(_col_lieu) or '').strip(),
                "lieu_en": "", "lieu_es": "",
            })
        _out.sort(key=lambda a: a["heure"])
        return _out

    animations        = _parse_onglet(paris_now.strftime("%d%m"))
    animations_demain = _parse_onglet(demain.strftime("%d%m"))

    # ── 3. Traduction via API Claude (jour + lendemain, cases vides uniquement) ──
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    _all_anims = animations + animations_demain
    if _all_anims and anthropic_key:
        try:
            texts = []
            for a in _all_anims:
                texts.append(a["fr"])
                if a["lieu"]:
                    texts.append(a["lieu"])
            translations = translate_batch(texts, anthropic_key)
            tidx = 0
            for a in _all_anims:
                if tidx < len(translations):
                    if not a["en"]: a["en"] = translations[tidx].get("en", "")
                    if not a["es"]: a["es"] = translations[tidx].get("es", "")
                    tidx += 1
                if a["lieu"] and tidx < len(translations):
                    a["lieu_en"] = translations[tidx].get("en", "")
                    a["lieu_es"] = translations[tidx].get("es", "")
                    tidx += 1
        except Exception as te:
            import sys; print(f"Traduction error: {te}", file=sys.stderr)

except Exception as e:
    import sys; print(f"Animations error: {e}", file=sys.stderr)

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
        transports[_k] = _upcoming[:8] if _upcoming else _sorted[-3:]
except Exception as te:
    import sys; print(f"Transport error: {te}", file=sys.stderr)


# ── 5. Bus Txik Txak (lignes 23, 29, I-fêtes) ────────────────────────────────
bus = {"plage": [], "ondres_bourg": [], "st_martin": [], "bayonne": [], "fetes_bayonne": []}
try:
    import zipfile, io as _io
    import time as _time
    _gtfs_data = None
    for _gt_att in range(3):
        try:
            _gtfs_data = urllib.request.urlopen(
                urllib.request.Request(
                    "https://www.data.gouv.fr/api/1/datasets/r/011b5a77-604b-4e12-bf8a-c944164acdd6",
                    headers={"User-Agent": "Mozilla/5.0"}
                ), timeout=30
            ).read()
            break
        except Exception:
            _time.sleep(8)
    if _gtfs_data is None:
        raise ValueError("data.gouv indisponible")
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
        # Jusqu'à 10 horaires à venir + les 3 premiers du lendemain :
        # le front filtre lui-même les heures passées (data.json peut avoir du retard)
        s = sorted(set(filter(None, times)))
        upcoming = [t for t in s if t >= _now]
        return (upcoming + [t + "+1" for t in s[:3]])[:10]

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
        _FETES_FILTER_ACTIF = True  # Affichage limité aux dates des fêtes lues dans le calendrier NeTEx
        if _FETES_FILTER_ACTIF and not any(f <= _today <= t for f, t in _per):
            _fetes_bayonne = []
    except Exception as _fe:
        import sys; print(f"Fetes cal error: {_fe}", file=sys.stderr)

    bus["fetes_bayonne"]  = _next3(_fetes_bayonne) if _fetes_bayonne else []

except Exception as _be:
    import sys; print(f"Bus error: {_be}", file=sys.stderr)
    # Filet : reprendre les horaires du data.json précédent plutôt que d'afficher vide
    try:
        import os as _os, json as _json2
        if _os.path.exists("data.json") and not any(bus.values()):
            bus = _json2.load(open("data.json")).get("bus", bus)
    except Exception:
        pass

print(json.dumps({"meteo": meteo, "animations": animations, "anim_date": anim_date, "animations_demain": animations_demain, "anim_date_demain": anim_date_demain, "transports": transports, "bus": bus},
                 ensure_ascii=False, indent=2))
