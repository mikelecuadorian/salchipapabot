"""
zonas.py — Zonas operativas: point-in-polygon + rotación semanal.
Sin dependencias externas (solo stdlib). Importable desde los scripts de asignación.

Uso típico:
    from zonas import Zonas
    z = Zonas()                       # carga zonas de la BD (cachea)
    z.cual(lat, lon)                  # -> 'ZONASUR-ART-PUNTILLA' o None
    z.cuadrilla_semana(lat, lon)      # -> (zona, cuadrilla, semana_iso) o (None,None,None)

CLI:
    python3 zonas.py --test                      # auto-test de geometría
    python3 zonas.py --punto -2.13 -79.87        # qué zona contiene ese punto
    python3 zonas.py --cobertura                 # % de trámites cubiertos
    python3 zonas.py --semana                    # rotación de esta semana
"""
import sqlite3, json, os, sys
from datetime import date, datetime, timedelta

DB = os.environ.get(
    "GESTION_DB",
    "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db")


# ---------------------------------------------------------------- geometría
def punto_en_poligono(lat, lon, anillo):
    """Ray casting. anillo = [(lat,lon), ...] (abierto o cerrado).
    Devuelve True si (lat,lon) está dentro."""
    n = len(anillo)
    if n < 3:
        return False
    dentro = False
    j = n - 1
    for i in range(n):
        yi, xi = anillo[i]
        yj, xj = anillo[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            dentro = not dentro
        j = i
    return dentro


def en_bbox(lat, lon, bbox):
    minla, maxla, minlo, maxlo = bbox
    return minla <= lat <= maxla and minlo <= lon <= maxlo


def semana_iso(d=None):
    """'YYYY-Www' para una fecha (por defecto hoy)."""
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def rango_semana(d=None):
    """(lunes, domingo) de la semana ISO de la fecha dada."""
    d = d or date.today()
    lunes = d - timedelta(days=d.weekday())
    return lunes, lunes + timedelta(days=6)


# ---------------------------------------------------------------- API
class Zonas:
    def __init__(self, db_path=DB, recargar=True):
        self.db = db_path
        self._zonas = None
        if recargar:
            self.cargar()

    def cargar(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=20)
        try:
            rows = con.execute("""
                SELECT id, nombre, codigo, geojson, bbox_minlat, bbox_maxlat,
                       bbox_minlon, bbox_maxlon, area_km2
                FROM zonas_operativas WHERE activa=1
            """).fetchall()
        finally:
            con.close()
        self._zonas = []
        for (zid, nombre, codigo, gj, mnla, mxla, mnlo, mxlo, area) in rows:
            anillo = [(float(p[0]), float(p[1])) for p in json.loads(gj)]
            self._zonas.append({
                "id": zid, "nombre": nombre, "codigo": codigo,
                "anillo": anillo, "bbox": (mnla, mxla, mnlo, mxlo), "area": area,
            })
        return self

    @property
    def zonas(self):
        if self._zonas is None:
            self.cargar()
        return self._zonas

    def cual(self, lat, lon):
        """Nombre de la zona que contiene el punto, o None."""
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return None
        if not (-5.5 < lat < 1.5 and -82.5 < lon < -78.0):
            return None
        for z in self.zonas:
            if not en_bbox(lat, lon, z["bbox"]):
                continue
            if punto_en_poligono(lat, lon, z["anillo"]):
                return z["nombre"]
        return None

    def objeto(self, lat, lon):
        """El dict completo de la zona que contiene el punto, o None."""
        nombre = self.cual(lat, lon)
        if not nombre:
            return None
        return next(z for z in self.zonas if z["nombre"] == nombre)

    # ------------------------------------------------ rotación
    def cuadrilla_semana(self, lat, lon, d=None, db=None):
        """(zona, cuadrilla, semana_iso). cuadrilla=None si no hay rotación cargada."""
        zona = self.cual(lat, lon)
        if not zona:
            return None, None, semana_iso(d)
        return zona, self.cuadrilla_de_zona(zona, d, db=db), semana_iso(d)

    def cuadrilla_de_zona(self, zona_nombre, d=None, db=None, turno=None):
        """Cuadrilla titular vigente para la zona en la fecha dada. None si no hay.

        turno: 'manana' | 'tarde' | None. Con turno, devuelve la cuadrilla de ESE turno.
        Sin turno, devuelve la de mayor prioridad (mañana antes que tarde).
        """
        d = d or date.today()
        f = d.isoformat()
        con = sqlite3.connect(f"file:{db or self.db}?mode=ro", uri=True, timeout=20)
        try:
            sql = """
                SELECT r.cuadrilla FROM rotacion_semanal r
                JOIN zonas_operativas z ON z.id = r.zona_id
                WHERE z.nombre = ?
                  AND r.activa = 1
                  AND r.fecha_desde <= ? AND r.fecha_hasta >= ?
            """
            par = [zona_nombre, f, f]
            if turno:
                sql += " AND (r.turno = ? OR r.turno = 'todo')"
                par.append(turno)
            sql += """ ORDER BY CASE r.turno WHEN 'manana' THEN 1 WHEN 'todo' THEN 2 ELSE 3 END,
                                r.prioridad ASC, r.id ASC LIMIT 1 """
            row = con.execute(sql, par).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def rotacion_semana(self, d=None, db=None):
        """Lista de (zona, cuadrilla, prioridad, turno) para la fecha dada."""
        d = d or date.today()
        f = d.isoformat()
        con = sqlite3.connect(f"file:{db or self.db}?mode=ro", uri=True, timeout=20)
        try:
            return con.execute("""
                SELECT z.nombre, r.cuadrilla, r.prioridad, r.turno
                FROM rotacion_semanal r
                JOIN zonas_operativas z ON z.id = r.zona_id
                WHERE r.activa = 1 AND r.fecha_desde <= ? AND r.fecha_hasta >= ?
                ORDER BY z.nombre,
                         CASE r.turno WHEN 'manana' THEN 1 WHEN 'todo' THEN 2 ELSE 3 END,
                         r.prioridad
            """, (f, f)).fetchall()
        finally:
            con.close()


# ---------------------------------------------------------------- CLI
def _test():
    # cuadrado de prueba: lat -2.10..-2.00, lon -79.90..-79.80
    sq = [(-2.10, -79.90), (-2.10, -79.80), (-2.00, -79.80), (-2.00, -79.90)]
    casos = [
        ((-2.05, -79.85), True, "centro"),
        ((-2.10, -79.85), None, "borde (indefinido en ray casting)"),
        ((-2.15, -79.85), False, "sur, fuera"),
        ((-1.95, -79.85), False, "norte, fuera"),
        ((-2.05, -79.95), False, "oeste, fuera"),
        ((-2.05, -79.75), False, "este, fuera"),
        ((-2.001, -79.801), True, "casi esquina dentro"),
        ((-1.999, -79.799), False, "casi esquina fuera"),
    ]
    ok = 0
    for (lat, lon), esp, desc in casos:
        got = punto_en_poligono(lat, lon, sq)
        if esp is None:
            print(f"  ~  {desc:26s} -> {got} (no verificado)")
            continue
        m = "✅" if got == esp else "❌"
        ok += got == esp
        print(f"  {m}  {desc:26s} -> {got} (esperado {esp})")
    print(f"\n{ok}/{len([c for c in casos if c[1] is not None])} casos correctos")

    print("\n=== ZONAS CARGADAS ===")
    z = Zonas()
    for zz in z.zonas:
        print(f"  {zz['nombre']:42s} {zz['area']:>7.2f} km²  {len(zz['anillo']):>3d} vértices")


def _cobertura():
    z = Zonas()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    rows = con.execute("SELECT parroquia, lat_cnel, lon_cnel FROM recorrido_cuadrillas").fetchall()
    con.close()
    from collections import Counter
    dentro, fuera = Counter(), Counter()
    for parq, la, lo in rows:
        zz = z.cual(la, lo)
        if zz:
            dentro[zz] += 1
        else:
            fuera[parq or "(sin)"] += 1
    tot = sum(dentro.values()) + sum(fuera.values())
    print(f"Trámites con coordenada: {tot:,}")
    print(f"Dentro: {sum(dentro.values()):,} ({100*sum(dentro.values())/tot:.1f}%)")
    print(f"Fuera:  {sum(fuera.values()):,} ({100*sum(fuera.values())/tot:.1f}%)\n")
    for k, v in dentro.most_common():
        print(f"  {k:42s} {v:>7,}")
    print("\nTop fuera:")
    for k, v in fuera.most_common(8):
        print(f"  {str(k)[:38]:38s} {v:>7,}")


def _semana():
    z = Zonas()
    print(f"Semana ISO: {semana_iso()}  ({rango_semana()[0]} → {rango_semana()[1]})")
    rot = z.rotacion_semana()
    if not rot:
        print("\n⚠️  No hay rotación cargada para esta semana.")
        print("   Cargá con: python3 rotacion.py --cargar ...")
        return
    for zona, cuad, pri in rot:
        etq = "titular" if pri == 1 else f"apoyo {pri}"
        print(f"  {zona:42s} {cuad:32s} ({etq})")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--punto" in a:
        i = a.index("--punto")
        la, lo = float(a[i+1]), float(a[i+2])
        z = Zonas()
        print(f"Punto ({la}, {lo})")
        print(f"  zona: {z.cual(la, lo)}")
    elif "--cobertura" in a:
        _cobertura()
    elif "--semana" in a:
        _semana()
    else:
        _test()
