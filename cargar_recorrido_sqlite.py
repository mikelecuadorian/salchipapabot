import pandas as pd
import sqlite3
import os
import sys
import numpy as np

DB_PATH = r"/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db" # Ajusta si es necesario

def crear_tabla_si_no_existe(conn):
    """Crea la tabla recorrido_cuadrillas si no existe (esquema coherente con PostgreSQL)"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recorrido_cuadrillas (
            id_recorrido INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_tramite TEXT,
            tramite_origen INTEGER,
            numero_solicitud TEXT,
            numero_servicio TEXT,
            gestion TEXT,
            se_posterga INTEGER,
            motivo_postergacion TEXT,
            estado_insp TEXT,
            fecha_solicitud TEXT,
            fecha_analisis TEXT,
            fecha_planificacion TEXT,
            fecha_ejecucion TEXT,
            fecha_sincronizacion TEXT,
            fecha_fin TEXT,
            dias_transcurridos INTEGER,
            dias_por_vencer INTEGER,
            id_cuadrilla TEXT,
            cuadrilla TEXT,
            accion_ejecutada TEXT,
            fin_regla_negocio TEXT,
            cod_tipo_solicitud TEXT,
            tipo_solicitud TEXT,
            cod_motivo_solicitud TEXT,
            motivo_solicitud TEXT,
            oficina_solicitud TEXT,
            lon_cnel REAL,
            lat_cnel REAL,
            lon_amobile REAL,
            lat_amobile REAL,
            coordenada_y_amobile REAL,
            coordenada_x_amobile REAL,
            unidad TEXT,
            pro TEXT,
            can TEXT,
            pla INTEGER,
            sec INTEGER,
            rut INTEGER,
            secu INTEGER,
            cod_parroquia TEXT,
            parroquia TEXT,
            zona TEXT,
            cuenta_contrato TEXT,
            codigo_cliente TEXT,
            identificacion_cliente TEXT,
            cliente TEXT,
            tarifa TEXT,
            med_numero TEXT,
            med_serie TEXT,
            facturas_vencidas INTEGER,
            deuda_total REAL,
            direccion TEXT,
            mru TEXT,
            telefono_sol TEXT,
            movil_sol TEXT
        )
    """)
    conn.commit()

