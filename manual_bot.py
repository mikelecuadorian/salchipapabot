#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG de los manuales CNEL para SalchipapaBot — DOS manuales, doble backend.

MANUALES:
  · MN-COM-AC-001 V04 — Instalación de acometida y sistema de medición
      364 cláusulas + 61 figuras del Anexo 1
  · MN-GG-COM-001 V05 — Políticas Comerciales
      233 cláusulas (sin anexo gráfico)

PROBLEMA (2026-10-29): el bot corre en **Termux nativo**, donde NO existen
`fastembed` ni `onnxruntime`. El RAG vive en PRoot (Ubuntu), que sí los tiene.
Importar `manual_rag` desde el bot falla con:
    ❌ Error consultando el manual: No module named 'manual_bot'

SOLUCIÓN: dos backends, elegidos en runtime.
  1) import  -> si `manual_rag` se puede importar (estamos dentro de PRoot)
  2) puente  -> si no, se llama `rag_manual_bridge.sh` (proot-distro login)
                que devuelve JSON por stdout.

El puente cuesta ~8-30 s la primera vez. Por eso hay timeout y se avisa.
"""
import json
import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

RAG_DIR = "/root/rag_manual"
BRIDGE = "/data/data/com.termux/files/home/salchipapabot/rag_manual_bridge.sh"
TIMEOUT = 180

DISPARADORES = (
    "manual", "norma", "cláusula", "clausula", "numeral", "acometida",
    "bornera", "socket", "tablero", "medidor", "puesta a tierra", "sellado",
    "ducto", "caja de paso", "caja de distribución", "caja de distribucion",
    "transformador", "altura de montaje", "especificación", "especificacion",
    "se permite", "está permitido", "esta permitido", "cuántos medidores",
    "cuantos medidores", "qué dice", "que dice",
)

PROMPT = """Eres un asistente normativo de CNEL EP. Respondes SOLO con base en el
CONTEXTO, que puede venir de DOS documentos distintos:

  A) MANUAL DE INSTALACIÓN — "Manual para la Instalación de la Acometida y
     Sistema de Medición a los Consumidores de CNEL EP" (MN-COM-AC-001, V04).
     Es el documento TÉCNICO: cotas, materiales, tipos de caja/tablero, planos.
  B) MANUAL DE POLÍTICAS COMERCIALES (MN-GG-COM-001, V05, 2022).
     Es el documento COMERCIAL: plazos, depósitos, facturación, suspensión,
     reclamos, cobranza, sanciones. NO da medidas ni especificaciones técnicas.

REGLAS ESTRICTAS:
1. Usa ÚNICAMENTE la información del CONTEXTO. No agregues conocimiento propio,
   no completes con lo que "normalmente" se hace, no supongas medidas ni plazos.
2. CITA SIEMPRE el numeral, el manual y la página:
   "(cláusula 4.181, pág. 27)"  o  "(MN-GG-COM-001, 2.36, pág. 6)".
   Si el dato viene de una figura: "(ver FIG. 34, pág. 104)".
3. Si el CONTEXTO no responde la pregunta, di exactamente: "Eso no está en el
   manual" y explica en una línea qué sí se encontró. NO inventes.
4. NUNCA mezcles los dos manuales en una misma cita. Si el dato es técnico, cita
   el MN-COM-AC-001; si es de plazos/dinero/derechos, cita el MN-GG-COM-001.
5. En el manual técnico distingue los tipos de solución, que son distintos:
   - CAJA DE DISTRIBUCIÓN: instalación aérea en fachada, medidor 2F-3H CL-100.
   - TABLERO METÁLICO BORNERA: armario metálico (2, 3, 4, 6 o 20 medidores).
   - TABLERO VITRINA: socket, no bornera.
   - MÓDULO INDIVIDUAL: un solo medidor.
   Si la pregunta menciona un número de medidores, respeta EXACTAMENTE ese número.
6. El MN-GG-COM-001 es de marzo 2022 y cita normativa de hasta 2020. Si lo usas
   para algo que pueda haber cambiado, agrega al final:
   "⚠️ Verificar vigencia: el MN-GG-COM-001 es de marzo 2022."
7. Sé breve y práctico: viñetas cortas, cotas en metros o centímetros como las
   da el manual. Español de Ecuador, tono técnico directo.
