# database.py
# =====================================================
# FUNCIONES DE BASE DE DATOS
# =====================================================

import sqlite3
import time
from config import DB_PATH, DB_CONNECTION_TIMEOUT, DB_RETRY_ATTEMPTS, MAX_RESULTADOS
from utils import log


def consultar_sqlite(consulta, params=()):
    """Ejecuta una consulta SQL con reintentos y devuelve (resultados, columnas)"""
    for intento in range(DB_RETRY_ATTEMPTS):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=DB_CONNECTION_TIMEOUT)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(consulta, params)
            # Para SELECT, obtener resultados
            if consulta.strip().upper().startswith("SELECT"):
                resultados = cur.fetchall()
                columnas = [desc[0] for desc in cur.description]
                conn.close()
                return resultados, columnas
            else:
                conn.commit()
                filas_afectadas = cur.rowcount
                conn.close()
                return filas_afectadas, None
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and intento < DB_RETRY_ATTEMPTS - 1:
                time.sleep(0.5)
                continue
            log(f"❌ Error SQLite: {e}")
            return None, None
        except Exception as e:
            log(f"❌ Error en consulta SQLite: {e}")
            return None, None
    return None, None


def ejecutar_sql_seguro(sql):
    """Ejecuta una consulta SQL directa (solo SELECT permitido).
    Retorna (resultados, columnas). Si hay error, resultado=None y columna trae el mensaje de error."""
    sql = sql.strip()
    if not sql.upper().startswith("SELECT"):
        return None, "Solo se permiten consultas SELECT"
    
    # Verificar que no tenga sentencias peligrosas
    sql_lower = sql.lower()
    for peligroso in ["drop", "delete", "insert", "update", "alter", "create", "pragma", "attach", "detach"]:
        if peligroso in sql_lower:
            return None, f"Sentencia '{peligroso}' no permitida"
    
    return consultar_sqlite(sql)


# ============================================
# BÚSQUEDAS EN TABLA medidores
# ============================================

def buscar_medidor(nro_serie):
    """Busca en tabla medidores por nro_serie"""
    sql = """
        SELECT cuadrilla_nombre, nro_tramite 
        FROM medidores 
        WHERE nro_serie = ?
        LIMIT 1
    """
    resultado, _ = consultar_sqlite(sql, (str(nro_serie),))
    if resultado:
        return resultado[0]
    return None

# ============================================
# BÚSQUEDAS EN TABLA medidores_retirados
# ============================================

def buscar_medidor_retirado(medidor):
    """Busca en tabla medidores_retirados por número de medidor"""
    numero = str(medidor).strip()
    # Normalizar: quitar sufijo .0 si viene como float y espacios
    numero_limpio = numero.replace('.0', '') if numero.endswith('.0') else numero
    
    sql = """
        SELECT medidor, marca, lugar_entrega
        FROM medidores_retirados
        WHERE medidor = ?
        LIMIT 1
    """
    resultado, _ = consultar_sqlite(sql, (numero_limpio,))
    if resultado:
        return resultado[0]
    return None

# ============================================
# BÚSQUEDAS EN TABLA gestion_tramites
# ============================================

def buscar_medidor_avanzado(numero_medidor):
    """Busca en gestion_tramites por cualquier campo que pueda contener el número"""
    numero = str(numero_medidor).strip()
    
    sql = f"""
        SELECT 
            numero_tramite, 
            numero_solicitud,
            cuadrilla,
            fecha_ejecucion,
            observacion_gestion,
            nota_materiales,
            motivo_no_ejecucion,
            detalle_no_ejecucion,
            med_numero,
            med_serie,
            medidor_cont_1,
            medidor_cont_2,
            med_nue_num,
            med_nue_ser,
            med_ret_num,
            med_ret_ser
        FROM gestion_tramites 
        WHERE 
            observacion_gestion LIKE '%' || ? || '%'
            OR nota_materiales LIKE '%' || ? || '%'
            OR motivo_no_ejecucion LIKE '%' || ? || '%'
            OR detalle_no_ejecucion LIKE '%' || ? || '%'
            OR TRIM(REPLACE(med_numero, '.0', '')) = ? 
            OR TRIM(REPLACE(med_serie, '.0', '')) = ?
            OR TRIM(REPLACE(medidor_cont_1, '.0', '')) = ? 
            OR TRIM(REPLACE(medidor_cont_2, '.0', '')) = ?
            OR TRIM(REPLACE(med_nue_num, '.0', '')) = ? 
            OR TRIM(REPLACE(med_nue_ser, '.0', '')) = ?
            OR TRIM(REPLACE(med_ret_num, '.0', '')) = ? 
            OR TRIM(REPLACE(med_ret_ser, '.0', '')) = ?
        LIMIT 1
    """
    datos, _ = consultar_sqlite(sql, (numero, numero, numero, numero, 
                                        numero, numero, numero, numero, 
                                        numero, numero, numero, numero))
    
    if not datos:
        return None, None
    
    datos = datos[0]
    numero_tramite = datos[0]
    
    # Buscar en recorrido_cuadrillas
    sql_rc = """
        SELECT cuenta_contrato, motivo_solicitud
        FROM recorrido_cuadrillas 
        WHERE numero_tramite = ?
        LIMIT 1
    """
    rc, _ = consultar_sqlite(sql_rc, (str(numero_tramite),))
    
    return datos, rc


