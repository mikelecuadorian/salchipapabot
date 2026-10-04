#!/data/data/com.termux/files/usr/bin/bash
# ==========================================================================
# observacion_sap_warm.sh — Arranca el daemon SAP "tibio" (segundo plano)
# --------------------------------------------------------------------------
# El bot lo lanza (detached) cuando una consulta tuvo que ir por el camino
# FRÍO, para que la siguiente sea rápida (~5 s). El daemon se apaga solo tras
# 10 min sin uso. Si ya hay uno corriendo, el nuevo no puede tomar el puerto y
# sale solo (no hace daño).
# ==========================================================================
exec /data/data/com.termux/files/usr/bin/proot-distro login ubuntu -- \
     /usr/bin/python3 /data/data/com.termux/files/home/salchipapabot/observacion_sap_daemon.py
