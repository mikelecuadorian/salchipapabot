# config.py
# =====================================================
# ARCHIVO DE CONFIGURACIÓN DEL BOT
# =====================================================

import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env (las claves reales van ahi)
load_dotenv()

# ========== TOKENS Y API KEYS ==========
# Token de Telegram (obténlo de @BotFather)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN_SALCHIPABOT", "8565609913:***")

# API Key de DeepSeek (obténlo de platform.deepseek.com)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY_SALCHIPABOT", "sk-bea...288a")

# Groq API Key para transcripcion de audio (gratis - console.groq.com)
GROQ_API_KEY = os.getenv("GROQ_API_KEY_SALCHIPABOT", "gsk_fx...yE2d")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "whisper-large-v3-turbo"

# ========== RUTAS ==========
# Ruta de la base de datos SQLite (Termux)
DB_PATH = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"

# Ruta del archivo de log (Termux)
# LOG_PATH = "/data/data/com.termux/files/usr/var/log/bot/current"
LOG_PATH = "/data/data/com.termux/files/usr/var/log/salchipapabot.log"
LOG_FILE = LOG_PATH  # Para compatibilidad

# ========== CONFIGURACIÓN DE DEEPSEEK ==========
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"  # Para openai>=1.0.0
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_TEMPERATURE = 0.7
DEEPSEEK_MAX_TOKENS = 1000
DEEPSEEK_TIMEOUT = 30

# Contexto del sistema para DeepSeek
DEEPSEEK_SYSTEM_PROMPT = """Eres un asistente experto en bases de datos SQLite, 
PostgreSQL y sistemas de gestión de medidores eléctricos. 
Ayudas a usuarios con consultas sobre trámites, medidores y gestión de datos.
Responde de manera clara, concisa y en español."""

# ========== CONFIGURACIÓN DEL BOT ==========
# Límite de resultados por consulta
MAX_RESULTADOS = 20

# Longitud máxima de texto en respuestas
MAX_TEXTO_LONGITUD = 200

# Configuración de seguridad
ALLOWED_USERS = []  # Lista vacía = todos los usuarios permitidos
MAX_MESSAGE_LENGTH = 4096

# ========== MENSAJES DEL BOT ==========
MENSAJE_BIENVENIDA = """
🤖 *SALCHIPAPABOT — CONSORCIO ART*

Bot de consultas para gestión de trámites, medidores y datos operativos del Consorcio ART.

*Comandos disponibles:*

📌 `/medidor` [número] - Buscar medidor por número de serie (bodega del Consorcio ART)
📌 `/tramite` [número] - Buscar por número de trámite
📌 `/solicitud` [número] - Buscar por número de solicitud
📌 `/medidor_avanzado` [número] - Búsqueda en cualquier campo
📌 `/cuenta_contrato` [cuenta] - Búsqueda por cuenta contrato (muestra medidor N°)
📌 `/datos_cuenta_contrato` [cuenta] - Datos del cliente del último trámite
📌 `/coordenadas` [cuenta] - Coordenadas CNEL y GPS en Google Maps
📌 `/orden_sap` [solicitud] - Buscar orden SAP por número de solicitud
📌 `/preguntar` [consulta] - Consulta en lenguaje natural a la base de datos

*Resúmenes:*
📊 `/resumenayer` - Ejecutados del día anterior
📊 `/resumendia` [dd/mm/aaaa] - Ejecutados de una fecha específica
📊 `/resumenmes` - Ejecutados del mes hasta ayer
📊 `/resumen_reclamos_dia` [dd/mm/aaaa] - Reclamos RECL del día

*Herramientas:*
🔧 `/sql` [SQL] - Ejecutar consulta SQL directa

*Ejemplos:*
`/medidor 20230249290`
`/tramite 269565885`
`/solicitud 42743024`
`/resumenayer`
`/preguntar cuántos trámites hizo Carlos Pérez`
"""

MENSAJE_ERROR = "❌ Ocurrió un error al procesar tu solicitud. Intenta de nuevo."
MENSAJE_NO_ENCONTRADO = "❌ No se encontraron resultados para: {}"
MENSAJE_BUSCANDO = "🔍 Buscando en la base de datos..."
MENSAJE_BUSCANDO_MEDIDOR = "🔍 Buscando en 12 campos de la base de datos..."
MENSAJE_DEEPSEEK = "🤔 Consultando a DeepSeek..."

MEDIDOR_NO_ENCONTRADO_MSG = "❌ El número {} no pertenece al Consorcio ART. Por favor, verificá el número e intentá nuevamente."

# ========== CONFIGURACIÓN DE LOGS ==========
LOG_MAX_SIZE_MB = 100
LOG_BACKUP_COUNT = 5
LOG_TO_CONSOLE = True
LOG_TO_FILE = True

# ========== CONFIGURACIÓN DE BASE DE DATOS ==========
DB_CONNECTION_TIMEOUT = 10
DB_RETRY_ATTEMPTS = 3

# ========== COMANDOS DEL BOT ==========
BOT_COMMANDS = {
    "start": "Mensaje de bienvenida",
    "medidor": "Buscar medidor por número de serie (bodega del Consorcio ART)",
    "tramite": "Buscar trámite por número",
    "solicitud": "Buscar solicitud por número",
    "medidor_avanzado": "Búsqueda avanzada en cualquier campo",
    "cuenta_contrato": "Búsqueda por cuenta contrato (muestra medidor N°)",
    "coordenadas": "Coordenadas CNEL y GPS en Google Maps",
    "orden_sap": "Buscar orden SAP por número de solicitud",
    "preguntar": "Consulta en lenguaje natural a la base de datos",
    "resumenayer": "Ejecutados del día anterior",
    "resumendia": "Ejecutados de una fecha específica",
    "resumenmes": "Ejecutados del mes hasta ayer",
    "resumen_reclamos_dia": "Reclamos RECL del día",
    "sql": "Ejecutar consulta SQL directa"
}

# ========== VALIDACIÓN ==========
def validar_configuracion():
    """Verifica que la configuración sea válida"""
    errores = []
    
    if TELEGRAM_TOKEN == "TU_TOKEN_TELEGRAM_AQUI":
        errores.append("❌ TELEGRAM_TOKEN no ha sido configurado")
    
    if DEEPSEEK_API_KEY == "TU_API_KEY_DEEPSEEK_AQUI":
        errores.append("❌ DEEPSEEK_API_KEY no ha sido configurado")
    
    if not os.path.exists(DB_PATH):
        errores.append(f"❌ Base de datos no encontrada en: {DB_PATH}")
    
    # Crear directorio de logs si no existe
    log_dir = os.path.dirname(LOG_PATH)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except:
            pass  # En Termux puede que no tenga permisos
    
    return errores
