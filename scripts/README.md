# Scripts del vigía CNEL / AFlow

Copia versionada de los scripts que corren en la tablet, en
`/root/.hermes/scripts/`. **La copia que ejecuta el cron es la de
`~/.hermes/scripts`, no esta.** Este directorio existe para tener historial y
poder restaurar.

Para instalar en una máquina nueva: copiar a `~/.hermes/scripts/` respetando la
estructura (`tools/` dentro de `scripts/`).

## Cuentas y cédulas vigiladas

`vigia_cedulas_operativo.py` — núcleo del vigía. Contiene los dos diccionarios
que definen qué se vigila:

- `CUENTAS_CONTRATO` — `{cuenta: RECORDATORIO}`. Si el recordatorio trae
  *planificar/asignar/pasar* **y** el nombre exacto de una cuadrilla, el
  trámite se auto-asigna (ver `vigia_autoasignar.py`).
- `RECORDATORIOS_CEDULA` — lo mismo, pero por cédula del cliente.

Corre por cron cada hora a los `:30` (`30 0,6-23 * * *`).
`--probar` hace dry-run: no toca AFlow ni guarda estado.

## Auto-asignación

`vigia_autoasignar.py` — decide y ejecuta. Deriva el mapa
`AUTO_ASIGNAR` leyendo los recordatorios (no hay mapa fijo que mantener).

Comportamiento por estado del trámite:

| Estado | Acción |
|---|---|
| sin cuadrilla | PLANIFICAR |
| con otra cuadrilla | REASIGNAR |
| con la cuadrilla correcta | nada |
| ya ejecutado/liberado | **nunca** se toca |

- **1 trámite** → `tools/aflow_asignar_auto.py` (una sesión, ~80 s).
- **≥2 trámites** → `tools/aflow_lote.py` (una sola sesión para todos, ahorra
  ~22 s de setup por trámite). Limitado por `MAX_LOTE`.
- Un fallo **no se pierde**: queda en `autoasignaciones_pendientes` y se
  reintenta en la corrida siguiente.

## AFlow

`tools/aflow_asignar_auto.py` — **el script principal**. Decide solo:

1. Login (3 reintentos; el portal tarda ~24 s).
2. Filtra por trámite en **Por Planificar Med** → si está, **planifica**.
3. Si no, filtra en **Planificados Med** → si está, **reasigna**.
4. Si no está en ninguno → **"no existe en ACIIS"**.

Sin modo seguro, y verifica al final releyendo la bandeja.

```bash
python3 tools/aflow_asignar_auto.py --tramite 345139370 --cuadrilla 0926550781
python3 tools/aflow_asignar_auto.py --tramite X --nombre "CARLOS PEREZ"
```

| exit | significado |
|---|---|
| 0 | asignado/reasignado y verificado |
| 2 | argumentos inválidos |
| 4 | el trámite no existe en ACIIS |
| 5 | login / iframe no apareció |
| 6 | cuadrilla no está en el desplegable |
| 7 | se pulsó pero no se pudo verificar |

**Credenciales:** `ACIIS_USER` / `ACIIS_PASS` en `~/.hermes/.env`. Sin ellas el
script sale con exit 5 y el mensaje apunta al iframe, no a la causa. Cualquier
script nuevo que corra desde cron debe cargarlas por su cuenta.

## Tres trampas del portal (ya resueltas en el código)

1. **El filtro es pegajoso.** Al volver a un nodo, la grilla conserva el filtro
   anterior y ya viene con filas. Hay que **limpiar el input antes** de buscar,
   y en la verificación comprobar el `value` del input, no solo las filas.
2. **El iframe del planificador tapa el menú.** Tras asignar queda
   `planificador2.jsp` abierto y su overlay intercepta el click en "Filtrar".
   Cerrarlo antes de verificar, y elegir el frame por `main.jsp` — no por
   "aflow", que también matchea el planificador.
3. **Nunca leer la primera fila sin validar.** `filtrar_tramite` del lote hacía
   `querySelector('tbody tr')` y devolvía esa fila sin comprobar el número:
   con el filtro pegajoso se podía actuar sobre el trámite equivocado. Ahora se
   valida el contenido y se devuelve `None` si no coincide.

## Capacidad de cuadrillas

`capacidad_cuadrillas.py` — ranking de productividad **real**, no el promedio
bruto. Categoriza `motivo_postergacion` en causas propias vs externas y excluye
días con evento (vehículo dañado, comisión) o parciales.

```bash
python3 capacidad_cuadrillas.py --dias 30
python3 capacidad_cuadrillas.py --ambiguos
python3 capacidad_cuadrillas.py --evento "CARLOS GUARACA" 2026-10-19 vehiculo_danado
```

Eventos en `~/.hermes/state/cuadrillas_contexto.json`. Usar **30 días** para
decidir: con ventanas cortas manda el azar de los eventos.

## Estado

`~/.hermes/state/vigia_cedulas_operativo.json` guarda:

- `tramites_avisados` / `tramites_ejecutados` — para no reavisar.
- `tramites_autoasignados` — idempotencia.
- `autoasignaciones_pendientes` — la cola de reintentos.

Al agregar una cuenta que **ya tiene trámites**, hay que sembrarlos en
`avisados` + `ejecutados` o el vigía dispara un aviso falso. Hacer backup del
JSON antes de tocarlo a mano.