# ============================================
# BÚSQUEDAS COMBINADAS (recorrido_cuadrillas + gestion_tramites)
# ============================================

def buscar_tramite(numero_tramite):
    """Busca en recorrido_cuadrillas y gestion_tramites"""
    sql_rec = """
        SELECT numero_solicitud, tipo_solicitud, cuadrilla, fecha_ejecucion,
               cuenta_contrato
        FROM recorrido_cuadrillas 
        WHERE numero_tramite = ?
        LIMIT 1
    """
    recorrido, _ = consultar_sqlite(sql_rec, (str(numero_tramite),))
    
    if recorrido:
        sql_obs = """
            SELECT observacion_gestion
            FROM gestion_tramites 
            WHERE numero_tramite = ?
            LIMIT 1
        """
        observacion, _ = consultar_sqlite(sql_obs, (str(numero_tramite),))
        return recorrido, observacion
    
    # Si no está en recorrido_cuadrillas, buscar directamente en gestion_tramites
    sql_gt = """
        SELECT numero_solicitud, tipo_solicitud, cuadrilla, fecha_ejecucion,
               cuenta_contrato, observacion_gestion
        FROM gestion_tramites 
        WHERE numero_tramite = ?
        LIMIT 1
    """
    resultado, cols = consultar_sqlite(sql_gt, (str(numero_tramite),))
    if resultado:
        r = resultado[0]
        # Devolver en el mismo formato: (numero_solicitud, tipo, cuadrilla, fecha, cuenta_contrato)
        recorrido = [(r[0], r[1], r[2], r[3], r[4])]
        observacion = [(r[5],)]
        return recorrido, observacion
    
    return None, None


def buscar_solicitud(numero_solicitud):
    """Busca en recorrido_cuadrillas y gestion_tramites por numero_solicitud"""
    sql_rec = """
        SELECT numero_tramite, tipo_solicitud, cuadrilla, fecha_ejecucion,
               cuenta_contrato
        FROM recorrido_cuadrillas 
        WHERE numero_solicitud = ?
        LIMIT 1
    """
    recorrido, _ = consultar_sqlite(sql_rec, (str(numero_solicitud),))
    
    if recorrido:
        numero_tramite = recorrido[0][0]
        sql_obs = """
            SELECT observacion_gestion
            FROM gestion_tramites 
            WHERE numero_tramite = ?
            LIMIT 1
        """
        observacion, _ = consultar_sqlite(sql_obs, (str(numero_tramite),))
        return recorrido, observacion
    
    # Si no está en recorrido_cuadrillas, buscar en gestion_tramites
    sql_gt = """
        SELECT numero_tramite, tipo_solicitud, cuadrilla, fecha_ejecucion,
               cuenta_contrato, observacion_gestion
        FROM gestion_tramites 
        WHERE numero_solicitud = ?
        LIMIT 1
    """
    resultado, cols = consultar_sqlite(sql_gt, (str(numero_solicitud),))
    if resultado:
        r = resultado[0]
        # Devolver en el mismo formato: (numero_tramite, tipo, cuadrilla, fecha, cuenta_contrato)
        recorrido = [(r[0], r[1], r[2], r[3], r[4])]
        observacion = [(r[5],)]
        return recorrido, observacion
    
    return None, None


# ============================================
# FOTOS DE GESTIÓN (gestion_fotos)
# ============================================

