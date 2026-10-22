#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aflow_asignar_auto.py — Asignar/reasignar un trámite en AFlow sin intervención.

FLUJO (decide solo, sin modo seguro):
  1. Login (con reintentos largos).
  2. Busca el trámite en "Por Planificar Med" con el filtro del portal.
     - Si está  → PLANIFICA a la cuadrilla destino.
  3. Si no está, busca en "Planificados Med".
     - Si está  → REASIGNA a la cuadrilla destino.
  4. Si no está en ninguno → informa que el trámite NO EXISTE en ACIIS.
        exit 4

DIFERENCIA CON LOS SCRIPTS VIEJOS
  `aflow_seleccionar_cuadrilla.py` (planificar) y `aflow_reasignar.py` (reasignar)
  se quedan como están: sirven para depurar un paso concreto. Este los unifica y
  corre el camino completo de una vez. No pide confirmación.

USO
  python3 tools/aflow_asignar_auto.py --tramite 345139370 --cuadrilla 0926550781
  python3 tools/aflow_asignar_auto.py --tramite 345139370 --nombre "CARLOS PEREZ"
  python3 tools/aflow_asignar_auto.py --tramite X --cuadrilla Y --verbose

SALIDAS
  0 = asignado/reasignado y verificado
  2 = argumentos inválidos
  3 = no se pudo abrir el filtro (portal raro)
  4 = el trámite NO EXISTE en ACIIS (en ninguno de los dos nodos)
  5 = login / iframe no apareció
  6 = la cuadrilla no existe en el desplegable
  7 = se pulsó pero la verificación posterior falló
"""

import argparse
import os
import re
import sys
import time

SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
os.makedirs(SHOTS, exist_ok=True)

PORTAL = "https://amobile.altura.systems/aportal/"

# Cuadrillas conocidas (cédula → nombre). Se puede sobrescribir con --nombre.
CUADRILLAS = {
    "0928817774": "CARLOS MIRANDA MOLESTINA",
    "0926550781": "CARLOS PEREZ",
    "0923112445": "JIMMY VELIZ",
    "1203218654": "ROBERTO BRIONES",
    "0923465934": "BRYAN ACOSTA",
    "0919327783": "JULIO CUZCO",
    "0921533568": "CHRISTOPHER ORTIZ",
    "1207067438": "ORLYN CABRERA",
    "0956072698": "TOMMY SAMANIEGO",
    "0958556813": "JOSE CARRION",
    "0924125057": "HAROLD VELOZ",
    "0920197241": "CARLOS GUARACA",
    "0923275630": "DIEGO LUNA",
    "1206588558": "EDINSON QUIÑONEZ",
    "0705129153": "SERGIO CASTRO",
    "0942935149": "ACCEL CEVALLOS",
    "0916272438": "CARLOS SAMUEL GUERRERO LAMOTA",
    "0924379910": "JORGE LOPEZ",
    "0924954563": "JOHNNY GARCIA",
    "0941296550": "JOEL ESPINAR",
    "0926265059": "CORREA ORTEGA ANA BELEN",
    "0916893340": "DARWIN INDACOCHEA",
    "0954123535": "VICENTE GONZALEZ",
}

# Tiempos. El portal es lento e irregular: mejor esperar MÁS en el login y
# menos en cada paso interno (los pasos internos responden rápido cuando ya
# está cargado). Ajustables por entorno.
T = {
    "login_load": 2500,       # tras goto
    "login_post": 7000,       # tras click INICIAR SESIÓN
    "tile_post": 8000,        # tras click tile AFlow
    "iframe_reintentos": 30,  # × 1500 ms = 45 s máximo
    "iframe_espera": 1500,
    "nodo_post": 2000,        # tras click en el nodo
    "grilla_reintentos": 25,  # × 1000 ms = 25 s máximo
    "grilla_espera": 1000,
    "filtro_post": 2000,
    "filtro_filas": 5000,
    "plan_ventana": 5000,
    "plan_metodo": 3000,
    "verif_reintentos": 10,
    "verif_espera": 1200,
}


def log(msg):
    print(msg, flush=True)


# ─────────────────────────────────────────────────────────────────────────
# Helpers de DOM
# ─────────────────────────────────────────────────────────────────────────
JS_CLICK_NODO = """
(nombre)=>{let tg=null;
  document.querySelectorAll('span.title-nodo').forEach(e=>{
    if((e.innerText||'').trim()===nombre && !tg) tg=e;});
  if(!tg) return false;
  const r=tg.getBoundingClientRect();
  if(r.width<=0) return false;
  const el=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2) || tg;
  ['mousedown','mouseup','click'].forEach(t=>el.dispatchEvent(new MouseEvent(t,
    {bubbles:true,cancelable:true,view:window,
     clientX:r.left+r.width/2, clientY:r.top+r.height/2})));
  return true;}
