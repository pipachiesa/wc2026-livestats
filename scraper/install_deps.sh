#!/bin/bash
# Instala todas las dependencias necesarias para el scraper WC2026
echo "Instalando dependencias..."
pip install pandas requests beautifulsoup4 Pillow faker \
            nodriver pydoll-python undetected-chromedriver \
            mplsoccer matplotlib numpy ipython \
            --break-system-packages 2>/dev/null || \
pip install pandas requests beautifulsoup4 Pillow faker \
            nodriver pydoll-python undetected-chromedriver \
            mplsoccer matplotlib numpy ipython
echo "Listo."
