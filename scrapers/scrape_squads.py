#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_squads.py — Convocados oficiales del Mundial 2026 (FIFA)
==============================================================
Fuente: API pública de FIFA (api.fifa.com/api/v3), sin autenticación.
  - Competición 17  = Copa Mundial de la FIFA
  - Temporada  285023 = Copa Mundial de la FIFA 2026

Para los 48 equipos clasificados extrae los 26 convocados:
    número, nombre, posición (GK/DEF/MID/FWD) y club (best-effort).
El entrenador sale de Officials (Role == 1).
El club NO viene en la API de FIFA: se enriquece por nombre desde los
datos curados que ya existen inline en index.html.

Salida:
    data/squads.json          — keyed por código FIFA de 3 letras (ARG, MEX, ...)
    data/scrape_log.txt       — log de la corrida (append)

Uso:
    python3 scrapers/scrape_squads.py
"""

import sys
import re
import json
import time
import unicodedata
from pathlib import Path
from datetime import datetime, timezone

import requests

# ── rutas ────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
DATA_DIR  = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
OUT_FILE  = DATA_DIR / "squads.json"
LOG_FILE  = DATA_DIR / "scrape_log.txt"
INDEX     = ROOT / "index.html"

# ── FIFA ─────────────────────────────────────────────────────────────────────
COMP, SEASON = "17", "285023"
BASE = "https://api.fifa.com/api/v3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
POS_MAP = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD"}  # FIFA Position code -> nuestro código


# ── logging ──────────────────────────────────────────────────────────────────
def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── HTTP con reintentos ──────────────────────────────────────────────────────
def get_json(url, tries=4, pause=1.5):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(pause * (i + 1))
    raise RuntimeError(f"fallo GET {url} :: {last}")


# ── normalización de nombres para matching ───────────────────────────────────
def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm_key(name):
    """Clave de matching: primer token + último token (nombre + apellido), completos.
    Usar tokens completos (no inicial) evita colisiones tipo Lisandro/Lautaro Martínez."""
    name = strip_accents(name).lower()
    name = re.sub(r"[^a-z\s]", " ", name)
    toks = [t for t in name.split() if t]
    if not toks:
        return ""
    if len(toks) == 1:
        return toks[0]
    return toks[0] + "|" + toks[-1]


def titlecase_fifa(name):
    """'Emiliano MARTINEZ' -> 'Emiliano Martinez' respetando partículas."""
    parts = name.split()
    small = {"de", "da", "do", "van", "von", "der", "den", "la", "le", "el", "al", "bin", "ben"}
    out = []
    for i, p in enumerate(parts):
        low = strip_accents(p).lower()
        if i != 0 and low in small:
            out.append(low)
        elif "-" in p:
            out.append("-".join(w.capitalize() for w in p.split("-")))
        else:
            out.append(p.capitalize())
    return " ".join(out)


# ── enriquecimiento de club desde index.html (datos inline curados) ───────────
def build_club_namemap():
    """
    Lee index.html y arma mapas de club/nombre-bonito POR EQUIPO (namespace por
    código de selección) para evitar colisiones de apellidos entre equipos:
       club_by_code = { 'ARG': { norm_key: club }, ... }
       name_by_code = { 'ARG': { norm_key: nombre_con_acentos }, ... }
    Los bloques de equipo se delimitan por nameCode:'XXX'; cada mkP(...) que
    sigue pertenece a ese equipo hasta el próximo nameCode.
    mkP(id, name, short, pos, jer, age, club, nat, ...)
    """
    club_by_code, name_by_code = {}, {}
    if not INDEX.exists():
        return club_by_code, name_by_code
    txt = INDEX.read_text(encoding="utf-8", errors="ignore")

    code_pat = re.compile(r"nameCode:'([A-Z]{2,4})'")
    mkp_pat = re.compile(
        r"mkP\(\s*\d+\s*,\s*'([^']*)'\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,"
        r"\s*\d+\s*,\s*\d+\s*,\s*'([^']*)'"
    )
    # posiciones de inicio de cada bloque de equipo
    marks = [(m.start(), m.group(1)) for m in code_pat.finditer(txt)]
    marks.append((len(txt), None))
    for i in range(len(marks) - 1):
        start, code = marks[i]
        end = marks[i + 1][0]
        if not code:
            continue
        cm = club_by_code.setdefault(code, {})
        nm = name_by_code.setdefault(code, {})
        for pm in mkp_pat.finditer(txt, start, end):
            name, club = pm.group(1), pm.group(2)
            k = norm_key(name)
            if k:
                cm[k] = club
                nm[k] = name
    return club_by_code, name_by_code


# ── obtención de equipos (48) desde el calendario ─────────────────────────────
def fetch_teams():
    url = f"{BASE}/calendar/matches?idCompetition={COMP}&idSeason={SEASON}&language=es&count=400"
    data = get_json(url)
    teams = {}
    for m in data.get("Results", []):
        for side in ("Home", "Away"):
            t = m.get(side) or {}
            code = t.get("IdCountry") or t.get("Abbreviation")
            if not code or code in teams:
                continue
            grp = ""
            gn = m.get("GroupName") or []
            if gn:
                desc = gn[0].get("Description", "")
                mm = re.search(r"([A-L])\s*$", desc)
                grp = mm.group(1) if mm else desc.replace("Grupo", "").strip()
            name = ""
            tn = t.get("TeamName") or []
            if tn:
                name = tn[0].get("Description", "")
            teams[code] = {
                "code": code,
                "fifa_id": t.get("IdTeam"),
                "name": name,
                "group": grp,
            }
    return teams


def coach_of(squad_json):
    for o in squad_json.get("Officials", []) or []:
        if o.get("Role") == 0:  # head coach (Role 1 = asistentes)
            nm = o.get("Name") or []
            if nm:
                return titlecase_fifa(nm[0].get("Description", ""))
    return None


def fetch_squad(team, club_map, name_map):
    """club_map / name_map son los dicts del equipo (norm_key -> club / nombre)."""
    tid = team["fifa_id"]
    url = f"{BASE}/teams/{tid}/squad?idCompetition={COMP}&idSeason={SEASON}&language=es"
    data = get_json(url)
    players, matched = [], 0
    for p in data.get("Players", []) or []:
        raw = (p.get("PlayerName") or [{}])[0].get("Description", "")
        k = norm_key(raw)
        display = name_map.get(k) or titlecase_fifa(raw)
        club = club_map.get(k)
        if club:
            matched += 1
        players.append({
            "number": p.get("JerseyNum"),
            "name": display,
            "position": POS_MAP.get(p.get("Position"), "MID"),
            "club": club,
            "fifa_id": p.get("IdPlayer"),
            "birth": (p.get("BirthDate") or "")[:10] or None,
        })
    players.sort(key=lambda x: (["GK", "DEF", "MID", "FWD"].index(x["position"]),
                                x["number"] or 99))
    coach = coach_of(data)
    return players, coach, matched


def main():
    t0 = time.time()
    log("===== scrape_squads.py START =====")
    club_by_code, name_by_code = build_club_namemap()
    log(f"club-map inline: {sum(len(v) for v in club_by_code.values())} jugadores "
        f"en {len(club_by_code)} equipos")

    try:
        teams = fetch_teams()
    except Exception as e:  # noqa: BLE001
        log(f"FATAL no se pudo obtener la lista de equipos: {e}")
        sys.exit(1)
    log(f"equipos encontrados: {len(teams)}")

    out = {}
    ok = fail = 0
    for i, (code, team) in enumerate(sorted(teams.items()), 1):
        try:
            players, coach, matched = fetch_squad(
                team, club_by_code.get(code, {}), name_by_code.get(code, {}))
            if len(players) < 11:
                log(f"  ! {code}: solo {len(players)} jugadores (squad incompleto en FIFA)")
            out[code] = {
                "name": team["name"],
                "group": team["group"],
                "coach": coach,
                "fifa_id": team["fifa_id"],
                "players": players,
            }
            ok += 1
            log(f"  [{i:2}/{len(teams)}] {code} {team['name']:<22} "
                f"{len(players)} jug · club match {matched}/{len(players)} · DT {coach or '—'}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            log(f"  [{i:2}/{len(teams)}] {code} ERROR: {e}")
        time.sleep(0.6)  # cortesía

    # orden por grupo y código para un JSON estable
    ordered = dict(sorted(out.items(), key=lambda kv: (kv[1]["group"], kv[0])))
    OUT_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"escrito {OUT_FILE.relative_to(ROOT)} · {ok} equipos OK · {fail} fallidos "
        f"· {time.time()-t0:.1f}s")
    log("===== scrape_squads.py END =====\n")


if __name__ == "__main__":
    main()
