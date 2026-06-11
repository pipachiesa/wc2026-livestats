#!/usr/bin/env python3
"""
WC2026 Data Scraper — usa LanusStats (SofaScore)
================================================
Scrapea para los 48 equipos del Mundial 2026:
  - Plantel de 26 (posición, edad, club, dorsal)
  - Stats de jugadores de los últimos 2 años de torneos importantes:
      Copa América 2024, Euro 2024, Nations League 2024/25,
      AFCON 2024, Copa Asia 2024, Qualifiers WC2026, WC2026 (en vivo)
  - XI probable basado en los últimos 10 partidos importantes de cada selección
  - Stats agregadas de equipo

Cómo correr:
    cd WC2026
    python scraper/scrape_wc2026.py

Output:
    data/wc2026_data.json       — JSON final listo para la página HTML
    data/checkpoint.json        — checkpoint para resumir si se corta

Requisitos:
    pip install lanusstats undetected-chromedriver nodriver pydoll-python
    (o pip install -e . desde la carpeta WC2026 si copiaron el repo)
"""

import sys
import os
import json
import time
import random
import math
from pathlib import Path
from datetime import date, datetime
from collections import Counter

# ── path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from LanusStats.sofascore import SofaScore

# ── directorios ──────────────────────────────────────────────────────────────
DATA_DIR = ROOT / 'data'
DATA_DIR.mkdir(exist_ok=True)
CHECKPOINT_FILE = DATA_DIR / 'checkpoint.json'
OUTPUT_FILE     = DATA_DIR / 'wc2026_data.json'

# ── IDs de torneos en SofaScore ──────────────────────────────────────────────
WC_T  = 16        # World Cup tournament ID
WC_S  = 58210     # World Cup 2026 season ID

# Torneos para stats de jugadores (últimos 2 años)
# Formato: (tournament_id, season_id, nombre, [confederaciones relevantes])
STAT_TOURNAMENTS = [
    # ── Torneos continentales ─────────────────────────────────────────
    (133,   57114, "Copa América 2024",           ["CONMEBOL"]),
    (1,     56953, "Euro 2024",                    ["UEFA"]),
    (11798, 58776, "Nations League UEFA 24/25 F",  ["UEFA"]),   # Final Four
    (11798, 57477, "Nations League UEFA 24/25",    ["UEFA"]),   # Liga A completa
    (11789, 57393, "CONCACAF Nations League 2024", ["CONCACAF"]),
    (11790, 56822, "AFCON 2024",                   ["CAF"]),
    (11791, 55951, "Copa Asia AFC 2024",            ["AFC"]),
    (11802, 56901, "OFC Nations Cup 2024",          ["OFC"]),
    # ── Clasificatorias al Mundial ────────────────────────────────────
    (11797, 57339, "Eliminatorias CONMEBOL 2026",  ["CONMEBOL"]),
    (119,   57435, "Eliminatorias UEFA 2026",       ["UEFA"]),
    (11792, 57312, "Eliminatorias CONCACAF 2026",  ["CONCACAF"]),
    (11793, 57368, "Eliminatorias CAF 2026",        ["CAF"]),
    (11794, 57340, "Eliminatorias AFC 2026",        ["AFC"]),
    (11795, 57341, "Eliminatorias OFC 2026",        ["OFC"]),
    # ── WC2026 (partidos ya jugados) ──────────────────────────────────
    (WC_T,  WC_S,  "World Cup 2026",               ["ALL"]),
]

# Campos de stats que pedimos a SofaScore
STAT_FIELDS = [
    'goals', 'yellowCards', 'redCards',
    'groundDuelsWon', 'groundDuelsWonPercentage',
    'aerialDuelsWon', 'aerialDuelsWonPercentage',
    'successfulDribbles', 'successfulDribblesPercentage',
    'tackles', 'assists', 'accuratePassesPercentage',
    'totalDuelsWon', 'totalDuelsWonPercentage',
    'minutesPlayed', 'wasFouled', 'fouls',
    'appearances', 'started',
    'totalShots', 'shotsOnTarget', 'blockedShots',
    'expectedGoals', 'passToAssist',
    'bigChancesCreated', 'bigChancesMissed',
    'keyPasses', 'accurateLongBalls',
    'interceptions', 'clearances', 'dribbledPast',
    'offsides', 'hitWoodwork',
    'saves', 'cleanSheets',
    'errorLeadToGoal', 'errorLeadToShot',
    'rating',
]

