import pandas as pd
import sqlite3
import os
import sys
import numpy as np

DB_PATH = r"/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db" 

def crear_tabla_materiales_si_no_existe(conn):
    """Crea la tabla materiales_tramite en SQLite si no existe"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS materiales_tramite (
            id_registro INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_tramite TEXT,
            numero_solicitud TEXT,
            numero_servicio TEXT,
            tipo_solicitud TEXT,
            descripcion_solicitud TEXT,
            id_cuadrilla TEXT,
            cuadrilla TEXT,
            fecha_ejecucion TEXT,
            fecha_sincronizacion TEXT,
            coordenada_x REAL,
            coordenada_y REAL,
            material TEXT,
            cantidad REAL,
            unidad TEXT,
            pro TEXT,
            can TEXT,
            pla INTEGER,
            sec INTEGER,
            rut INTEGER,
            secu INTEGER,
            cod_parroquia TEXT,
            zona TEXT,
            codigo_cliente TEXT,
            identificacion_cliente TEXT,
            cliente TEXT,
            tarifa TEXT,
            med_numero TEXT,
            med_serie TEXT,
            direccion TEXT
        )
    """)
    conn.commit()

def convertir_columnas_numericas(df, columnas):
    """Convierte columnas a tipo nullable Int64"""
    for col in columnas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].astype('Int64')
    return df

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

