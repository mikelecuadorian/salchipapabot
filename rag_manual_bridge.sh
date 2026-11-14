#!/data/data/com.termux/files/usr/bin/bash
# Puente: ejecuta el RAG del manual DENTRO del rootfs Ubuntu (PRoot), donde vive
# fastembed. El bot corre en Termux nativo, que NO tiene fastembed/onnxruntime.
#
# Uso:  rag_manual_bridge.sh "pregunta"
# Salida: JSON (una línea) en stdout.
#
# HISTORIAL DE FALLOS (2026-10-29) — leer antes de tocar:
#  1. `proot-distro login` se niega a correr bajo PRoot e imprime warnings →
#     se invoca `proot` directo sobre el rootfs.
#  2. `taskset -c 0` puede no tener permiso bajo runsv → se quitó.
#  3. 🔴 `nice -n 19` + `exec`: el proceso quedaba con estado 't' (stopped) y
#     NUNCA terminaba. Se acumularon 7 procesos rag_json en futex_wait,
#     consumiendo RAM hasta que las consultas nuevas se colgaban.
#     → NO usar nice. Prioridad normal; el propio modelo ya es ligero (28 textos).
set -u

PREGUNTA="${1:-}"
if [ -z "$PREGUNTA" ]; then
  echo '{"hay": false, "error": "sin pregunta"}'
  exit 2
fi

ROOTFS=/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs
PROOT=/data/data/com.termux/files/usr/bin/proot

if [ ! -x "$PROOT" ]; then
  echo "{\"hay\": false, \"error\": \"proot no ejecutable en $PROOT\"}"
  exit 3
fi
if [ ! -d "$ROOTFS" ]; then
  echo "{\"hay\": false, \"error\": \"rootfs no existe: $ROOTFS\"}"
  exit 4
fi
if [ ! -f "$ROOTFS/root/rag_manual/rag_json.py" ]; then
  echo "{\"hay\": false, \"error\": \"no existe rag_json.py en el rootfs\"}"
  exit 5
fi

# --kill-on-exit: si el puente muere, el hijo dentro del rootfs no queda huérfano.
# Se ejecuta SIN exec para poder limpiar si algo falla.
#
# TMPDIR (2026-10-29) — fastembed NO usa HF_HOME: usa `cache_dir` = $TMPDIR/fastembed_cache.
#   · desde un shell con TMPDIR vacío → /tmp DENTRO del rootfs → modelo presente ✅
#   · desde el bot, TMPDIR=/data/data/com.termux/files/usr/tmp (heredado de Termux)
#     → apunta FUERA del rootfs, ahí no hay modelo, y fastembed se pone a DESCARGAR
#     142 MB por consulta → se cuelga y el padre lo mata (rc=124).
# Por eso "desde mi terminal funciona y desde el bot no": mismo script, distinto TMPDIR.
#
# ⚠️ CORRECCIÓN 2026-11-13 — /tmp se BORRA al reiniciar:
#   Depender de TMPDIR=/tmp resolvía el problema de HOY, pero la caché vivía en
#   un directorio efímero. Un reinicio limpio de la tablet = RAG caído, y sin error
#   claro (el modelo intentaba descargar 118 MB en medio de una consulta).
#   Ahora rag_config.py fija FASTEMBED_CACHE_PATH a /root/.cache/fastembed (persistente)
#   y pasa cache_dir= explícito a TextEmbedding. TMPDIR se mantiene fijo a /tmp
#   solo por si algún módulo viejo aún lo consulta.
export TMPDIR=/tmp
export FASTEMBED_CACHE_PATH=/root/.cache/fastembed
export HF_HOME=/root/.cache/huggingface
export HF_HUB_DISABLE_TELEMETRY=1
"$PROOT" -0 -r "$ROOTFS" \
  -b /dev -b /proc -b /sys -b /root \
  --kill-on-exit --link2symlink -w /root \
  /usr/bin/python3 -u /root/rag_manual/rag_json.py "$PREGUNTA"

RC=$?
# Red de seguridad: matar cualquier rag_json que haya quedado colgado.
if [ $RC -ne 0 ]; then
  pkill -9 -f "rag_json.py" 2>/dev/null || true
fi
exit $RC
