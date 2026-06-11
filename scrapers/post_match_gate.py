#!/usr/bin/env python3
"""
post_match_gate.py — decide si hay que correr la actualización post-partido.

Se ejecuta cada pocos minutos desde GitHub Actions. En vez de scrapear siempre,
solo dispara el update cuando un partido del Mundial terminó hace ~30 min.

Lógica:
  • Un partido es "candidato" cuando  ahora >= kickoff + THRESHOLD_MIN
    (≈ 30 min después de que un partido de ~2h finaliza) y todavía NO está
    en data/.processed_matches.json.
  • Si hay al menos un candidato → modo `check` imprime "RUN" y sale 0.
    Si no hay ninguno → imprime "SKIP" y sale 1.
  • Después de scrapear, el workflow llama a este script en modo `mark`: relee
    matches.json y marca como procesados SOLO los partidos cuyo status ya es
    "played" (o que pasaron GIVEUP_MIN, p.ej. suspendidos). Así un partido con
    alargue/penales que todavía no terminó se vuelve a intentar en el próximo
    ciclo en lugar de quedar marcado con datos incompletos.

Uso:
  python3 scrapers/post_match_gate.py            # modo check  → RUN / SKIP
  python3 scrapers/post_match_gate.py mark        # modo mark   → actualiza estado
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATCHES = ROOT / "data" / "matches.json"
STATE = ROOT / "data" / ".processed_matches.json"

# ~30 min después de que un partido de grupo (~2h reales) finaliza.
THRESHOLD_MIN = int(os.environ.get("WC_THRESHOLD_MIN", "145"))
# Si pasó esto desde el kickoff y el partido sigue sin estar "played"
# (suspendido / sin datos), lo damos por procesado para no reintentar infinito.
GIVEUP_MIN = int(os.environ.get("WC_GIVEUP_MIN", "360"))


def load_matches():
    return json.loads(MATCHES.read_text(encoding="utf-8")).get("matches", [])


def load_state():
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_state(ids):
    STATE.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=0),
                     encoding="utf-8")


def kickoff(m):
    dt = m.get("datetime")
    if not dt:
        return None
    return datetime.fromisoformat(dt.replace("Z", "+00:00"))


def check():
    now = datetime.now(timezone.utc)
    processed = load_state()
    candidates = []
    for m in load_matches():
        mid = m.get("id")
        ko = kickoff(m)
        if not mid or ko is None or mid in processed:
            continue
        if now >= ko + timedelta(minutes=THRESHOLD_MIN):
            candidates.append(mid)
    if candidates:
        print("RUN " + ",".join(candidates))
        return 0
    print("SKIP")
    return 1


def mark():
    now = datetime.now(timezone.utc)
    processed = load_state()
    added = []
    for m in load_matches():
        mid = m.get("id")
        ko = kickoff(m)
        if not mid or ko is None or mid in processed:
            continue
        finished = m.get("status") == "played"
        gave_up = now >= ko + timedelta(minutes=GIVEUP_MIN)
        if finished or gave_up:
            processed.add(mid)
            added.append(mid)
    save_state(processed)
    print(f"marked {len(added)}: {','.join(added) if added else '-'}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    sys.exit(mark() if mode == "mark" else check())
