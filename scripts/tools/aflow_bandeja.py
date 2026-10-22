#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AFlow — llegar a la bandeja "Por Planificar Med" y mapearla.
MODO SEGURO: navega y filtra, NUNCA pulsa PLANIFICAR (eso lo hará aflow_ejecutar.py aparte).
"""
import os, sys, time, json, argparse
USER = os.environ.get("ACIIS_USER", "0927194159")
PASS = os.environ.get("ACIIS_PASS", "")
SHOTS = "/root/.hermes/scripts/tools/aflow_shots"
from playwright.sync_api import sync_playwright


def snap(pg, n):
    p = f"{SHOTS}/{n}.png"
    try: pg.screenshot(path=p); print(f"  📸 {p}"); return p
    except Exception as e: print(f"  ⚠️ {e}"); return None


def login_y_aflow(pg):
    pg.goto("https://amobile.altura.systems/aportal/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(2000)
    pg.fill("#usuario", USER); pg.fill("#clave", PASS)
    pg.query_selector('button:has-text("INICIAR SESIÓN")').click(timeout=8000)
    pg.wait_for_timeout(6000)
    pg.evaluate("""()=>{document.querySelectorAll('div.grid-app').forEach(e=>{const t=e.querySelector('.title');
      if(t&&/AFlow/i.test(t.innerText)){const r=e.getBoundingClientRect();
        e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
          clientX:r.left+r.width/2,clientY:r.top+r.height/2}));}});}""")
    pg.wait_for_timeout(7000)
    fr = pg.query_selector("iframe").content_frame()
    fr.wait_for_timeout(1500)
    return fr


def click_nodo(fr, texto):
    return fr.evaluate("""(txt)=>{
      let n=0;
      document.querySelectorAll('span.title-nodo, div.collapsible-header, li').forEach(e=>{
        if((e.innerText||'').trim()===txt){
          const r=e.getBoundingClientRect();
          if(r.width>0){
            e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window,
              clientX:r.left+r.width/2,clientY:r.top+r.height/2}));
            n++;
          }
        }
      });
      return n;
    }""", texto)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tramite", default="")
    a = ap.parse_args()

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        ctx = b.new_context(locale="es-EC", viewport={"width": 1500, "height": 950})
        pg = ctx.new_page()

        print("1) Login + abrir AFlow…")
        fr = login_y_aflow(pg)
        print("   iframe listo")

        print("2) Click 'Por Planificar Med'…")
        n = click_nodo(fr, "Por Planificar Med")
        print(f"   clicks: {n}")
        fr.wait_for_timeout(9000)
        snap(pg, "60_bandeja")
        print("   Texto bandeja:", fr.evaluate("()=>document.body.innerText.slice(0,900)").replace("\n"," | "))

        print("3) Estructura de la bandeja:")
        info = fr.evaluate("""()=>{
          const out={headers:[],rows:[],buttons:[],checks:0};
          document.querySelectorAll('th').forEach(h=>{const t=(h.innerText||'').trim(); if(t)out.headers.push(t);});
          let n=0;
          document.querySelectorAll('tr').forEach(tr=>{
            const tds=tr.querySelectorAll('td');
            if(tds.length>4 && n<6){
              out.rows.push(Array.from(tds).slice(0,17).map(td=>(td.innerText||'').trim().slice(0,20)));
              n++;
            }
          });
          document.querySelectorAll('button,a.btn,input[type=button],input[type=submit]').forEach(b=>{
            const t=(b.innerText||b.value||'').trim();
            if(t&&t.length<30) out.buttons.push(t);
          });
          out.checks=document.querySelectorAll('input[type=checkbox], .checkbox').length;
          return out;
        }""")
        print("   HEADERS:", info['headers'])
        print("   BOTONES:", info['buttons'][:30])
        print("   CHECKBOXES:", info['checks'])
        print("   FILAS:")
        for r in info['rows']:
            print("    ", " | ".join(r))

        print("4) ¿Aparece grilla de datos en algún sub-iframe?")
        for i, f in enumerate(fr.query_selector_all("iframe")):
            print(f"   subiframe[{i}] src={(f.get_attribute('src') or '')[:120]}")
        b.close()


if __name__ == "__main__":
    main()