def cargar_materiales_upsert_conteo_sqlite(ruta_archivo):
    print(f"📂 Procesando (SQLite): {ruta_archivo}")
    
    df = pd.read_excel(ruta_archivo, sheet_name='AReports Data')
    df = df.replace({np.nan: None})
    
    # Renombrar columnas (soporte dual)
    df = df.rename(columns={
        'N° Trámite': 'numero_tramite',
        'Numero Orden': 'numero_solicitud',
        'Tipo Orden': 'tipo_solicitud',
        'Descripcion Orden': 'descripcion_solicitud',
        'Número Solicitud': 'numero_solicitud',
        'Tipo Solicitud': 'tipo_solicitud',
        'Descripción Solicitud': 'descripcion_solicitud',
        'Número Servicio': 'numero_servicio',
        'Id Cuadrilla': 'id_cuadrilla',
        'Cuadrilla': 'cuadrilla',
        'Fecha Ejecución': 'fecha_ejecucion',
        'Fecha Sincronización': 'fecha_sincronizacion',
        'Coordenada X': 'coordenada_x',
        'Coordenada Y': 'coordenada_y',
        'Material': 'material',
        'Cantidad': 'cantidad',
        'UN': 'unidad',
        'PRO': 'pro',
        'CAN': 'can',
        'PLA': 'pla',
        'SEC': 'sec',
        'RUT': 'rut',
        'SECU': 'secu',
        'Cód. Parroquia': 'cod_parroquia',
        'Zona': 'zona',
        'Código Cliente': 'codigo_cliente',
        'N° Identificación': 'identificacion_cliente',
        'Cliente': 'cliente',
        'Tarifa': 'tarifa',
        'Med. Numero': 'med_numero',
        'Med. Serie': 'med_serie',
        'Dirección': 'direccion'
    })
    
    # Convertir fechas a string ISO
    columnas_fecha = ['fecha_ejecucion', 'fecha_sincronizacion']
    for col in columnas_fecha:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format='%d/%m/%Y %H:%M:%S', errors='coerce')
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Columnas necesarias
    columnas_tabla = [
        'numero_tramite', 'numero_solicitud', 'numero_servicio',
        'tipo_solicitud', 'descripcion_solicitud',
        'id_cuadrilla', 'cuadrilla',
        'fecha_ejecucion', 'fecha_sincronizacion',
        'coordenada_x', 'coordenada_y',
        'material', 'cantidad',
        'unidad', 'pro', 'can', 'pla', 'sec', 'rut', 'secu',
        'cod_parroquia', 'zona', 'codigo_cliente', 'identificacion_cliente',
        'cliente', 'tarifa', 'med_numero', 'med_serie', 'direccion'
    ]
    
    columnas_existentes = [col for col in columnas_tabla if col in df.columns]
    df_to_load = df[columnas_existentes].copy()
    
    # Limpiar numero_tramite
    if 'numero_tramite' in df_to_load.columns:
        df_to_load = df_to_load.dropna(subset=['numero_tramite'])
        df_to_load = df_to_load[df_to_load['numero_tramite'] != 0]
        df_to_load = df_to_load[df_to_load['numero_tramite'] != '']
        df_to_load['numero_tramite'] = df_to_load['numero_tramite'].astype(str).str.strip()
        df_to_load = df_to_load[df_to_load['numero_tramite'] != '']
    
    # Convertir columnas numéricas a Int64 nullable
    columnas_numericas = ['pla', 'sec', 'rut', 'secu']
    df_to_load = convertir_columnas_numericas(df_to_load, columnas_numericas)
    
    # Convertir cantidad a float (puede tener decimales)
    if 'cantidad' in df_to_load.columns:
        df_to_load['cantidad'] = pd.to_numeric(df_to_load['cantidad'], errors='coerce')
    
    # Normalizar identificadores numéricos (evita '37130507.0' → '37130507')
    columnas_id = ['numero_tramite', 'numero_solicitud', 'numero_servicio',
                   'codigo_cliente', 'med_numero', 'med_serie']
    for col in columnas_id:
        if col in df_to_load.columns:
            df_to_load[col] = df_to_load[col].apply(formatear_identificador)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    crear_tabla_materiales_si_no_existe(conn)
    
    cursor = conn.cursor()
    cursor.execute("SELECT numero_tramite FROM materiales_tramite")
    existentes = [row[0] for row in cursor.fetchall() if row[0] is not None]
    existentes_set = set(str(x) for x in existentes)
    
    df_nuevos = df_to_load[~df_to_load['numero_tramite'].isin(existentes_set)]
    df_existentes = df_to_load[df_to_load['numero_tramite'].isin(existentes_set)]
    
    print(f"   Registros en archivo: {len(df_to_load)}")
    print(f"   Nuevos a insertar: {len(df_nuevos)}")
    print(f"   Existentes a actualizar: {len(df_existentes)}")
    
    if not df_nuevos.empty:
        df_nuevos.to_sql('materiales_tramite', conn, if_exists='append', index=False)
        print(f"   ✅ Insertados {len(df_nuevos)}")
    
    if not df_existentes.empty:
        registros = df_existentes.to_dict('records')
        columnas_update = [col for col in df_existentes.columns if col != 'numero_tramite']
        set_clause = ", ".join([f"{col} = ?" for col in columnas_update])
        sql_update = f"UPDATE materiales_tramite SET {set_clause} WHERE numero_tramite = ?"
        
        params_list = []
        for reg in registros:
            params = [reg[col] for col in columnas_update]
            params.append(reg['numero_tramite'])
            params_list.append(params)
        
        cursor.executemany(sql_update, params_list)
        conn.commit()
        print(f"   🔄 Actualizados {len(df_existentes)}")
    
    conn.close()
    print(f"✅ Proceso completado: {len(df_nuevos)} insertados, {len(df_existentes)} actualizados")
    return len(df_nuevos), len(df_existentes)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        ruta = sys.argv[1]
    else:
        ruta = "H:\\Mi unidad\\CONSORCIO-ART\\ANALISIS\\Junio 2026\\Resumen Material Retirado - 1 al 7 junio 2026.xlsx"
    
    if os.path.exists(ruta):
        cargar_materiales_upsert_conteo_sqlite(ruta)
    else:
        print(f"❌ Archivo no encontrado: {ruta}")
        print("\n💡 Uso: python cargar_materiales_sqlite.py 'C:\\ruta\\a\\tu\\archivo.xlsx'")
