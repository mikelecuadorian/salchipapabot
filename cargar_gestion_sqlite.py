import pandas as pd
import sqlite3
import os
import sys
import numpy as np

# Configuración SQLite
DB_PATH = r"/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"   # Ajusta la ruta si es necesario

def crear_tabla_gestion_si_no_existe(conn):
    """Crea la tabla gestion_tramites si no existe, con los tipos compatibles con SQLite"""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gestion_tramites (
            id_gestion INTEGER PRIMARY KEY AUTOINCREMENT,
            numero_tramite TEXT,
            fecha_solicitud TEXT,
            oficina_solicitud TEXT,
            numero_solicitud TEXT,
            estado TEXT,
            tipo_solicitud TEXT,
            id_cuadrilla TEXT,
            cuadrilla TEXT,
            fecha_analisis TEXT,
            fecha_ejecucion TEXT,
            fecha_sincronizacion TEXT,
            dias_transcurridos INTEGER,
            coordenada_x REAL,
            coordenada_y REAL,
            observacion_gestion TEXT,
            insp_tarifa TEXT,
            condiciones_tecnicas TEXT,
            se_posterga INTEGER,
            posible_manipulacion INTEGER,
            existe_transferencia INTEGER,
            caja_distrib INTEGER,
            med_en_sitio INTEGER,
            cuarto_transf INTEGER,
            motivo_no_ejecucion TEXT,
            detalle_no_ejecucion TEXT,
            estado_instalacion TEXT,
            tipo_construccion TEXT,
            t_origen TEXT,
            coordenada_y_cnel REAL,
            coordenada_x_cnel REAL,
            unidad TEXT,
            pro TEXT,
            can TEXT,
            sec INTEGER,
            rut INTEGER,
            secu INTEGER,
            cod_parroquia TEXT,
            parroquia TEXT,
            zona TEXT,
            mru TEXT,
            cuenta_contrato TEXT,
            codigo_cliente TEXT,
            identificacion_cliente TEXT,
            cliente TEXT,
            tarifa TEXT,
            med_numero TEXT,
            med_serie TEXT,
            direccion TEXT,
            correo_cliente TEXT,
            telefono_cliente TEXT,
            tubo_poste_cantidad INTEGER,
            const_colum TEXT,
            inst_int_tab TEXT,
            inst_int_cent_c TEXT,
            insp_tipo_acometida TEXT,
            tipo_acometida TEXT,
            acometida_mts REAL,
            alimentador_salida TEXT,
            alimentador_salida_mts REAL,
            caja_dist_sello TEXT,
            caja_dist_tipo_aco TEXT,
            caja_dist_mts REAL,
            sello_caja_prot_u TEXT,
            sello_cuarto_transf_u TEXT,
            sello_caja_prot_1_tipo TEXT,
            sello_caja_prot_1 TEXT,
            sello_caja_prot_2_tipo TEXT,
            sello_caja_prot_2 TEXT,
            sello_camara_tipo TEXT,
            sello_camara TEXT,
            sello_tapa_breaker TEXT,
            sello_tapa_vidrio TEXT,
            tipo_medidor TEXT,
            caja_medidor TEXT,
            cant_caja INTEGER,
            medidor_cont_1 TEXT,
            medidor_cont_2 TEXT,
            ubicacion_medidor TEXT,
            tipo_servicio TEXT,
            uso_servicio TEXT,
            med_nue_num TEXT,
            med_nue_ser TEXT,
            med_nue_tipo TEXT,
            med_nue_caja TEXT,
            med_nue_cant_caja INTEGER,
            estado_caja TEXT,
            med_nue_marca TEXT,
            med_nue_lec REAL,
            med_nue_cont1 TEXT,
            med_nue_cont2 TEXT,
            med_nue_clase TEXT,
            med_nue_termin TEXT,
            conexion_dir INTEGER,
            med_ret_num TEXT,
            med_ret_ser TEXT,
            med_ret_marca TEXT,
            med_ret_tipo TEXT,
            med_ret_caja TEXT,
            med_ret_cant_caja INTEGER,
            med_ret_cont1 TEXT,
            med_ret_cont2 TEXT,
            med_ret_sello_bornera TEXT,
            med_ret_sello_caja TEXT,
            med_ret_lec REAL,
            med_ret_clase TEXT,
            med_ret_termin TEXT,
            cinta_aislante INTEGER,
            breaker_u INTEGER,
            breaker_cap TEXT,
            grapa_2 INTEGER,
            grapa_5 INTEGER,
            grapa_6 INTEGER,
            conector_puerto_gel INTEGER,
            conector_tipo_barraje INTEGER,
            kit_conector_ranuras_u INTEGER,
            kit_separador_u INTEGER,
            kit_conector_estanco_u INTEGER,
            kit_cartucho_fusible_u INTEGER,
            kit_porta_fusible_u INTEGER,
            kit_derivador_term_u INTEGER,
            kit_mensula_poste_u INTEGER,
            kit_mensula_fachada_u INTEGER,
            kit_pinza_termoplasticas_u INTEGER,
            kit_precintos_plasticos_u INTEGER,
            pt_varilla_u INTEGER,
            pt_conector_varilla_u INTEGER,
            pt_cable_cobre_u INTEGER,
            pt_tubo_pvc_u INTEGER,
            pt_conector_tubo_u INTEGER,
            pt_grapas_emt_u INTEGER,
            pt_taco_f6_u INTEGER,
            pt_tornillo_taco_f6_u INTEGER,
            pt_canaleta_u INTEGER,
            n_sticker INTEGER,
            nota_materiales TEXT
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

def convertir_columnas_numericas(df, columnas):
    """Convierte columnas a tipo nullable Int64 (soporta NaN/None)"""
    for col in columnas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].astype('Int64')
    return df

