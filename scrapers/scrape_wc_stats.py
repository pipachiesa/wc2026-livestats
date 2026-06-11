#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_wc_stats.py — Resultados, fixture y stats del Mundial 2026
=================================================================
Fuente principal: API pública de FIFA (api.fifa.com/api/v3).
  Competición 17 · Temporada 285023 (Copa Mundial 2026)

Genera / actualiza (modo UPSERT, re-ejecutable):
  data/matches.json             — los 104 partidos (grupo + eliminación)
  data/player_stats_wc.json     — stats por jugador EN el Mundial (se llena a
                                  medida que se juegan partidos)
  data/player_stats_season.json — stats de temporada 2025/26 por jugador
                                  (seed desde los datos curados de index.html;
                                  FBref/SofaScore es el upgrade documentado)

Qué stats de partido da gratis la API de FIFA:
  goles, asistencias, minutos jugados, tarjetas, titularidades.
  (xG / pases / tiros NO están en el endpoint gratuito — quedan en season.)

Uso:
  python3 scrapers/scrape_wc_stats.py
"""

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
INDEX     = ROOT / "index.html"
F_MATCHES = DATA_DIR / "matches.json"
F_WC      = DATA_DIR / "player_stats_wc.json"
F_SEASON  = DATA_DIR / "player_stats_season.json"
LOG_FILE  = DATA_DIR / "scrape_log.txt"

# ── FIFA ─────────────────────────────────────────────────────────────────────
COMP, SEASON = "17", "285023"
BASE = "https://api.fifa.com/api/v3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
# MatchStatus FIFA → estado nuestro
STATUS_MAP = {0: "played", 1: "upcoming", 2: "upcoming"}


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
    raise RuntimeError(f"fallo GET {url} :: {last}")


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default
    return default


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm_key(name):
    """Idéntico al de scrape_squads.py y al frontend: primer+último token."""
    name = strip_accents(name or "").lower()
    name = re.sub(r"[^a-z\s]", " ", name)
    toks = [t for t in name.split() if t]
    if not toks:
        return ""
    return toks[0] if len(toks) == 1 else toks[0] + "|" + toks[-1]


# ═════════════════════════ 1) MATCHES / FIXTURE ══════════════════════════════
def grp_letter(match):
    gn = match.get("GroupName") or []
    desc = gn[0].get("Description", "") if gn else ""
    m = re.search(r"([A-L])\s*$", desc)
    return m.group(1) if m else ""


def code_of(side):
    if not side:
        return "TBD"
    return side.get("IdCountry") or side.get("Abbreviation") or "TBD"


def scrape_matches():
    url = f"{BASE}/calendar/matches?idCompetition={COMP}&idSeason={SEASON}&language=es&count=400"
    data = get_json(url)
    res = data.get("Results", [])
    existing = load_json(F_MATCHES, {"matches": []})
    by_fid = {m.get("fifa_id"): m for m in existing.get("matches", [])}

    out, changed, created = [], 0, 0
    seq = {}
    for m in res:
        fid = m.get("IdMatch")
        grp = grp_letter(m)
        home, away = code_of(m.get("Home")), code_of(m.get("Away"))
        status = STATUS_MAP.get(m.get("MatchStatus"), "live")
        hs, as_ = m.get("HomeTeamScore"), m.get("AwayTeamScore")
        stage = (m.get("StageName") or [{}])[0].get("Description", "")
        stadium = ""
        if m.get("Stadium"):
            stadium = (m["Stadium"].get("Name") or [{}])[0].get("Description", "")
        # id legible y único (varios partidos pueden compartir par de equipos en KO=TBD)
        base_id = f"{home}-{away}"
        seq[base_id] = seq.get(base_id, 0) + 1
        rid = base_id if seq[base_id] == 1 else f"{base_id}-{seq[base_id]}"
        rec = {
            "id": rid,
            "fifa_id": fid,
            "group": grp,
            "stage": stage,
            "home": home,
            "away": away,
            "date": (m.get("Date") or "")[:10],
            "datetime": m.get("Date"),
            "stadium": stadium,
            "home_score": hs,
            "away_score": as_,
            "status": status,
        }
        old = by_fid.get(fid)
        if old is None:
            created += 1
        elif any(old.get(k) != rec.get(k) for k in
                 ("home", "away", "home_score", "away_score", "status", "date")):
            changed += 1
            log(f"  match upd {rid}: {old.get('home_score')}-{old.get('away_score')}"
                f"({old.get('status')}) -> {hs}-{as_}({status})")
        out.append(rec)

    out.sort(key=lambda r: (r.get("datetime") or "", r["id"]))
    F_MATCHES.write_text(json.dumps({"matches": out}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    log(f"matches.json: {len(out)} partidos · {created} nuevos · {changed} actualizados")
    return out


# ═══════════════════ 2) STATS DE JUGADOR EN EL MUNDIAL ═══════════════════════
def team_player_minutes(team, match_minutes):
    """Devuelve {IdPlayer: {minutes, started}} a partir de titulares + cambios."""
    mins = {}
    starters = []
    for p in team.get("Players", []) or []:
        # Status 1 = titular, 2 = suplente (heurística FIFA)
        if p.get("Status") == 1:
            starters.append(p.get("IdPlayer"))
    for pid in starters:
        mins[pid] = {"minutes": match_minutes, "started": 1}
    for s in team.get("Substitutions", []) or []:
        minute = s.get("Minute") or ""
        mm = re.search(r"(\d+)", str(minute))
        t = int(mm.group(1)) if mm else match_minutes
        off, on = s.get("IdPlayerOff"), s.get("IdPlayerOn")
        if off in mins:
            mins[off]["minutes"] = min(mins[off]["minutes"], t)
        if on:
            mins[on] = {"minutes": max(0, match_minutes - t), "started": 0}
    return mins


def aggregate_match(match_json, acc):
    """Suma stats de un partido jugado a acc[code][pid]."""
    total_min = 90
    for side in ("HomeTeam", "AwayTeam"):
        team = match_json.get(side) or {}
        code = team.get("IdCountry") or team.get("Abbreviation")
        if not code:
            continue
        bucket = acc.setdefault(code, {})
        mins = team_player_minutes(team, total_min)
        # base: apariciones + minutos
        for pid, mv in mins.items():
            e = bucket.setdefault(pid, _empty_wc())
            e["appearances"] += 1
            e["started"] += mv["started"]
            e["minutes"] += mv["minutes"]
        # goles + asistencias
        for g in team.get("Goals", []) or []:
            if g.get("Type") == 3:   # gol en contra: no acreditar al ejecutor
                continue
            sc = g.get("IdPlayer")
            if sc:
                bucket.setdefault(sc, _empty_wc())["goals"] += 1
            ast = g.get("IdAssistPlayer")
            if ast:
                bucket.setdefault(ast, _empty_wc())["assists"] += 1
        # tarjetas
        for b in team.get("Bookings", []) or []:
            pid = b.get("IdPlayer")
            if not pid:
                continue
            e = bucket.setdefault(pid, _empty_wc())
            if b.get("Card") in (2, 4):       # roja directa / doble amarilla
                e["red_cards"] += 1
            else:
                e["yellow_cards"] += 1


def _empty_wc():
    return {"appearances": 0, "started": 0, "minutes": 0, "goals": 0,
            "assists": 0, "yellow_cards": 0, "red_cards": 0}


def scrape_wc_player_stats(matches):
    played = [m for m in matches if m.get("status") == "played"]
    if not played:
        # asegura que el archivo exista (vacío) para que el frontend no rompa
        if not F_WC.exists():
            F_WC.write_text(json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8")
        log("player_stats_wc.json: 0 partidos jugados todavía (se llenará al arrancar el torneo)")
        return
    acc = {}
    for m in played:
        try:
            # necesitamos el IdStage; lo tomamos del calendario por fifa_id
            mj = get_json(f"{BASE}/calendar/matches?idCompetition={COMP}&idSeason={SEASON}"
                          f"&idMatch={m['fifa_id']}&language=en")
            stage = (mj.get("Results") or [{}])[0].get("IdStage")
            if not stage:
                continue
            live = get_json(f"{BASE}/live/football/{COMP}/{SEASON}/{stage}/{m['fifa_id']}?language=en")
            aggregate_match(live, acc)
            log(f"  WC stats agregadas: {m['id']} ({m['home']}-{m['away']})")
            time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            log(f"  ! no se pudo agregar {m['id']}: {e}")
    F_WC.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")
    tot = sum(len(v) for v in acc.values())
    log(f"player_stats_wc.json: {len(acc)} equipos · {tot} jugadores con datos WC")


# ═══════════════════ 3) STATS DE TEMPORADA (seed inline) ═════════════════════
# mkP(id,name,short,pos,jer,age,club,nat,xi,g,a,xg,xa,sh,sot,fc,fr,tck,yc,min,rat,sv,cs)
MKP_RE = re.compile(
    r"mkP\(\s*(\d+)\s*,\s*'([^']*)'\s*,\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*(\d+)\s*,"
    r"\s*(\d+)\s*,\s*'[^']*'\s*,\s*'[^']*'\s*,\s*(true|false)\s*,"
    r"\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,"
    r"\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,"
    r"\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+))?\s*(?:,\s*([\d.]+))?\s*\)"
)
CODE_RE = re.compile(r"nameCode:'([A-Z]{2,4})'")


def seed_season_stats():
    if not INDEX.exists():
        log("season: index.html no encontrado, no se puede sembrar")
        return
    txt = INDEX.read_text(encoding="utf-8", errors="ignore")
    marks = [(m.start(), m.group(1)) for m in CODE_RE.finditer(txt)]
    marks.append((len(txt), None))

    existing = load_json(F_SEASON, {})
    out = dict(existing)  # upsert: parte de lo que ya había
    n_players = 0
    for i in range(len(marks) - 1):
        start, code = marks[i]
        end = marks[i + 1][0]
        if not code:
            continue
        bucket = out.setdefault(code, {})
        for pm in MKP_RE.finditer(txt, start, end):
            g = pm.groups()
            # índices: 0 id · 1 name · 2 pos · 3 jer · 4 age · 5 xi ·
            # 6 goals 7 assists 8 xg 9 xa 10 shots 11 sot 12 fc 13 fr 14 tck
            # 15 yc 16 minutes 17 rating 18 saves 19 clean_sheets
            name = g[1]
            minutes = float(g[16])
            app = max(1, round(minutes / 82))
            stats = {
                "appearances": app, "minutes": int(minutes),
                "goals": _num(g[6]), "assists": _num(g[7]),
                "xg": _num(g[8]), "xa": _num(g[9]),
                "shots": _num(g[10]), "shots_on_target": _num(g[11]),
                "fouls_committed": _num(g[12]), "fouls_received": _num(g[13]),
                "tackles": _num(g[14]), "yellow_cards": _num(g[15]),
                "rating": _num(g[17]),
                "saves": _num(g[18]) if g[18] else 0,
                "clean_sheets": _num(g[19]) if g[19] else 0,
            }
            bucket[norm_key(name)] = stats
            n_players += 1
    F_SEASON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"player_stats_season.json: {len(out)} equipos · {n_players} jugadores "
        f"(seed desde index.html · fuente real futura: FBref)")


def _num(s):
    if s is None:
        return 0
    f = float(s)
    return int(f) if f == int(f) else f


def main():
    t0 = time.time()
    log("===== scrape_wc_stats.py START =====")
    matches = scrape_matches()
    scrape_wc_player_stats(matches)
    seed_season_stats()
    log(f"===== scrape_wc_stats.py END · {time.time()-t0:.1f}s =====\n")


if __name__ == "__main__":
    main()