def buscar_fotos_tramite(numero_tramite):
    """Busca fotos en gestion_fotos por numero_tramite.
    Retorna un dict con las URLs de las fotos o None si no hay."""
    sql = """
        SELECT foto_predio, foto_medidor, foto_red_servicio, foto_ubic_med,
               foto_puesta_tierra, foto_sello_medidor, foto_antes_aper_med,
               foto_entrega_notif, foto_lectura_med_nuevo, foto_lectura_med_retirado
        FROM gestion_fotos 
        WHERE numero_tramite = ?
        LIMIT 1
    """
    resultado, cols = consultar_sqlite(sql, (str(numero_tramite),))
    if not resultado:
        return None
    
    row = resultado[0]
    fotos = {}
    nombres = ['foto_predio', 'foto_medidor', 'foto_red_servicio', 'foto_ubic_med',
               'foto_puesta_tierra', 'foto_sello_medidor', 'foto_antes_aper_med',
               'foto_entrega_notif', 'foto_lectura_med_nuevo', 'foto_lectura_med_retirado']
    etiquetas = ['🏠 Predio', '📟 Medidor', '🔌 Red Servicio', '📍 Ubic. Med.',
                 '⏚ Puesta Tierra', '🔒 Sello Medidor', '🔧 Antes Aper. Med.',
                 '📄 Entrega Notif.', '📖 Lectura Med. Nuevo', '📖 Lectura Med. Retirado']
    
    for i, nombre in enumerate(nombres):
        url = row[i]
        if url:
            fotos[etiquetas[i]] = url
    
    return fotos if fotos else None


# ============================================
# FUNCIONES AUXILIARES
# ============================================

def obtener_todas_tablas():
    """Obtiene la lista de todas las tablas en la base de datos"""
    sql = "SELECT name FROM sqlite_master WHERE type='table';"
    resultados, _ = consultar_sqlite(sql)
    return resultados


def contar_registros(tabla):
    """Cuenta el número de registros en una tabla"""
    sql = f"SELECT COUNT(*) FROM {tabla}"
    resultado, _ = consultar_sqlite(sql)
    return resultado[0][0] if resultado else 0


def buscar_por_cuenta_contrato_avanzado(cuenta_contrato, limite=10):
    """Busca en recorrido_cuadrillas por cuenta_contrato, cruza con gestion_tramites.
    Incluye trámites SIN gestión: datos=None + rc_extra con info de recorrido_cuadrillas."""
    cuenta = str(cuenta_contrato).strip().rstrip('.0')
    
    sql_rc = """
        SELECT numero_tramite, cuenta_contrato, motivo_solicitud,
               numero_solicitud, cuadrilla, fecha_planificacion, fecha_ejecucion,
               estado_insp, tipo_solicitud, fecha_solicitud, cliente
        FROM recorrido_cuadrillas 
        WHERE cuenta_contrato LIKE ? || '%'
        LIMIT ?
    """
    resultados_rc, _ = consultar_sqlite(sql_rc, (cuenta, limite))
    
    if not resultados_rc:
        return None
    
    resultados = []
    for rc in resultados_rc:
        numero_tramite = rc[0]
        cuenta_cto = rc[1]
        motivo = rc[2]
        
        sql_gt = """
            SELECT 
                numero_tramite, numero_solicitud, cuadrilla, fecha_ejecucion,
                observacion_gestion, nota_materiales, motivo_no_ejecucion,
                detalle_no_ejecucion, med_numero, med_serie, medidor_cont_1,
                medidor_cont_2, med_nue_num, med_nue_ser, med_ret_num, med_ret_ser
            FROM gestion_tramites 
            WHERE numero_tramite = ?
            LIMIT 1
        """
        datos, _ = consultar_sqlite(sql_gt, (str(numero_tramite),))
        
        rc_extra = {
            'numero_tramite': numero_tramite,
            'numero_solicitud': rc[3],
            'cuadrilla': rc[4],
            'fecha_planificacion': rc[5],
            'fecha_ejecucion': rc[6],
            'estado_insp': rc[7],
            'tipo_solicitud': rc[8],
            'fecha_solicitud': rc[9],
            'cliente': rc[10],
        }
        # datos=None si el trámite NO está gestionado
        resultados.append((datos[0] if datos else None, cuenta_cto, motivo, rc_extra))
    
    return resultados if resultados else None


def buscar_por_cuenta_contrato(cuenta, limite=10):
    """Busca en recorrido_cuadrillas por cuenta_contrato"""
    cuenta_limpio = cuenta.strip().rstrip('.0')
    sql = """
        SELECT numero_tramite, fecha_ejecucion,
               lon_cnel, lat_cnel,
               lon_amobile, lat_amobile
        FROM recorrido_cuadrillas
        WHERE cuenta_contrato LIKE ? || '%'
        LIMIT ?
    """
    resultados, _ = consultar_sqlite(sql, (cuenta_limpio, limite))
    return resultados


