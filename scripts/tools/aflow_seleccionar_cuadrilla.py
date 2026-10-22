#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AFlow — PASO 7: llegar al planificador, seleccionar ROBERTO BRIONES y VERIFICAR
que queda marcado. NO pulsa PLANIFICAR (eso es --ejecutar).

Uso:
  aflow_seleccionar_cuadrilla.py --tramite 344710141 --cuadrilla 1203218654
  aflow_seleccionar_cuadrilla.py --tramite 344710141 --cuadrilla 1203218654 --ejecutar
"""
import os, sys, time, json, argparse
USER = os.environ.get("ACIIS_USER", "0927194159")
PASS = os.environ.get("ACIIS_PASS", "")
SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tramite", required=True)
    ap.add_argument("--cuadrilla", required=True, help="cédula, ej 1203218654")
    ap.add_argument("--ejecutar", action="store_true", help="pulsar PLANIFICAR (ESCRITURA REAL)")
    ap.add_argument("--si", dest="confirm", default="")
    a = ap.parse_args()

    if a.ejecutar and a.confirm != "CONFIRMO ASIGNAR":
        print('❌ Para --ejecutar hay que pasar --si "CONFIRMO ASIGNAR"'); sys.exit(2)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = b.new_context(locale="es-EC", viewport={"width": 1500, "height": 950})
        pg = ctx.new_page()
        pg.goto("https://amobile.altura.systems/aportal/", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(2000)
        pg.fill("#usuario", USER); pg.fill("#clave", PASS)
        pg.query_selector('button:has-text("INICIAR SESIÓN")').click(timeout=8000)
        pg.wait_for_timeout(6000)
        pg.evaluate("""()=>{document.querySelectorAll('div.grid-app').forEach(e=>{const t=e.querySelector('.title');
          if(t&&/AFlow/i.test(t.innerText)){const r=e.getBoundingClientRect();
            e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""")
        pg.wait_for_timeout(8000)
        # Esperar el iframe del portal con reintentos (NO fallar a la primera:
        # el 2026-10-16 el iframe aún no había cargado y el script murió con
        # 'NoneType' object has no attribute 'content_frame').
        fr = None
        for _intento in range(15):
            _el = pg.query_selector("iframe")
            if _el:
                fr = _el.content_frame()
                if fr:
                    break
            pg.wait_for_timeout(2000)
        if fr is None:
            print("❌ iframe del portal no apareció tras 30s"); sys.exit(5)
        fr.wait_for_timeout(1500)
        fr.evaluate("""()=>{document.querySelectorAll('span.title-nodo,div.collapsible-header,li').forEach(e=>{
          if((e.innerText||'').trim()==='Por Planificar Med'){const r=e.getBoundingClientRect();
            if(r.width>0)e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""")
        fr.wait_for_timeout(8000)
        fr.evaluate("()=>{document.querySelectorAll('.lean-overlay').forEach(o=>{if(o.getBoundingClientRect().width>0)o.remove();});}")
        print("1) Filtrar por trámite…")
        # Abrir filtro con reintentos (el handler inyecta la fila de inputs en el thead)
        abierto = False
        for intento in range(4):
            fr.evaluate("()=>{document.querySelectorAll('.lean-overlay').forEach(o=>{if(o.getBoundingClientRect().width>0)o.remove();});}")
            try:
                fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000)
            except Exception:
                try: fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000, force=True)
                except Exception: pass
            # esperar a que aparezca la fila de inputs de filtro
            try:
                fr.wait_for_selector('table.tabBandeja thead input[index-field]', timeout=6000)
                abierto = True; break
            except Exception:
                print(f"   (intento {intento+1}: aún sin fila de filtros, reintentando)")
                fr.wait_for_timeout(2000)
        if not abierto:
            print("❌ No se logró abrir la fila de filtros"); sys.exit(3)
        heads = fr.evaluate("""()=>[...document.querySelector('table.tabBandeja thead').children[0].querySelectorAll('th')].map(x=>(x.innerText||'').trim())""")
        idx = next((i for i,h in enumerate(heads) if 'TRAMI' in h.upper()), 15)
        loc = fr.locator(f'table.tabBandeja thead input[index-field="{idx}"]').first
        loc.wait_for(state="visible", timeout=8000)
        loc.click(timeout=8000); loc.fill(a.tramite); fr.wait_for_timeout(500); loc.press("Enter")
        fr.wait_for_timeout(7000)
        n = fr.evaluate("()=>document.querySelectorAll('table.tabBandeja tbody tr').length")
        print(f"   filas: {n}")

        print("2) Marcar checkbox de la fila…")
        fr.evaluate("""()=>{const cb=document.querySelector('table.tabBandeja tbody input[type=checkbox]'); if(cb&&!cb.checked)cb.click();}""")
        fr.wait_for_timeout(1200)

        print("3) Abrir ventana de planificación…")
        fr.evaluate("""()=>{const li=document.querySelector('#btnPlanificador');const r=li.getBoundingClientRect();
          (li.querySelector('a')||li).dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}""")
        fr.wait_for_timeout(7000)
        pin = [f for f in pg.frames if "planificador2" in f.url][0]; pin.wait_for_timeout(2000)

        print("4) Seleccionar 'División Absoluta'…")
        pin.evaluate("""()=>{const s=document.getElementById('cmbMetodo'); s.value='1'; s.dispatchEvent(new Event('change',{bubbles:true}));}""")
        pin.wait_for_timeout(4000)

        print(f"5) Clic en cuadrilla {a.cuadrilla}…")
        sel = pin.evaluate("""(idc)=>{
          const el=document.querySelector('#lstCuadrillas a[idc="'+idc+'"]');
          if(!el) return 'no encontrada';
          const r=el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
            clientX:r.left+r.width/2,clientY:r.top+r.height/2}));
          return el.getAttribute('c');
        }""", a.cuadrilla)
        print("   click en:", sel)
        pin.wait_for_timeout(2500)
        pg.screenshot(path=f"{SHOTS}/97_cuadrilla_sel.png")

        # VERIFICAR estado de selección
        est = pin.evaluate("""()=>{
          const out={activos:[], resumen:'', fecha:''};
          document.querySelectorAll('#lstCuadrillas a').forEach(a=>{
            if(a.classList.contains('active')||a.classList.contains('selected')||
               a.getAttribute('aria-selected')==='true'||
               (a.style.backgroundColor && a.style.backgroundColor!=='')){
              out.activos.push({c:a.getAttribute('c'), cls:a.className, style:a.getAttribute('style')||''});
            }
          });
          const f=document.getElementById('txtFechaEjecucion')||document.querySelector('input[id*=echa]');
          if(f) out.fecha=f.value;
          // resumen textual
          const t=document.body.innerText;
          const m=t.match(/\\d+\\s*[Gg]esti[oó]n/); out.resumen=m?m[0]:'';
          return out;
        }""")
        print("=== ESTADO SELECCIÓN ===")
        print(json.dumps(est, indent=1, ensure_ascii=False)[:900])

        if a.ejecutar:
            print("⚠️ PULSANDO PLANIFICAR (escritura real)…")
            r = fr.evaluate("""()=>{const el=document.getElementById('btnPlanificarAuto');
              if(!el) return 'no btn';
              const rc=el.getBoundingClientRect();
              el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
                clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));
              return 'click emitido';}""")
            print("   ", r)
            fr.wait_for_timeout(9000)
            pg.screenshot(path=f"{SHOTS}/99_post_planificar.png")
            print("   📸 post-planificar")
            print("   Texto:", fr.evaluate("()=>document.body.innerText.slice(0,500)").replace("\n"," | "))
        else:
            print("✅ PASO 7 verificado. NO se pulsó PLANIFICAR (modo seguro).")
        b.close()


if __name__ == "__main__":
    main()
