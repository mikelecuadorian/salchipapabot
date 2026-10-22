#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capacidad_cuadrillas.py — Capacidad real por cuadrilla, con motivos categorizados.

PROBLEMA QUE RESUELVE
  El promedio bruto de trámites por día castiga injustamente:
    - a quien tuvo lluvia (no se instala medidor con agua encima)
    - a quien prestaron para otra tarea (cortes a industrias por estiaje)
    - a quien tuvo el vehículo dañado
    - a quien recibió trámites mal generados por CNEL
  Todos esos días aparecen como "poca productividad" cuando en realidad no
  dependen de la cuadrilla.

QUÉ HACE
  1. Categoriza `motivo_postergacion` en causas propias vs externas.
  2. Marca días excluidos por evento conocido (vehículo, comisión, feriado,
     lluvia) desde un archivo de contexto editable.
  3. Calcula dos métricas que sí son comparables:
       - tram_dia_util   : trámites por día útil (excluye días sin evento)
       - pct_post_propio : % de postergaciones imputables a la cuadrilla
  4. Reporta la dispersión, para no decidir con un solo número.

USO
  python3 capacidad_cuadrillas.py                 # últimos 15 días
  python3 capacidad_cuadrillas.py --dias 30
  python3 capacidad_cuadrillas.py --desde 2026-10-07 --hasta 2026-10-21
  python3 capacidad_cuadrillas.py --json

CONTEXTO
  Los eventos (vehículo dañado, comisión, lluvia) viven en
  ~/.hermes/state/cuadrillas_contexto.json y se editan a mano o con
  `--evento`. Sin ese archivo el script igual corre, solo no excluye nada.