def buscar_ultimo_por_cuenta_contrato(cuenta_contrato):
    """Busca el último trámite en recorrido_cuadrillas por cuenta_contrato y devuelve datos del cliente"""
    cuenta = str(cuenta_contrato).strip().rstrip('.0')
    sql = """
        SELECT identificacion_cliente, cliente, tarifa, direccion,
               mru, med_numero, med_serie, lon_cnel, lat_cnel
        FROM recorrido_cuadrillas
        WHERE cuenta_contrato LIKE ? || '%'
        ORDER BY id_recorrido DESC
        LIMIT 1
    """
    resultados, cols = consultar_sqlite(sql, (cuenta,))
    if resultados:
        return resultados[0]
    return None


# ============================================
# ANALIZADOR DE PREGUNTAS (en vez de DeepSeek para SQL)
# ============================================

COLUMNAS_INFO = [
    "numero_tramite", "cuadrilla", "fecha_ejecucion", "cliente",
    "estado", "cuenta_contrato", "tipo_solicitud",
    "motivo_no_ejecucion", "detalle_no_ejecucion",
    "observacion_gestion", "med_numero", "med_serie", "direccion",
    "parroquia", "zona"
]

STOP_WORDS = {"los", "las", "del", "para", "por", "con", "que", "una", "una", "sus",
              "este", "esta", "está", "entre", "sobre", "cada", "tiene", "tienen",
              "todo", "toda", "todos", "todas", "muy", "más", "mas", "pero", "sin",
              "eso", "esa", "esos", "esas", "ese", "qué", "cómo", "como", "dónde",
              "donde", "cuándo", "cuando", "cuánto", "cuanto", "quién", "quien",
              "eres", "hace", "hizo", "dame", "lista", "busca", "dame", "dame",
              "muestra", "muéstrame", "encuentra", "resumen", "reporte", "informe"}

def expandir_nombres_cuadrilla(pregunta):
    """
    Expande nombres cortos a nombres completos de cuadrilla.
    Busca en gestion_tramites y recorrido_cuadrillas.
    Ej: "Samaniego" → "TOMMY SAMANIEGO"
    Ej: "Joel Espinar" → "JOEL ESPINAR"
    """
    import re
    conn = sqlite3.connect(DB_PATH, timeout=DB_CONNECTION_TIMEOUT)
    try:
        cur = conn.cursor()
        # Buscar cuadrillas en ambas tablas
        cur.execute("""
            SELECT DISTINCT cuadrilla FROM gestion_tramites
            WHERE cuadrilla IS NOT NULL AND cuadrilla != ''
            UNION
            SELECT DISTINCT cuadrilla FROM recorrido_cuadrillas
            WHERE cuadrilla IS NOT NULL AND cuadrilla != ''
        """)
        cuadrillas = [r[0] for r in cur.fetchall()]

        pregunta_mod = pregunta
        pregunta_upper = pregunta_mod.upper()

        # PASO 1: Verificar si algún nombre completo YA está en la pregunta
        nombre_completo_presente = None
        for nombre in sorted(cuadrillas, key=len, reverse=True):
            if nombre.upper() in pregunta_upper:
                nombre_completo_presente = nombre
                log(f"🔍 Nombre completo '{nombre}' ya presente en la pregunta")
                break

        if nombre_completo_presente:
            pregunta_mod = re.sub(
                re.escape(nombre_completo_presente),
                nombre_completo_presente.upper(),
                pregunta_mod,
                flags=re.IGNORECASE,
                count=1
            )
            log(f"🔍 Normalizado a mayúsculas: '{nombre_completo_presente}' → '{nombre_completo_presente.upper()}'")
            return pregunta_mod

        # PASO 2: Buscar coincidencias parciales - palabra por palabra
        palabras = re.findall(r'\b\w+\b', pregunta)
        palabras_vistas = set()

        for palabra in palabras:
            if palabra.lower() in STOP_WORDS or len(palabra) < 4:
                continue
            if palabra.lower() in palabras_vistas:
                continue
            palabras_vistas.add(palabra.lower())

            palabra_upper = palabra.upper()
            for nombre_completo in cuadrillas:
                partes_nombre = nombre_completo.upper().split()
                if palabra_upper in partes_nombre:
                    if nombre_completo.upper() not in pregunta_mod.upper():
                        pregunta_mod = re.sub(r'\b' + re.escape(palabra) + r'\b', nombre_completo, pregunta_mod, count=1)
                        log(f"🔍 Expandido '{palabra}' → '{nombre_completo}'")
                        for p in nombre_completo.lower().split():
                            palabras_vistas.add(p)
                    break

        return pregunta_mod
    finally:
        conn.close()


