import sqlite3
import pandas as pd
import os
import sys
import argparse

# ============================================
# CONFIGURACIÓN
# ============================================
DB_PATH = r"/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
TABLA_MEDIDORES = "medidores"

MODO_POR_DEFECTO = 2
# ============================================

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

def conectar_sqlite():
    """Conectar a SQLite"""
    return sqlite3.connect(DB_PATH)

def crear_tabla_si_no_existe(cur):
    """Crear tabla de medidores si no existe con PRIMARY KEY"""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLA_MEDIDORES} (
            nro_serie TEXT PRIMARY KEY,
            cuadrilla_nombre TEXT,
            nro_tramite TEXT
        )
    """)

def cargar_medidores_sqlite(excel_path, modo=MODO_POR_DEFECTO):
    """Carga medidores desde Excel a SQLite"""
    
    # 1. Verificar archivo Excel
    if not os.path.exists(excel_path):
        print(f"❌ Archivo no encontrado: {excel_path}")
        return False
    
    # 2. Leer Excel
    print(f"📖 Leyendo archivo Excel: {os.path.basename(excel_path)}")
    try:
        df = pd.read_excel(excel_path, sheet_name=0,
                           usecols=['nro_serie', 'cuadrilla_nombre', 'nro_tramite'])
    except Exception as e:
        print(f"❌ Error leyendo Excel: {e}")
        return False
    
    # 3. Limpiar datos
    df['nro_serie'] = df['nro_serie'].apply(formatear_identificador)
    df['cuadrilla_nombre'] = df['cuadrilla_nombre'].astype(str).str.strip()
    df['nro_tramite'] = df['nro_tramite'].apply(formatear_identificador)
    df = df.dropna(subset=['nro_serie'])
    df = df[df['nro_serie'] != '']
    df = df[df['nro_serie'] != 'nan']
    
    print(f"✅ Leídos {len(df)} registros del Excel")
    
    # 4. Conectar a SQLite
    print(f"🔌 Conectando a SQLite: {DB_PATH}")
    conn = conectar_sqlite()
    cursor = conn.cursor()
    
    # 5. Crear tabla si no existe (con PRIMARY KEY)
    crear_tabla_si_no_existe(cursor)
    conn.commit()
    
    # 6. Contar registros antes
    cursor.execute(f"SELECT COUNT(*) FROM {TABLA_MEDIDORES}")
    antes = cursor.fetchone()[0]
    print(f"\n📊 Registros ANTES: {antes}")
    
    # ==========================================
    # MODO 3: BORRAR TODO Y REPLANTEAR
    # ==========================================
    if modo == 3:
        print("🗑️  Modo: BORRAR TODO y reemplazar con Excel")
        cursor.execute(f"DELETE FROM {TABLA_MEDIDORES}")
        df.to_sql(TABLA_MEDIDORES, conn, if_exists='append', index=False)
        conn.commit()
        
        cursor.execute(f"SELECT COUNT(*) FROM {TABLA_MEDIDORES}")
        despues = cursor.fetchone()[0]
        print(f"✅ Reemplazado. Registros AHORA: {despues}")
    
    # ==========================================
    # MODO 1: SOLO AGREGAR
    # ==========================================
    elif modo == 1:
        print("➕ Modo: SOLO AGREGAR (mantiene existentes)")
        
        # Obtener series existentes
        cursor.execute(f"SELECT nro_serie FROM {TABLA_MEDIDORES}")
        existentes = set(row[0] for row in cursor.fetchall())
        
        # Filtrar nuevos
        df_nuevos = df[~df['nro_serie'].isin(existentes)]
        print(f"   🆕 Nuevos a insertar: {len(df_nuevos)}")
        print(f"   📌 Existentes que se mantienen: {len(existentes)}")
        
        if len(df_nuevos) > 0:
            df_nuevos.to_sql(TABLA_MEDIDORES, conn, if_exists='append', index=False)
            conn.commit()
        
        cursor.execute(f"SELECT COUNT(*) FROM {TABLA_MEDIDORES}")
        despues = cursor.fetchone()[0]
        print(f"✅ Finalizado. Registros AHORA: {despues}")
    
    # ==========================================
    # MODO 2: ACTUALIZAR + AGREGAR
    # ==========================================
    elif modo == 2:
        print("🔄 Modo: ACTUALIZAR existentes + AGREGAR nuevos")
        
        nuevos = 0
        actualizados = 0
        
        # Obtener todos los registros existentes
        cursor.execute(f"SELECT nro_serie, cuadrilla_nombre, nro_tramite FROM {TABLA_MEDIDORES}")
        existentes = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        
        # Separar entre actualizar e insertar
        actualizar_lista = []
        insertar_lista = []
        
        for _, row in df.iterrows():
            nro_serie = row['nro_serie']
            cuadrilla = row['cuadrilla_nombre']
            nro_tramite = row['nro_tramite']
            
            if nro_serie in existentes:
                # Verificar si cambió
                if existentes[nro_serie] != (cuadrilla, nro_tramite):
                    actualizar_lista.append((cuadrilla, nro_tramite, nro_serie))
                    actualizados += 1
            else:
                insertar_lista.append((nro_serie, cuadrilla, nro_tramite))
                nuevos += 1
        
        # Insertar nuevos
        if insertar_lista:
            cursor.executemany(f"""
                INSERT INTO {TABLA_MEDIDORES} (nro_serie, cuadrilla_nombre, nro_tramite)
                VALUES (?, ?, ?)
            """, insertar_lista)
            print(f"   🆕 Insertados: {len(insertar_lista)}")
        
        # Actualizar existentes
        if actualizar_lista:
            cursor.executemany(f"""
                UPDATE {TABLA_MEDIDORES} 
                SET cuadrilla_nombre = ?, nro_tramite = ?
                WHERE nro_serie = ?
            """, actualizar_lista)
            print(f"   ✅ Actualizados: {len(actualizar_lista)}")
        
        conn.commit()
        
        cursor.execute(f"SELECT COUNT(*) FROM {TABLA_MEDIDORES}")
        despues = cursor.fetchone()[0]
        
        print(f"\n   📊 Registros AHORA: {despues}")
        print(f"   📈 Diferencia: +{despues - antes}")
    
    conn.close()
    
    print(f"\n📋 Resumen:")
    print(f"   - Archivo: {os.path.basename(excel_path)}")
    print(f"   - Antes: {antes}")
    print(f"   - Después: {despues}")
    print(f"   - Diferencia: +{despues - antes}")
    
    return True

# ========== FUNCIONES ADICIONALES ==========
def verificar_medidor(nro_serie=None):
    """Verificar un medidor específico"""
    conn = conectar_sqlite()
    cursor = conn.cursor()
    
    if nro_serie:
        cursor.execute(f"SELECT * FROM {TABLA_MEDIDORES} WHERE nro_serie = ?", (nro_serie,))
    else:
        cursor.execute(f"SELECT * FROM {TABLA_MEDIDORES} LIMIT 5")
    
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def mostrar_estadisticas():
    """Mostrar estadísticas de la tabla"""
    conn = conectar_sqlite()
    cursor = conn.cursor()
    
    cursor.execute(f"SELECT COUNT(*) FROM {TABLA_MEDIDORES}")
    total = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT COUNT(DISTINCT cuadrilla_nombre) FROM {TABLA_MEDIDORES}")
    cuadrillas = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"\n📊 ESTADÍSTICAS:")
    print(f"   - Total medidores: {total}")
    print(f"   - Cuadrillas distintas: {cuadrillas}")

# ========== PARSEO DE ARGUMENTOS ==========
def main():
    parser = argparse.ArgumentParser(
        description='Cargar medidores desde Excel a SQLite',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  python cargar_medidores_sqlite.py Medidores3.xlsx
  python cargar_medidores_sqlite.py Medidores3.xlsx --modo 1
  python cargar_medidores_sqlite.py Medidores3.xlsx --modo 2
  python cargar_medidores_sqlite.py Medidores3.xlsx --modo 3
  python cargar_medidores_sqlite.py Medidores3.xlsx --modo 2 --stats

Modos disponibles:
  1 - SOLO AGREGAR (no borra, solo inserta nuevos)
  2 - ACTUALIZAR + AGREGAR (recomendado)
  3 - BORRAR TODO Y REPLANTEAR
        """
    )
    
    parser.add_argument('archivo', help='Ruta del archivo Excel a procesar')
    parser.add_argument('--modo', '-m', type=int, choices=[1, 2, 3],
                        default=MODO_POR_DEFECTO, help='Modo de carga')
    parser.add_argument('--stats', '-s', action='store_true',
                        help='Mostrar estadísticas después de la carga')
    
    args = parser.parse_args()
    
    # Mostrar configuración
    print("=" * 50)
    print("🔄 CARGADOR DE MEDIDORES - SQLITE")
    print("=" * 50)
    
    modo_texto = {
        1: "SOLO AGREGAR (no borra nada)",
        2: "ACTUALIZAR + AGREGAR",
        3: "BORRAR TODO Y REPLANTEAR"
    }
    print(f"📌 Archivo: {args.archivo}")
    print(f"📌 Modo: {modo_texto.get(args.modo, 'Desconocido')}")
    print(f"📌 Base de datos: {DB_PATH}")
    print("=" * 50)
    
    # Ejecutar carga
    exito = cargar_medidores_sqlite(args.archivo, args.modo)
    
    if exito:
        print("\n✅ PROCESO COMPLETADO CON ÉXITO")
        
        # Mostrar estadísticas si se solicita
        if args.stats:
            mostrar_estadisticas()
    else:
        print("\n❌ ERROR EN EL PROCESO")
        sys.exit(1)

if __name__ == "__main__":
    main()
