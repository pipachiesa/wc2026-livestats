#!/usr/bin/env bash
# update.sh — actualización diaria de datos del Mundial 2026
# Corre los scrapers y registra el timestamp para el footer de index.html.
set -uo pipefail

cd "$(dirname "$0")"
DATA_DIR="data"
mkdir -p "$DATA_DIR"

echo "▶ [$(date '+%Y-%m-%d %H:%M:%S')] Iniciando actualización WC2026…"

# 1) Plantel oficial desde FIFA (nombre, número, posición, DT)
python3 scrapers/scrape_squads.py
SQ=$?

# 1b) Completar el club de cada jugador desde SofaScore (FIFA no lo da)
python3 scrapers/enrich_clubs_sofascore.py
CL=$?

# 2) Resultados, fixture y stats del Mundial (lo principal)
python3 scrapers/scrape_wc_stats.py
WC=$?

# 3) Timestamp de última actualización (lo lee el footer del index.html)
TS="$(date '+%Y-%m-%d %H:%M')"
echo "$TS" > "$DATA_DIR/last_update.txt"

# 4) Embeber los JSON en index.html → funciona con doble clic (file://), sin servidor
python3 scrapers/embed_data.py
EM=$?

if [ $SQ -eq 0 ] && [ $CL -eq 0 ] && [ $WC -eq 0 ] && [ $EM -eq 0 ]; then
  echo "✓ [$TS] Actualización completa. last_update.txt = $TS"
else
  echo "⚠ [$TS] Terminó con errores (squads=$SQ, clubs=$CL, wc=$WC, embed=$EM). Revisá data/scrape_log.txt"
fi