"""

JS_LIMPIAR_OVERLAYS = """
()=>{document.querySelectorAll('.lean-overlay,.modal-overlay').forEach(o=>{
  if(o.getBoundingClientRect().width>0) o.remove();});}
"""

JS_LEER_GRILLA = """
()=>{
  const t=document.querySelector('table.tabBandeja');
  if(!t) return {ok:false, n:0, msg:'sin tabla', filas:[]};
  const rows=[...t.querySelectorAll('tbody tr')].filter(tr=>tr.querySelectorAll('td').length>3);
  const m=document.body.innerText.match(/Mostrando de .{0,40}Registros/);
  return {ok:true, n:rows.length, msg:m?m[0]:'?',
    filas:rows.map(tr=>[...tr.querySelectorAll('td')].map(td=>(td.innerText||'').trim()))};
}
"""


def esperar_grilla(fr, intentos, espera):
    """Espera a que la grilla tenga filas reales. Devuelve el dict de JS_LEER_GRILLA."""
    r = {"ok": False, "n": 0, "msg": "no intentado", "filas": []}
    for _ in range(intentos):
        fr.wait_for_timeout(espera)
        try:
            r = fr.evaluate(JS_LEER_GRILLA)
        except Exception:
            continue
        if r.get("n", 0) > 0:
            return r
    return r


def abrir_nodo(fr, nombre, espera):
    ok = fr.evaluate(JS_CLICK_NODO, nombre)
    if not ok:
        return False
    fr.wait_for_timeout(espera)
    return True


def filtrar_por_tramite(fr, tramite):
    """Abre el filtro del portal y escribe el número de trámite.

    ⚠️ BUG CORREGIDO (2026-10-22): el filtro del portal es PEGAJOSO. Al volver a
    un nodo, la grilla conserva el filtro de la búsqueda anterior, así que ya
    viene con 1 fila y `esperar_grilla` devolvía esa fila como si fuera el
    resultado de un filtro nuevo. La verificación final leía el estado VIEJO y
    fallaba aunque la reasignación hubiera funcionado.
    Solución: limpiar el input ANTES de leer nada, y confirmar que el valor
    escrito es el que pedimos.

    Devuelve (abierto: bool, indice: int|None)
    """
    fr.evaluate(JS_LIMPIAR_OVERLAYS)
    abierto = False
    for _ in range(5):
        try:
            fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=7000)
        except Exception:
            try:
                fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=7000, force=True)
            except Exception:
                pass
        try:
            fr.wait_for_selector('table.tabBandeja thead input[index-field]', timeout=5000)
            abierto = True
            break
        except Exception:
            fr.wait_for_timeout(1500)
    if not abierto:
        return (False, None)

    heads = fr.evaluate(
        """()=>{const th=document.querySelector('table.tabBandeja').querySelector('thead');
           return [...th.children[0].querySelectorAll('th')].map(t=>(t.innerText||'').trim());}"""
    )
    idx = next((i for i, h in enumerate(heads) if "TRAMI" in h.upper()), 15)
    sel = f'table.tabBandeja thead input[index-field="{idx}"]'
    loc = fr.locator(sel).first

    # 1) LIMPIAR el filtro pegado de la búsqueda anterior
    try:
        loc.fill("", timeout=4000)
        loc.press("Enter", timeout=3000)
        fr.wait_for_timeout(1200)
    except Exception:
        pass

    # 2) escribir el trámite y confirmar que quedó escrito
    escrito = False
    try:
        loc.wait_for(state="visible", timeout=5000)
        loc.fill(tramite, timeout=5000)
        escrito = True
    except Exception:
        pass
    if not escrito:
        # Plan B por JS: el thead puede estar cubierto por un overlay
        fr.evaluate(
            """(args)=>{const [idx,val]=args;
               const i=document.querySelector(
                 'table.tabBandeja thead input[index-field="'+idx+'"]');
               if(i){i.value=val;i.dispatchEvent(new Event('input',{bubbles:true}));
                     i.dispatchEvent(new Event('change',{bubbles:true}));}}""",
            [idx, tramite],
        )
    try:
        loc.press("Enter", timeout=4000)
    except Exception:
        fr.evaluate(
            """([idx])=>{const i=document.querySelector(
                 'table.tabBandeja thead input[index-field="'+idx+'"]');
               if(i){i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true}));}}""",
            [idx],
        )
    return (True, idx)


def buscar_en_nodo(fr, nodo, tramite, verbose=False):
    """Abre un nodo, filtra por trámite y devuelve la fila (o None)."""
    if verbose:
        log(f"   → abriendo «{nodo}»…")
    if not abrir_nodo(fr, nodo, T["nodo_post"]):
        if verbose:
            log(f"   ⚠️ nodo «{nodo}» no encontrado")
        return None

    # esperar la grilla del nodo
    r = esperar_grilla(fr, T["grilla_reintentos"], T["grilla_espera"])
    if verbose:
        log(f"     grilla: {r.get('msg')} ({r.get('n')} filas)")

    ok, _ = filtrar_por_tramite(fr, tramite)
    if not ok:
        if verbose:
            log("     ⚠️ no se pudo abrir el filtro")
        return None

    r = esperar_grilla(fr, 8, T["grilla_espera"])
    if verbose:
        log(f"     tras filtro: {r.get('msg')} ({r.get('n')} filas)")

    filas = r.get("filas") or []
    # La grilla trae el trámite en una de las columnas; comparar por contenido
    for f in filas:
        if any(tramite == c.strip() for c in f):
            return f
    # Algunas veces el filtro deja 1 fila que no coincide exactamente (formato)
    if len(filas) == 1 and any(tramite in c for c in filas[0]):
        return filas[0]
    return None


def ejecutar_en_planificador(pg, fr, cuadrilla_idc, accion, verbose=False):
    """Selecciona División Absoluta + cuadrilla y pulsa el botón (planificar o reasignar).

    accion: 'planificar' (abre ventana con btnPlanificador) | 'reasignar' (ya abierta)
    """
    if accion == "planificar":
        if verbose:
            log("   → abriendo ventana de planificación…")
        fr.evaluate(
            """()=>{const li=document.getElementById('btnPlanificador');
               if(!li) return; const r=li.getBoundingClientRect();
               (li.querySelector('a')||li).dispatchEvent(new MouseEvent('click',
                 {bubbles:true,cancelable:true,view:window,
                  clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}"""
        )
        fr.wait_for_timeout(T["plan_ventana"])

    # esperar el iframe del planificador
    pin = None
    for _ in range(20):
        cands = [f for f in pg.frames if "planificador" in f.url]
        if cands:
            pin = cands[-1]
            try:
                if pin.evaluate("()=>!!document.getElementById('cmbMetodo')"):
                    break
            except Exception:
                pass
        pg.wait_for_timeout(1000)
    if pin is None:
        return (False, "no apareció el iframe del planificador")

    pin.wait_for_timeout(1500)

    # División Absoluta (método 1) = asignación directa, sin reparto automático
    pin.evaluate(
        """()=>{const s=document.getElementById('cmbMetodo');
           if(s){s.value='1'; s.dispatchEvent(new Event('change',{bubbles:true}));}}"""
    )
    pin.wait_for_timeout(T["plan_metodo"])

    n_cuads = pin.evaluate(
        """()=>{const l=document.getElementById('lstCuadrillas');
           return l?l.querySelectorAll('a').length:0;}"""
    )
    if verbose:
        log(f"   cuadrillas en el desplegable: {n_cuads}")

    sel = pin.evaluate(
        """(idc)=>{const el=document.querySelector('#lstCuadrillas a[idc="'+idc+'"]');
           if(!el) return null;
           const r=el.getBoundingClientRect();
           el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
             clientX:r.left+r.width/2, clientY:r.top+r.height/2}));
           return el.getAttribute('c');}""",
        cuadrilla_idc,
    )
    if not sel:
        return (False, f"cuadrilla {cuadrilla_idc} no está en el desplegable")

    pin.wait_for_timeout(2000)
    activos = pin.evaluate(
        """()=>{const out=[];document.querySelectorAll('#lstCuadrillas a').forEach(a=>{
           if(a.classList.contains('active')) out.push(a.getAttribute('c'));});return out;}"""
    )
    if verbose:
        log(f"   activos: {activos}")

    # pulsar
    r = fr.evaluate(
        """()=>{const el=document.getElementById('btnPlanificarAuto');
           if(!el) return 'sin botón';
           const rc=el.getBoundingClientRect();
           el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
             clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));
           return 'ok';}"""
    )
    if r != "ok":
        return (False, f"botón de confirmación no disponible ({r})")
    pg.wait_for_timeout(8000)
    return (True, sel)


def _frame_aflow(pg, espera=8000):
    """Devuelve el frame principal de AFlow (main.jsp), esperando si hace falta.

    Tras reasignar, el iframe del planificador (planificador2.jsp) queda abierto y
    `pg.frames` cambia. Buscar por "main.jsp" evita quedarse con el planificador, que
    no tiene la bandeja y hacía fallar la verificación.
    """
    fin = time.time() + espera / 1000.0
    while True:
        for f in pg.frames:
            if "aflow" in f.url and "main.jsp" in f.url:
                return f
        if time.time() > fin:
            cands = [f for f in pg.frames if "aflow" in f.url and "planificador" not in f.url]
            return cands[-1] if cands else None
        pg.wait_for_timeout(500)


def _cerrar_planificador(pg, verbose=False):
    """Cierra el iframe del planificador que queda abierto tras asignar.

    Sin esto el overlay del planificador tapa el menú 'Filtrar' y la verificación
    falla con "no se pudo reabrir el filtro" aunque la asignación haya funcionado.
    Se intenta: click en 'done'/'Salir' del panel, Escape, y por último quitar el
    contenedor del modal del DOM.
    """
    pins = [f for f in pg.frames if "planificador" in f.url]
    if not pins:
        return True
    for pin in pins:
        for intento in (
            """()=>{const b=[...document.querySelectorAll('a,button,i,span')]
                 .find(x=>/^(done|Salir|SALIR|Cerrar)$/i.test((x.innerText||'').trim()));
               if(b){b.click(); return 'click';} return null;}""",
        ):
            try:
                if pin.evaluate(intento):
                    pg.wait_for_timeout(1500)
                    break
            except Exception:
                pass
    # si sigue vivo, cerrar desde el frame principal quitando el modal
    if [f for f in pg.frames if "planificador" in f.url]:
        fr = _frame_aflow(pg, espera=1500)
        if fr:
            try:
                fr.evaluate(
                    """()=>{
                       document.querySelectorAll('iframe').forEach(f=>{
                         if(/planificador/.test(f.src||'')) {
                           const box=f.closest('.modal,.lean-overlay,div[class*=modal]')||f;
                           box.style.display='none'; box.remove(); }});
                       document.querySelectorAll('.lean-overlay,.modal-overlay')
                         .forEach(o=>o.remove());}"""
                )
                pg.wait_for_timeout(1200)
            except Exception:
                pass
    try:
        pg.keyboard.press("Escape")
    except Exception:
        pass
    pg.wait_for_timeout(800)
    if verbose:
        vivo = [f.url.split("/")[-1][:30] for f in pg.frames if "planificador" in f.url]
        log(f"   planificador tras cerrar: {vivo or 'cerrado'}")
    return not [f for f in pg.frames if "planificador" in f.url]


def verificar(pg, tramite, cuadrilla_idc, verbose=False):
    """Relee Planificados Med y comprueba que el trámite quedó con la cuadrilla."""
    _cerrar_planificador(pg, verbose)
    fr = _frame_aflow(pg)
    if fr is None:
        return (None, "no se encontró el frame de AFlow")

    # volver a BANDEJA desde el nodo raíz por si quedó en otra vista
    try:
        fr.evaluate(JS_LIMPIAR_OVERLAYS)
    except Exception:
        pass

    # recargar el nodo desde cero para leer estado fresco
    abierto = abrir_nodo(fr, "Planificados Med", T["nodo_post"])
    if not abierto:
        # el árbol puede estar colapsado: reintentar una vez con más espera
        fr.wait_for_timeout(3000)
        abierto = abrir_nodo(fr, "Planificados Med", T["nodo_post"])
    if not abierto:
        return (None, "no se pudo reabrir Planificados Med")

    r = esperar_grilla(fr, 20, 1200)
    if verbose:
        log(f"   verificación grilla: {r.get('msg')}")

    ok, _ = filtrar_por_tramite(fr, tramite)
    if not ok:
        # un segundo intento: a veces el overlay del planificador tapa el menú
        fr.wait_for_timeout(2500)
        ok, _ = filtrar_por_tramite(fr, tramite)
    if not ok:
        return (None, "no se pudo reabrir el filtro para verificar")

    for _ in range(T["verif_reintentos"]):
        r = esperar_grilla(fr, 3, T["verif_espera"])
        # comprobar que el filtro realmente tiene nuestro trámite (no uno pegado)
        val = fr.evaluate(
            """(idx)=>{const i=document.querySelector(
                 'table.tabBandeja thead input[index-field="'+idx+'"]');
               return i? i.value : null;}""",
            [15],
        )
        if val is not None and val.strip() not in ("", tramite):
            # el filtro no quedó con nuestro número: reescribir
            filtrar_por_tramite(fr, tramite)
            fr.wait_for_timeout(1500)
            continue

        filas = [f for f in (r.get("filas") or []) if any(tramite == c.strip() for c in f)]
        if filas:
            fila = filas[0]
            # la columna "Cuadrilla" va justo antes de TRAMITE
            idx_tr = next((i for i, c in enumerate(fila) if c.strip() == tramite), None)
            idx_cu = idx_tr - 1 if idx_tr and idx_tr > 0 else 14
            nombre_cu = fila[idx_cu] if idx_cu < len(fila) else ""
            esperado = CUADRILLAS.get(cuadrilla_idc, "")
            if verbose:
                log(f"   verificación: cuadrilla en portal = «{nombre_cu}»")
            if esperado and esperado.upper() in nombre_cu.upper():
                return (True, nombre_cu)
            if not esperado and nombre_cu:
                return (True, nombre_cu)
        fr.wait_for_timeout(1000)
    return (False, "el trámite no aparece con la cuadrilla esperada")


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tramite", required=True)
    ap.add_argument("--cuadrilla", help="cédula de la cuadrilla destino")
    ap.add_argument("--nombre", help="nombre de la cuadrilla (alternativa a --cuadrilla)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--sin-verificar", action="store_true",
                    help="no releer la bandeja al final (más rápido, menos seguro)")
    ap.add_argument("--headful", action="store_true", help="navegador visible (debug)")
    a = ap.parse_args()

    # resolver cuadrilla
    idc = a.cuadrilla
    if not idc and a.nombre:
        buscado = a.nombre.upper().strip()
        for k, v in CUADRILLAS.items():
            if v.upper() == buscado:
                idc = k
                break
        if not idc:
            log(f"❌ Cuadrilla «{a.nombre}» no está en el catálogo")
            sys.exit(2)
    if not idc:
        log("❌ Falta --cuadrilla o --nombre")
        sys.exit(2)

    nombre_cu = CUADRILLAS.get(idc, idc)
    log(f"🎯 Trámite {a.tramite} → {nombre_cu} ({idc})")

    from playwright.sync_api import sync_playwright

    USER = os.environ.get("ACIIS_USER", "")
    PASS = os.environ.get("ACIIS_PASS", "")
    if not PASS:
        log("❌ ACIIS_PASS vacía (ver ~/.hermes/.env)")
        sys.exit(5)

    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=not a.headful, args=["--no-sandbox", "--disable-gpu"]
        )
        pg = b.new_context(
            locale="es-EC", viewport={"width": 1600, "height": 1000}
        ).new_page()
        pg.set_default_timeout(15000)

        # ── 1. LOGIN con reintentos largos ──────────────────────────────
        fr = None
        for intento in range(3):
            try:
                pg.goto(PORTAL, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(T["login_load"])
                pg.fill("#usuario", USER, timeout=20000)
                pg.fill("#clave", PASS, timeout=20000)
                btn = pg.query_selector('button:has-text("INICIAR SESIÓN")')
                if not btn:
                    pg.wait_for_timeout(4000)
                    btn = pg.query_selector('button:has-text("INICIAR SESIÓN")')
                btn.click(timeout=15000)
                pg.wait_for_timeout(T["login_post"])

                pg.evaluate(
                    """()=>{document.querySelectorAll('div.grid-app').forEach(e=>{
                       const t=e.querySelector('.title');
                       if(t&&/AFlow/i.test(t.innerText)){const r=e.getBoundingClientRect();
                         e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
                           clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}"""
                )
                pg.wait_for_timeout(T["tile_post"])

                for _ in range(T["iframe_reintentos"]):
                    el = pg.query_selector("iframe")
                    if el:
                        f = el.content_frame()
                        if f:
                            fr = f
                            break
                    pg.wait_for_timeout(T["iframe_espera"])
                if fr:
                    break
                if a.verbose:
                    log(f"   ⚠️ iframe no apareció (intento {intento+1}/3), reintentando login…")
            except Exception as e:
                if a.verbose:
                    log(f"   ⚠️ login intento {intento+1} falló: {str(e)[:120]}")

        if fr is None:
            log("❌ No se pudo entrar a AFlow tras 3 intentos (login/iframe)")
            pg.screenshot(path=f"{SHOTS}/auto_login_fail.png")
            b.close()
            sys.exit(5)
        log(f"✅ Login OK ({time.time()-t0:.0f}s)")
        fr.wait_for_timeout(1500)

        # ── 2. Buscar en Por Planificar Med ─────────────────────────────
        log("🔍 Buscando en «Por Planificar Med»…")
        fila = buscar_en_nodo(fr, "Por Planificar Med", a.tramite, a.verbose)

        if fila:
            log("✅ Encontrado en «Por Planificar Med» → planificando")
            fr.evaluate(JS_LIMPIAR_OVERLAYS)
            fr.evaluate(
                """()=>{const cb=document.querySelector('table.tabBandeja tbody input[type=checkbox]');
                   if(cb&&!cb.checked) cb.click();}"""
            )
            fr.wait_for_timeout(800)
            ok, det = ejecutar_en_planificador(pg, fr, idc, "planificar", a.verbose)
            accion = "PLANIFICADO"
        else:
            # ── 3. Buscar en Planificados Med ───────────────────────────
            log("🔍 No está en «Por Planificar Med». Buscando en «Planificados Med»…")
            fila = buscar_en_nodo(fr, "Planificados Med", a.tramite, a.verbose)
            if not fila:
                log(f"❌ Trámite {a.tramite} NO EXISTE en ACIIS "
                    f"(no está en «Por Planificar Med» ni en «Planificados Med»)")
                b.close()
                sys.exit(4)

            actual = fila[14] if len(fila) > 14 else "?"
            log(f"✅ Encontrado en «Planificados Med» (cuadrilla actual: {actual}) → reasignando")
            fr.evaluate(JS_LIMPIAR_OVERLAYS)
            fr.evaluate(
                """()=>{const cb=document.querySelector('table.tabBandeja tbody input[type=checkbox]');
                   if(cb&&!cb.checked) cb.click();}"""
            )
            fr.wait_for_timeout(800)
            fr.evaluate(
                """()=>{const li=document.querySelector('li.opc[op="reasignar"]');
                   if(!li) return; const rc=li.getBoundingClientRect();
                   (li.querySelector('a')||li).dispatchEvent(new MouseEvent('click',
                     {bubbles:true,cancelable:true,view:window,
                      clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));}"""
            )
            fr.wait_for_timeout(5000)
            ok, det = ejecutar_en_planificador(pg, fr, idc, "reasignar", a.verbose)
            accion = "REASIGNADO"

        if not ok:
            log(f"❌ No se pudo {accion.lower()}: {det}")
            pg.screenshot(path=f"{SHOTS}/auto_fail.png")
            b.close()
            sys.exit(6 if "desplegable" in str(det) else 7)

        log(f"💾 {accion} enviado ({det}). Verificando…")

        # ── 4. Verificar ────────────────────────────────────────────────
        if a.sin_verificar:
            log(f"✅ {accion}: trámite {a.tramite} → {nombre_cu} (sin verificar)")
            log(f"⏱️  Total: {time.time()-t0:.0f}s")
            b.close()
            sys.exit(0)

        v_ok, v_det = verificar(pg, a.tramite, idc, a.verbose)
        if v_ok:
            log(f"✅ VERIFICADO: trámite {a.tramite} → {v_det}")
            log(f"⏱️  Total: {time.time()-t0:.0f}s")
            b.close()
            sys.exit(0)
        else:
            log(f"⚠️ Enviado pero la verificación no lo confirma: {v_det}")
            pg.screenshot(path=f"{SHOTS}/auto_verify_fail.png")
            b.close()
            sys.exit(7)


if __name__ == "__main__":
    main()
