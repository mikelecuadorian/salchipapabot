"""
rotacion.py — Gestión de la rotación semanal zona → cuadrilla.

Comandos:
  --ver [--fecha YYYY-MM-DD]          Ver la rotación de una semana
  --cargar --zona ZONA --cuadrilla C --semana YYYY-Www [--prioridad 1]
  --ciclo --desde YYYY-MM-DD --semanas N --patron "A,B,C"
                                      Genera N semanas rotando las cuadrillas
  --borrar --zona ZONA --semana YYYY-Www
  --historico [--zona ZONA]           Ver todo lo cargado

Cuadrillas: se valida contra los nombres reales de la BD (tabla recorrido_cuadrillas).
"""
import sqlite3, sys, os, argparse
from datetime import date, timedelta

DB = os.environ.get(
    "GESTION_DB",
    "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db")


def conectar(ro=True):
    if ro:
        return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    return sqlite3.connect(DB, timeout=20)


def semana_iso(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def rango(d):
    lunes = d - timedelta(days=d.weekday())
    return lunes, lunes + timedelta(days=6)


def parse_semana(s):
    """'2026-W43' -> lunes de esa semana."""
    y, w = s.split("-W")
    return date.fromisocalendar(int(y), int(w), 1)


def zonas_existentes():
    c = conectar()
    try:
        return [r[0] for r in c.execute("SELECT nombre FROM zonas_operativas WHERE activa=1 ORDER BY nombre")]
    finally:
        c.close()


def cuadrillas_existentes():
    c = conectar()
    try:
        return [r[0] for r in c.execute("""SELECT DISTINCT cuadrilla FROM recorrido_cuadrillas
                                           WHERE cuadrilla IS NOT NULL AND cuadrilla <> ''
                                           ORDER BY cuadrilla""")]
    finally:
        c.close()


def resolver_zona(txt):
    """Acepta nombre exacto, código o coincidencia parcial."""
    zs = zonas_existentes()
    if txt in zs:
        return txt
    up = txt.upper()
    cand = [z for z in zs if up in z.upper()]
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        print(f"❌ '{txt}' es ambiguo: {cand}")
        sys.exit(1)
    print(f"❌ Zona '{txt}' no existe. Disponibles:")
    for z in zs:
        print(f"     {z}")
    sys.exit(1)


def resolver_cuadrilla(txt):
    cs = cuadrillas_existentes()
    if txt in cs:
        return txt
    up = txt.upper()
    cand = [c for c in cs if up in c.upper()]
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        print(f"❌ '{txt}' es ambiguo, coincide con {len(cand)}:")
        for c in cand[:12]:
            print(f"     {c}")
        sys.exit(1)
    print(f"❌ Cuadrilla '{txt}' no encontrada en la BD.")
    sys.exit(1)


def cmd_ver(fecha=None):
    d = date.fromisoformat(fecha) if fecha else date.today()
    lunes, dom = rango(d)
    c = conectar()
    try:
        rows = c.execute("""
            SELECT z.nombre, z.codigo, r.cuadrilla, r.prioridad, r.fecha_desde, r.fecha_hasta, r.nota, r.turno
            FROM rotacion_semanal r JOIN zonas_operativas z ON z.id=r.zona_id
            WHERE r.activa=1 AND r.fecha_desde <= ? AND r.fecha_hasta >= ?
            ORDER BY z.nombre,
                     CASE r.turno WHEN 'manana' THEN 1 WHEN 'todo' THEN 2 ELSE 3 END,
                     r.prioridad
        """, (d.isoformat(), d.isoformat())).fetchall()
    finally:
        c.close()

    print(f"📅 Semana {semana_iso(d)}  ({lunes} → {dom})")
    if not rows:
        print("\n⚠️  Sin rotación cargada para esta semana.")
        print("\nZonas disponibles:")
        for z in zonas_existentes():
            print(f"   {z}")
        return
    for zona, cod, cuad, pri, fd, fh, nota, turno in rows:
        etq = "titular" if pri == 1 else f"apoyo #{pri}"
        print(f"\n▸ {zona}")
        print(f"    código:    {cod}")
        print(f"    cuadrilla: {cuad}  ({etq} · {turno})")
        print(f"    vigencia:  {fd} → {fh}")
        if nota:
            print(f"    nota:      {nota}")


def cmd_cargar(zona, cuadrilla, semana, prioridad=1, nota=None, turno="todo"):
    z = resolver_zona(zona)
    cu = resolver_cuadrilla(cuadrilla)
    lunes = parse_semana(semana) if "-W" in semana else date.fromisoformat(semana)
    lunes, dom = rango(lunes)

    c = conectar(ro=False)
    try:
        zid = c.execute("SELECT id FROM zonas_operativas WHERE nombre=?", (z,)).fetchone()[0]
        c.execute("""
            INSERT INTO rotacion_semanal
              (zona_id, turno, semana_iso, fecha_desde, fecha_hasta, cuadrilla, prioridad, nota)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(zona_id, fecha_desde, turno, cuadrilla) DO UPDATE SET
              prioridad=excluded.prioridad, nota=excluded.nota,
              fecha_hasta=excluded.fecha_hasta, activa=1
        """, (zid, turno, semana_iso(lunes), lunes.isoformat(), dom.isoformat(), cu, prioridad, nota))
        c.commit()
    finally:
        c.close()
    print(f"✅ {z}")
    print(f"   {semana_iso(lunes)}  {lunes} → {dom}  →  {cu}  (prioridad {prioridad})")


def cmd_ciclo(desde, semanas, patron):
    """Genera N semanas rotando la lista de cuadrillas.
    patron: 'CARLOS MIRANDA|ORLYN CABRERA|TOMMY SAMANIEGO' (separa con |)
    Cada semana todas las zonas reciben la misma cuadrilla, rotando.
    Si el patrón tiene más de una cuadrilla por zona en la misma semana, usar ';'.
    """
    cuad = [x.strip() for x in patron.split("|") if x.strip()]
    if not cuad:
        print("❌ Patrón vacío")
        return
    resueltas = [resolver_cuadrilla(x) for x in cuad]
    lunes0 = date.fromisoformat(desde) if desde else rango(date.today())[0]
    lunes0, _ = rango(lunes0)
    zs = zonas_existentes()

    print(f"Generando {semanas} semanas × {len(zs)} zonas = {semanas*len(zs)} filas\n")
    c = conectar(ro=False)
    n = 0
    try:
        for s in range(semanas):
            lunes = lunes0 + timedelta(weeks=s)
            dom = lunes + timedelta(days=6)
            cu = resueltas[s % len(resueltas)]
            for z in zs:
                zid = c.execute("SELECT id FROM zonas_operativas WHERE nombre=?", (z,)).fetchone()[0]
                c.execute("""
                    INSERT INTO rotacion_semanal
                      (zona_id, semana_iso, fecha_desde, fecha_hasta, cuadrilla, prioridad)
                    VALUES (?,?,?,?,?,1)
                    ON CONFLICT(zona_id, semana_iso, cuadrilla) DO UPDATE SET
                      fecha_desde=excluded.fecha_desde, fecha_hasta=excluded.fecha_hasta, activa=1
                """, (zid, semana_iso(lunes), lunes.isoformat(), dom.isoformat(), cu))
                n += 1
            print(f"  {semana_iso(lunes)}  {lunes} → {dom}  →  {cu}")
        c.commit()
    finally:
        c.close()
    print(f"\n✅ {n} filas cargadas")


def cmd_borrar(zona, semana):
    z = resolver_zona(zona)
    c = conectar(ro=False)
    try:
        zid = c.execute("SELECT id FROM zonas_operativas WHERE nombre=?", (z,)).fetchone()[0]
        cur = c.execute("DELETE FROM rotacion_semanal WHERE zona_id=? AND semana_iso=?", (zid, semana))
        c.commit()
        print(f"🗑  {cur.rowcount} fila(s) borradas de {z} en {semana}")
    finally:
        c.close()


def cmd_historico(zona=None):
    c = conectar()
    try:
        q = """SELECT z.nombre, r.semana_iso, r.fecha_desde, r.fecha_hasta, r.cuadrilla, r.prioridad, r.activa
               FROM rotacion_semanal r JOIN zonas_operativas z ON z.id=r.zona_id"""
        args = ()
        if zona:
            q += " WHERE z.nombre=?"
            args = (resolver_zona(zona),)
        q += " ORDER BY r.fecha_desde DESC, z.nombre, r.prioridad"
        rows = c.execute(q, args).fetchall()
    finally:
        c.close()
    if not rows:
        print("Sin rotación cargada.")
        return
    print(f"{'ZONA':42s} {'SEMANA':10s} {'DESDE':11s} {'HASTA':11s} {'CUADRILLA':30s} P")
    print("-" * 112)
    for n, s, fd, fh, cu, p, act in rows:
        flag = "" if act else " (inactiva)"
        print(f"{n[:42]:42s} {s:10s} {fd:11s} {fh:11s} {cu[:30]:30s} {p}{flag}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Rotación semanal zona → cuadrilla")
    p.add_argument("--ver", action="store_true")
    p.add_argument("--fecha")
    p.add_argument("--cargar", action="store_true")
    p.add_argument("--zona")
    p.add_argument("--cuadrilla")
    p.add_argument("--semana")
    p.add_argument("--prioridad", type=int, default=1)
    p.add_argument("--nota")
    p.add_argument("--turno", default="todo", choices=["manana", "tarde", "todo"])
    p.add_argument("--ciclo", action="store_true")
    p.add_argument("--desde")
    p.add_argument("--semanas", type=int, default=4)
    p.add_argument("--patron")
    p.add_argument("--borrar", action="store_true")
    p.add_argument("--historico", action="store_true")
    a = p.parse_args()

    if a.cargar:
        if not (a.zona and a.cuadrilla and a.semana):
            print("❌ Falta --zona, --cuadrilla o --semana")
            sys.exit(1)
        cmd_cargar(a.zona, a.cuadrilla, a.semana, a.prioridad, a.nota, a.turno)
    elif a.ciclo:
        if not a.patron:
            print("❌ Falta --patron \"CUAD1|CUAD2|CUAD3\"")
            sys.exit(1)
        cmd_ciclo(a.desde, a.semanas, a.patron)
    elif a.borrar:
        cmd_borrar(a.zona, a.semana)
    elif a.historico:
        cmd_historico(a.zona)
    else:
        cmd_ver(a.fecha)
