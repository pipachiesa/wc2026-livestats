#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_player_stats_sofascore.py — stats REALES de jugador (SofaScore)
=====================================================================
Reemplaza el seed sintético de player_stats_season.json por datos reales
agregados desde SofaScore, SOLO de torneos oficiales relevantes (sin
amistosos):

  Copa América 2024 · Euro 2024 · Nations League UEFA 24/25 (Liga A + Final
  Four) · CONCACAF Nations League 2024 · AFCON 2024 · Copa Asia AFC 2024 ·
  OFC Nations Cup 2024 · Eliminatorias WC2026 (CONMEBOL, UEFA, CONCACAF,
  CAF, AFC, OFC) · World Cup 2026 (partidos ya jugados).

Endpoint: /unique-tournament/{ut}/season/{s}/statistics  (sin `group`, con
`fields` explícito) → trae todos los campos por jugador, paginado.

Las stats se SUMAN entre torneos; el rating se promedia ponderado por minutos.
Cada jugador se asigna a su selección por el id de equipo de SofaScore, así
no se cruzan jugadores entre países.

Salida: data/player_stats_season.json  (mismo formato que consume el front:
        {CODE: {claveNombre: {goals, assists, xg, ...}}})

Uso:  python3 scrapers/scrape_player_stats_sofascore.py
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
OUT      = DATA_DIR / "player_stats_season.json"
LOG_FILE = DATA_DIR / "scrape_log.txt"

SOFA = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
WC_UT, WC_SEASON = "16", "58210"

# (unique_tournament_id, season_id, nombre) — oficiales, sin amistosos.
# IDs verificados contra el endpoint de statistics (devuelven datos reales).
# Cobertura por confederación:
#   CONMEBOL: Copa América + Elim CONMEBOL · UEFA: Euro + Nations League + Elim UEFA
#   CONCACAF: Nations League + Elim CONCACAF · CAF: AFCON + Elim CAF
#   AFC: Copa Asia (sus elim. 2026 no tienen stats agregadas en SofaScore)
#   OFC: OFC Nations Cup
TOURNAMENTS = [
    (133,   57114, "Copa América 2024"),
    (1,     56953, "Euro 2024"),
    (10783, 58337, "UEFA Nations League 2024/25"),
    (14100, 61662, "CONCACAF Nations League 2024/25"),
    (270,   56021, "AFCON 2023 (ene-2024)"),
    (246,   51384, "AFC Asian Cup 2023 (ene-2024)"),
    (22716, 61582, "OFC Nations Cup 2024"),
    (11,    69427, "Eliminatorias UEFA 2026"),
    (295,   53820, "Eliminatorias CONMEBOL 2026"),
    (13,    56249, "Eliminatorias CAF 2026"),
    (14,    58146, "Eliminatorias CONCACAF 2026"),
    (int(WC_UT), int(WC_SEASON), "World Cup 2026 (en vivo)"),
]

FIELDS = ("goals,assists,expectedGoals,expectedAssists,totalShots,shotsOnTarget,"
          "minutesPlayed,appearances,tackles,interceptions,clearances,fouls,"
          "wasFouled,yellowCards,redCards,saves,keyPasses,bigChancesCreated,"
          "bigChancesMissed,rating")

# código FIFA (mío) -> nameCode SofaScore cuando difieren
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
            if r.status_code == 404:
                return None
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:120]
        time.sleep(pause * (i + 1))
    raise RuntimeError(f"GET {url} :: {last}")


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def name_keys(name):
    """Claves robustas a orden/guiones (igual que el enricher de clubes)."""
    n = strip_accents(name).lower()
    n = re.sub(r"[^a-z\s]", " ", n)
    t = [x for x in n.split() if x]
    if not t:
        return []
    keys = [t[0]] if len(t) == 1 else [t[0] + "|" + t[-1], t[-1] + "|" + t[0]]
    keys += ["|".join(sorted(t)), "".join(t), "".join(t[::-1])]
    return keys


def primary_key(name):
    k = name_keys(name)
    return k[0] if k else ""


def team_id_to_code():
    """{idEquipoSofa: códigoFIFA} usando las tablas del WC2026."""
    sofa2my = {}
    for code in json.loads(SQUADS.read_text(encoding="utf-8")):
        sofa2my[CODE_ALIAS.get(code, code)] = code
    d = get_json(f"{SOFA}/unique-tournament/{WC_UT}/season/{WC_SEASON}/standings/total")
    out = {}
    for grp in (d or {}).get("standings", []):
        for row in grp.get("rows", []):
            t = row["team"]
            my = sofa2my.get(t.get("nameCode"))
            if my:
                out[t["id"]] = my
    return out