def analizar_pregunta(pregunta):
    """Analiza la pregunta con regex y extrae: tipo, cuadrilla, periodo, tabla.
    Devuelve un dict con los parámetros para construir SQL."""
    import re
    q = pregunta.lower().strip()
    resultado = {
        "tipo": "lista",
        "cuadrilla": None,
        "periodo": "todo",
        "numero_tramite": None,
        "tabla": "gestion_tramites",  # tabla por defecto
        "campo_fecha": "fecha_ejecucion"  # campo de fecha por defecto
    }

    # === TABLA A CONSULTAR ===
    # Si pregunta por un número grande sin contexto de medidor, asumir trámite
    tiene_numero = bool(re.search(r'\b(\d{7,13})\b', pregunta))

    # 1) MEDIDORES — "de quién es el medidor X", "buscar medidor", "serie"
    if any(p in q for p in ["medidor", "medidores", "nro_serie", "número de serie",
                              "de quién", "de quien", "pertenece", "a quién"]):
        resultado["tabla"] = "medidores"
        resultado["tipo"] = "un_medidor"

    # 2) RECORRIDO_CUADRILLAS — asignados, planificados, pendientes
    elif any(p in q for p in ["asignado", "asignados", "asignación", "asignaciones",
                               "planificado", "planificados", "recorrido", "recorridos",
                               "en curso", "por hacer", "trabajo asignado",
                               "trabajos asignados", "tramite asignado",
                               "trámite asignado", "tramites asignados",
                               "trámites asignados"]):
        resultado["tabla"] = "recorrido_cuadrillas"

    # 3) GESTION_TRAMITES — ejecutados, realizados, hechos
    elif any(p in q for p in ["ejecutado", "ejecutados", "ejecución", "ejecucion",
                               "gestion", "gestión", "gestion_tramites",
                               "realizado", "realizados", "hecho", "hechos",
                               "tramite ejecutado", "trámite ejecutado",
                               "tramites ejecutados", "trámites ejecutados"]):
        resultado["tabla"] = "gestion_tramites"

    # 4) Si menciona específicamente la tabla
    elif "recorrido_cuadrillas" in q or "recorrido cuadrillas" in q:
        resultado["tabla"] = "recorrido_cuadrillas"
    elif "gestion_tramites" in q or "gestion tramites" in q or "gestión trámites" in q:
        resultado["tabla"] = "gestion_tramites"
    elif "medidores" in q:
        resultado["tabla"] = "medidores"

    # === CAMPO DE FECHA ESPECÍFICO ===
    # Detectar si menciona un campo de fecha distinto a fecha_ejecucion
    campos_fecha = {
        "fecha_analisis": ["fecha_analisis", "fecha de análisis", "fecha de analisis", "fecha analisis"],
        "fecha_solicitud": ["fecha_solicitud", "fecha de solicitud", "fecha solicitud"],
        "fecha_planificacion": ["fecha_planificacion", "fecha de planificación", "fecha de planificacion",
                                  "fecha planificacion", "fecha planificación"],
        "fecha_sincronizacion": ["fecha_sincronizacion", "fecha de sincronización", "fecha sincronizacion"],
        "fecha_fin": ["fecha_fin", "fecha de fin", "fecha fin"]
    }
    for campo, keywords in campos_fecha.items():
        if any(k in q for k in keywords):
            resultado["campo_fecha"] = campo
            log(f"📅 Campo fecha detectado: {campo}")
            break

    # === TIPO DE CONSULTA ===
    # Detectar "todos los campos" / "todos los campos de la tabla" / "columnas"
    if any(p in q for p in ["todos los campos", "todas las columnas", "campos de la tabla",
                              "estructura de la tabla", "columnas de la tabla",
                              "todos los campos de", "todas las columnas de"]):
        resultado["tipo"] = "todos_campos"
    
    if any(p in q for p in ["cuantos", "cuántos", "conteo", "total", "cantidad", "cuantas", "cuántas"]):
        resultado["tipo"] = "conteo"
    elif any(p in q for p in ["resumen", "reporte", "informe"]):
        resultado["tipo"] = "resumen"
    elif any(p in q for p in ["estado", "situación", "situacion", "en qué", "en que"]):
        resultado["tipo"] = "estado"
    elif any(p in q for p in ["liberado", "pendiente", "aprobado", "reprobado"]):
        resultado["tipo"] = "estado"

    # === NÚMERO DE TRÁMITE (12-13 dígitos) ===
    match_num = re.search(r'\b(\d{12,13})\b', pregunta)
    if match_num and resultado["tabla"] != "medidores":
        resultado["numero_tramite"] = match_num.group(1)
        if resultado["tipo"] not in ("conteo", "resumen"):
            resultado["tipo"] = "un_tramite"

    # === NÚMERO DE MEDIDOR (7-11 dígitos) para tabla medidores ===
    if resultado["tabla"] == "medidores":
        match_med = re.search(r'\b(\d{7,12})\b', pregunta)
        if match_med:
            resultado["numero_tramite"] = match_med.group(1)

    # === CUADRILLA (nombres en mayúsculas después de expansión) ===
    palabras_mayus = re.findall(r'\b[A-ZÁÉÍÓÚÑ]{3,}(?:\s+[A-ZÁÉÍÓÚÑ]{3,})+\b', pregunta)
    nombres_filtrados = []
    for nombre in palabras_mayus:
        n = nombre.strip().upper()
        partes = n.split()
        if len(partes) >= 2 and all(len(p) >= 2 for p in partes):
            palabras_descartar = {"CUANTOS", "CUÁNTOS", "LISTA", "DAME", "TRÁMITE",
                                  "TRAMITE", "HIZO", "ESTA", "ESTÁ", "SEMANA",
                                  "HOY", "MES", "DÍA", "DIA", "AYER", "RESUMEN",
                                  "REPORTE", "INFORME", "TODOS", "TODAS", "CADA",
                                  "EN CURSO", "POR HACER"}
            if not any(p in palabras_descartar for p in partes):
                nombres_filtrados.append(n)
    if nombres_filtrados:
        resultado["cuadrilla"] = nombres_filtrados[-1]

    # === PERIODO (fechas corregidas) ===
    if any(p in q for p in ["esta semana", "está semana", "la semana", "semana pasada", "semana"]):
        resultado["periodo"] = "semana"
    elif any(p in q for p in ["hoy", "de hoy", "el día de hoy", "dia", "este día"]):
        resultado["periodo"] = "hoy"
    elif any(p in q for p in ["este mes", "el mes", "del mes", "mensual"]):
        resultado["periodo"] = "mes"
    elif any(p in q for p in ["ayer", "día anterior", "dia anterior", "el día de ayer", "el dia de ayer"]):
        resultado["periodo"] = "ayer"
    elif any(p in q for p in ["anual", "año", "este año"]):
        resultado["periodo"] = "ano"

    log(f"📊 Análisis: tabla={resultado['tabla']}, tipo={resultado['tipo']}, "
        f"cuadrilla={resultado['cuadrilla']}, periodo={resultado['periodo']}, "
        f"campo_fecha={resultado['campo_fecha']}, "
        f"tramite={resultado['numero_tramite']}")
    return resultado