"""

import argparse
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
CONTEXTO = os.path.expanduser("~/.hermes/state/cuadrillas_contexto.json")

# ─────────────────────────────────────────────────────────────────────────
# CATEGORIZACIÓN DE MOTIVOS
# ─────────────────────────────────────────────────────────────────────────
# 'propio'    → depende de la cuadrilla (se le puede exigir)
# 'externo'   → NO depende: clima, cliente, zona, logística
# 'revisar'   → motivo ambiguo que necesita criterio humano (verificar_ambiguos)
#
# El orden importa: se evalúa subcadena en minúsculas, gana la primera.
# Todo lo que NO esté en la lista cae en 'revisar', NUNCA en 'propio': ante
# duda no se castiga.
CATEGORIAS = [
    # ── CLIMA (externo) ────────────────────────────────────────────────
    ("clima", "externo", ["clima", "lluvia", "mal tiempo"]),
    # ── LOGÍSTICA / seguridad (externo) ────────────────────────────────
    ("logistica", "externo", ["difícil acceso", "zona inundada", "zona peligrosa",
                              "candado", "no existe redes"]),
    # ── CLIENTE (externo: el cliente no está o no permite) ─────────────
    ("cliente", "externo", ["no se encuentra nadie", "no permite instalación",
                            "clientes conflictivos", "en construcción"]),
    # ── IMPUTABLE A LA CUADRILLA (propio) ──────────────────────────────
    # Verificado con Miguel (2026-10-22):
    ("predio", "propio", ["predio no cumple", "reubicar medidor",
                          "medidor dentro del predio"]),
    # 'Error de coordenadas' = mala dirección / mala referencia geográfica Y el
    # usuario no contesta. Es fallo del levantamiento: se pudo verificar la
    # dirección antes de salir. Miguel lo confirmó como imputable.
    ("direccion", "propio", ["error de coordenadas"]),
    # ── AMBIGUOS → revisar (2026-10-22) ────────────────────────────────
    # 'atendida por personal de CNEL' → Miguel: "CNEL llegó primero". Es un
    # incumplimiento de la cuadrilla (llegó tarde), pero NO se clasifica como
    # propio porque el sistema NO puede distinguir "llegó tarde" de "le
    # asignaron el trámite después de que CNEL ya lo hizo". Se cuenta aparte
    # para que no infle la postergación externa sin evidencia.
    # 'Entidad oficial' → Miguel no sabe todavía qué significa (2026-10-22, va
    # a averiguar). NO tocar hasta que responda. En el lote de JOHNNY GARCIA
    # del 09-oct eran 43 trámites consecutivos → castigar sería brutal si el
    # motivo resulta no imputable.
    # 'Medidor no existe', 'medidor retirado', 'solicitud mal generada',
    # 'ya canceló' → pueden venir mal de CNEL o del levantamiento. Sin criterio.
    ("ambiguo", "revisar", ["entidad oficial", "atendida por personal de cnel",
                            "medidor no existe", "medidor retirado",
                            "solicitud mal generada", "ya canceló"]),
]

# Motivos que no aparecen en la lista → se clasifican como 'revisar'
DEFAULT_CAT = ("revisar", "externo")   # ante duda NO se castiga


def categorizar(motivo):
    """Devuelve (categoria, tipo) para un motivo de postergación."""
    if not motivo:
        return ("sin_motivo", "revisar")
    m = motivo.lower().strip()
    for cat, tipo, claves in CATEGORIAS:
        for k in claves:
            if k in m:
                return (cat, tipo)
    return DEFAULT_CAT


# ─────────────────────────────────────────────────────────────────────────
# CONTEXTO DE EVENTOS
# ─────────────────────────────────────────────────────────────────────────
def cargar_contexto():
    """{'ROBERTO BRIONES': {'2026-10-21': 'comision_estiaje+lluvia', ...}}"""
    if not os.path.exists(CONTEXTO):
        return {}
    with open(CONTEXTO, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for cu, eventos in (raw.get("eventos") or {}).items():
        cu_u = cu.upper().strip()
        out[cu_u] = {}
        for ev in eventos:
            out[cu_u][ev["fecha"]] = ev.get("tipo", "evento")
    return out


def guardar_contexto(data):
    os.makedirs(os.path.dirname(CONTEXTO), exist_ok=True)
    with open(CONTEXTO, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def agregar_evento(cuadrilla, fecha, tipo, nota=""):
    data = {"eventos": {}}
    if os.path.exists(CONTEXTO):
        with open(CONTEXTO, encoding="utf-8") as f:
            data = json.load(f)
    ev = data.setdefault("eventos", {})
    lista = ev.setdefault(cuadrilla.upper().strip(), [])
    if any(x["fecha"] == fecha for x in lista):
        for x in lista:
            if x["fecha"] == fecha:
                x["tipo"] = tipo
                x["nota"] = nota
    else:
        lista.append({"fecha": fecha, "tipo": tipo, "nota": nota})
    lista.sort(key=lambda x: x["fecha"])
    guardar_contexto(data)


# ─────────────────────────────────────────────────────────────────────────
# CÁLCULO
# ─────────────────────────────────────────────────────────────────────────
def calcular(desde, hasta):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    q = """
    SELECT TRIM(UPPER(cuadrilla)) cu, date(fecha_ejecucion) d,
           COUNT(*) n, SUM(COALESCE(se_posterga,0)) post,
           motivo_postergacion
    FROM recorrido_cuadrillas
    WHERE date(fecha_ejecucion) BETWEEN ? AND ?
      AND cuadrilla IS NOT NULL AND TRIM(cuadrilla) <> ''
    GROUP BY cu, d, motivo_postergacion
    """
    tot = defaultdict(int)          # trámites
    post = defaultdict(int)         # postergados
    por_dia = defaultdict(lambda: defaultdict(int))
    cat_cnt = defaultdict(lambda: defaultdict(int))
    cat_tipo = {}
    motivos = defaultdict(int)

    for cu, d, n, p, motivo in cur.execute(q, (desde, hasta)):
        cu = cu.strip()
        tot[cu] += n
        post[cu] += p or 0
        por_dia[cu][d] += n
        if p:
            cat, tipo = categorizar(motivo)
            cat_cnt[cu][cat] += p
            cat_tipo[cat] = tipo
            motivos[motivo or "(vacío)"] += p
    con.close()

    ctx = cargar_contexto()

    # días del rango
    d0 = datetime.strptime(desde, "%Y-%m-%d").date()
    d1 = datetime.strptime(hasta, "%Y-%m-%d").date()
    dias = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

    filas = []
    for cu in tot:
        evs = ctx.get(cu, {})
        excluidos = {d: t for d, t in evs.items() if d in dias}
        # día útil = hubo actividad y NO está excluido por evento.
        # Se descartan además los "días parciales" (<2 trámites): suelen ser
        # media jornada, guardia, cierre de mes o post-cierre. Contarlos como
        # día completo hunde el mínimo y ensucia la comparación.
        parciales = {d: n for d, n in por_dia[cu].items()
                     if d in dias and 0 < n < 2 and d not in excluidos}
        utiles = [d for d in dias
                  if por_dia[cu].get(d, 0) >= 2 and d not in excluidos]
        valores = [por_dia[cu][d] for d in utiles]

        post_propio = sum(v for c, v in cat_cnt[cu].items() if cat_tipo.get(c) == "propio")
        post_externo = sum(v for c, v in cat_cnt[cu].items() if cat_tipo.get(c) == "externo")
        post_revisar = sum(v for c, v in cat_cnt[cu].items() if cat_tipo.get(c) == "revisar")

        filas.append({
            "cuadrilla": cu,
            "tramites": tot[cu],
            "dias_utiles": len(utiles),
            "dias_excluidos": excluidos,
            "dias_parciales": parciales,
            "tram_dia_util": round(statistics.mean(valores), 2) if valores else 0.0,
            "mediana_dia_util": statistics.median(valores) if valores else 0.0,
            "min_dia": min(valores) if valores else 0,
            "max_dia": max(valores) if valores else 0,
            "desv": round(statistics.pstdev(valores), 2) if len(valores) > 1 else 0.0,
            "postergados": post[cu],
            "pct_post": round(100 * post[cu] / tot[cu], 1) if tot[cu] else 0.0,
            "post_propio": post_propio,
            "post_externo": post_externo,
            "post_revisar": post_revisar,
            "pct_post_propio": round(100 * post_propio / tot[cu], 1) if tot[cu] else 0.0,
            "cats": dict(cat_cnt[cu]),
        })

    filas.sort(key=lambda x: -x["tram_dia_util"])
    return {
        "desde": desde, "hasta": hasta, "n_dias": len(dias),
        "n_cuadrillas": len(filas),
        "total_tramites": sum(f["tramites"] for f in filas),
        "filas": filas,
        "categorias_tipo": cat_tipo,
        "motivos_top": sorted(motivos.items(), key=lambda x: -x[1])[:15],
        "eventos_registrados": sum(len(v) for v in ctx.values()),
    }


def imprimir(res):
    print(f"\n📊 CAPACIDAD REAL POR CUADRILLA  ·  {res['desde']} → {res['hasta']}"
          f"  ({res['n_dias']} días)")
    print(f"   {res['total_tramites']} trámites · {res['n_cuadrillas']} cuadrillas · "
          f"{res['eventos_registrados']} eventos de contexto cargados\n")
    hdr = (f"{'CUADRILLA':28s} {'TRAM':>5s} {'D.ÚTIL':>6s} {'T/DÍA':>6s} "
           f"{'MED':>4s} {'MIN':>4s} {'MAX':>4s} {'POST%':>6s} {'PROP%':>6s}")
    print(hdr)
    print("─" * len(hdr))
    for f in res["filas"]:
        print(f"{f['cuadrilla'][:28]:28s} {f['tramites']:5d} {f['dias_utiles']:6d} "
              f"{f['tram_dia_util']:6.1f} {f['mediana_dia_util']:4.0f} "
              f"{f['min_dia']:4d} {f['max_dia']:4d} "
              f"{f['pct_post']:5.1f}% {f['pct_post_propio']:5.1f}%")
    print()
    ex = [(f["cuadrilla"], f["dias_excluidos"]) for f in res["filas"] if f["dias_excluidos"]]
    if ex:
        print("🔧 Días excluidos por evento de contexto:")
        for cu, dd in ex:
            print(f"   {cu}: " + ", ".join(f"{d} ({t})" for d, t in sorted(dd.items())))
        print()
    pa = [(f["cuadrilla"], f["dias_parciales"]) for f in res["filas"] if f.get("dias_parciales")]
    if pa:
        print("⏱️  Días parciales descartados (<2 trámites):")
        for cu, dd in pa:
            print(f"   {cu}: " + ", ".join(f"{d} ({n})" for d, n in sorted(dd.items())))
        print()
    print("🏷️  Clasificación de motivos:")
    for cat, tipo in sorted(res["categorias_tipo"].items()):
        marca = "🔴 propio" if tipo == "propio" else "⚪ externo"
        print(f"   {cat:10s} → {marca}")
    print()


def verificar_ambiguos(desde, hasta, limite=6):
    """Muestra una muestra de los trámites clasificados como 'revisar'.

    Objetivo: que Miguel (que tiene el contexto de calle) decida si un motivo
    ambiguo debe pasar a 'propio' o quedarse 'externo'. Sin esto, la categoría
    'revisar' se vuelve un cajón donde se esconde el desempeño real.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = """SELECT TRIM(UPPER(cuadrilla)), motivo_postergacion, COUNT(*)
           FROM recorrido_cuadrillas
           WHERE date(fecha_ejecucion) BETWEEN ? AND ? AND se_posterga=1
           GROUP BY 1,2 ORDER BY 3 DESC"""
    agrup = defaultdict(lambda: defaultdict(int))
    for cu, m, n in con.execute(q, (desde, hasta)):
        cat, tipo = categorizar(m)
        if tipo != "revisar":
            continue
        agrup[cu][m] += n
    con.close()

    if not agrup:
        print("\n✅ No hay motivos ambiguos en el rango.\n")
        return

    print("\n🔎 MOTIVOS AMBIGUOS (necesitan tu criterio)")
    print("   Si un motivo es imputable a la cuadrilla, avísame y lo paso a 'propio'.\n")
    for cu in sorted(agrup):
        print(f"   {cu}")
        for m, n in sorted(agrup[cu].items(), key=lambda x: -x[1]):
            print(f"     {n:3d}× {m}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=15)
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ambiguos", action="store_true",
                    help="mostrar motivos que necesitan criterio humano")
    ap.add_argument("--evento", nargs=3, metavar=("CUADRILLA", "FECHA", "TIPO"),
                    help="registrar evento: 'ROBERTO BRIONES' 2026-10-21 comision_estiaje")
    ap.add_argument("--nota", default="")
    a = ap.parse_args()

    if a.evento:
        agregar_evento(a.evento[0], a.evento[1], a.evento[2], a.nota)
        print(f"✅ Evento guardado: {a.evento[0]} {a.evento[1]} → {a.evento[2]}")
        return

    if a.desde and a.hasta:
        desde, hasta = a.desde, a.hasta
    else:
        hasta = (date.today() - timedelta(days=1)).isoformat()
        desde = (date.today() - timedelta(days=a.dias)).isoformat()

    res = calcular(desde, hasta)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        imprimir(res)
        if a.ambiguos:
            verificar_ambiguos(desde, hasta)


if __name__ == "__main__":
    main()
