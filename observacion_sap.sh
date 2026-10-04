#!/data/data/com.termux/files/usr/bin/bash
# ==========================================================================
# observacion_sap.sh — Invoca el extractor de Observación SAP
# --------------------------------------------------------------------------
# Playwright + Chromium viven DENTRO del contenedor Ubuntu (proot), no en
# Termux. Por eso el bot llama a este wrapper, que entra al contenedor y corre
# el script (que vive en el repo del bot, junto a este wrapper).
#   (Mismo mecanismo con que el servicio 'hermes' arranca:
#        proot-distro login ubuntu -- <comando>)
# Uso:  observacion_sap.sh <nro_orden>
# Salida: línea con marcador "@@OBS@@ {json}"
# ==========================================================================
if [ -z "$1" ]; then
    echo "uso: observacion_sap.sh <nro_orden>" >&2
    exit 64
fi
exec /data/data/com.termux/files/usr/bin/proot-distro login ubuntu -- \
     /usr/bin/python3 /data/data/com.termux/files/home/salchipapabot/observacion_sap.py "$1"