def cargar_gestion_upsert_conteo_sqlite(ruta_archivo):
    print(f"📂 Procesando (SQLite): {ruta_archivo}")
    
    # Leer Excel
    df = pd.read_excel(ruta_archivo, sheet_name='AReports Data')
    df = df.replace({np.nan: None})
    
    # ========== RENOMBRAR COLUMNAS (soporta nombres antiguos y nuevos) ==========
    df = df.rename(columns={
        # TRÁMITE
        'N° Trámite': 'numero_tramite',
        'Fecha Orden': 'fecha_solicitud',
        'Fecha Solicitud': 'fecha_solicitud',
        'Oficina Orden': 'oficina_solicitud',
        'Oficina Solicitud': 'oficina_solicitud',
        'Número Orden': 'numero_solicitud',
        'Número Solicitud': 'numero_solicitud',
        'Estado': 'estado',
        'Tipo Orden': 'tipo_solicitud',
        'Tipo Solicitud': 'tipo_solicitud',
        'Id Cuadrilla': 'id_cuadrilla',
        'Cuadrilla': 'cuadrilla',
        'Fecha Análisis': 'fecha_analisis',
        'Fecha Ejecución': 'fecha_ejecucion',
        'Fecha Sincronización': 'fecha_sincronizacion',
        'Días Transcurridos': 'dias_transcurridos',
        'Coordenada X': 'coordenada_x',
        'Coordenada Y': 'coordenada_y',
        'Observación Gestión': 'observacion_gestion',
        'Insp. Tarifa': 'insp_tarifa',
        'Condiciones Técnicas': 'condiciones_tecnicas',
        'Se Posterga': 'se_posterga',
        'Motivo No Ejecución': 'motivo_no_ejecucion',
        'Detalle No Ejecución': 'detalle_no_ejecucion',
        'Posible Manip.': 'posible_manipulacion',
        'Existe Transf.': 'existe_transferencia',
        'Caja Distrib.': 'caja_distrib',
        'Med En Sitio': 'med_en_sitio',
        'Cuarto Transf.': 'cuarto_transf',
        'Estado Inst.': 'estado_instalacion',
        'Tipo Construcción': 'tipo_construccion',
        'T. Origen': 't_origen',
        'CoordenadaYCNEL': 'coordenada_y_cnel',
        'CoordenadaXCNEL': 'coordenada_x_cnel',
        # CLIENTE
        'UN': 'unidad',
        'PRO': 'pro',
        'CAN': 'can',
        'SEC': 'sec',
        'RUT': 'rut',
        'SECU': 'secu',
        'Cód. Parroquia': 'cod_parroquia',
        'Parroquia': 'parroquia',
        'Zona': 'zona',
        'MRU': 'mru',
        'Cuenta Contrato': 'cuenta_contrato',
        'Clase Orden': 'codigo_cliente',
        #'Cod. Motivo Orden': 'codigo_cliente',
        #'Código Cliente': 'codigo_cliente',
        'N° Identificación': 'identificacion_cliente',
        'Cliente': 'cliente',
        'Tarifa': 'tarifa',
        'Med. Numero': 'med_numero',
        'Med. Serie': 'med_serie',
        'Dirección': 'direccion',
        'Correo Cliente': 'correo_cliente',
        'Teléfono Cliente': 'telefono_cliente',
        # MATERIALES (resumen)
        'Tubo Poste Cantidad': 'tubo_poste_cantidad',
        'Const. Colum.': 'const_colum',
        'Inst. Int. Tab.': 'inst_int_tab',
        'Inst. Int. Cent. C.': 'inst_int_cent_c',
        'Insp. Tipo Acometida': 'insp_tipo_acometida',
        'Tipo Acometida': 'tipo_acometida',
        'Acometida Mts.': 'acometida_mts',
        'Alimentador Salida': 'alimentador_salida',
        'Alimentador Salida Mts.': 'alimentador_salida_mts',
        'Caja Dist. Sello': 'caja_dist_sello',
        'Caja Dist. Tipo Aco.': 'caja_dist_tipo_aco',
        'Caja Dist. Mts.': 'caja_dist_mts',
        'Sello Caja Prot. U': 'sello_caja_prot_u',
        'Sello Cuarto Transf. U': 'sello_cuarto_transf_u',
        'Sello Caja Prot. 1 Tipo': 'sello_caja_prot_1_tipo',
        'Sello Caja Prot. 1': 'sello_caja_prot_1',
        'Sello Caja Prot. 2 Tipo': 'sello_caja_prot_2_tipo',
        'Sello Caja Prot. 2': 'sello_caja_prot_2',
        'Sello Cámara Tipo': 'sello_camara_tipo',
        'Sello Cámara': 'sello_camara',
        'Sello Tapa Breaker': 'sello_tapa_breaker',
        'Sello Tapa Vidrio': 'sello_tapa_vidrio',
        'Tipo Medidor': 'tipo_medidor',
        'Caja Medidor': 'caja_medidor',
        'Cant. Caja': 'cant_caja',
        'Medidor Cont. 1': 'medidor_cont_1',
        'Meddior Cont. 2': 'medidor_cont_2',
        'Ubicacipon Medidor': 'ubicacion_medidor',
        'Tipo Servicio': 'tipo_servicio',
        'Uso Servicio': 'uso_servicio',
        'Med. Nue. Num.': 'med_nue_num',
        'Med. Nue. Ser.': 'med_nue_ser',
        'Med. Nue. Tipo': 'med_nue_tipo',
        'Med. Nue. Caja': 'med_nue_caja',
        'Med. Nue. Cant. Caja': 'med_nue_cant_caja',
        'Estado Caja': 'estado_caja',
        'Med. Nue. Marca': 'med_nue_marca',
        'Med. Nue. Lec.': 'med_nue_lec',
        'Med. Nue. Cont. 1': 'med_nue_cont1',
        'Med. Nue. Cont. 2': 'med_nue_cont2',
        'Med. Nue. Clase': 'med_nue_clase',
        'Med. Nue. Termin.': 'med_nue_termin',
        'Conexión Dir.': 'conexion_dir',
        'Med. Ret. Num.': 'med_ret_num',
        'Med. Ret. Ser': 'med_ret_ser',
        'Med. Ret Marca': 'med_ret_marca',
        'Med. Ret. Tipo': 'med_ret_tipo',
        'Med. Ret. Caja': 'med_ret_caja',
        'Med. Ret. Cant. Caja': 'med_ret_cant_caja',
        'Med. Ret. Cont.1': 'med_ret_cont1',
        'Med. Ret. Cont.2': 'med_ret_cont2',
        'Med. Ret. Sello Bornera': 'med_ret_sello_bornera',
        'Med. Ret. Sello Caja': 'med_ret_sello_caja',
        'Med. Ret. Lec.': 'med_ret_lec',
        'Med. Ret. Clase': 'med_ret_clase',
        'Med. Ret. Termin.': 'med_ret_termin',
        'Cinta Aislante': 'cinta_aislante',
        'Breaker U.': 'breaker_u',
        'Breaker Cap.': 'breaker_cap',
        'Grapa #2': 'grapa_2',
        'Grapa #5': 'grapa_5',
        'Grapa #6': 'grapa_6',
        'Conector Puerto Gel': 'conector_puerto_gel',
        'Conector Tipo Barraje': 'conector_tipo_barraje',
        'Kit Conector Ranuras U': 'kit_conector_ranuras_u',
        'Kit Separador U': 'kit_separador_u',
        'Kit Conector Estanco U': 'kit_conector_estanco_u',
        'Kit Cartucho Fusible U': 'kit_cartucho_fusible_u',
        'Kit Porta Fusible U': 'kit_porta_fusible_u',
        'Kit Derivador Term. U': 'kit_derivador_term_u',
        'Kit Ménsula de Poste U': 'kit_mensula_poste_u',
        'Kit Ménsula Fachada U': 'kit_mensula_fachada_u',
        'Kit Pinza Termoplásticas U': 'kit_pinza_termoplasticas_u',
        'Kit Precintos Plásticos U': 'kit_precintos_plasticos_u',
        'PT Varilla U': 'pt_varilla_u',
        'PT Conector Varilla U': 'pt_conector_varilla_u',
        'PT Cable Cobre U': 'pt_cable_cobre_u',
        'PT Tubo PVC U': 'pt_tubo_pvc_u',
        'PT Conector Tubo U': 'pt_conector_tubo_u',
        'PT Grapas EMT U': 'pt_grapas_emt_u',
        'PT Taco F6 U': 'pt_taco_f6_u',
        'PT Tornillo Taco F6 U': 'pt_tornillo_taco_f6_u',
        'PT Cnaleta U': 'pt_canaleta_u',
        'N. Sticker': 'n_sticker',
        'Nota Materiales': 'nota_materiales'
    })
    
    # Convertir fechas (formato dd/mm/aaaa HH:MM:SS) a string ISO para SQLite
    columnas_fecha = ['fecha_solicitud', 'fecha_analisis', 'fecha_ejecucion', 'fecha_sincronizacion']
    for col in columnas_fecha:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format='%d/%m/%Y %H:%M:%S', errors='coerce')
            df[col] = df[col].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    # Convertir booleanos a 0/1
    campos_booleanos = ['se_posterga', 'posible_manipulacion', 'existe_transferencia',
                        'caja_distrib', 'med_en_sitio', 'cuarto_transf', 'conexion_dir']
    for campo in campos_booleanos:
        if campo in df.columns:
            df[campo] = df[campo].map({'Si': True, 'Sí': True, 'No': False}).astype('Int64')
    
    # Columnas que debe tener la tabla
    columnas_tabla = [
        'numero_tramite', 'fecha_solicitud', 'oficina_solicitud', 'numero_solicitud',
        'estado', 'tipo_solicitud', 'id_cuadrilla', 'cuadrilla', 'fecha_analisis',
        'fecha_ejecucion', 'fecha_sincronizacion', 'dias_transcurridos', 'coordenada_x',
        'coordenada_y', 'observacion_gestion', 'insp_tarifa', 'condiciones_tecnicas',
        'se_posterga', 'motivo_no_ejecucion', 'detalle_no_ejecucion', 'posible_manipulacion',
        'existe_transferencia', 'caja_distrib', 'med_en_sitio', 'cuarto_transf',
        'estado_instalacion', 'tipo_construccion', 't_origen', 'coordenada_y_cnel',
        'coordenada_x_cnel', 'unidad', 'pro', 'can', 'sec', 'rut', 'secu', 'cod_parroquia',
        'parroquia', 'zona', 'mru', 'cuenta_contrato', 'codigo_cliente', 'identificacion_cliente',
        'cliente', 'tarifa', 'med_numero', 'med_serie', 'direccion', 'correo_cliente',
        'telefono_cliente', 'tubo_poste_cantidad', 'const_colum', 'inst_int_tab', 'inst_int_cent_c',
        'insp_tipo_acometida', 'tipo_acometida', 'acometida_mts', 'alimentador_salida',
        'alimentador_salida_mts', 'caja_dist_sello', 'caja_dist_tipo_aco', 'caja_dist_mts',
        'sello_caja_prot_u', 'sello_cuarto_transf_u', 'sello_caja_prot_1_tipo', 'sello_caja_prot_1',
        'sello_caja_prot_2_tipo', 'sello_caja_prot_2', 'sello_camara_tipo', 'sello_camara',
        'sello_tapa_breaker', 'sello_tapa_vidrio', 'tipo_medidor', 'caja_medidor', 'cant_caja',
        'medidor_cont_1', 'medidor_cont_2', 'ubicacion_medidor', 'tipo_servicio', 'uso_servicio',
        'med_nue_num', 'med_nue_ser', 'med_nue_tipo', 'med_nue_caja', 'med_nue_cant_caja',
        'estado_caja', 'med_nue_marca', 'med_nue_lec', 'med_nue_cont1', 'med_nue_cont2',
        'med_nue_clase', 'med_nue_termin', 'conexion_dir', 'med_ret_num', 'med_ret_ser',
        'med_ret_marca', 'med_ret_tipo', 'med_ret_caja', 'med_ret_cant_caja', 'med_ret_cont1',
        'med_ret_cont2', 'med_ret_sello_bornera', 'med_ret_sello_caja', 'med_ret_lec',
        'med_ret_clase', 'med_ret_termin', 'cinta_aislante', 'breaker_u', 'breaker_cap',
        'grapa_2', 'grapa_5', 'grapa_6', 'conector_puerto_gel', 'conector_tipo_barraje',
        'kit_conector_ranuras_u', 'kit_separador_u', 'kit_conector_estanco_u',
        'kit_cartucho_fusible_u', 'kit_porta_fusible_u', 'kit_derivador_term_u',
        'kit_mensula_poste_u', 'kit_mensula_fachada_u', 'kit_pinza_termoplasticas_u',
        'kit_precintos_plasticos_u', 'pt_varilla_u', 'pt_conector_varilla_u',
        'pt_cable_cobre_u', 'pt_tubo_pvc_u', 'pt_conector_tubo_u', 'pt_grapas_emt_u',
        'pt_taco_f6_u', 'pt_tornillo_taco_f6_u', 'pt_canaleta_u', 'n_sticker', 'nota_materiales'
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
    
    # Limpiar codigo_cliente
    if 'codigo_cliente' in df_to_load.columns:
        df_to_load['codigo_cliente'] = df_to_load['codigo_cliente'].astype(str).str.strip()
        df_to_load['codigo_cliente'] = df_to_load['codigo_cliente'].replace('nan', None)
    
    # Convertir columnas numéricas a Int64 nullable
    columnas_numericas = [
        'dias_transcurridos', 'sec', 'rut', 'secu', 'tubo_poste_cantidad', 'cant_caja',
        'med_nue_cant_caja', 'med_ret_cant_caja', 'cinta_aislante', 'breaker_u',
        'grapa_2', 'grapa_5', 'grapa_6', 'conector_puerto_gel', 'conector_tipo_barraje',
        'kit_conector_ranuras_u', 'kit_separador_u', 'kit_conector_estanco_u',
        'kit_cartucho_fusible_u', 'kit_porta_fusible_u', 'kit_derivador_term_u',
        'kit_mensula_poste_u', 'kit_mensula_fachada_u', 'kit_pinza_termoplasticas_u',
        'kit_precintos_plasticos_u', 'pt_varilla_u', 'pt_conector_varilla_u',
        'pt_cable_cobre_u', 'pt_tubo_pvc_u', 'pt_conector_tubo_u', 'pt_grapas_emt_u',
        'pt_taco_f6_u', 'pt_tornillo_taco_f6_u', 'pt_canaleta_u', 'n_sticker'
    ]
    df_to_load = convertir_columnas_numericas(df_to_load, columnas_numericas)
    
    # Normalizar identificadores numéricos (evita '37130507.0' → '37130507')
    columnas_id = ['numero_tramite', 'numero_solicitud', 'cuenta_contrato', 'codigo_cliente',
                   'med_numero', 'med_serie', 'medidor_cont_1', 'medidor_cont_2',
                   'med_nue_num', 'med_nue_ser', 'med_nue_cont1', 'med_nue_cont2',
                   'med_ret_num', 'med_ret_ser', 'med_ret_cont1', 'med_ret_cont2']
    for col in columnas_id:
        if col in df_to_load.columns:
            df_to_load[col] = df_to_load[col].apply(formatear_identificador)
    
    # Conectar a SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Crear tabla si no existe
    crear_tabla_gestion_si_no_existe(conn)
    
    # Obtener números de trámite existentes
    cursor = conn.cursor()
    cursor.execute("SELECT numero_tramite FROM gestion_tramites")
    existentes = [row[0] for row in cursor.fetchall() if row[0] is not None]
    existentes_set = set(str(x) for x in existentes)
    
    df_nuevos = df_to_load[~df_to_load['numero_tramite'].isin(existentes_set)]
    df_existentes = df_to_load[df_to_load['numero_tramite'].isin(existentes_set)]
    
    print(f"   Registros en archivo: {len(df_to_load)}")
    print(f"   Nuevos a insertar: {len(df_nuevos)}")
    print(f"   Existentes a actualizar: {len(df_existentes)}")
    
    # Insertar nuevos
    if not df_nuevos.empty:
        df_nuevos.to_sql('gestion_tramites', conn, if_exists='append', index=False)
        print(f"   ✅ Insertados {len(df_nuevos)} registros nuevos")
    
    # Actualizar existentes con executemany
    if not df_existentes.empty:
        registros = df_existentes.to_dict('records')
        columnas_update = [col for col in df_existentes.columns if col != 'numero_tramite']
        set_clause = ", ".join([f"{col} = ?" for col in columnas_update])
        sql_update = f"UPDATE gestion_tramites SET {set_clause} WHERE numero_tramite = ?"
        
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
        ruta = "H:\\Mi unidad\\CONSORCIO-ART\\ANALISIS\\Junio 2026\\Resumen Gestión - 1 al 7 junio 2026.xlsx"
    
    if os.path.exists(ruta):
        cargar_gestion_upsert_conteo_sqlite(ruta)
    else:
        print(f"❌ Archivo no encontrado: {ruta}")
        print("\n💡 Uso: python cargar_gestion_sqlite.py 'C:\\ruta\\a\\tu\\archivo.xlsx'")
