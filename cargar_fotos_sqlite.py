import pandas as pd
import sqlite3
import os
import sys
import numpy as np

# Configuración SQLite
DB_PATH = r"/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"

def crear_tabla_fotos_si_no_existe(conn):
    """Crea la tabla gestion_fotos si no existe"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gestion_fotos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_tramite INTEGER,
            gestion TEXT,
            codigo_cliente INTEGER,
            med_numero TEXT,
            med_serie INTEGER,
            numero_orden INTEGER,
            oficina_orden INTEGER,
            fecha_ejecucion TEXT,
            foto_predio TEXT,
            foto_medidor TEXT,
            foto_red_servicio TEXT,
            foto_ubic_med TEXT,
            foto_puesta_tierra TEXT,
            foto_sello_medidor TEXT,
            foto_antes_aper_med TEXT,
            foto_entrega_notif TEXT,
            foto_lectura_med_nuevo TEXT,
            foto_lectura_med_retirado TEXT
        )
    """)
    conn.commit()

def cargar_fotos_upsert_conteo_sqlite(ruta_archivo):
    print(f"📂 Procesando (SQLite): {ruta_archivo}")
    
    # Leer Excel
    df = pd.read_excel(ruta_archivo)
    df = df.replace({np.nan: None})
    
    # ========== RENOMBRAR COLUMNAS ==========
    df = df.rename(columns={
        # Metadatos
        'N° Trámite': 'numero_tramite',
        'Gestión': 'gestion',
        'Código Cliente': 'codigo_cliente',
        'Med. Numero': 'med_numero',
        'Med. Serie': 'med_serie',
        'Número Orden': 'numero_orden',
        'Oficina Orden': 'oficina_orden',
        'Fecha Ejecución': 'fecha_ejecucion',
        # Fotos (versión limpia .1 con URL directa)
        'Foto Predio.1': 'foto_predio',
        'Foto Medidor.1': 'foto_medidor',
        'Foto Red Serv.': 'foto_red_servicio',
        'Foto Ubic. Med..1': 'foto_ubic_med',
        'Foto Puesta Tierra.1': 'foto_puesta_tierra',
        'Foto Sello Medidor.1': 'foto_sello_medidor',
        'Foto Antes Aper. Med..1': 'foto_antes_aper_med',
        'Foto Entrega Notif..1': 'foto_entrega_notif',
        'Foto Lectura Med. Nuevo.1': 'foto_lectura_med_nuevo',
        'Foto Lectura Med. Retirado.1': 'foto_lectura_med_retirado',
        # Soportar también nombres sin .1 (formato HTML)
        'Foto Predio': 'foto_predio_html',
        'Foto Medidor': 'foto_medidor_html',
        'Foto Red Servicio': 'foto_red_servicio_html',
        'Foto Ubic. Med.': 'foto_ubic_med_html',
        'Foto Puesta Tierra': 'foto_puesta_tierra_html',
        'Foto Sello Medidor': 'foto_sello_medidor_html',
        'Foto Antes Aper. Med.': 'foto_antes_aper_med_html',
        'Foto Entrega Notif.': 'foto_entrega_notif_html',
        'Foto Lectura Med. Nuevo': 'foto_lectura_med_nuevo_html',
        'Foto Lectura Med. Retirado': 'foto_lectura_med_retirado_html'
    })
    
    # Convertir fecha formato dd/mm/aaaa HH:MM:SS a ISO
    if 'fecha_ejecucion' in df.columns:
        df['fecha_ejecucion'] = pd.to_datetime(df['fecha_ejecucion'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
        df['fecha_ejecucion'] = df['fecha_ejecucion'].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Columnas que debe tener la tabla
    columnas_tabla = [
        'numero_tramite', 'gestion', 'codigo_cliente', 'med_numero',
        'med_serie', 'numero_orden', 'oficina_orden', 'fecha_ejecucion',
        'foto_predio', 'foto_medidor', 'foto_red_servicio', 'foto_ubic_med',
        'foto_puesta_tierra', 'foto_sello_medidor', 'foto_antes_aper_med',
        'foto_entrega_notif', 'foto_lectura_med_nuevo', 'foto_lectura_med_retirado'
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
    
    # Conectar a SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Crear tabla si no existe
    crear_tabla_fotos_si_no_existe(conn)
    
    # Obtener números de trámite existentes
    cursor = conn.cursor()
    cursor.execute("SELECT numero_tramite FROM gestion_fotos")
    existentes = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
    existentes_set = set(existentes)
    
    df_nuevos = df_to_load[~df_to_load['numero_tramite'].isin(existentes_set)]
    df_existentes = df_to_load[df_to_load['numero_tramite'].isin(existentes_set)]
    
    print(f"   Registros en archivo: {len(df_to_load)}")
    print(f"   Nuevos a insertar: {len(df_nuevos)}")
    print(f"   Existentes a actualizar: {len(df_existentes)}")
    
    # Insertar nuevos
    if not df_nuevos.empty:
        df_nuevos.to_sql('gestion_fotos', conn, if_exists='append', index=False)
        print(f"   ✅ Insertados {len(df_nuevos)} registros nuevos")
    
    # Actualizar existentes
    if not df_existentes.empty:
        registros = df_existentes.to_dict('records')
        columnas_update = [col for col in df_existentes.columns if col != 'numero_tramite']
        set_clause = ", ".join([f"{col} = ?" for col in columnas_update])
        sql_update = f"UPDATE gestion_fotos SET {set_clause} WHERE numero_tramite = ?"
        
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
        ruta = "fotos_gestion.xlsx"
    
    if os.path.exists(ruta):
        cargar_fotos_upsert_conteo_sqlite(ruta)
    else:
        print(f"❌ Archivo no encontrado: {ruta}")
        print("\n💡 Uso: python cargar_fotos_sqlite.py 'ruta/al/archivo.xlsx'")
