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

html_page = fetch("https://www.plages-landes.info/ondres/")
marees_raw = list(re.finditer(r"(Haute|Basse)</span>\s*<span[^>]*>(\d{2}h\d{2})</span>", html_page))
marees = [{"type": m.group(1), "heure": m.group(2)} for m in marees_raw[:4]]
coef_m = re.search(r"COEF\.?&nbsp;\s*<span[^>]*>([\d\s/]+)</span>", html_page)
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
       45:"Brouillard",51:"Bruine legere",61:"Pluie legere",63:"Pluie moderee",80:"Averses",95:"Orage"}

cur = om["current"]; daily = om["daily"]
uv_val = cur["uv_index"]; uv_max = daily["uv_index_max"][0]
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
            if a["lieu"] and t_idx < len(translations):
                a["lieu_en"] = translations[t_idx].get("en", "")
                a["lieu_es"] = translations[t_idx].get("es", "")
                t_idx += 1

except Exception as e:
    import sys; print(f"Animations error: {e}", file=sys.stderr)
    # anim_date conserve sa valeur si elle avait été assignée

print(json.dumps({"meteo": meteo, "animations": animations, "anim_date": anim_date},
                 ensure_ascii=False, indent=2))
