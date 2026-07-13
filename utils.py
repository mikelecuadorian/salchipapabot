# utils.py
# =====================================================
# FUNCIONES AUXILIARES (LOGS, LIMPIEZA, ETC.)
# =====================================================

import datetime
import os
import gzip
from config import LOG_PATH, LOG_TO_CONSOLE, LOG_TO_FILE, LOG_MAX_SIZE_MB, LOG_BACKUP_COUNT

def log(mensaje):
    """Registra un mensaje en el log (consola y/o archivo)"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linea = f"[{timestamp}] {mensaje}"
    
    if LOG_TO_CONSOLE:
        print(linea)
    
    if LOG_TO_FILE:
        try:
            with open(LOG_PATH, "a") as f:
                f.write(linea + "\n")
        except Exception as e:
            print(f"Error escribiendo log: {e}")

def limpiar_logs_si_es_necesario():
    """Verifica y limpia logs si exceden el tamaño máximo"""
    if not os.path.exists(LOG_PATH):
        return
    
    try:
        tamaño_mb = os.path.getsize(LOG_PATH) / (1024 * 1024)
        if tamaño_mb > LOG_MAX_SIZE_MB:
            # Rotar logs
            for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
                old = f"{LOG_PATH}.{i}.gz"
                new = f"{LOG_PATH}.{i+1}.gz"
                if os.path.exists(old):
                    os.rename(old, new)
            
            # Comprimir el actual
            with open(LOG_PATH, 'rb') as f_in:
                with gzip.open(f"{LOG_PATH}.1.gz", 'wb') as f_out:
                    f_out.writelines(f_in)
            
            # Vaciar el archivo actual
            with open(LOG_PATH, 'w') as f:
                f.write("")
            
            log("📋 Logs rotados automáticamente")
    except Exception as e:
        print(f"Error rotando logs: {e}")

def formatear_resultados(resultados, campos):
    """Formatea los resultados de una consulta para mostrarlos en Telegram"""
    if not resultados:
        return "No se encontraron resultados."
    
    lineas = []
    for i, fila in enumerate(resultados[:10], 1):
        linea = f"{i}. "
        for j, campo in enumerate(campos):
            valor = fila[j] if j < len(fila) else "N/A"
            linea += f"**{campo}:** {valor}  "
        lineas.append(linea)
    
    return "\n".join(lineas)