def construir_sql(params):
    """Construye SQL basado en los parámetros analizados.
    Soporta gestion_tramites, recorrido_cuadrillas y medidores.
    Sin DeepSeek."""
    
    tabla = params.get("tabla", "gestion_tramites")
    
    # ============================================
    # TABLA MEDIDORES — búsqueda directa
    # ============================================
    if tabla == "medidores":
        numero = params.get("numero_tramite", "")
        if numero:
            sql = f"""
                SELECT * FROM medidores
                WHERE nro_serie = '{numero}'
                   OR nro_medidor = '{numero}'
                LIMIT 1
            """
            log(f"💾 SQL medidores: buscar {numero}")
            return sql
        return "SELECT * FROM medidores LIMIT 10"
    
    # ============================================
    # TABLAS: gestion_tramites (alias gt) / recorrido_cuadrillas (alias rc)
    # ============================================
    alias = "gt" if tabla == "gestion_tramites" else "rc"
    campo_fecha = params.get("campo_fecha", "fecha_ejecucion")
    
    condiciones = []
    
    # === FILTRO POR CUADRILLA ===
    if params["cuadrilla"]:
        cuad_escaped = params["cuadrilla"].replace("'", "''")
        condiciones.append(f"UPPER({alias}.cuadrilla) LIKE UPPER('%{cuad_escaped}%')")
    
    # === FILTRO POR NÚMERO DE TRÁMITE ===
    if params["numero_tramite"]:
        return f"SELECT * FROM {tabla} {alias} WHERE {alias}.numero_tramite = '{params['numero_tramite']}' LIMIT 1"
    
    # === FILTRO POR PERIODO (CORREGIDO) ===
    if params["periodo"] == "hoy":
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') = DATE('now', 'localtime')")
    elif params["periodo"] == "ayer":
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') = DATE('now', 'localtime', '-1 day')")
    elif params["periodo"] == "semana":
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') >= DATE('now', 'localtime', '-7 days')")
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') <= DATE('now', 'localtime', '-1 day')")
    elif params["periodo"] == "mes":
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') BETWEEN DATE('now', 'localtime', 'start of month') AND DATE('now', 'localtime', '-1 day')")
    elif params["periodo"] == "ano":
        condiciones.append(f"DATE({alias}.{campo_fecha}, 'localtime') >= DATE('now', 'localtime', '-365 days')")
    
    where = " AND ".join(condiciones) if condiciones else "1=1"
    
    # === COLUMNAS COMUNES ===
    columnas_base = f"{alias}.numero_tramite, {alias}.cuadrilla, {alias}.fecha_ejecucion"
    
    if tabla == "gestion_tramites":
        columnas_extra = f"COALESCE({alias}.estado, 'PENDIENTE') as estado, {alias}.cliente"
    else:  # recorrido_cuadrillas
        columnas_extra = f"{alias}.tipo_solicitud, {alias}.cuenta_contrato, {alias}.cliente"
    
    # === TODOS LOS CAMPOS (SELECT *) ===
    if params["tipo"] == "todos_campos":
        sql = f"SELECT * FROM {tabla} {alias} WHERE {where} LIMIT 15"
        log(f"💾 SQL [{tabla}]: SELECT * (todos los campos)")
        return sql
    
    # === CONSTRUIR SEGÚN TIPO ===
    if params["tipo"] == "conteo":
        sql = f"""
            SELECT COUNT(*) as total,
                   COALESCE({alias}.cuadrilla, 'Sin asignar') as cuadrilla
            FROM {tabla} {alias}
            WHERE {where}
        """
        if params["cuadrilla"]:
            sql += f" GROUP BY {alias}.cuadrilla"
        sql += " LIMIT 15"
        
    elif params["tipo"] == "resumen":
        sql = f"""
            SELECT {alias}.cuadrilla, COUNT(*) as total
            FROM {tabla} {alias}
            WHERE {where}
            GROUP BY {alias}.cuadrilla
            ORDER BY total DESC
            LIMIT 15
        """
        
    elif params["tipo"] == "estado":
        sql = f"""
            SELECT {columnas_base}, {columnas_extra}
            FROM {tabla} {alias}
            WHERE {where}
            ORDER BY {alias}.{campo_fecha} DESC
            LIMIT 15
        """
        
    else:  # lista
        sql = f"""
            SELECT {columnas_base}, {columnas_extra}
            FROM {tabla} {alias}
            WHERE {where}
            ORDER BY {alias}.{campo_fecha} DESC
            LIMIT 15
        """
    
    log(f"💾 SQL construido [{tabla}]: {sql.replace(chr(10), ' ').strip()[:200]}...")
    return sql