8. No repitas el contexto literal; redáctalo para que se entienda.
"""


# ─────────────────── backend 1: import directo (dentro de PRoot) ───────────────────

def _consultar_directo(pregunta):
    if RAG_DIR not in sys.path:
        sys.path.insert(0, RAG_DIR)
    import manual_rag
    r = manual_rag.consultar(pregunta, k=3)
    return {
        "hay": r["hay"],
        "manual": r.get("manual"),
        "manual_nombre": r.get("manual_nombre"),
        "contexto": r["contexto"],
        "clausulas": [
            {"num": c["num"], "pagina": c["pagina"], "sim": c["sim"]}
            for c in r["clausulas"] if c["sim"] >= 0 or True
        ],
        "figuras": [
            {"id": f["id"], "titulo": f["titulo"], "pagina": f["pagina"],
             "dimensiones": f["dimensiones"], "url": f.get("url")}
            for f in r["figuras"]
        ],
        "ambos": r.get("ambos", []),
    }


# ─────────────────── backend 2: puente a PRoot (Termux nativo) ───────────────────

def _consultar_puente(pregunta):
    if not os.path.exists(BRIDGE):
        raise RuntimeError(f"no existe el puente {BRIDGE}")
    # Entorno MÍNIMO. No heredar dict(os.environ) completo: bajo runsv trae
    # PROOT_L2S_DIR apuntando a un caché de symlinks que este proceso no puede
    # usar, y proot se CUELGA (>180 s) en vez de fallar. Medido 2026-10-29:
    #   con PROOT_L2S_DIR heredado -> timeout 180 s
    #   con entorno mínimo          -> 44 s y JSON correcto
    env = {
        "PATH": "/data/data/com.termux/files/usr/bin:/system/bin",
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TMPDIR": "/data/data/com.termux/files/usr/tmp",
    }
    # Ruta ABSOLUTA al bash de Termux: `["bash", ...]` depende del PATH del
    # servicio y fallaba con rc=1 sin stderr (2026-10-29).
    bash = "/data/data/com.termux/files/usr/bin/bash"
    if not os.path.exists(bash):
        bash = "/system/bin/sh"
    try:
        p = subprocess.run(
            [bash, BRIDGE, pregunta],
            capture_output=True, text=True, timeout=TIMEOUT, env=env,
            stdin=subprocess.DEVNULL,
            cwd="/data/data/com.termux/files/home/salchipapabot",
            # 🔴 close_fds: proot hereda los descriptores del bot (sockets de
            # Telegram, BD) y se queda bloqueado esperando sobre ellos. Desde
            # bash funciona; desde Python colgaba >180 s. Medido 2026-10-29.
            close_fds=True,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"el RAG tardó más de {TIMEOUT}s (PRoot + modelo). Intenta de nuevo."
        )
    salida = (p.stdout or "").strip()
    # El login de PRoot puede escupir warnings antes del JSON: tomar la última línea '{"'
    linea = ""
    for ln in reversed(salida.splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            linea = ln
            break
    if not linea:
        raise RuntimeError(
            f"puente sin JSON (rc={p.returncode}, bash={bash}) "
            f"stdout={salida[-300:]!r} stderr={(p.stderr or '')[-300:]!r}"
        )
    d = json.loads(linea)
    if d.get("error"):
        raise RuntimeError(d["error"])
    return d


def consultar(pregunta):
    """Intenta import directo; si no hay fastembed, usa el puente a PRoot."""
    try:
        import fastembed  # noqa: F401
        hay_libs = True
    except ImportError:
        hay_libs = False

    if hay_libs:
        try:
            return _consultar_directo(pregunta)
        except Exception as e:
            log.warning(f"RAG directo falló ({e}); usando puente PRoot")

    return _consultar_puente(pregunta)


# ─────────────────── redacción con DeepSeek ───────────────────

def construir_prompt(pregunta, contexto):
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": f"CONTEXTO DEL MANUAL:\n{contexto}\n\nPREGUNTA: {pregunta}"},
    ]


def es_pregunta_manual(texto):
    t = (texto or "").lower()
    return any(d in t for d in DISPARADORES)


def _etiqueta_fuente(d):
    """Devuelve 'MN-COM-AC-001 V04' o 'MN-GG-COM-001 V05'."""
    nombre = d.get("manual_nombre") or ""
    return nombre if nombre else "MN"


def redactar(pregunta, cliente, modelo, temperature=0.1, max_tokens=900):
    r = consultar(pregunta)

    if not r.get("hay"):
        ambos = r.get("ambos") or []
        detalle = ""
        if len(ambos) > 1:
            detalle = "\n\n_Busqué en los dos documentos: MN-COM-AC-001 V04 " \
                      "(364 cláusulas + 61 figuras) y MN-GG-COM-001 V05 " \
                      "(233 cláusulas)._"
        else:
            detalle = "\n\n_Busqué en el MN-COM-AC-001 V04 (364 cláusulas + " \
                      "61 figuras) y en el MN-GG-COM-001 V05 (233 cláusulas)._"
        return {
            "texto": "📕 *Eso no está en el manual.*" + detalle,
            "figuras": [], "fuentes": [],
        }

    resp = cliente.chat.completions.create(
        model=modelo,
        messages=construir_prompt(pregunta, r["contexto"]),
        temperature=temperature,
        max_tokens=max_tokens,
    )
    texto = resp.choices[0].message.content.strip()

    etq = _etiqueta_fuente(r)
    fuentes = [f"{etq} {c['num']} (pág. {c['pagina']})" for c in r["clausulas"]]
    fuentes += [f"{f['id']} (pág. {f['pagina']})" for f in r["figuras"]]
    if fuentes:
        texto += "\n\n📎 *Fuente:* " + " · ".join(dict.fromkeys(fuentes))

    return {"texto": texto, "figuras": r["figuras"], "fuentes": fuentes,
            "manual": r.get("manual"), "manual_nombre": r.get("manual_nombre")}


if __name__ == "__main__":
    p = " ".join(sys.argv[1:]) or "cuántos medidores bornera cuando son 8 en un predio"
    d = consultar(p)
    print(f"HAY: {d.get('hay')}  MANUAL: {d.get('manual_nombre')}")
    print(f"CLÁUSULAS: {[c['num'] for c in d.get('clausulas', [])]}")
    print(f"FIGURAS: {[f['id'] for f in d.get('figuras', [])]}")
