#!/usr/bin/env python3
"""
Scraper SAP Web v3.1 - Sincrónico + optimizado
Extrae órdenes SAP 2-3x más rápido: navegación directa por JS, sleeps reducidos
"""
import sqlite3
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

SAP_URL = "https://sapgw.redenergia.gob.ec:8200/sap/bc/ui5_ui5/sap/zord/index.html?sap-language=ES"
USER = "0952050581"
PASS = "Joel7451595"
DB_PATH = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"

TABS = {
    "POR_ASIGNAR": {"id": "SupNavAsig"},
    "EN_TRATAMIENTO": {"id": "SupNavTrat"},
}


def extraer_tabla_js(page):
    """Extrae TODOS los datos visibles usando JS - más rápido que iterar DOM"""
    return page.evaluate("""() => {
        const rows = document.querySelectorAll('[role="row"]');
        const data = [];
        for (const row of rows) {
            const cells = row.querySelectorAll('[role="gridcell"]');
            if (cells.length < 11) continue;
            const rowData = [];
            for (let i = 0; i < cells.length; i++) {
                const cell = cells[i];
                let val = '';
                if (i >= 2 && i <= 9) {
                    const inp = cell.querySelector('input');
                    val = inp ? (inp.value || '') : (cell.textContent || '').trim();
                } else if (i === 10) {
                    const icon = cell.querySelector('[data-sap-ui-icon-content]');
                    val = icon ? (icon.getAttribute('data-sap-ui-icon-content') || '') : (cell.textContent || '').trim();
                } else if (i === 11) {
                    const inp = cell.querySelector('input');
                    val = inp ? (inp.value || '') : (cell.textContent || '').trim();
                } else {
                    val = (cell.textContent || '').trim();
                }
                rowData.push(val);
            }
            if (rowData.length > 1 && /^\\d+$/.test(rowData[1])) {
                data.push(rowData);
            }
        }
        return data;
    }""")


def navegar_pagina(page, num_pag):
    """Navega a página específica por JS directo"""
    return page.evaluate(f"""() => {{
        const links = document.querySelectorAll('a.sapUiLnk');
        for (const link of links) {{
            if ((link.textContent || '').trim() === '{num_pag}') {{
                link.click();
                return true;
            }}
        }}
        return false;
    }}""")


def click_continuar(page):
    """Click en Continuar si no está deshabilitado"""
    return page.evaluate("""() => {
        const forwardLinks = document.querySelectorAll('[id$="-forwardLink"]');
        for (const link of forwardLinks) {
            const parent = link.closest('.sapUiPag');
            if (!parent) continue;
            if (link.getAttribute('aria-disabled') === 'true') continue;
            link.click();
            return true;
        }
        return false;
    }""")


def obtener_paginas(page):
    """Obtiene páginas visibles"""
    return page.evaluate("""() => {
        const items = document.querySelectorAll('.sapUiPagPage');
        const pages = [];
        for (const item of items) {
            const link = item.querySelector('a, span');
            if (!link) continue;
            const t = (link.textContent || '').trim();
            if (/^[0-9]+$/.test(t)) pages.push(parseInt(t));
        }
        return pages.sort((a,b) => a-b);
    }""")


def click_pestana(page, tab_id):
    """Hace clic en una pestaña del facetBar"""
    try:
        el = page.query_selector(f'#{tab_id}')
        if el:
            el.click()
            time.sleep(3)
            return True
    except Exception as e:
        print(f"   ❌ Error click: {e}")
    return False


def limpiar_orden(s):
    return s.lstrip('0')