def preguntar_bd(pregunta, cliente):
    """
    Toma una pregunta en lenguaje natural, ANALIZA con regex (NO DeepSeek),
    construye SQL según la tabla detectada, ejecuta y formatea.
    DeepSeek SOLO se usa para formatear la respuesta final.
    """
    from config import DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE, DEEPSEEK_MAX_TOKENS

    # ===== PASO 0: Expandir nombres cortos de cuadrillas =====
    pregunta_expandida = expandir_nombres_cuadrilla(pregunta)
    if pregunta_expandida != pregunta:
        log(f"🔍 Pregunta expandida: {pregunta_expandida[:80]}...")

    # ===== PASO 1: Analizar la pregunta con regex =====
    params = analizar_pregunta(pregunta_expandida)

    # ===== PASO 1.5: TABLA MEDIDORES — respuesta directa (sin DeepSeek) =====
    if params["tabla"] == "medidores":
        numero = params.get("numero_tramite", "")
        if not numero:
            return "🔍 Decime el número del medidor que querés buscar, ej: *¿de quién es el medidor 20230249290?*"

        from config import MEDIDOR_NO_ENCONTRADO_MSG
        info = buscar_medidor(numero)
        if info:
            cuadrilla, tramite = info
            return (
                f"🔍 **Medidor {numero}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 **Cuadrilla:** {cuadrilla or 'No registrada'}\n"
                f"📋 **N° Trámite:** {tramite or 'No registrado'}"
            )
        else:
            return MEDIDOR_NO_ENCONTRADO_MSG.format(numero)

    # ===== PASO 2: Construir SQL directamente (SIN DeepSeek) =====
    sql = construir_sql(params)
    
    # ===== PASO 3: Ejecutar SQL =====
    resultados, columnas = ejecutar_sql_seguro(sql)
    
    if resultados is None:
        # Fallback: intentar SQL más simple
        log("⚠️ Falló SQL construido, intentando consulta simple...")
        sql_fallback = "SELECT numero_tramite, cuadrilla, fecha_ejecucion, estado FROM gestion_tramites LIMIT 15"
        resultados, columnas = ejecutar_sql_seguro(sql_fallback)
    
    if resultados is None:
        return "❌ No pude consultar la base de datos con esa pregunta. ¿Podrías intentar reformularla?"
    
    if len(resultados) == 0:
        if params["cuadrilla"] and params["periodo"] != "todo":
            return f"🔍 No se encontraron trámites para {params['cuadrilla']} en el período solicitado."
        elif params["cuadrilla"]:
            return f"🔍 No se encontraron trámites para {params['cuadrilla']}."
        elif params["periodo"] != "todo":
            return "🔍 No se encontraron trámites en el período solicitado."
        return "🔍 No se encontraron resultados en la base de datos para tu consulta."
    
    # ===== PASO 4: Formatear resultados con DeepSeek (solo decoración) =====
    filas_texto = []
    for fila in resultados[:15]:
        vals = [str(v) if v is not None else "-" for v in fila]
        filas_texto.append(" | ".join(vals))
    
    datos_texto = "\n".join(filas_texto)
    columnas_texto = " | ".join(columnas) if columnas else ""
    
    # Mapear tabla a descripción legible
    tabla_desc = {
        "medidores": "medidores (información de medidores eléctricos)",
        "recorrido_cuadrillas": "recorrido_cuadrillas (trámites asignados/planificados a cuadrillas)",
        "gestion_tramites": "gestion_tramites (trámites ya ejecutados por las cuadrillas)"
    }
    desc_tabla = tabla_desc.get(params["tabla"], params["tabla"])
    
    prompt_formateo = f"""Eres un asistente que responde consultas sobre trámites de una base de datos.

DATOS REALES OBTENIDOS (NO inventes nada):
- Tabla consultada: {desc_tabla}
- Total de filas obtenidas: {len(resultados)}

Pregunta del usuario: {pregunta}

Resultados:
Columnas: {columnas_texto}
Datos:
{datos_texto}

INSTRUCCIONES ESTRICTAS:
- Responde SOLO con los datos reales que te doy arriba.
- NO inventes información, NO agregues números que no estén en los datos.
- Si la pregunta es un conteo ("cuántos", "total"), responde con el número exacto.
- Si la pregunta pide una lista, muestra los resultados de forma legible e indica cuántos hay en total.
- Sé breve y directo. Usa emojis.
- Responde en español.
"""
    
    try:
        resp_final = cliente.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt_formateo}],
            temperature=DEEPSEEK_TEMPERATURE,
            max_tokens=DEEPSEEK_MAX_TOKENS
        )
        return resp_final.choices[0].message.content
    except Exception as e:
        log(f"❌ Error formateando respuesta: {e}")
        # Fallback: formateo manual
        return formatear_resultados_manual(resultados, columnas, pregunta)


