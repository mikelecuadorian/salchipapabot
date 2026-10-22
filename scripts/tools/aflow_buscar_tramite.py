#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AFlow — filtrar la bandeja por TRAMITE y leer la fila resultante.
MODO SEGURO: solo filtra y muestra. No selecciona ni planifica.
Uso: aflow_buscar_tramite.py --tramite 344710141
"""
import os, sys, time, json, argparse
USER = os.environ.get("ACIIS_USER", "0927194159")
PASS = os.environ.get("ACIIS_PASS", "")
SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tramite", required=True)
    a = ap.parse_args()

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

        # Cerrar posibles overlays (lean-overlay de Materialize) que intercepten clicks
        def limpiar_overlays():
            n = fr.evaluate("""()=>{
              let n=0;
              document.querySelectorAll('.lean-overlay,.modal-overlay').forEach(o=>{
                const r=o.getBoundingClientRect();
                if(r.width>0){ o.remove(); n++; }
              });
              document.querySelectorAll('.modal.open,.modal').forEach(m=>{
                const r=m.getBoundingClientRect();
                if(r.width>0 && r.height>0){ m.classList.remove('open'); m.style.display='none'; n++; }
              });
              return n;
            }""")
            if n: print(f"   (overlays cerrados: {n})")
            return n
        limpiar_overlays()

        # Abrir filtro (click nativo; force por si queda overlay)
        try:
            fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000)
        except Exception as e:
            print(f"   ⚠️ click normal falló ({str(e)[:60]}); limpiando overlay y reintentando")
            limpiar_overlays()
            fr.locator('li.opc[op="filtrar"] > a').first.click(timeout=8000, force=True)
        print("   ✅ filtro abierto")
        fr.wait_for_timeout(2500)

        # Mapear header->index (la 2da fila del thead tiene los inputs con index-field)
        heads = fr.evaluate("""()=>{
          const tab=document.querySelector('table.tabBandeja'); const thead=tab.querySelector('thead');
          const ths=[...thead.children[0].querySelectorAll('th')].map(t=>(t.innerText||'').trim());
          const inputs=[...thead.querySelectorAll('input[index-field]')].map(i=>i.getAttribute('index-field'));
          return {ths, inputs};
        }""")
        print("HEADERS:", heads['ths'])
        # buscar índice de TRAMITE
        idx = None
        for i, h in enumerate(heads['ths']):
            if h.upper() in ("TRAMITE", "TRÁMITE", "TRAMI") or "TRAMI" in h.upper():
                idx = i
        print("Índice TRAMITE detectado:", idx)
        if idx is None:
            print("⚠️ No hallé columna TRAMITE; usando por defecto 14")
            idx = 14
        inp_id = f"input[index-field='{idx}']"
        print(f"Escribiendo '{a.tramite}' en {inp_id}…")

        # Escribir y Enter (usando Playwright nativo sobre el input del thead).
        # ⚠️ NO hacer click() antes: el thead suele estar cubierto por un overlay
        # (lean-overlay de Materialize) y el click expira con TimeoutError aunque
        # el input exista. `fill()` enfoca y escribe sin necesidad de click.
        loc = fr.locator(f'table.tabBandeja thead input[index-field="{idx}"]').first
        try:
            loc.fill(a.tramite, timeout=10000)
        except Exception:
            # plan B: escribir por JS si el input está oculto/deshabilitado
            fr.evaluate(
                """([idx,val])=>{const i=document.querySelector(
                     `table.tabBandeja thead input[index-field="${idx}"]`);
                   if(i){i.value=val;i.dispatchEvent(new Event('input',{bubbles:true}));
                         i.dispatchEvent(new Event('change',{bubbles:true}));}}""",
                [idx, a.tramite])
        fr.wait_for_timeout(500)
        loc.press("Enter")
        print("   Enter enviado. Esperando recarga…")
        fr.wait_for_timeout(7000)
        pg.screenshot(path=f"{SHOTS}/81_filtrado.png")

        # Leer resultados
        res = fr.evaluate("""()=>{
          const tab=document.querySelector('table.tabBandeja');
          const rows=[...tab.querySelectorAll('tbody tr')].map(tr=>
            [...tr.querySelectorAll('td')].map(td=>(td.innerText||'').trim().slice(0,24)));
          const pie=(document.body.innerText.match(/Mostrando[^\\n]*/)||[''])[0];
          const chks=[...tab.querySelectorAll('tbody input[type=checkbox]')].map(c=>c.id);
          return {n:rows.length, rows:rows.slice(0,10), pie, chks:chks.slice(0,10)};
        }""")
        print(f"=== RESULTADOS: {res['n']} filas ===")
        print("   pie:", res['pie'])
        print("   checkboxes:", res['chks'])
        for r in res['rows']:
            print("    ", " | ".join(r))
        b.close()


if __name__ == "__main__":
    main()