# Umbrales de tiempo entre requests (segundos)
SLEEP_BETWEEN_REQUESTS = (1.2, 2.8)
SLEEP_BETWEEN_TEAMS    = (2.0, 4.0)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def sleep(lo=None, hi=None):
    lo = lo or SLEEP_BETWEEN_REQUESTS[0]
    hi = hi or SLEEP_BETWEEN_REQUESTS[1]
    time.sleep(random.uniform(lo, hi))


def safe_req(ss: SofaScore, path: str, retries: int = 3):
    """Llama sofascore_request con retry y manejo de errores."""
    for attempt in range(1, retries + 1):
        try:
            return ss.sofascore_request(path)
        except Exception as exc:
            print(f"    [!] intento {attempt}/{retries} falló → {exc}")
            if attempt < retries:
                time.sleep(4 * attempt)
            else:
                print(f"    [✗] abandona {path}")
                return None


def calc_age(dob) -> int | None:
    """Calcula edad a partir de timestamp Unix o string YYYY-MM-DD."""
    if not dob:
        return None
    try:
        if isinstance(dob, (int, float)):
            d = datetime.utcfromtimestamp(dob).date()
        else:
            d = date.fromisoformat(str(dob)[:10])
        today = date.today()
        return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    except Exception:
        return None


def position_code(p: dict) -> str:
    """Convierte posición SofaScore a GK / DEF / MID / FWD."""
    raw = p.get('position', '')
    if isinstance(raw, dict):
        raw = raw.get('abbreviation', raw.get('name', ''))
    raw = str(raw).upper().strip()
    MAP = {
        'G': 'GK', 'GK': 'GK', 'GOALKEEPER': 'GK',
        'D': 'DEF', 'DF': 'DEF', 'CB': 'DEF', 'LB': 'DEF', 'RB': 'DEF',
        'WB': 'DEF', 'DEFENDER': 'DEF',
        'M': 'MID', 'MF': 'MID', 'CM': 'MID', 'DM': 'MID', 'AM': 'MID',
        'LM': 'MID', 'RM': 'MID', 'MIDFIELDER': 'MID',
        'F': 'FWD', 'FW': 'FWD', 'CF': 'FWD', 'LW': 'FWD', 'RW': 'FWD',
        'SS': 'FWD', 'FORWARD': 'FWD', 'STRIKER': 'FWD', 'ATTACKER': 'FWD',
        'A': 'FWD',
    }
    return MAP.get(raw, 'MID')


def infer_formation(player_positions: list[str]) -> str:
    """Infiere formación a partir de lista de posiciones de los 11 titulares."""
    c = Counter(player_positions)
    d = c.get('DEF', 0)
    m = c.get('MID', 0)
    f = c.get('FWD', 0)
    known = {
        (4,3,3): '4-3-3',  (4,4,2): '4-4-2',  (4,2,4): '4-2-3-1',
        (4,5,1): '4-5-1',  (4,1,5): '4-1-4-1', (3,5,2): '3-5-2',
        (3,4,3): '3-4-3',  (5,3,2): '5-3-2',   (5,4,1): '5-4-1',
        (3,6,1): '3-6-1',  (4,6,0): '4-6-0',
    }
    # ajuste por medio-punta (AM suele contarse como M pero a veces como F)
    if (d,m,f) == (4,2,4):
        return '4-2-3-1'
    return known.get((d,m,f), f'{d}-{m}-{f}' if d+m+f == 10 else '4-3-3')


# ═══════════════════════════════════════════════════════════════════════════════
# CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════════════

def load_cp() -> dict:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    return {
        'teams': None,
        'tournament_stats': {},   # key: f'{t_id}_{s_id}' → lista de rows
        'teams_data': {},         # key: str(team_id) → team object
    }


