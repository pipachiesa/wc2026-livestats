#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enrich_clubs_sofascore.py — completa el CLUB de cada convocado
==============================================================
La API de FIFA da nombre/número/posición pero NO el club. SofaScore sí:
el endpoint de plantel de selección incluye, por jugador, su club actual
(`team.name`). Este script:

  1. Lee los equipos del Mundial 2026 en SofaScore
     (unique-tournament 16 · season 58210) → {nameCode: idEquipo}
  2. Para cada selección baja /team/{id}/players y arma {nombre → club}
  3. Abre data/squads.json y rellena el campo "club" de cada jugador
     matcheando por nombre (primer+último token, sin acentos).

Es re-ejecutable (idempotente) y NO inventa datos: si SofaScore no tiene al
jugador, se deja el club previo (o null). Datos en vivo, cero training data.

Uso:
    python3 scrapers/enrich_clubs_sofascore.py
"""

import re
import json
import time
import unicodedata
from pathlib import Path
from datetime import datetime, timezone

import requests

ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SQUADS   = DATA_DIR / "squads.json"
LOG_FILE = DATA_DIR / "scrape_log.txt"

SOFA = "https://api.sofascore.com/api/v1"
UT, SEASON = "16", "58210"   # FIFA World Cup · 2026
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
# código FIFA (mío) -> nameCode de SofaScore cuando difieren
CODE_ALIAS = {"ALG": "DZA", "COD": "DCO", "CUW": "CUR",
              "MAR": "MOR", "IRN": "IRI", "IRQ": "IRA"}


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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
    raise RuntimeError(f"GET {url} :: {last}")


def _tokens(name):
    name = "".join(c for c in unicodedata.normalize("NFD", name or "")
                   if unicodedata.category(c) != "Mn").lower()
    name = re.sub(r"[^a-z\s]", " ", name)
    return [t for t in name.split() if t]


def name_keys(name):
    """Claves candidatas robustas a orden apellido/nombre (KOR, JPN, etc.)."""
    t = _tokens(name)
    if not t:
        return []
    keys = []
    if len(t) == 1:
        keys.append(t[0])
    else:
        keys.append(t[0] + "|" + t[-1])        # primer+último
        keys.append(t[-1] + "|" + t[0])        # invertido (orden asiático)
    keys.append("|".join(sorted(t)))           # conjunto ordenado de todos los tokens
    keys.append("".join(t))                    # colapsado en orden (Son Heung-min == Son Heungmin)
    keys.append("".join(t[::-1]))              # colapsado en orden invertido (Lee Kang-in / Kang-in Lee)
    return keys


def sofa_team_index():
    """{nameCode: (idEquipo, nombre)} a partir de las tablas del WC2026."""
    d = get_json(f"{SOFA}/unique-tournament/{UT}/season/{SEASON}/standings/total")
    idx = {}
    for grp in d.get("standings", []):
        for row in grp.get("rows", []):
            t = row["team"]
            idx[t.get("nameCode")] = (t["id"], t["name"])
    return idx


def club_map_for_team(team_id):
    """{norm_key(nombre): club} de la selección."""
    d = get_json(f"{SOFA}/team/{team_id}/players")
    out = {}
    for entry in d.get("players", []):
        p = entry.get("player") or {}
        club = (p.get("team") or {}).get("name")
        if not club:
            continue
        for k in name_keys(p.get("name", "")):
            out.setdefault(k, club)   # no pisar: la 1ª (más específica) gana
    return out


def main():
    log("===== enrich_clubs_sofascore.py START =====")
    squads = json.loads(SQUADS.read_text(encoding="utf-8"))
    try:
        sofa = sofa_team_index()
    except Exception as e:  # noqa: BLE001
        log(f"FATAL no se pudieron leer los equipos de SofaScore: {e}")
        return
    log(f"equipos SofaScore WC2026: {len(sofa)}")

    filled = total = teams_ok = teams_fail = 0
    for code, team in squads.items():
        sofa_code = CODE_ALIAS.get(code, code)
        ent = sofa.get(sofa_code)
        if not ent:
            log(f"  ! {code}: sin equipo SofaScore (code {sofa_code})")
            teams_fail += 1
            continue
        try:
            cmap = club_map_for_team(ent[0])
        except Exception as e:  # noqa: BLE001
            log(f"  ! {code}: error bajando plantel SofaScore: {e}")
            teams_fail += 1
            continue
        hit = 0
        for p in team["players"]:
            total += 1
            club = next((cmap[k] for k in name_keys(p["name"]) if k in cmap), None)
            if club:
                p["club"] = club        # SofaScore = fuente real preferida
                filled += 1
                hit += 1
        teams_ok += 1
        log(f"  {code:<4} {team['name']:<22} club {hit}/{len(team['players'])} (SofaScore {ent[1]})")
        time.sleep(0.5)

    SQUADS.write_text(json.dumps(squads, ensure_ascii=False, indent=2), encoding="utf-8")
    with_club = sum(1 for t in squads.values() for p in t["players"] if p.get("club"))
    log(f"OK: {teams_ok} equipos · {teams_fail} fallidos · {filled} clubes desde SofaScore")
    log(f"cobertura final de club: {with_club}/{total} ({100*with_club//max(total,1)}%)")
    log("===== enrich_clubs_sofascore.py END =====\n")


if __name__ == "__main__":
    main()
