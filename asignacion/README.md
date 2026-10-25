# Asignación automática de trámites (AFlow / ACIIS)

Motor que asigna trámites del Consorcio ART en el portal AFlow sin intervención
manual. Dos flujos independientes, a propósito:

| Flujo | Script | Criterio de asignación |
|---|---|---|
| **Emergencias** | `asignar_por_zona.py` | coordenada → polígono → zona → **rotación semanal** |
| **Desconexiones (DESCO)** | `asignar_desco_duran.py` | **parroquia** fija → cuadrilla fija |

No se mezclan: una emergencia se asigna por dónde cae geográficamente y a quién
le toca esa semana; una desconexión definitiva va siempre a la cuadrilla de DESCO.

---

## Flujo de emergencias (`asignar_por_zona.py`)

```
trámite sin cuadrilla (motivo = ORDEN EMERGENCIAS)
        │
        ├─ coordenada (lat, lon) UTM→WGS84 ya convertida
        │
        ▼
  point-in-polygon contra `zonas_operativas`  ← ray casting sobre KML
        │
        ▼
  zona  →  cuadrilla según `rotacion_semanal` + turno de la hora actual
        │
        ▼
  se agrupa UN lote por cuadrilla → `tools/aflow_lote.py`
        │
        ▼
  verifica en el portal + marca anti-reintento
```

### Turnos

- **mañana** 06:00 – 15:59
- **tarde** 16:00 – 23:59
- **00:00 – 05:59** → no hay turno, no se asigna

Una fila de rotación con `turno = 'todo'` aplica a cualquier hora. Si hay una
fila `todo` y otra específica, gana la específica.

### Uso

```bash
python3 asignar_por_zona.py --probar   # dry-run: no toca AFlow
python3 asignar_por_zona.py            # asigna y verifica
```

Sin rotación cargada para hoy el script sale **en silencio** (no es un error:
es fin de semana sin guardia, o el lunes antes de cargar la semana).

---

## Flujo DESCO (`asignar_desco_duran.py`)

Filtra `tipo_solicitud ∈ {Desco. pedido del cliente, Desco. por mora}` en
DURÁN / DIVINO NIÑO / EL RECREO con `cuadrilla` vacía y `accion_ejecutada ≠ SI`,
y asigna **todo** a CARLOS MIRANDA (`0928817774`).

```bash
python3 asignar_desco_duran.py --probar
python3 asignar_desco_duran.py
```

---

## Gestión de zonas y rotación

```bash
python3 rotacion.py --ver                        # rotación de la semana
python3 rotacion.py --cargar --zona ZONASUR-ART-PUNTILLA \
                            --cuadrilla "JIMMY VELIZ" \
                            --desde 2026-10-19 --hasta 2026-10-25 --turno manana
python3 rotacion.py --borrar --semana 2026-W43
python3 cuadrillas.py                            # lista de cuadrillas y cédulas
```

`rotacion.py --cargar` **valida el nombre contra `cuadrillas.py`** antes de
escribir: si el nombre no existe, no carga nada. Esto evita el fallo clásico de
AFlow, que rechaza la asignación cuando el nombre no coincide exactamente.

⚠️ **Nombres exactos.** `EDINSON QUIÑONEZ` va con Ñ. `CARLOS MIRANDA MOLESTINA`
es el nombre completo en la BD, no `Carlos Miranda`.

---

## Estructura

```
asignacion/
├── asignar_por_zona.py      # emergencias por polígono + rotación
├── asignar_desco_duran.py   # desconexiones por parroquia
├── zonas.py                 # point-in-polygon (ray casting), UTM→WGS84
├── rotacion.py              # carga/consulta de la rotación semanal
├── cuadrillas.py            # FUENTE ÚNICA nombre → cédula
└── tools/
    └── aflow_lote.py        # Playwright: login + asignación en lote
```

### Tablas que usa (`gestion_medidores.db`)

- **`zonas_operativas`** — `id, nombre, codigo, poligono (JSON), area_km2, activa`
- **`rotacion_semanal`** — `zona_id, cuadrilla, turno, fecha_desde, fecha_hasta, activa, prioridad`
- **`recorrido_cuadrillas`** — trámites, coordenadas (`lat_cnel`, `lon_cnel`), `motivo_solicitud`
- **`gestion_tramites`** — `estado` (guardarraíl: nunca reasignar LIBERADO/CERRADO)

Zonas activas: `ZONASUR-ART-PUNTILLA`, `ZONANORTE-ART-SAMBORONDON-TARIFA-VICTORIA`,
`ZONAESTE-ART-AURORA-DAULE`, `ZONAOESTE-ART-DURAN`.

---

## Variables de entorno

| Variable | Default | Para qué |
|---|---|---|
| `ACIIS_USER` | *(vacío)* | usuario del portal AFlow |
| `ACIIS_PASS` | *(vacío)* | clave del portal AFlow |
| `GESTION_DB` | `~/salchipapabot/gestion_medidores.db` | ruta de la base |
| `HERMES_SCRIPTS` | directorio del script | dónde buscar `zonas`, `cuadrillas`… |
| `ASIGNACION_STATE` | `~/.hermes/state/emergencias_zona_asignados.json` | marca anti-reintento |
| `DESCO_STATE` | `~/.hermes/state/desco_duran_asignados.json` | marca anti-reintento DESCO |

⚠️ **NUNCA hardcodear credenciales.** Bajo cron `no_agent` el entorno no trae
`ACIIS_USER`/`ACIIS_PASS` en el proceso, así que los scripts los leen de
`~/.hermes/.env` sin pisar lo que ya esté en el entorno.

---

## Pitfalls (todos costaron una caída)

1. **Intérprete explícito `/usr/bin/python3`.** Bajo cron `no_agent`,
   `sys.executable` no tiene Playwright instalado. Los scripts invocan
   `tools/aflow_lote.py` con la ruta absoluta del intérprete.
2. **Un solo asignador por zona.** Tener dos crons compitiendo por la misma zona
   asigna el mismo trámite a dos cuadrillas distintas. Existió ese bug: se
   eliminó el cron duplicado (`287dedaac0a9`) y quedó solo `78ec3054aac2`.
3. **`NameError` por variable local.** `CEDULA` era local de `main()` y se usaba
   como global en `asignar()`. El dry-run no lo detectaba porque retorna antes
   de llegar a `asignar()`. Moraleja: probar el camino real, no solo `--probar`.
4. **Marca anti-reintento en archivo JSON**, no en la BD: el portal tarda ~17 min
   en reflejar la asignación (AReports indexa con retraso), así que sin la marca
   el script reasignaría lo que acaba de asignar.
5. **Guardarraíl de estado.** Antes de asignar se consulta `gestion_tramites.estado`;
   si contiene `LIBERADO` o `CERRADO`, se salta. Nunca reasignar trámites cerrados.