def formatear_identificador(valor):
    """Normaliza identificadores numéricos: 37130507.0 → '37130507'.
    Preserva strings alfanuméricos (SL-2181794) y decimales reales (1234.5)."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))
        return str(valor)
    if isinstance(valor, int):
        return str(valor)
    s = str(valor).strip()
    if s.lower() in ('nan', 'none'):
        return None
    if s.endswith('.0'):
        s = s[:-2]
    return s if s else None

def cargar_recorrido_upsert_conteo_sqlite(ruta_archivo):
    print(f"📂 Procesando (SQLite): {ruta_archivo}")
    
    # Leer Excel
    df = pd.read_excel(ruta_archivo, sheet_name='AReports Data')
    df = df.replace({np.nan: None})
    
    # Renombrar columnas (igual que en PostgreSQL)
    df = df.rename(columns={
        'N° Trámite': 'numero_tramite',
        'Trámite Origen': 'tramite_origen',
        'Número Solicitud': 'numero_solicitud',
        'Número Orden': 'numero_solicitud',
        'Número Servicio': 'numero_servicio',
        'Gestión': 'gestion',
        'Se Posterga': 'se_posterga',
        'Motivo': 'motivo_postergacion',
        'Estado Insp.': 'estado_insp',
        'Fecha Orden': 'fecha_solicitud',
        'Oficina Orden': 'oficina_solicitud',
        'Cod. Tipo Orden': 'cod_tipo_solicitud',
        'Tipo Orden': 'tipo_solicitud',
        'Cod. Motivo Orden': 'cod_motivo_solicitud',
        'Motivo Orden': 'motivo_solicitud',
        'Fecha Análisis': 'fecha_analisis',
        'Fecha Planificación': 'fecha_planificacion',
        'Id Cuadrilla': 'id_cuadrilla',
        'Cuadrilla': 'cuadrilla',
        'Acción Ejecutada': 'accion_ejecutada',
        'Fin Regla Negocio': 'fin_regla_negocio',
        'Fecha Ejecución': 'fecha_ejecucion',
        'Fecha Sincronización': 'fecha_sincronizacion',
        'Fecha Fin': 'fecha_fin',
        'Días Transcurridos': 'dias_transcurridos',
        'Días Por Vencer': 'dias_por_vencer',
        'LonCnel': 'lon_cnel',
        'LatCnel': 'lat_cnel',
        'LonAMobile': 'lon_amobile',
        'LatAMobile': 'lat_amobile',
        'CoordenadaYAMobile': 'coordenada_y_amobile',
        'CoordenadaXAMobile': 'coordenada_x_amobile',
        'UN': 'unidad',
        'PRO': 'pro',
        'CAN': 'can',
        'PLA': 'pla',
        'SEC': 'sec',
        'RUT': 'rut',
        'SECU': 'secu',
        'Cód. Parroquia': 'cod_parroquia',
        'Parroquia': 'parroquia',
        'Zona': 'zona',
        'Cuenta Contrato': 'cuenta_contrato',
        'Código Cliente': 'codigo_cliente',
        'N° Identificación': 'identificacion_cliente',
        'Cliente': 'cliente',
        'Tarifa': 'tarifa',
        'Med. Numero': 'med_numero',
        'Med. Serie': 'med_serie',
        'Facturas Vencidas': 'facturas_vencidas',
        'Deuda Total': 'deuda_total',
        'Dirección': 'direccion',
        'MRU': 'mru',
        'Teléfono Sol.': 'telefono_sol',
        'Móvil Sol.': 'movil_sol'
    })
    
    # Convertir booleano 'Se Posterga': manejar NaN y convertir a 0/1
    if 'se_posterga' in df.columns:
        # Mapear valores 'Si'/'Sí' a 1, 'No' a 0, otros (NaN) a 0
        df['se_posterga'] = df['se_posterga'].map(lambda x: 1 if x in ['Si', 'Sí', True, 'Yes'] else (0 if x in ['No', False] else 0))
        # Asegurar que es entero (llenar NaN con 0)
        df['se_posterga'] = df['se_posterga'].fillna(0).astype(int)
    
    # Convertir fechas a string ISO para SQLite
    columnas_fecha = ['fecha_solicitud', 'fecha_analisis', 'fecha_planificacion', 
                      'fecha_ejecucion', 'fecha_sincronizacion', 'fecha_fin']
    for col in columnas_fecha:
        if col in df.columns:
            # Convertir a datetime, luego a string
            df[col] = pd.to_datetime(df[col], format='%d/%m/%Y %H:%M:%S', errors='coerce')
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Columnas esperadas
    columnas_tabla = [
        'numero_tramite', 'tramite_origen', 'numero_solicitud', 'numero_servicio',
        'gestion', 'se_posterga', 'motivo_postergacion', 'estado_insp',
        'fecha_solicitud', 'oficina_solicitud', 'cod_tipo_solicitud', 'tipo_solicitud',
        'cod_motivo_solicitud', 'motivo_solicitud', 'fecha_analisis', 'fecha_planificacion',
        'id_cuadrilla', 'cuadrilla', 'accion_ejecutada', 'fin_regla_negocio',
        'fecha_ejecucion', 'fecha_sincronizacion', 'fecha_fin', 'dias_transcurridos', 'dias_por_vencer',
        'lon_cnel', 'lat_cnel', 'lon_amobile', 'lat_amobile', 'coordenada_y_amobile', 'coordenada_x_amobile',
        'unidad', 'pro', 'can', 'pla', 'sec', 'rut', 'secu',
        'cod_parroquia', 'parroquia', 'zona', 'cuenta_contrato', 'codigo_cliente', 'identificacion_cliente',
        'cliente', 'tarifa', 'med_numero', 'med_serie', 'facturas_vencidas', 'deuda_total',
        'direccion', 'mru', 'telefono_sol', 'movil_sol'
    ]
    
    # Filtrar columnas existentes
    columnas_existentes = [col for col in columnas_tabla if col in df.columns]
    df_to_load = df[columnas_existentes].copy()
    
    # Limpiar numero_tramite
    df_to_load = df_to_load.dropna(subset=['numero_tramite'])
    df_to_load = df_to_load[df_to_load['numero_tramite'] != 0]
    df_to_load['numero_tramite'] = df_to_load['numero_tramite'].astype(str).str.strip()
    df_to_load = df_to_load[df_to_load['numero_tramite'] != '']
    
    # Normalizar identificadores numéricos (evita '37130507.0' → '37130507')
    columnas_id = ['numero_tramite', 'numero_solicitud', 'numero_servicio',
                   'cuenta_contrato', 'codigo_cliente', 'med_numero', 'med_serie']
    for col in columnas_id:
        if col in df_to_load.columns:
            df_to_load[col] = df_to_load[col].apply(formatear_identificador)
    
    # Conectar a SQLite y crear tabla si no existe
    conn = sqlite3.connect(DB_PATH)
    crear_tabla_si_no_existe(conn)
    
    # Obtener existentes
    cursor = conn.cursor()
    cursor.execute("SELECT numero_tramite FROM recorrido_cuadrillas")
    existentes = [row[0] for row in cursor.fetchall() if row[0] is not None]
    existentes_set = set(str(x) for x in existentes)
    
    # Clasificar
    df_nuevos = df_to_load[~df_to_load['numero_tramite'].isin(existentes_set)]
    df_existentes = df_to_load[df_to_load['numero_tramite'].isin(existentes_set)]
    
    print(f"   Registros en el archivo: {len(df_to_load)}")
    print(f"   Nuevos a insertar: {len(df_nuevos)}")
    print(f"   Existentes a actualizar: {len(df_existentes)}")
    
    # Insertar nuevos
    if not df_nuevos.empty:
        df_nuevos.to_sql('recorrido_cuadrillas', conn, if_exists='append', index=False)
        print(f"   ✅ Insertados {len(df_nuevos)} registros nuevos")
    
    # Actualizar existentes
    if not df_existentes.empty:
        registros = df_existentes.to_dict('records')
        columnas_update = [col for col in df_existentes.columns if col != 'numero_tramite']
        set_clause = ", ".join([f"{col} = ?" for col in columnas_update])
        sql_update = f"UPDATE recorrido_cuadrillas SET {set_clause} WHERE numero_tramite = ?"
        
        params_list = []
        for reg in registros:
            params = [reg[col] for col in columnas_update]
            params.append(reg['numero_tramite'])
            params_list.append(params)
        
        cursor.executemany(sql_update, params_list)
        conn.commit()
        print(f"   🔄 Actualizados {len(df_existentes)} registros existentes")
    
    conn.close()
    print(f"✅ Proceso completado: {len(df_nuevos)} insertados, {len(df_existentes)} actualizados")
    return len(df_nuevos), len(df_existentes)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        ruta = sys.argv[1]
    else:
        ruta = "H:\\Mi unidad\\CONSORCIO-ART\\ANALISIS\\Junio 2026\\Análisis de Recorrido de Cuadrillas - 1 al 7 junio 2026.xlsx"
    
    if os.path.exists(ruta):
        cargar_recorrido_upsert_conteo_sqlite(ruta)
    else:
        print(f"❌ Archivo no encontrado: {ruta}")
        print("\n💡 Uso: python cargar_recorrido_sqlite.py 'C:\\ruta\\a\\tu\\archivo.xlsx'")
