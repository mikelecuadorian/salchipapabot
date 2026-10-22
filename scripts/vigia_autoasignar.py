#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-asignación de trámites nuevos a cuadrillas (módulo del VIGÍA).

Diseñado 2026-10-15 a pedido de Miguel. Reglas:
  1. Si AFlow falla → se reintenta en la próxima corrida (no se marca como hecho).
  2. Al actuar → SÍ se avisa (también si falla).
  3. Solo se auto-asigna lo que en su RECORDATORIO diga "asignar", "planificar" o
     "pasar" Y tenga una cuadrilla VÁLIDA (reconocible en el directorio).
  4. (2026-10-15, ampliación) Si el trámite nuevo ya tiene OTRA cuadrilla distinta
     a la del vigía → se REASIGNA a la del vigía. Pero SOLO si el trámite está
     PENDIENTE (no ejecutado / no liberado): un trabajo ya hecho NO se reasigna
     (no se falsea el historial de quién lo realizó).

QUÉ HACE, SEGÚN EL ESTADO DEL TRÁMITE NUEVO
    sin cuadrilla            → PLANIFICAR (asignar) a la cuadrilla del vigía
    con la cuadrilla correcta→ no hacer nada
    con OTRA cuadrilla       → REASIGNAR a la del vigía (si no está ejecutado)
    ejecutado / liberado     → no hacer nada (nunca, ni aunque la cuadrilla difiera)

DERIVACIÓN AUTOMÁTICA (no hay mapa fijo que mantener):
  Se leen los recordatorios de CUENTAS_CONTRATO y RECORDATORIOS_CEDULA del vigía
  (con AST, sin importarlo). Un ítem entra al auto-asignado si su texto contiene
  "asignar", "planificar" o "pasar" Y menciona una cuadrilla válida.

Uso standalone:
  python3 vigia_autoasignar.py                 # lista el mapa derivado
  python3 vigia_autoasignar.py --dry-run --tramite X --cuadrilla Y