def formatear_resultados_manual(resultados, columnas, pregunta):
    """Fallback de formateo cuando DeepSeek falla"""
    if columnas and ("COUNT(*)" in str(columnas) or str(columnas[0]).lower() == "total"):
        # Formato para conteos y resúmenes
        lineas = []
        for fila in resultados:
            valores = [str(v) if v is not None else "-" for v in fila]
            lineas.append("▫️ " + " — ".join(valores))
        
        if len(resultados) == 1 and len(resultados[0]) == 1:
            total = resultados[0][0]
            return f"📊 **Total:** *{total}*"
        
        salida = "📊 *Resultados*\n" + "\n".join(lineas)
        if len(resultados) > 1:
            total_general = sum(r[0] if len(r) >= 2 else 0 for r in resultados) if len(resultados[0]) >= 2 else sum(r[0] for r in resultados)
            salida += f"\n📈 **Total general:** {total_general}"
        return salida
    
    lineas = []
    for i, fila in enumerate(resultados[:10], 1):
        partes_fila = []
        for idx, val in enumerate(fila):
            nombre_col = columnas[idx].upper() if columnas and idx < len(columnas) else f"COL{idx}"
            if nombre_col in ("CUADRILLA", "CLIENTE", "ESTADO"):
                val_str = str(val).strip() if val else "-"
                partes_fila.append(f"{val_str}")
        
        lineas.append(f"{'|'.join(partes_fila)}")
    
    header = " | ".join(columnas) if columnas else ""
    total = f"\n\n📊 Total: {len(resultados)} resultados (mostrando {min(10, len(resultados))})"
    
    salida = f"📋 *Resultados*\n`{header}`\n`{'\\n'.join(lineas)}`{total}"
    return salida[:4000]