def guardar_en_db(ordenes, estado, conn):
    c = conn.cursor()
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    guardadas = nuevas = actualizadas = 0

    for fila in ordenes:
        num_orden = fila[1].strip()
        if not num_orden.isdigit():
            continue
        num_solicitud = limpiar_orden(num_orden)
        cl_orden = fila[2].strip() if len(fila) > 2 else ''
        actividad = fila[3].strip() if len(fila) > 3 else ''
        mru = fila[4].strip() if len(fila) > 4 else ''
        p_trabajo = fila[5].strip() if len(fila) > 5 else ''
        fecha_ini = fila[6].strip() if len(fila) > 6 else ''
        canton = fila[7].strip() if len(fila) > 7 else ''
        distrito = fila[8].strip() if len(fila) > 8 else ''
        calle = fila[9].strip() if len(fila) > 9 else ''
        usuario = fila[11].strip() if len(fila) > 11 else ''

        c.execute("SELECT estado FROM ordenes_sap WHERE numero_orden_sap = ?", (num_orden,))
        existente = c.fetchone()

        if existente:
            ea = existente[0]
            if ea != estado:
                c.execute("INSERT INTO historial_ordenes (numero_orden_sap, estado_anterior, estado_nuevo, fecha_cambio) VALUES (?, ?, ?, ?)",
                          (num_orden, ea, estado, ahora))
                print(f"     🔄 {num_orden}: {ea} → {estado}")
            c.execute("""UPDATE ordenes_sap SET estado=?,numero_solicitud=?,actividad_pm=?,
                mru_secuencia=?,p_trabajo_res=?,fecha_inicio=?,canton=?,distrito=?,
                calle_no=?,usuario=?,ultima_vista=?,
                fecha_cambio_estado=CASE WHEN estado!=? THEN ? ELSE fecha_cambio_estado END
                WHERE numero_orden_sap=?""",
                      (estado, num_solicitud, actividad, mru, p_trabajo, fecha_ini,
                       canton, distrito, calle, usuario, ahora, estado, ahora, num_orden))
            actualizadas += 1
        else:
            c.execute("""INSERT INTO ordenes_sap (numero_solicitud, numero_orden_sap, estado,
                actividad_pm, mru_secuencia, p_trabajo_res, fecha_inicio,
                canton, distrito, calle_no, usuario, capturado_en, ultima_vista, fecha_cambio_estado)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (num_solicitud, num_orden, estado, actividad, mru, p_trabajo, fecha_ini,
                       canton, distrito, calle, usuario, ahora, ahora, ahora))
            nuevas += 1
        guardadas += 1

    conn.commit()
    return guardadas, nuevas, actualizadas


def procesar_pestana(page, clave_tab, conn):
    tab_id = TABS[clave_tab]["id"]
    print(f"\n{'='*50}")
    print(f"📋 {clave_tab}")
    print(f"{'='*50}")

    if not click_pestana(page, tab_id):
        return 0, 0, 0, set()

    todas = []
    while True:
        pags = obtener_paginas(page)
        if not pags:
            datos = extraer_tabla_js(page)
            if datos:
                todas.extend(datos)
            break

        pag_actual = page.evaluate("""() => {
            const c = document.querySelector('.sapUiPagCurrentPage');
            if (!c) return 0;
            const l = c.querySelector('a,span');
            return l ? parseInt((l.textContent||'').trim()) : 0;
        }""")
        if pag_actual == 0:
            pag_actual = pags[0]

        print(f"  Pág {pag_actual}/{max(pags)} ({len(pags)} visibles)")
        time.sleep(1.5)

        datos = extraer_tabla_js(page)
        print(f"    → {len(datos)} órdenes")
        if datos:
            for o in datos[:2]:
                print(f"      {o[1]} | {o[6] if len(o)>6 else '?'} | {o[7] if len(o)>7 else '?'}")
        todas.extend(datos)

        sig = pag_actual + 1
        if sig <= max(pags):
            navegar_pagina(page, sig)
            continue

        if click_continuar(page):
            time.sleep(2)
            nuevas_pags = obtener_paginas(page)
            if nuevas_pags and max(nuevas_pags) > max(pags):
                continue
        break

    total, nuevas, actualizadas = guardar_en_db(todas, clave_tab, conn)
    print(f"  ✅ {total} órdenes ({nuevas} nuevas, {actualizadas} actualizadas)")

    vistas = set()
    for f in todas:
        n = f[1].strip()
        if n.isdigit():
            vistas.add(n)
    return total, nuevas, actualizadas, vistas


def health_check():
    print("🔍 Health check...")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True,
                                   args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])
            p = b.new_page()
            p.goto('about:blank', timeout=10000)
            b.close()
        print("   ✅ OK")
        return True
    except Exception as e:
        print(f"   ❌ {e}")
        return False


def main():
    inicio = datetime.now()
    print(f"🚀 SCRAPER SAP v3.1 - {inicio.strftime('%Y-%m-%d %H:%M:%S')}")

    conn = sqlite3.connect(DB_PATH)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
        )
        page = browser.new_page(
            viewport={'width': 1920, 'height': 1080},
            locale='es-EC',
            timezone_id='America/Guayaquil',
            ignore_https_errors=True
        )

        print("\n1️⃣  Navegando...")
        page.goto(SAP_URL, timeout=60000, wait_until='load')
        time.sleep(4)

        print("\n2️⃣  Login...")
        page.wait_for_selector('#usuario-inner', timeout=30000)
        page.fill('#usuario-inner', USER)
        page.fill('#contrasena-inner', PASS)
        page.click('button')
        time.sleep(8)

        # Capturar estado previo EN_TRATAMIENTO
        c = conn.cursor()
        c.execute("SELECT numero_orden_sap FROM ordenes_sap WHERE estado='EN_TRATAMIENTO'")
        antes = {r[0] for r in c.fetchall()}
        print(f"\n📋 EN_TRATAMIENTO en DB: {len(antes)} órdenes")

        total_asig, nuevas_asig, _, _ = procesar_pestana(page, "POR_ASIGNAR", conn)
        total_trat, nuevas_trat, _, vistas = procesar_pestana(page, "EN_TRATAMIENTO", conn)

        # CIERRE_TECNICO
        desaparecidas = antes - vistas
        if desaparecidas:
            ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for n in sorted(desaparecidas):
                c.execute("INSERT INTO historial_ordenes (numero_orden_sap, estado_anterior, estado_nuevo, fecha_cambio) VALUES (?,?,?,?)",
                          (n, 'EN_TRATAMIENTO', 'CIERRE_TECNICO', ahora))
                c.execute("UPDATE ordenes_sap SET estado='CIERRE_TECNICO',fecha_cambio_estado=?,ultima_vista=? WHERE numero_orden_sap=?",
                          (ahora, ahora, n))
            conn.commit()
            print(f"\n🔒 CIERRE TÉCNICO: {len(desaparecidas)} órdenes")
            for i, n in enumerate(sorted(desaparecidas)[:10], 1):
                print(f"   {i}. {n}")
            if len(desaparecidas) > 10:
                print(f"   ... y {len(desaparecidas)-10} más")
        else:
            print(f"\n✅ Sin CIERRE_TECNICO")

        # Resumen
        print(f"\n{'='*50}")
        print("📊 RESUMEN")
        print(f"{'='*50}")
        print(f"   POR ASIGNAR:     {total_asig} ({nuevas_asig} nuevas)")
        print(f"   EN TRATAMIENTO:  {total_trat} ({nuevas_trat} nuevas)")
        c.execute("SELECT estado,COUNT(*) FROM ordenes_sap GROUP BY estado")
        for est, cnt in c.fetchall():
            print(f"   {est}: {cnt}")

        conn.close()
        browser.close()
        duracion = (datetime.now() - inicio).total_seconds()
        print(f"\n⏱️  {duracion:.0f}s")
        print(f"✅ Completado - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def main_with_retry(max_retries=3):
    for i in range(1, max_retries + 1):
        print(f"\n{'='*50}")
        print(f"🚀 Intento {i}/{max_retries}")
        print(f"{'='*50}")
        try:
            main()
            return True
        except Exception as e:
            print(f"\n❌ Intento {i}: {e}")
            if i < max_retries:
                t = i * 30
                print(f"   ⏳ Reintentando en {t}s...")
                time.sleep(t)
    return False


if __name__ == "__main__":
    print(f"🛡️  Scraper SAP v3.1 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if not health_check():
        sys.exit(1)
    if not main_with_retry(3):
        print("💥 Scraper falló después de 3 intentos")
        sys.exit(1)
    print(f"\n✅ Scraper completado - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