"""
import os, re, sys, subprocess, unicodedata

PY = "/root/sap_venv/bin/python3"
SCRIPT_AUTO = "/root/.hermes/scripts/tools/aflow_asignar_auto.py"  # ← el que usa el vigía
SCRIPT_ASIGNAR = "/root/.hermes/scripts/tools/aflow_seleccionar_cuadrilla.py"  # legado
SCRIPT_REASIGNAR = "/root/.hermes/scripts/tools/aflow_reasignar.py"            # legado
SCRIPT_LOTE = "/root/.hermes/scripts/tools/aflow_lote.py"
VIGIA = "/root/.hermes/scripts/vigia_cedulas_operativo.py"

# ── Directorio REAL de cuadrillas en AFlow (cédulas leídas del DOM 2026-10-15) ──
CUADRILLA_CEDULA = {
    "CHRISTOPHER ORTIZ": "0921533568",
    "ORLYN CABRERA": "1207067438",
    "TOMMY SAMANIEGO": "0956072698",
    "ROBERTO BRIONES": "1203218654",
    "JOSE CARRION": "0958556813",
    "HAROLD VELOZ": "0924125057",
    "CARLOS GUARACA": "0920197241",
    "JULIO CUZCO": "0919327783",
    "DIEGO LUNA": "0923275630",
    "EDINSON QUIÑONEZ": "1206588558",
    "CARLOS PEREZ": "0926550781",
    "CARLOS MIRANDA MOLESTINA": "0928817774",
    "SERGIO CASTRO": "0705129153",
    "ACCEL CEVALLOS": "0942935149",
    "JIMMY VELIZ": "0923112445",
    "BRYAN ACOSTA": "0923465934",
    "CARLOS SAMUEL GUERRERO LAMOTA": "0916272438",
    "JORGE LOPEZ": "0924379910",
    "JOHNNY GARCIA": "0924954563",
    "JOEL ESPINAR": "0941296550",
    "CORREA ORTEGA ANA BELEN": "0926265059",
    "DARWIN INDACOCHEA": "0916893340",
    "VICENTE GONZALEZ": "0954123535",
}

# Estados que significan "ya terminado" → NUNCA reasignar
ESTADOS_TERMINADOS = ("LIBERADO", "CERRADO")

TIMEOUT_ACCION = 300
# El lote hace login UNA vez + ~37 s por trámite. Con el límite de 3 min del cron,
# el lote se auto-limita a MAX_LOTE trámites por corrida; el resto queda en la cola
# (`autoasignaciones_pendientes`) y sigue en la corrida siguiente.
TIMEOUT_LOTE = 170          # < 180 s (límite duro del cron) para poder guardar estado
MAX_LOTE = 3                # trámites por corrida en modo lote (≈23s + 3×37s ≈ 134s)


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").upper())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


# ── Derivación automática desde los recordatorios del vigía ────────────────────

def _leer_recordatorios():
    import ast
    try:
        tree = ast.parse(open(VIGIA, encoding="utf-8").read())
    except Exception as e:
        print(f"⚠️ No pude leer recordatorios del vigía: {e}", file=sys.stderr)
        return {}, {}
    out = {}
    for name in ("CUENTAS_CONTRATO", "RECORDATORIOS_CEDULA"):
        d = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        try: d = ast.literal_eval(n.value)
                        except Exception: d = {}
        out[name] = d
    return out.get("CUENTAS_CONTRATO", {}), out.get("RECORDATORIOS_CEDULA", {})


def _cuadrilla_en_texto(texto):
    t = _norm(texto)
    for nombre in sorted(CUADRILLA_CEDULA, key=len, reverse=True):
        if _norm(nombre) in t:
            return nombre
    return None


def _es_instruccion_asignar(texto):
    t = _norm(texto)
    return ("ASIGNAR" in t) or ("PLANIFICAR" in t) or ("PASAR" in t)


def construir_mapa_autoasignar():
    cuentas, cedulas = _leer_recordatorios()
    mapa = {}
    for clave, valor, prefijo in (
        *[(k, v, "cuenta") for k, v in cuentas.items()],
        *[(k, v, "cedula") for k, v in cedulas.items()],
    ):
        if not isinstance(valor, str):
            continue
        if not _es_instruccion_asignar(valor):
            continue
        cuad = _cuadrilla_en_texto(valor)
        if not cuad:
            continue
        mapa[f"{prefijo}:{clave}"] = cuad
    return mapa


AUTO_ASIGNAR = construir_mapa_autoasignar()


def cuadrilla_de(clave):
    nombre = AUTO_ASIGNAR.get(clave)
    if not nombre:
        return None, None
    return nombre, CUADRILLA_CEDULA.get(nombre)


# ── Estado del trámite en la BD ───────────────────────────────────────────────

def estado_tramite(con, numero_tramite):
    """Devuelve dict {'cuadrilla', 'terminado'} del trámite, mirando la BD.

    terminado=True si ya está ejecutado/liberado/cerrado → nunca reasignar.
    Busca en gestion_tramites y recorrido_cuadrillas.
    """
    cuadrilla, terminado = "", False
    # gestion_tramites trae 'estado'
    try:
        r = con.execute(
            "SELECT cuadrilla, estado FROM gestion_tramites WHERE numero_tramite = ? LIMIT 1",
            (str(numero_tramite),)).fetchone()
        if r:
            cuadrilla = (r[0] or "").strip()
            est = _norm(r[1] or "")
            if any(t in est for t in ESTADOS_TERMINADOS):
                terminado = True
    except Exception:
        pass
    # recorrido_cuadrillas trae 'accion_ejecutada'
    try:
        r = con.execute(
            "SELECT cuadrilla, accion_ejecutada FROM recorrido_cuadrillas "
            "WHERE numero_tramite = ? LIMIT 1",
            (str(numero_tramite),)).fetchone()
        if r:
            if not cuadrilla:
                cuadrilla = (r[0] or "").strip()
            if str(r[1] or "").strip().upper() in ("SI", "SÍ", "1", "TRUE"):
                terminado = True
    except Exception:
        pass
    return {"cuadrilla": cuadrilla, "terminado": terminado}


# ── Acciones en AFlow ─────────────────────────────────────────────────────────

def _correr(script, numero_tramite, cedula_cuadrilla, frase, dry_run):
    if dry_run:
        return True, f"[DRY-RUN] {os.path.basename(script)} {numero_tramite} → {cedula_cuadrilla}"
    cmd = [PY, script, "--tramite", str(numero_tramite), "--cuadrilla", str(cedula_cuadrilla),
           "--ejecutar", "--si", frase]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT_ACCION, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return False, f"timeout ({TIMEOUT_ACCION}s)"
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        ult = [l for l in out.strip().splitlines() if l.strip()][-3:]
        return False, "exit %d: %s" % (p.returncode, " | ".join(ult))
    if "click emitido" not in out:
        ult = [l for l in out.strip().splitlines() if l.strip()][-3:]
        return False, "no se confirmó la acción: " + " | ".join(ult)
    return True, "ok"


def _correr_auto(numero_tramite, cedula_cuadrilla, dry_run=False, verbose=False):
    """Ejecuta la asignación con el script unificado `aflow_asignar_auto.py`.

    Este script decide solo el camino (Por Planificar → planifica · Planificados →
    reasigna · ninguno → no existe), NO tiene modo seguro y verifica al final.
    Reemplaza a `aflow_seleccionar_cuadrilla.py` / `aflow_reasignar.py`, que
    exigían pasar la frase de confirmación y elegir a mano planificar vs reasignar.

    Devuelve (ok, detalle). El detalle incluye la acción real ejecutada para que el
    aviso al usuario diga la verdad (PLANIFICADO vs REASIGNADO).
    """
    if dry_run:
        return True, f"[DRY-RUN] aflow_asignar_auto.py {numero_tramite} → {cedula_cuadrilla}"
    cmd = [PY, SCRIPT_AUTO, "--tramite", str(numero_tramite),
           "--cuadrilla", str(cedula_cuadrilla)]
    if verbose:
        cmd.append("--verbose")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT_ACCION, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        return False, f"timeout ({TIMEOUT_ACCION}s)"
    out = (p.stdout or "") + (p.stderr or "")
    rc = p.returncode
    if rc == 0:
        # leer la acción y la cuadrilla verificada del propio stdout
        accion = "asignado"
        for ln in out.splitlines():
            if "VERIFICADO" in ln:
                accion = "verificado"
        return True, f"ok ({accion})"
    if rc == 4:
        return False, "el trámite NO EXISTE en ACIIS (ni Por Planificar ni Planificados)"
    ult = [l for l in out.strip().splitlines() if l.strip()][-3:]
    return False, "exit %d: %s" % (rc, " | ".join(ult))


def asignar_en_aflow(numero_tramite, cedula_cuadrilla, dry_run=False):
    """Asignar = camino automático completo (planifica o reasigna según dónde esté)."""
    return _correr_auto(numero_tramite, cedula_cuadrilla, dry_run=dry_run)


def reasignar_en_aflow(numero_tramite, cedula_cuadrilla, dry_run=False):
    """Se mantiene por compatibilidad: el script automático ya decide el camino."""
    return _correr_auto(numero_tramite, cedula_cuadrilla, dry_run=dry_run)


def correr_lote(items, dry_run=False, log=print):
    """Procesa VARIOS trámites en UNA sola sesión de AFlow (optimización 2026-10-16).

    items: [{'numero_tramite','cuadrilla'}] — el tipo (planificar/reasignar) lo
    decide el propio lote según la cuadrilla actual del trámite en la BD.
    Devuelve: {tramite: ('OK'|'PEND'|'SKIP', motivo)}.

    Reusa login + carga de AFlow + navegación: ahorra ~22 s de setup por trámite
    a partir del segundo (medido: 15–30% según N).
    """
    if dry_run:
        return {str(i["numero_tramite"]): ("OK", "[DRY-RUN] lote simulado") for i in items}
    args = [PY, SCRIPT_LOTE]
    for it in items:
        args += ["--item", f'{it["numero_tramite"]}:{it["cuadrilla"]}']
    args += ["--ejecutar", "--si", "CONFIRMO LOTE"]
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=TIMEOUT_LOTE, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        log(f"   ⏱️  lote: timeout ({TIMEOUT_LOTE}s)")
        return {}
    # Salida parseable: '<OK|PEND|SKIP> <tramite> <motivo>'
    out = {}
    for linea in (p.stdout or "").splitlines():
        partes = linea.strip().split(" ", 2)
        if len(partes) >= 2 and partes[0] in ("OK", "PEND", "SKIP"):
            out[partes[1]] = (partes[0], partes[2] if len(partes) > 2 else "")
    if p.returncode != 0 and not out:
        log(f"   ❌ lote falló (exit {p.returncode}): {(p.stderr or '')[-200:]}")
    return out


def _analizar(tr, clave, con, log, visto_por):
    """Decide qué hacer con un trámite SIN tocar AFlow. Devuelve dict o None.

    {'tr','clave','cuadrilla_destino','actual','accion'} donde accion ∈
    {'planificar','reasignar','saltar'}.
    """
    nombre, cedula = cuadrilla_de(clave)
    if not nombre or not cedula:
        log(f"   ⏭️  {tr}: clave sin cuadrilla destino → se salta")
        return {"tr": tr, "clave": clave, "accion": "saltar", "motivo": "sin cuadrilla destino"}
    est = estado_tramite(con, tr)
    actual = est["cuadrilla"]
    if est["terminado"]:
        log(f"   ⏭️  {tr}: ya terminado ('{actual or 's/c'}')")
        return {"tr": tr, "clave": clave, "accion": "saltar", "motivo": "ya terminado"}
    if actual and _norm(actual) == _norm(nombre):
        log(f"   ⏭️  {tr}: ya tiene la cuadrilla correcta ('{actual}')")
        return {"tr": tr, "clave": clave, "accion": "saltar", "motivo": "ya correcto"}
    accion = "planificar" if not actual else "reasignar"
    return {"tr": tr, "clave": clave, "accion": accion, "actual": actual,
            "cuadrilla": nombre, "cedula": cedula}


def intentar_autoasignar(nuevos, con, ya_asignados, pendientes=None,
                         dry_run=False, log=print):
    """
    nuevos:     [{'numero_tramite', 'clave'}] de los trámites NUEVOS de esta corrida.
    pendientes: [{'numero_tramite', 'clave'}] que FALLARON antes y hay que reintentar.
    Devuelve (lineas, nuevos_ok, errores, pendientes_restantes).

    OPTIMIZACIÓN (2026-10-16) — por CANTIDAD de acciones a ejecutar:
      · 1 acción  → script individual (login propio; ya probado y estable)
      · ≥2 acciones → LOTE: una sola sesión de AFlow (ahorra ~22 s de setup por
        trámite adicional). El lote se limita a MAX_LOTE por corrida; lo que no
        alcance queda en `pendientes_restantes` (cola) para la corrida siguiente.
    Reintentos: un fallo deja el trámite en `pendientes` y se reintenta después.
    """
    lineas, nuevos_ok, errores = [], set(), []
    pendientes_restantes = []
    vistos = set()

    # ── 1) Analizar TODOS (pendientes + nuevos) SIN tocar AFlow ────────────────
    analisis = []
    for n in list(pendientes or []) + list(nuevos):
        tr = str(n.get("numero_tramite", "")).strip()
        clave = n.get("clave", "")
        if not tr or tr in vistos:
            continue
        vistos.add(tr)
        if tr in ya_asignados:
            continue
        a = _analizar(tr, clave, con, log, None)
        if a is None:
            continue
        if a["accion"] == "saltar":
            if a.get("motivo") in ("ya terminado", "ya correcto"):
                nuevos_ok.add(tr)      # nada que hacer: no reintentar
            continue                    # 'sin cuadrilla destino' → no reintentar
        analisis.append(a)

    # ── 2) Ejecutar: 1 → individual · ≥2 → lote (limitado por MAX_LOTE) ────────
    if analisis:
        if len(analisis) == 1:
            a = analisis[0]
            log(f"   ⚙️  {a['tr']} → {a['accion']} a {a['cuadrilla']} (modo individual)…")
            if a["accion"] == "planificar":
                ok, det = asignar_en_aflow(a["tr"], a["cedula"], dry_run=dry_run)
                verbo = "Auto-asignado"
            else:
                ok, det = reasignar_en_aflow(a["tr"], a["cedula"], dry_run=dry_run)
                verbo = "Auto-reasignado"
            if ok:
                lineas.append(f"⚙️ *{verbo}*: trámite `{a['tr']}` → *{a['cuadrilla']}*")
                nuevos_ok.add(a["tr"])
                log(f"      ✅ {det}")
            else:
                lineas.append(f"⚠️ *Falló la {verbo.lower()}*: trámite `{a['tr']}` → "
                              f"*{a['cuadrilla']}*\n   motivo: {det}\n"
                              f"   (se reintentará en la próxima corrida)")
                pendientes_restantes.append({"numero_tramite": a["tr"], "clave": a["clave"]})
                errores.append(f"{a['tr']}: {det}")
                log(f"      ❌ {det}")
        else:
            # ── LOTE ──
            lote = analisis[:MAX_LOTE]
            cola = analisis[MAX_LOTE:]
            log(f"   ⚙️  LOTE: {len(lote)} en esta corrida, {len(cola)} a la cola "
                f"(una sola sesión de AFlow)…")
            items = [{"numero_tramite": a["tr"], "cuadrilla": a["cedula"]} for a in lote]
            res = correr_lote(items, dry_run=dry_run, log=log)
            for a in lote:
                st, mot = res.get(a["tr"], ("PEND", "no reportado por el lote"))
                if st == "OK":
                    verbo = "Auto-asignado" if a["accion"] == "planificar" else "Auto-reasignado"
                    lineas.append(f"⚙️ *{verbo}*: trámite `{a['tr']}` → *{a['cuadrilla']}*")
                    nuevos_ok.add(a["tr"])
                elif st == "SKIP":
                    # ya estaba correcto / seguro: no reintentar
                    nuevos_ok.add(a["tr"])
                else:
                    ant = f" (estaba en {a['actual']})" if a.get("actual") else ""
                    lineas.append(f"⚠️ *Falló la auto-asignación (lote)*: trámite "
                                  f"`{a['tr']}`{ant} → *{a['cuadrilla']}*\n"
                                  f"   motivo: {mot}\n   (se reintentará en la próxima corrida)")
                    pendientes_restantes.append({"numero_tramite": a["tr"], "clave": a["clave"]})
                    errores.append(f"{a['tr']}: {mot}")
            # Los de la cola NO se ejecutan: pasan a pendientes para la próxima corrida.
            for a in cola:
                pendientes_restantes.append({"numero_tramite": a["tr"], "clave": a["clave"]})
            if cola:
                log(f"   ⏳ {len(cola)} trámite(s) en cola para la próxima corrida "
                    f"(límite {MAX_LOTE}/corrida)")

    return lineas, nuevos_ok, errores, pendientes_restantes


# ── Inspección / prueba standalone ────────────────────────────────────────────
def _self_test():
    import argparse, sqlite3
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tramite"); ap.add_argument("--cuadrilla")
    ap.add_argument("--reasignar", action="store_true")
    a = ap.parse_args()
    if a.tramite and a.cuadrilla:
        fn = reasignar_en_aflow if a.reasignar else asignar_en_aflow
        ok, det = fn(a.tramite, a.cuadrilla, dry_run=a.dry_run)
        print(f"{'OK' if ok else 'FALLO'}: {det}")
        return
    print(f"AUTO_ASIGNAR derivado: {len(AUTO_ASIGNAR)} ítems")
    for k, v in sorted(AUTO_ASIGNAR.items(), key=lambda x: (x[1], x[0])):
        print(f"  {k:26s} → {v} ({CUADRILLA_CEDULA[v]})")
    print(f"\ncuadrillas distintas ({len(set(AUTO_ASIGNAR.values()))}): "
          f"{sorted(set(AUTO_ASIGNAR.values()))}")


if __name__ == "__main__":
    _self_test()