def save_cp(cp: dict):
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(cp, fh, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# PASO 1 — Equipos WC2026
# ═══════════════════════════════════════════════════════════════════════════════

def get_wc_teams(ss: SofaScore) -> list[dict]:
    """Trae los 48 equipos del torneo WC2026 desde SofaScore."""
    data = safe_req(ss, f'api/v1/unique-tournament/{WC_T}/season/{WC_S}/teams')
    if not data or 'teams' not in data:
        print("  [!] No se pudieron obtener los equipos, usando fallback manual")
        return []

    teams = []
    for t in data['teams']:
        country = t.get('country', {})
        teams.append({
            'id':        t['id'],
            'name':      t.get('name', ''),
            'shortName': t.get('shortName', t.get('nameCode', '')),
            'nameCode':  t.get('nameCode', ''),
            'country':   country.get('name', '') if isinstance(country, dict) else '',
            'alpha2':    country.get('alpha2', '') if isinstance(country, dict) else '',
            'flag_url':  f'https://api.sofascore.com/api/v1/team/{t["id"]}/image',
        })
    return teams


# ═══════════════════════════════════════════════════════════════════════════════
# PASO 2 — Stats bulk por torneo
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_tournament_stats(ss: SofaScore, t_id: int, s_id: int) -> list[dict]:
    """
    Trae stats de TODOS los jugadores de un torneo (igual que scrape_league_stats
    pero sin depender del diccionario de ligas de functions.py).
    """
    fields_enc = '%2C'.join(STAT_FIELDS)
    positions  = 'G~D~M~F'
    offset     = 0
    rows       = []

    for page_num in range(50):   # max 5000 jugadores
        path = (
            f'api/v1/unique-tournament/{t_id}/season/{s_id}/statistics'
            f'?limit=100&order=-rating&offset={offset}'
            f'&accumulation=total'
            f'&fields={fields_enc}'
            f'&filters=position.in.{positions}'
        )
        data = safe_req(ss, path)
        if not data or 'results' not in data:
            break

        for r in data['results']:
            player = r.get('player', {})
            team   = r.get('team', {})
            row = {
                'player_id':   player.get('id'),
                'player_name': player.get('name', ''),
                'team_id':     team.get('id'),
                'team_name':   team.get('name', ''),
                'position':    player.get('position', ''),
            }
            for f in STAT_FIELDS:
                row[f] = r.get(f)
            rows.append(row)

        curr_page  = data.get('page', 1)
        total_pages = data.get('pages', 1)
        if curr_page >= total_pages:
            break
        offset += 100
        sleep(1.0, 2.0)

    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# PASO 3 — Plantel de un equipo
# ═══════════════════════════════════════════════════════════════════════════════

def get_squad(ss: SofaScore, team_id: int) -> list[dict]:
    """
    Trae el plantel registrado para WC2026.
    Intenta endpoint de squad del torneo; si falla, usa squad general del equipo.
    """
    # Opción A: squad específico del torneo
    data = safe_req(ss, f'api/v1/unique-tournament/{WC_T}/season/{WC_S}/team/{team_id}/squad')
    if data:
        raw = (data.get('squad') or {})
        players_raw = raw.get('players', []) if isinstance(raw, dict) else raw
        if players_raw:
            return _parse_players(players_raw)

    # Opción B: squad general del equipo
    data = safe_req(ss, f'api/v1/team/{team_id}/players')
    if not data:
        return []
    return _parse_players(data.get('players', []))


def _parse_players(raw_list: list) -> list[dict]:
    players = []
    for item in raw_list:
        p = item.get('player', item)   # a veces el jugador está dentro de 'player'
        if not isinstance(p, dict):
            continue

        dob   = p.get('dateOfBirthTimestamp') or p.get('dateOfBirth')
        team  = p.get('team', {})
        country = p.get('country', {})

        players.append({
            'id':          p.get('id'),
            'name':        p.get('name', ''),
            'shortName':   p.get('shortName', p.get('name', '')),
            'positionCode': position_code(p),
            'jerseyNumber': item.get('jerseyNumber') or p.get('jerseyNumber'),
            'age':         calc_age(dob),
            'dateOfBirth': str(dob or ''),
            'height':      p.get('height'),
            'preferredFoot': p.get('preferredFoot', ''),
            'club':        team.get('name', '') if isinstance(team, dict) else '',
            'club_id':     team.get('id')       if isinstance(team, dict) else None,
            'nationality': country.get('name', '') if isinstance(country, dict) else '',
            'photo_url':   f'https://api.sofascore.com/api/v1/player/{p.get("id", 0)}/image',
        })
    return players


# ═══════════════════════════════════════════════════════════════════════════════
# PASO 4 — XI probable (últimos partidos importantes)
# ═══════════════════════════════════════════════════════════════════════════════

# IDs de torneos "importantes" para filtrar eventos del equipo
IMPORTANT_TOURNAMENT_IDS = {
    16,    # World Cup
    133,   # Copa América
    1,     # Euro
    11798, # Nations League UEFA
    11789, # CONCACAF Nations League
    11790, # AFCON
    11791, # Copa Asia
    11797, # Eliminatorias CONMEBOL
    119,   # Eliminatorias UEFA
    11792, # Eliminatorias CONCACAF
    11793, # Eliminatorias CAF
    11794, # Eliminatorias AFC
    11795, # Eliminatorias OFC
    11802, # OFC Nations Cup
}

def get_probable_xi(ss: SofaScore, team_id: int, n_matches: int = 10) -> dict:
    """
    Determina XI probable mirando los últimos partidos importantes del equipo.
    Devuelve {'formation': str, 'player_ids': set, 'players': [{'id','name'}]}
    """
    # Traer eventos recientes (hasta 3 páginas = ~60 partidos)
    recent_events = []
    for page in range(3):
        data = safe_req(ss, f'api/v1/team/{team_id}/events/last/{page}')
        if not data:
            break
        for ev in data.get('events', []):
            t_info = ev.get('tournament', {})
            ut     = t_info.get('uniqueTournament', {})
            t_id   = ut.get('id', 0)
            status = ev.get('status', {}).get('type', '')
            if status == 'finished' and t_id in IMPORTANT_TOURNAMENT_IDS:
                recent_events.append({
                    'id':       ev['id'],
                    'date':     ev.get('startTimestamp', 0),
                    't_id':     t_id,
                    't_name':   ut.get('name', ''),
                })
        sleep(0.8, 1.5)

    # Ordenar por fecha desc y tomar los últimos n_matches
    recent_events.sort(key=lambda x: x['date'], reverse=True)
    to_process = recent_events[:n_matches]

    player_starts   = {}   # player_id → {id, name, starts, positions}
    formation_votes = []   # lista de formaciones observadas

    for ev in to_process:
        lineup_data = safe_req(ss, f'api/v1/event/{ev["id"]}/lineups')
        if not lineup_data:
            continue

        for side in ('home', 'away'):
            side_data = lineup_data.get(side, {})
            team_info = side_data.get('team', {})
            if team_info.get('id') != team_id:
                continue

            # Formación declarada
            if side_data.get('formation'):
                formation_votes.append(side_data['formation'])

            for pi in side_data.get('players', []):
                if pi.get('substitute', True):
                    continue  # solo titulares
                p   = pi.get('player', {})
                pid = p.get('id')
                pos = position_code(p)
                if pid:
                    if pid not in player_starts:
                        player_starts[pid] = {
                            'id':        pid,
                            'name':      p.get('name', ''),
                            'starts':    0,
                            'positions': [],
                        }
                    player_starts[pid]['starts']    += 1
                    player_starts[pid]['positions'].append(pos)
            break  # ya encontramos nuestro lado

        sleep(0.6, 1.2)

    # Top 11 por starts
    ranked = sorted(player_starts.values(), key=lambda x: x['starts'], reverse=True)
    xi     = ranked[:11]

    # Formación: usar la más votada o inferirla de posiciones
    if formation_votes:
        formation = Counter(formation_votes).most_common(1)[0][0]
    else:
        pos_list = []
        for p in xi:
            if p['positions']:
                pos_list.append(Counter(p['positions']).most_common(1)[0][0])
        formation = infer_formation(pos_list)

    return {
        'formation':  formation,
        'player_ids': {p['id'] for p in xi},
        'players':    [{'id': p['id'], 'name': p['name']} for p in xi],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PASO 5 — Merge stats en jugadores
# ═══════════════════════════════════════════════════════════════════════════════

def build_stats_lookup(all_rows: list[dict]) -> dict:
    """
    Construye un dict {player_id → stats_agregadas} sumando todas las filas
    de distintos torneos. Rating se promedia ponderado por minutos.
    """
    lookup = {}
    for row in all_rows:
        pid = row.get('player_id')
        if not pid:
            continue

        if pid not in lookup:
            lookup[pid] = {
                '_minutes': 0,
                '_rating_weighted': 0.0,
            }

        mins  = float(row.get('minutesPlayed') or 0)
        rating = float(row.get('rating') or 0)

        # Acumular rating ponderado por minutos
        lookup[pid]['_minutes']          += mins
        lookup[pid]['_rating_weighted']  += rating * mins

        # Sumar campos numéricos (excepto porcentajes y rating)
        skip = {'accuratePassesPercentage', 'groundDuelsWonPercentage',
                'aerialDuelsWonPercentage', 'successfulDribblesPercentage',
                'totalDuelsWonPercentage', 'rating'}
        for f in STAT_FIELDS:
            if f in skip:
                continue
            val = row.get(f)
            if val is not None:
                lookup[pid][f] = lookup[pid].get(f, 0) + float(val)

    # Calcular rating ponderado final
    for pid, s in lookup.items():
        mins = s.pop('_minutes', 0)
        rw   = s.pop('_rating_weighted', 0)
        s['minutesPlayed'] = s.get('minutesPlayed', mins)  # ya acumulado arriba
        s['rating'] = round(rw / mins, 2) if mins > 0 else 0.0

    return lookup


def enrich_squad(squad: list[dict], lookup: dict, probable_ids: set) -> list[dict]:
    """Agrega stats y flag de XI probable a cada jugador del plantel."""
    for p in squad:
        pid   = p.get('id')
        s     = lookup.get(pid, {})

        p['in_probable_xi'] = pid in probable_ids
        p['stats'] = {
            # Volumen
            'appearances':        int(s.get('appearances', 0)),
            'started':            int(s.get('started', 0)),
            'minutes':            int(s.get('minutesPlayed', 0)),
            # Ataque
            'goals':              int(s.get('goals', 0)),
            'assists':            int(s.get('assists', 0)),
            'xg':                 round(float(s.get('expectedGoals', 0) or 0), 2),
            'xa':                 round(float(s.get('passToAssist', 0) or 0), 2),
            'shots':              int(s.get('totalShots', 0)),
            'shots_on_target':    int(s.get('shotsOnTarget', 0)),
            'big_chances_created': int(s.get('bigChancesCreated', 0)),
            'big_chances_missed':  int(s.get('bigChancesMissed', 0)),
            'key_passes':         int(s.get('keyPasses', 0)),
            'offsides':           int(s.get('offsides', 0)),
            # Defensa
            'tackles':            int(s.get('tackles', 0)),
            'interceptions':      int(s.get('interceptions', 0)),
            'clearances':         int(s.get('clearances', 0)),
            'dribbled_past':      int(s.get('dribbledPast', 0)),
            # Disciplina
            'fouls_committed':    int(s.get('fouls', 0)),
            'fouls_received':     int(s.get('wasFouled', 0)),
            'yellow_cards':       int(s.get('yellowCards', 0)),
            'red_cards':          int(s.get('redCards', 0)),
            # Pase
            'accurate_pass_pct':  round(float(s.get('accuratePassesPercentage', 0) or 0), 1),
            'long_balls':         int(s.get('accurateLongBalls', 0)),
            # Arquero
            'saves':              int(s.get('saves', 0)),
            'clean_sheets':       int(s.get('cleanSheets', 0)),
            # Rating
            'rating':             float(s.get('rating', 0) or 0),
        }
    return squad


# ═══════════════════════════════════════════════════════════════════════════════
# Stats agregadas de equipo
# ═══════════════════════════════════════════════════════════════════════════════

def team_aggregates(squad: list[dict]) -> dict:
    """Suma/promedia stats del plantel para los KPIs del equipo."""
    if not squad:
        return {}

    total_apps = sum(p['stats']['appearances'] for p in squad)
    if total_apps == 0:
        return {}

    def s(field):   return sum(p['stats'][field] for p in squad)
    def pg(field):  return round(s(field) / total_apps, 2)

    shots       = s('shots')
    shots_ot    = s('shots_on_target')
    fouls_c     = s('fouls_committed')
    fouls_r     = s('fouls_received')
    xg_total    = round(sum(p['stats']['xg'] for p in squad), 2)
    xa_total    = round(sum(p['stats']['xa'] for p in squad), 2)

    return {
        'total_goals':          s('goals'),
        'total_assists':        s('assists'),
        'total_xg':             xg_total,
        'total_xa':             xa_total,
        'total_shots':          shots,
        'total_shots_on_target': shots_ot,
        'shot_accuracy_pct':    round(shots_ot / shots * 100, 1) if shots else 0,
        'total_fouls_committed': fouls_c,
        'total_fouls_received':  fouls_r,
        'total_tackles':        s('tackles'),
        'total_interceptions':  s('interceptions'),
        'total_yellow_cards':   s('yellow_cards'),
        'total_red_cards':      s('red_cards'),
        'goals_per_game':       pg('goals'),
        'xg_per_game':          round(xg_total / total_apps, 2),
        'shots_per_game':       pg('shots'),
        'fouls_committed_per_game': pg('fouls_committed'),
        'fouls_received_per_game':  pg('fouls_received'),
        'tackles_per_game':     pg('tackles'),
        'total_appearances':    total_apps,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════╗")
    print("║      WC2026 Scraper — LanusStats     ║")
    print("╚══════════════════════════════════════╝")
    print(f"Output: {OUTPUT_FILE}\n")

    ss = SofaScore()
    cp = load_cp()

    # ── 1. Equipos ────────────────────────────────────────────────────────────
    if not cp['teams']:
        print("[1/4] Obteniendo equipos WC2026...")
        teams = get_wc_teams(ss)
        if not teams:
            print("ERROR: no se obtuvieron equipos, abortando.")
            ss.close(); return
        cp['teams'] = teams
        save_cp(cp)
        print(f"  ✓ {len(teams)} equipos\n")
    else:
        print(f"[1/4] Equipos: {len(cp['teams'])} (checkpoint) ✓\n")

    # ── 2. Stats bulk de torneos ──────────────────────────────────────────────
    print("[2/4] Scrapeando stats de torneos importantes...")
    all_rows = []

    for (t_id, s_id, name, confs) in STAT_TOURNAMENTS:
        key = f'{t_id}_{s_id}'
        if key in cp['tournament_stats']:
            rows = cp['tournament_stats'][key]
            print(f"  ✓  {name} — {len(rows)} jugadores (checkpoint)")
            all_rows.extend(rows)
            continue

        print(f"  →  {name}...", end='', flush=True)
        rows = scrape_tournament_stats(ss, t_id, s_id)
        cp['tournament_stats'][key] = rows
        all_rows.extend(rows)
        save_cp(cp)
        print(f" {len(rows)} jugadores")
        sleep(2.0, 4.0)

    print(f"\n  Total registros acumulados: {len(all_rows)}")
    stats_lookup = build_stats_lookup(all_rows)
    print(f"  Jugadores únicos en lookup: {len(stats_lookup)}\n")

    # ── 3. Plantel + XI probable por equipo ───────────────────────────────────
    print("[3/4] Scrapeando planteles y XI probable (48 equipos)...")
    teams = cp['teams']

    for i, team in enumerate(teams, 1):
        tid  = team['id']
        name = team['name']
        key  = str(tid)

        if key in cp['teams_data']:
            print(f"  [{i:2}/48] {name} ✓ (checkpoint)")
            continue

        print(f"  [{i:2}/48] {name}...")

        # Squad
        squad = get_squad(ss, tid)
        print(f"    plantel: {len(squad)} jugadores")
        sleep()

        # XI probable
        print(f"    XI probable...")
        xi    = get_probable_xi(ss, tid, n_matches=10)
        print(f"    → formación detectada: {xi['formation']}")

        # Enriquecer con stats
        squad = enrich_squad(squad, stats_lookup, xi['player_ids'])

        # Stats de equipo
        agg = team_aggregates(squad)

        cp['teams_data'][key] = {
            **team,
            'squad':       squad,
            'probable_xi': {
                'formation': xi['formation'],
                'players':   xi['players'],
            },
            'team_stats': agg,
        }
        save_cp(cp)
        sleep(*SLEEP_BETWEEN_TEAMS)

    # ── 4. Armar JSON final ───────────────────────────────────────────────────
    print("\n[4/4] Generando wc2026_data.json...")

    output = {
        'metadata': {
            'scraped_at':     datetime.utcnow().isoformat() + 'Z',
            'wc_tournament_id': WC_T,
            'wc_season_id':     WC_S,
            'stat_tournaments': [name for (_, _, name, _) in STAT_TOURNAMENTS],
            'stat_fields':      STAT_FIELDS,
        },
        'teams': list(cp['teams_data'].values()),
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    n_players = sum(len(t.get('squad', [])) for t in output['teams'])
    print(f"\n✓ Guardado en {OUTPUT_FILE}")
    print(f"  Equipos:   {len(output['teams'])}")
    print(f"  Jugadores: {n_players}")
    print(f"  Tamaño:    {OUTPUT_FILE.stat().st_size / 1024:.0f} KB")

    ss.close()
    print("\nListo. Ahora podés abrir la página HTML.")


if __name__ == '__main__':
    main()
