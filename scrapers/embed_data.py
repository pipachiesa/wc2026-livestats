#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embed_data.py — inyecta los JSON dentro de index.html
=====================================================
Chrome bloquea fetch() de archivos locales (file://), así que al abrir
index.html con doble clic los datos no cargan. Este script embebe los 4
JSON + el timestamp dentro de un <script id="__WC_DATA__"> para que la
página funcione SIN servidor. El index.html igual sigue usando fetch()
como fallback cuando se sirve por HTTP.

Corré esto al final del pipeline (update.sh ya lo hace).

Uso:
    python3 scrapers/embed_data.py
"""

import json
from pathlib import Path
from datetime import datetime, timezone

ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INDEX    = ROOT / "index.html"
LOG_FILE = DATA_DIR / "scrape_log.txt"

START = "<!--__WC_DATA_START__-->"
END   = "<!--__WC_DATA_END__-->"


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load(path, default):
    p = DATA_DIR / path
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default
    return default


def main():
    last_update = ""
    lu = DATA_DIR / "last_update.txt"
    if lu.exists():
        last_update = lu.read_text(encoding="utf-8").strip()

    payload = {
        "squads": load("squads.json", {}),
        "matches": load("matches.json", {"matches": []}),
        "wc": load("player_stats_wc.json", {}),
        "season": load("player_stats_season.json", {}),
        "last_update": last_update,
    }
    # serializar y proteger contra cierre prematuro de <script>
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")

    html = INDEX.read_text(encoding="utf-8")
    if START not in html or END not in html:
        log("FATAL: faltan los marcadores __WC_DATA__ en index.html")
        raise SystemExit(1)

    new_block = (f'{START}\n'
                 f'<script id="__WC_DATA__" type="application/json">{blob}</script>\n'
                 f'{END}')
    pre, rest = html.split(START, 1)
    _, post = rest.split(END, 1)
    INDEX.write_text(pre + new_block + post, encoding="utf-8")

    n_sq = sum(len(t.get("players", [])) for t in payload["squads"].values())
    log(f"embed_data: {len(payload['squads'])} equipos / {n_sq} jugadores, "
        f"{len(payload['matches'].get('matches', []))} partidos, "
        f"{len(blob)//1024} KB embebidos · last_update={last_update or '—'}")


if __name__ == "__main__":
    main()