def _empty():
    return {k: 0.0 for k in (
        "goals", "assists", "xg", "xa", "shots", "shots_on_target", "minutes",
        "appearances", "tackles", "interceptions", "clearances",
        "fouls_committed", "fouls_received", "yellow_cards", "red_cards",
        "saves", "key_passes", "big_chances_created", "big_chances_missed",
        "_rating_w", "_w")}


# mapa campo SofaScore -> campo nuestro (acumulables por suma)
SUM_MAP = {
    "goals": "goals", "assists": "assists", "expectedGoals": "xg",
    "expectedAssists": "xa", "totalShots": "shots", "shotsOnTarget": "shots_on_target",
    "minutesPlayed": "minutes", "appearances": "appearances", "tackles": "tackles",
    "interceptions": "interceptions", "clearances": "clearances",
    "fouls": "fouls_committed", "wasFouled": "fouls_received",
    "yellowCards": "yellow_cards", "redCards": "red_cards", "saves": "saves",
    "keyPasses": "key_passes", "bigChancesCreated": "big_chances_created",
    "bigChancesMissed": "big_chances_missed",
}


def scrape_tournament(ut, season, accum):
    """Agrega las stats de un torneo a accum[sofa_pid]."""
    offset, pages, got = 0, 1, 0
    while offset // 100 < pages and offset // 100 < 40:
        url = (f"{SOFA}/unique-tournament/{ut}/season/{season}/statistics"
               f"?limit=100&offset={offset}&order=-rating&accumulation=total&fields={FIELDS}")
        d = get_json(url)
        if not d:
            return got
        pages = d.get("pages", 1)
        rows = d.get("results", [])
        if not rows:
            break
        for row in rows:
            p = row.get("player") or {}
            t = row.get("team") or {}
            pid = p.get("id")
            if not pid:
                continue
            e = accum.get(pid)
            if e is None:
                e = {"name": p.get("name", ""), "team_id": t.get("id"), "s": _empty()}
                accum[pid] = e
            s = e["s"]
            for sf, nf in SUM_MAP.items():
                v = row.get(sf)
                if isinstance(v, (int, float)):
                    s[nf] += v
            rt, mins = row.get("rating"), row.get("minutesPlayed") or 0
            ap = row.get("appearances") or 0
            w = mins if mins else ap
            if isinstance(rt, (int, float)) and w:
                s["_rating_w"] += rt * w
                s["_w"] += w
            got += 1
        offset += 100
        time.sleep(0.5)
    return got


def main():
    log("===== scrape_player_stats_sofascore.py START =====")
    if not SQUADS.exists():
        log("FATAL: falta data/squads.json (corré scrape_squads.py primero)")
        return
    try:
        tid2code = team_id_to_code()
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: no se pudo mapear equipos SofaScore: {e}")
        return
    log(f"equipos WC2026 mapeados: {len(tid2code)}")

    accum = {}
    for ut, season, name in TOURNAMENTS:
        try:
            n = scrape_tournament(ut, season, accum)
            log(f"  {name:<42} filas: {n}")
        except Exception as e:  # noqa: BLE001
            log(f"  {name:<42} ERROR: {e}")
        time.sleep(0.6)

    # cerrar rating (promedio ponderado) y agrupar por selección
    out = {}
    for pid, e in accum.items():
        code = tid2code.get(e["team_id"])
        if not code:           # jugador de selección no clasificada → fuera
            continue
        s = e["s"]
        rating = round(s["_rating_w"] / s["_w"], 2) if s["_w"] else 0
        stats = {k: (round(v, 2) if isinstance(v, float) and v != int(v) else int(v))
                 for k, v in s.items() if not k.startswith("_")}
        stats["rating"] = rating
        stats["clean_sheets"] = 0   # SofaScore no lo expone a nivel agregado
        out.setdefault(code, {})[primary_key(e["name"])] = stats

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v) for v in out.values())
    log(f"player_stats_season.json: {len(out)} selecciones · {total} jugadores con "
        f"stats REALES de SofaScore ({len(TOURNAMENTS)} torneos, sin amistosos)")
    log("===== scrape_player_stats_sofascore.py END =====\n")


if __name__ == "__main__":
    main()
