#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AFlow — REASIGNAR un trámite a otra cuadrilla (bandeja Planificados Med).

MODO SEGURO por defecto: llega hasta seleccionar la cuadrilla, NO pulsa REASIGNAR.

Uso:
  aflow_reasignar.py --tramite 344710141 --cuadrilla 0928817774
  aflow_reasignar.py --tramite 344710141 --cuadrilla 0928817774 --ejecutar --si "CONFIRMO REASIGNAR"
"""
import os, sys, time, json, argparse
USER = os.environ.get("ACIIS_USER", "0927194159")
PASS = os.environ.get("ACIIS_PASS", "")
SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
os.makedirs(SHOTS, exist_ok=True)
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tramite", required=True)
    ap.add_argument("--cuadrilla", required=True, help="cédula destino, ej 0928817774")
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--si", dest="confirm", default="")
    a = ap.parse_args()
    if a.ejecutar and a.confirm != "CONFIRMO REASIGNAR":
        print('❌ Para --ejecutar: --si "CONFIRMO REASIGNAR"'); sys.exit(2)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = b.new_context(locale="es-EC", viewport={"width": 1500, "height": 950})
        pg = ctx.new_page()
        post = []
        pg.on("request", lambda r: post.append(((r.url.split("?")[0][-30:]), (r.post_data or "")[:250]))
              if r.method == "POST" and "aflow" in r.url else None)

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

        print("1) Ir a 'Planificados Med'…")
        fr.evaluate("""()=>{document.querySelectorAll('span.title-nodo,div.collapsible-header,li').forEach(e=>{
          if((e.innerText||'').trim()==='Planificados Med'){const r=e.getBoundingClientRect();
            if(r.width>0)e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""")
        fr.wait_for_timeout(9000)
        fr.evaluate("()=>{document.querySelectorAll('.lean-overlay').forEach(o=>{if(o.getBoundingClientRect().width>0)o.remove();});}")

        print("2) Filtrar por trámite…")
        ab = False
        for i in range(4):
            try: fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000)
            except Exception:
                try: fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000, force=True)
                except Exception: pass
            try:
                fr.wait_for_selector('table.tabBandeja thead input[index-field]', timeout=6000); ab = True; break
            except Exception: fr.wait_for_timeout(2000)
        if not ab: print("❌ filtro no abrió"); sys.exit(3)
        heads = fr.evaluate("""()=>[...document.querySelector('table.tabBandeja thead').children[0].querySelectorAll('th')].map(x=>(x.innerText||'').trim())""")
        idx = next((i for i,h in enumerate(heads) if 'TRAMI' in h.upper()), 15)
        loc = fr.locator(f'table.tabBandeja thead input[index-field="{idx}"]').first
        loc.wait_for(state="visible", timeout=8000)
        loc.click(timeout=8000); loc.fill(a.tramite); fr.wait_for_timeout(500); loc.press("Enter")
        fr.wait_for_timeout(7000)
        fila = fr.evaluate("""()=>{const tr=document.querySelector('table.tabBandeja tbody tr');
          return tr?[...tr.querySelectorAll('td')].map(td=>(td.innerText||'').trim().slice(0,24)):null;}""")
        print("   fila:", fila)
        if not fila:
            print("❌ no se encontró el trámite en Planificados Med"); sys.exit(4)

        print("3) Marcar checkbox…")
        fr.evaluate("""()=>{const cb=document.querySelector('table.tabBandeja tbody input[type=checkbox]'); if(cb&&!cb.checked)cb.click();}""")
        fr.wait_for_timeout(1500)

        print("4) Pulsar 'Reasignar'…")
        fr.evaluate("()=>{document.querySelectorAll('.lean-overlay').forEach(o=>{if(o.getBoundingClientRect().width>0)o.remove();});}")
        fr.evaluate("""()=>{const li=document.querySelector('li.opc[op="reasignar"]');
          const rc=li.getBoundingClientRect(); (li.querySelector('a')||li).dispatchEvent(new MouseEvent('click',
            {bubbles:true,cancelable:true,view:window,clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));}""")
        fr.wait_for_timeout(7000)
        # cerrar posible modal de error
        err = fr.evaluate("""()=>{const e=document.getElementById('alMod-mod-error');
          if(e){const r=e.getBoundingClientRect(); if(r.width>0){const b=[...e.querySelectorAll('a')].find(x=>/salir/i.test(x.innerText));
            if(b)b.click(); return 'error cerrado';}} return 'sin error visible';}""")
        print("   modal error:", err)
        fr.wait_for_timeout(2000)

        pin = [f for f in pg.frames if "planificador" in f.url]
        if not pin:
            print("⚠️ iframe planificador no encontrado"); pg.screenshot(path=f"{SHOTS}/R2_err.png"); sys.exit(5)
        pin = pin[-1]; pin.wait_for_timeout(2500)
        print("   iframe:", pin.url[:110])
        print("   texto:", pin.evaluate("()=>document.body.innerText.slice(0,400)").replace("\n"," | "))

        print("5) Seleccionar 'División Absoluta'…")
        pin.evaluate("""()=>{const s=document.getElementById('cmbMetodo'); if(s){s.value='1'; s.dispatchEvent(new Event('change',{bubbles:true}));}}""")
        pin.wait_for_timeout(4500)
        cuads = pin.evaluate("""()=>{const l=document.getElementById('lstCuadrillas');
          return l?[...l.querySelectorAll('a')].map(a=>({idc:a.getAttribute('idc'),c:a.getAttribute('c')})):[];}""")
        print(f"   cuadrillas disponibles: {len(cuads)}")

        print(f"6) Seleccionar cuadrilla {a.cuadrilla}…")
        sel = pin.evaluate("""(idc)=>{const el=document.querySelector('#lstCuadrillas a[idc="'+idc+'"]');
          if(!el) return 'no encontrada';
          const r=el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
            clientX:r.left+r.width/2,clientY:r.top+r.height/2}));
          return el.getAttribute('c');}""", a.cuadrilla)
        print("   →", sel)
        pin.wait_for_timeout(2500)
        pg.screenshot(path=f"{SHOTS}/R3_sel.png")
        est = pin.evaluate("""()=>{const out={activos:[],resumen:''};
          document.querySelectorAll('#lstCuadrillas a').forEach(a=>{if(a.classList.contains('active'))out.activos.push(a.getAttribute('c'));});
          const t=document.body.innerText; const m=t.match(/\\d+\\s*[Gg]esti[oó]n/); out.resumen=m?m[0]:'';
          return out;}""")
        print("   estado:", json.dumps(est, ensure_ascii=False))

        if a.ejecutar:
            print("⚠️ PULSANDO REASIGNAR (escritura real)…")
            post.clear()
            r = fr.evaluate("""()=>{const el=document.getElementById('btnPlanificarAuto');
              const rc=el.getBoundingClientRect();
              el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
                clientX:rc.left+rc.width/2,clientY:rc.top+rc.height/2}));
              return 'click emitido';}""")
            print("   ", r)
            fr.wait_for_timeout(10000)
            pg.screenshot(path=f"{SHOTS}/R4_post.png")
            print("=== POSTs ===")
            for u,body in post:
                print(f"   {u}\n      {body[:220]}")
            print("   texto:", fr.evaluate("()=>document.body.innerText.slice(0,400)").replace("\n"," | "))
        else:
            print("✅ PASO 6 verificado. NO se pulsó REASIGNAR (modo seguro).")
        b.close()


if __name__ == "__main__":
    main()
