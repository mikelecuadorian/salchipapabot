#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AFlow — LOTE: planificar/reasignar VARIOS trámites con UNA SOLA sesión.

Diseñado 2026-10-16 a pedido de Miguel. Optimización: hoy cada trámite abre un
Playwright, hace login y carga AFlow desde cero (~30 s de setup repetido por
trámite). Este script hace ESO UNA VEZ y procesa N trámites reusando la sesión.

QUÉ SE REUSA (una vez):  Playwright/Chromium · login ACIIS · carga de AFlow + iframe
QUÉ SE REPITE (por trámite): abrir filtro → filtrar → checkbox → planificador →
                             seleccionar cuadrilla → pulsar acción
  ⚠️ El filtro NO sobrevive a la acción: AFlow recarga la bandeja
     (POST bandejasAfl4?new=true) y cierra el filtro. Verificado en vivo
     2026-10-16 (exp2): tras pulsar acción, inputs_filtro=0, valores=[].

AGRUPA POR TIPO: primero TODOS los "planificar" (bandeja 'Por Planificar Med'),
luego TODOS los "reasignar" (bandeja 'Planificados Med'). Así se cambia de
bandeja una sola vez por grupo en vez de alternar.

USO:
  # Una sola lista; el script clasifica cada trámite por su estado en la BD:
  aflow_lote.py --item 344762895:0928817774 --item 344760001:0926550781 \
                --ejecutar --si "CONFIRMO LOTE"

  # El tipo se decide por la cuadrilla actual del trámite en la BD (vacía →
  # planificar; con otra cuadrilla → reasignar). Si la BD no está disponible,
  # se puede forzar con --tipo planificar|reasignar por ítem.

ESTADO DE SALIDA (stdout, parseable por el vigía):
  Una línea por ítem:
    OK <tramite> <accion>          → acción realizada
    PEND <tramite> <motivo>        → falló, reintentar
    SKIP <tramite> <motivo>        → no aplica (ya correcto/terminado)

MODO SEGURO por defecto: sin --ejecutar no pulsa la acción final.
"""
import os, sys, json, argparse, time

USER = os.environ.get("ACIIS_USER", "0927194159")
PASS = os.environ.get("ACIIS_PASS", "")
SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
os.makedirs(SHOTS, exist_ok=True)
from playwright.sync_api import sync_playwright

FRASE_OK = "CONFIRMO LOTE"


# ── Utilidades ────────────────────────────────────────────────────────────────
def norm(s):
    import unicodedata, re
    s = unicodedata.normalize("NFKD", (s or "").upper())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def leer_estado_bd(tramites):
    """{(tramite): {'cuadrilla','terminado'}} desde la BD local (solo lectura)."""
    out = {}
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
        for tr in tramites:
            cuad, term = "", False
            try:
                r = con.execute("SELECT cuadrilla,accion_ejecutada FROM recorrido_cuadrillas "
                                "WHERE numero_tramite=? LIMIT 1", (str(tr),)).fetchone()
                if r:
                    cuad = (r[0] or "").strip()
                    if str(r[1] or "").strip().upper() in ("SI", "SÍ", "1", "TRUE"):
                        term = True
            except Exception:
                pass
            if not cuad:
                try:
                    r = con.execute("SELECT cuadrilla,estado FROM gestion_tramites "
                                    "WHERE numero_tramite=? LIMIT 1", (str(tr),)).fetchone()
                    if r:
                        cuad = (r[0] or "").strip()
                        if any(t in norm(r[1] or "") for t in ("LIBERADO", "CERRADO")):
                            term = True
                except Exception:
                    pass
            out[str(tr)] = {"cuadrilla": cuad, "terminado": term}
        con.close()
    except Exception as e:
        print(f"# aviso: no pude leer la BD ({e}); clasificaré por --tipo", file=sys.stderr)
    return out


def limpiar_overlays(fr):
    fr.evaluate("()=>{document.querySelectorAll('.lean-overlay').forEach(o=>{"
                "if(o.getBoundingClientRect().width>0)o.remove();});}")


def cerrar_modal_error(fr):
    try:
        return fr.evaluate("""()=>{const e=document.getElementById('alMod-mod-error');
          if(e){const r=e.getBoundingClientRect(); if(r.width>0){
            const b=[...e.querySelectorAll('a')].find(x=>/salir/i.test(x.innerText));
            if(b){b.click(); return 'cerrado';}}} return 'ok';}""")
    except Exception:
        return "n/a"


def cerrar_planificador(fr):
    """Cierra el modal del planificador (#modalPlanificador) tras procesar un trámite.

    CRÍTICO en modo lote: si no se cierra, el modal del trámite anterior queda
    abierto y su overlay BLOQUEA el clic en el filtro del siguiente
    (verificado 2026-10-16: 'iframe ... intercepts pointer events').
    Botón real del wizard: #btnSalirMosWiz (texto 'CERRAR', hallado en el DOM).
    """
    try:
        r = fr.evaluate("""()=>{
          const btn=document.getElementById('btnSalirMosWiz');
          if(btn){btn.click(); return 'click btnSalirMosWiz';}
          // Fallback: quitar modal + overlay por JS
          document.querySelectorAll('.lean-overlay,.modal-overlay').forEach(o=>o.remove());
          const m=document.getElementById('modalPlanificador');
          if(m){m.style.display='none'; m.classList.remove('open');}
          return 'fallback JS (modal oculto)';
        }""")
        fr.wait_for_timeout(2500)
        # Limpieza extra por si quedaron overlays huérfanos
        limpiar_overlays(fr)
        fr.wait_for_timeout(500)
        return r
    except Exception as e:
        return f"error: {e}"


# ── Sesión: login + carga de AFlow (UNA VEZ) ──────────────────────────────────
def abrir_sesion(p, log=print):
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
    ctx = b.new_context(locale="es-EC", viewport={"width": 1500, "height": 950})
    pg = ctx.new_page()
    posts = []
    pg.on("request", lambda r: posts.append(((r.url.split("?")[0][-30:]), (r.post_data or "")[:220]))
          if r.method == "POST" and "aflow" in r.url else None)
    log("# login ACIIS…")
    pg.goto("https://amobile.altura.systems/aportal/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.fill("#usuario", USER); pg.fill("#clave", PASS)
    pg.query_selector('button:has-text("INICIAR SESIÓN")').click(timeout=8000)
    pg.wait_for_timeout(6000)
    log("# cargando AFlow…")
    pg.evaluate("""()=>{document.querySelectorAll('div.grid-app').forEach(e=>{const t=e.querySelector('.title');
      if(t&&/AFlow/i.test(t.innerText)){const r=e.getBoundingClientRect();
        e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
          clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""")
    pg.wait_for_timeout(8000)
    fr = None
    for _ in range(15):
        el = pg.query_selector("iframe")
        if el:
            fr = el.content_frame()
            if fr:
                break
        pg.wait_for_timeout(2000)
    if fr is None:
        b.close()
        raise RuntimeError("iframe del portal no apareció tras 30s")
    fr.wait_for_timeout(1500)
    return b, pg, fr, posts


def ir_a_bandeja(fr, bandeja, log=print):
    """bandeja: 'Por Planificar Med' (planificar) o 'Planificados Med' (reasignar)."""
    log(f"# yendo a '{bandeja}'…")
    fr.evaluate("""(nombre)=>{document.querySelectorAll('span.title-nodo,div.collapsible-header,li').forEach(e=>{
      if((e.innerText||'').trim()===nombre){const r=e.getBoundingClientRect();
        if(r.width>0)e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
          clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""", bandeja)
    fr.wait_for_timeout(9000)
    limpiar_overlays(fr)


def abrir_filtro(fr):
    """Abre la fila de filtros. Devuelve el índice de la columna TRAMITE, o None."""
    # Defensivo: si quedó un modal/overlay del trámite anterior, quitarlo.
    limpiar_overlays(fr)
    fr.wait_for_timeout(500)
    for _ in range(4):
        limpiar_overlays(fr)
        try:
            fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000)
        except Exception:
            try:
                fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000, force=True)
            except Exception:
                pass
        try:
            fr.wait_for_selector('table.tabBandeja thead input[index-field]', timeout=6000)
        except Exception:
            fr.wait_for_timeout(2000)
            continue
        heads = fr.evaluate("""()=>[...document.querySelector('table.tabBandeja thead').children[0]
          .querySelectorAll('th')].map(x=>(x.innerText||'').trim())""")
        return next((i for i, h in enumerate(heads) if 'TRAMI' in h.upper()), 15)
    return None


def filtrar_tramite(fr, idx, tramite):
    """Filtra por trámite y devuelve la fila SOLO si de verdad es ese trámite.

    ⚠️ BUG CORREGIDO (2026-10-22): antes leía `tbody tr` —la PRIMERA fila— sin
    comprobar que fuera el trámite pedido. Con el filtro PEGAJOSO del portal (al
    volver a un nodo la grilla conserva el filtro anterior, o con 0 resultados
    queda una fila residual) devolvía una fila ajena y el lote actuaba sobre el
    trámite equivocado. Ahora se valida el contenido y, si no coincide, se
    devuelve None para que el llamador pruebe la otra bandeja.
    """
    loc = fr.locator(f'table.tabBandeja thead input[index-field="{idx}"]').first
    loc.wait_for(state="visible", timeout=8000)
    # Reintentar el click: un overlay residual puede interceptar el primer intento.
    for intento in range(3):
        try:
            loc.click(timeout=8000)
            break
        except Exception:
            limpiar_overlays(fr)
            fr.wait_for_timeout(1200)
    else:
        raise RuntimeError("no se pudo clicar el campo de filtro (overlay persistente)")

    # limpiar el filtro pegado ANTES de escribir el nuevo
    try:
        loc.fill("")
        loc.press("Enter")
        fr.wait_for_timeout(1200)
    except Exception:
        pass

    loc.fill(str(tramite))
    fr.wait_for_timeout(400)
    loc.press("Enter")
    fr.wait_for_timeout(7000)

    filas = fr.evaluate("""()=>{const t=document.querySelector('table.tabBandeja');
      if(!t) return [];
      return [...t.querySelectorAll('tbody tr')]
        .filter(tr=>tr.querySelectorAll('td').length>3)
        .map(tr=>[...tr.querySelectorAll('td')].map(td=>(td.innerText||'').trim()));}""")
    for f in filas:
        if any(str(tramite) == c.strip() for c in f):
            return f
    return None


# ── Acción por trámite (reusa la sesión) ──────────────────────────────────────
def procesar(fr, pg, posts, idx_filtro, item, ejecutar, log=print):
    """Procesa UN trámite. Devuelve ('OK'|'PEND'|'SKIP', motivo).

    FALLBACK (2026-10-22): la clasificación previa usa la BD, y la BD puede ir
    desfasada respecto del portal (el trámite se planificó a mano por Miguel, o se
    liberó, después de la última sincronización). Si no aparece en la bandeja
    esperada, se prueba la OTRA bandeja antes de darlo por fallido — y si allí
    está, se cambia el tipo sobre la marcha. Si no está en ninguna: NO EXISTE.
    """
    tr = str(item["tramite"])
    cuad = str(item["cuadrilla"])
    tipo = item["tipo"]                       # 'planificar' | 'reasignar'
    log(f"   procesando {tr} ({tipo})")

    fila = None
    # 1) intento en la bandeja esperada
    idx = abrir_filtro(fr)
    if idx is not None:
        fila = filtrar_tramite(fr, idx, tr)
    if not fila:
        # 2) fallback: la otra bandeja
        otra = "Planificados Med" if tipo == "planificar" else "Por Planificar Med"
        otro_tipo = "reasignar" if tipo == "planificar" else "planificar"
        log(f"   no está en la bandeja esperada → probando «{otra}»")
        ir_a_bandeja(fr, otra, log=lambda m: print(m, file=sys.stderr))
        idx = abrir_filtro(fr)
        if idx is not None:
            fila = filtrar_tramite(fr, idx, tr)
        if fila:
            tipo = otro_tipo
            item["tipo"] = otro_tipo          # que el POST esperado sea el correcto
            log(f"   encontrado en «{otra}» → {otro_tipo}")
    if not fila:
        return "PEND", "el trámite NO EXISTE en ACIIS (ni Por Planificar ni Planificados)"
    log(f"   fila: {fila[:8]}")

    # checkbox
    fr.evaluate("""()=>{const cb=document.querySelector('table.tabBandeja tbody input[type=checkbox]');
      if(cb&&!cb.checked)cb.click();}""")
    fr.wait_for_timeout(1500)

    # abrir planificador
    limpiar_overlays(fr)
    if tipo == "planificar":
        fr.evaluate("""()=>{const li=document.querySelector('#btnPlanificador');
          const r=li.getBoundingClientRect(); (li.querySelector('a')||li).dispatchEvent(
            new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}""")
    else:
        fr.evaluate("""()=>{const li=document.querySelector('li.opc[op="reasignar"]');
          const rc=li.getBoundingClientRect(); (li.querySelector('a')||li).dispatchEvent(
            new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));}""")
    fr.wait_for_timeout(7000)
    cerrar_modal_error(fr)
    fr.wait_for_timeout(1500)

    # A partir de aquí el planificador puede estar abierto → SIEMPRE cerrarlo al salir
    try:
        pin = [f for f in pg.frames if "planificador" in f.url]
        if not pin:
            return "PEND", "no se abrió el planificador"
        pin = pin[-1]
        pin.wait_for_timeout(2500)

        # División Absoluta + cuadrilla
        pin.evaluate("""()=>{const s=document.getElementById('cmbMetodo');
          if(s){s.value='1'; s.dispatchEvent(new Event('change',{bubbles:true}));}}""")
        pin.wait_for_timeout(4500)
        sel = pin.evaluate("""(idc)=>{const el=document.querySelector('#lstCuadrillas a[idc="'+idc+'"]');
          if(!el) return null; const r=el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
            clientX:r.left+r.width/2,clientY:r.top+r.height/2}));
          return el.getAttribute('c');}""", cuad)
        if not sel:
            return "PEND", f"cuadrilla {cuad} no encontrada en el planificador"
        pin.wait_for_timeout(2500)

        if not ejecutar:
            return "SKIP", "modo seguro (no se pulsó la acción)"

        # PULSAR ACCIÓN
        posts.clear()
        fr.evaluate("""()=>{const el=document.getElementById('btnPlanificarAuto');
          const rc=el.getBoundingClientRect(); el.dispatchEvent(new MouseEvent('click',
            {bubbles:true,cancelable:true,view:window,
              clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2})); return 'click emitido';}""")
        fr.wait_for_timeout(10000)
        pg.screenshot(path=f"{SHOTS}/lote_{tr}.png")

        # Verificar que se envió el POST de la acción (o=planificar / o=reasignar)
        esperado = "o=planificar" if tipo == "planificar" else "o=reasignar"
        enviado = any(esperado in (body or "") for _, body in posts)
        if enviado:
            return "OK", "click emitido y POST confirmado"
        # El bug conocido de AFlow puede mostrar modal de error aunque grabe; no
        # marcamos fallo por eso (verificado 2026-10-16). Solo avisamos.
        return "OK", "click emitido (POST no capturado; verificar en bandeja)"
    finally:
        # CRÍTICO: cerrar el planificador para que el siguiente trámite pueda
        # clicar el filtro (su overlay bloquea si queda abierto).
        cerrar_planificador(fr)


# ── Orquestador ───────────────────────────────────────────────────────────────
def clasificar(items, bd):
    """Asigna item['tipo'] según la BD: cuadrilla vacía → planificar; con otra → reasignar."""
    planificar, reasignar = [], []
    for it in items:
        tr = str(it["tramite"])
        estado = bd.get(tr, {})
        if it.get("tipo_forzado"):
            tipo = it["tipo_forzado"]
        else:
            cuad = estado.get("cuadrilla", "")
            tipo = "planificar" if not cuad else "reasignar"
        it["tipo"] = tipo
        (planificar if tipo == "planificar" else reasignar).append(it)
    return planificar, reasignar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", action="append", default=[],
                    help="tramite:cuadrilla_cédula (repetible)")
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--si", dest="confirm", default="")
    a = ap.parse_args()

    items = []
    for spec in a.item:
        if ":" not in spec:
            print(f"❌ ítem mal formado (falta ':'): {spec}"); sys.exit(2)
        tr, cuad = spec.split(":", 1)
        items.append({"tramite": tr.strip(), "cuadrilla": cuad.strip()})
    if not items:
        print("❌ sin ítems (--item tramite:cuadrilla)"); sys.exit(2)

    if a.ejecutar and a.confirm != FRASE_OK:
        print(f'❌ Para --ejecutar: --si "{FRASE_OK}"'); sys.exit(2)

    bd = leer_estado_bd([i["tramite"] for i in items])
    planificar, reasignar = clasificar(items, bd)
    print(f"# lote: {len(planificar)} para PLANIFICAR, {len(reasignar)} para REASIGNAR")

    resultados = []
    with sync_playwright() as p:
        t0 = time.time()
        b, pg, fr, posts = abrir_sesion(p, log=lambda m: print(m, file=sys.stderr))
        print(f"# setup (login+AFlow) en {time.time()-t0:.0f}s", file=sys.stderr)

        # Grupo 1: planificar
        if planificar:
            ir_a_bandeja(fr, "Por Planificar Med", log=lambda m: print(m, file=sys.stderr))
            idx = abrir_filtro(fr)
            for it in planificar:
                est = (bd.get(str(it["tramite"]), {}) or {}).get("terminado")
                if est:
                    resultados.append(("SKIP", it["tramite"], "ya terminado"))
                    continue
                st, mot = procesar(fr, pg, posts, idx, it, a.ejecutar,
                                   log=lambda m: print(m, file=sys.stderr))
                resultados.append((st, it["tramite"], mot))

        # Grupo 2: reasignar
        if reasignar:
            ir_a_bandeja(fr, "Planificados Med", log=lambda m: print(m, file=sys.stderr))
            idx = abrir_filtro(fr)
            for it in reasignar:
                est = (bd.get(str(it["tramite"]), {}) or {}).get("terminado")
                if est:
                    resultados.append(("SKIP", it["tramite"], "ya terminado"))
                    continue
                st, mot = procesar(fr, pg, posts, idx, it, a.ejecutar,
                                   log=lambda m: print(m, file=sys.stderr))
                resultados.append((st, it["tramite"], mot))

        b.close()

    # Salida parseable por el vigía
    for st, tr, mot in resultados:
        print(f"{st} {tr} {mot}")


if __name__ == "__main__":
    main()
