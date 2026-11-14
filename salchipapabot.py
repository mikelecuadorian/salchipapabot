# bot.py
# =====================================================
# CÓDIGO PRINCIPAL DEL BOT
# =====================================================

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import BotCommand
from openai import OpenAI
import re
import os
import json
import sys
import unicodedata
import requests as http_requests

# RAG del manual CNEL (manual_rag.py / manual_bot.py viven aquí)
if "/root/rag_manual" not in sys.path:
    sys.path.insert(0, "/root/rag_manual")

# Ruta completa al rootfs de Ubuntu Proot
PROOT_ROOTFS = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs"
FFMPEG_CMD = [
    "/data/data/com.termux/files/usr/bin/proot",
    "-r", PROOT_ROOTFS,
    "-w", "/",
    "/usr/bin/ffmpeg",
    "-nostdin"
]

from config import (
    TELEGRAM_TOKEN, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
    DEEPSEEK_SYSTEM_PROMPT, DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE,
    DEEPSEEK_MAX_TOKENS, DEEPSEEK_TIMEOUT, MAX_MESSAGE_LENGTH,
    MENSAJE_ERROR, MENSAJE_DEEPSEEK, MEDIDOR_NO_ENCONTRADO_MSG,
    BOT_COMMANDS, validar_configuracion
)
from utils import log, limpiar_logs_si_es_necesario


# =====================================================
# TIMESTAMP EN STDERR / TRACEBACKS  (arreglo 2026-11-02)
# =====================================================
# PROBLEMA: `utils.log()` ya pone [fecha hora] a todo lo que loguea el bot,
# pero las EXCEPCIONES de Python las escribe el propio intérprete (y el
# traceback de asyncio/telegram) directo a stderr. El `run` del servicio
# redirige stderr al mismo archivo (> 2>&1), así que quedaban líneas SIN
# fecha mezcladas: 1.961 de 2.433 líneas del log no tenían timestamp — y
# eran justo las de los ERRORES, lo que más se necesita fechar.
#
# SOLUCIÓN: se envuelve sys.stderr en un objeto que parte por líneas y
# escribe cada una por `log()`. Así TODO (tracebacks, warnings, el "No error
# handlers are registered") sale con [YYYY-MM-DD HH:MM:SS].
class _StderrConTimestamp:
    """Envuelve stderr para que cada línea salga con timestamp vía log().

    Se escribe línea por línea (no por bloque) para que las trazas de varias
    líneas —cada `File "...", line N`— queden todas fechadas. `write` imprime
    SIEMPRE la fecha real del momento de la escritura; `flush` es no-op
    porque log() ya abre/cierra el archivo en cada llamada.
    """

    def __init__(self, original):
        self._original = original
        self._buffer = ""

    def write(self, texto):
        if not texto:
            return 0
        self._buffer += texto
        while "\n" in self._buffer:
            linea, self._buffer = self._buffer.split("\n", 1)
            if linea.strip():
                try:
                    log(linea)
                except Exception:
                    self._original.write(linea + "\n")
        return len(texto)

    def flush(self):
        # Resto sin salto de línea: si quedó algo, se escribe (también fechado)
        if self._buffer.strip():
            try:
                log(self._buffer)
            except Exception:
                self._original.write(self._buffer)
            self._buffer = ""

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return False

    def __getattr__(self, nombre):
        return getattr(self._original, nombre)


sys.stderr = _StderrConTimestamp(sys.stderr)

from handlers import (
    start, medidor_command, tramite_command, 
    solicitud_command, medidor_avanzado_command, 
    cuenta_contrato_command, coordenadas_command,
    resumenayer_command, resumenmes_command, resumendia_command,
    sql_command, orden_sap_command,
    resumen_reclamos_dia_command, datos_cuenta_contrato_command,
    medidor_retirado_command, observacion_sap_command
)
from database import (
    buscar_medidor, preguntar_bd
)

# =====================================================
# VALIDAR CONFIGURACIÓN AL INICIO
# =====================================================

errores = validar_configuracion()
if errores:
    for error in errores:
        log(error)
    log("❌ El bot no puede iniciar debido a errores de configuración")
    exit(1)

# =====================================================
# CONFIGURAR CLIENTE DE DEEPSEEK
# =====================================================

cliente = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=DEEPSEEK_TIMEOUT
)


# =====================================================
# DETECTAR SI UNA PREGUNTA ES SOBRE LA BASE DE DATOS
# =====================================================

PALABRAS_DB = [
    "medidor", "medidores", "tramite", "trámite", "tramites", "trámites",
    "cuadrilla", "cuadrillas", "solicitud", "solicitudes",
    "cliente", "clientes", "cuenta", "cuentas", "contrato",
    "joel", "espinar", "lectura", "consumo", "consumos",
    "cnel", "art", "consorcio", "reclamo", "reclamos",
    "ejecución", "ejecucion", "ejecutado", "ejecutados",
    "instalación", "instalacion", "cambio", "cambios",
    "inspección", "inspeccion", "aprobado", "reprobado",
    "liberado", "pendiente", "postergado",
    "dirección", "direccion", "parroquia", "zona",
    "cuantos", "cuántos", "lista", "listado", "dame",
    "muestra", "muéstrame", "busca", "buscar", "encuentra",
    "samaniego", "espinar", "joel", "tommy", "liberados", "pendientes",
    "resumen", "reporte", "estado", "estados",
    "cuenta_contrato", "numero_tramite", "numero_solicitud",
    "med_numero", "med_serie", "fecha", "fechas",
    "total", "todos", "todas"
]

# Palabras que indican que NO es una consulta de BD
PALABRAS_NO_DB = [
    "hola", "buenos días", "buenas tardes", "buenas noches",
    "gracias", "muchas gracias", "de nada", "ok",
    "quién eres", "qué eres", "cómo funcionas", "quien eres",
    "bien", "bien y tú", "todo bien"
]


def es_consulta_bd(mensaje):
    """Detecta si el mensaje es una consulta sobre la base de datos"""
    m = mensaje.lower().strip()
    
    # Si es muy corto, probablemente no es consulta
    if len(m.split()) < 2:
        return False
    
    # Si contiene palabras NO-DB al inicio, descartar
    for no_db in PALABRAS_NO_DB:
        if m.startswith(no_db):
            return False

    # Si la pregunta es sobre el MANUAL (figuras, cláusulas, numerales) y no
    # trae lenguaje propio de la BD, NO desviar: va a /manual.
    if es_consulta_manual(mensaje) and not _tiene_lenguaje_bd(m):
        return False
    
    # Detectar menciones a números grandes (patrón de medidores/tramites)
    tiene_numero = bool(re.search(r'\b\d{7,12}\b', m))
    
    # Contar cuántas palabras clave de DB aparecen
    palabras_encontradas = sum(1 for p in PALABRAS_DB if p in m)
    
    return tiene_numero or palabras_encontradas >= 1


# =====================================================
# DETECTAR SI UNA PREGUNTA ES SOBRE EL MANUAL CNEL
# =====================================================

# Palabras que apuntan al manual. "muéstrame" y "dame" NO están aquí a
# propósito: son verbos genéricos que también usa la gente para pedir tablas
# de la BD. El disparador real es el sustantivo del manual.
PALABRAS_MANUAL = [
    "manual", "mn-com", "anexo", "norma", "cláusula", "clausula",
    "cláusulas", "clausulas", "numeral", "acápite", "acapite",
    "figura", "figuras", "fig",
    "bornera", "borneras", "medidor", "medidores",
    "acometida", "acometidas", "caja de medidor", "zona de medidor",
    "sello", "precinto", "visor", "poste", "ducto", "tubería", "tuberia",
    "calibre", "ampacidad", "amperaje", "altura de instalación",
]

# Palabras que confirman que la persona habla del manual y no de su BD
DISPARADORES_MANUAL = [
    "manual", "mn-com", "anexo", "cláusula", "clausula", "numeral",
    "figura", "figuras", "fig", "dice el manual", "dice la norma",
    "según el manual", "segun el manual", "según la norma",
]


def _tiene_lenguaje_bd(m):
    """Lenguaje inequívoco de la BD operativa (no del manual)."""
    pistas_bd = [
        "cuadrilla", "cuadrillas", "tramite", "trámite", "tramites",
        "trámites", "solicitud", "solicitudes", "cuenta_contrato",
        "cuenta contrato", "med_numero", "med_serie", "numero_tramite",
        "joel", "espinar", "samaniego", "tommy", "contratista",
        "liberado", "liberados", "reprobado", "reprobados",
        "postergado", "postergados", "reclamo", "reclamos",
        "mi cuadrilla", "mis trámites", "mis tramites",
    ]
    return any(p in m for p in pistas_bd)


def es_consulta_manual(mensaje):
    """True si la pregunta va dirigida al manual CNEL (no a la BD).

    Casos que captura:
      "muéstrame la figura 22 del manual"
      "figura 22"
      "qué dice el numeral 4.37"
      "cuántos medidores bornera para 8 en un predio"
      "hasta cuántos amperios se puede usar una bornera"

    Lo que NO captura (sigue yendo a BD):
      "cuántos medidores instaló tommy"   -> verbo de gestión + nombre
      "lista de trámites de la cuadrilla 5"
    """
    m = mensaje.lower().strip()

    # Un disparador explícito manda, aunque venga con "muéstrame" o "dame"
    if any(d in m for d in DISPARADORES_MANUAL):
        return True

    # "figura 22" / "fig 22" / "figura 22-A" sin la palabra manual
    if re.search(r'\bfig(ura)?\s*\d', m):
        return True

    # Consulta de ESPECIFICACIÓN técnica (cómo se instala / cuánto aguanta).
    # Estas no se resuelven con la BD operativa: están en el manual.
    tecnicas = [
        "bornera", "borneras", "ampacidad", "amperaje", "amperios",
        "calibre", "acometida", "acometidas", "ducto", "tubería", "tuberia",
        "altura de instalación", "altura de instalacion", "altura mínima",
        "altura minima", "precinto", "sello", "visor", "zona de medidor",
        "caja de medidor", "puesta a tierra", "aterramiento",
    ]
    return any(t in m for t in tecnicas)


# =====================================================
# TRANSCRIBIR AUDIO A TEXTO (Groq Whisper - acepta OGG directo)
# =====================================================

async def transcribir_audio_groq(ogg_path):
    """Transcribe OGG directo a texto usando Groq Whisper API (sin convertir)"""
    from config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL
    
    if not GROQ_API_KEY:
        log("⚠️ GROQ_API_KEY no configurada")
        return None
    
    try:
        log("🎤 Enviando audio a Groq Whisper...")
        with open(ogg_path, 'rb') as f:
            respuesta = http_requests.post(
                f"{GROQ_BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.ogg", f, "audio/ogg")},
                data={
                    "model": GROQ_MODEL,
                    "language": "es",
                    "response_format": "json"
                },
                timeout=30
            )
        
        if respuesta.status_code == 200:
            datos = respuesta.json()
            texto = datos.get("text", "").strip()
            if texto:
                log(f"🎤 Transcripción: {texto[:60]}...")
                return texto
        
        log(f"⚠️ Groq Whisper error HTTP {respuesta.status_code}")
        return None
        
    except Exception as e:
        log(f"❌ Error transcribiendo audio: {e}")
        return None


# =====================================================
# MANEJADOR DE MENSAJES DE VOZ
# =====================================================

async def manejar_voz(update, context):
    """Maneja mensajes de voz - descarga OGG, transcribe con Groq Whisper"""
    voice = update.message.voice
    
    await update.message.reply_text("🎤 Recibiendo audio...")
    
    try:
        # ===== PASO 1: Descargar el archivo de voz (OGG) =====
        log(f"🎵 Descargando voice_id={voice.file_id[:20]}... duración={voice.duration}s")
        tfile = await context.bot.get_file(voice.file_id)
        
        user_id = update.effective_user.id
        bot_dir = "/data/data/com.termux/files/home/salchipapabot"
        ogg_path = os.path.join(bot_dir, f"audio_{user_id}.ogg")
        
        # Descargar usando requests directamente
        if hasattr(tfile, 'file_path') and tfile.file_path:
            url = tfile.file_path
        else:
            from config import TELEGRAM_TOKEN as tk
            url = f"https://api.telegram.org/file/bot{tk}/{tfile.file_id}"
        
        log(f"📥 Descargando desde Telegram API...")
        resp_desc = http_requests.get(url, timeout=60)
        log(f"↪️ HTTP {resp_desc.status_code}, {len(resp_desc.content)} bytes recibidos")
        if resp_desc.status_code != 200:
            raise RuntimeError(f"Error HTTP {resp_desc.status_code} descargando audio")
        
        try:
            with open(ogg_path, 'wb') as f:
                f.write(resp_desc.content)
            log(f"✅ Audio guardado: {ogg_path} ({len(resp_desc.content)} bytes)")
        except Exception as e_write:
            log(f"❌ Error escribiendo archivo en {bot_dir}: {e_write}")
            raise
        
        if not os.path.exists(ogg_path):
            raise FileNotFoundError(f"No se pudo descargar el audio a {ogg_path}")
        
        # ===== PASO 2: Transcribir OGG directo con Groq Whisper =====
        log("🎤 Transcribiendo con Groq Whisper...")
        texto = await transcribir_audio_groq(ogg_path)
        
        # Limpiar archivo temporal
        try: os.remove(ogg_path)
        except: pass
        
        if not texto:
            await update.message.reply_text(
                "🎤 No pude entender el audio. ¿Podrías intentar hablar más claro o escribir el mensaje?"
            )
            return
        
        log(f"🎤 Audio transcrito: {texto[:80]}...")
        
        # Enviar transcripción
        await update.message.reply_text(f"🎤 *Transcripción:* {texto}", parse_mode='Markdown')
        
        # Procesar como texto
        await procesar_mensaje(update, context, texto)
        
    except FileNotFoundError as e:
        log(f"❌ Error de archivo en audio: {e}")
        await update.message.reply_text("❌ No se pudo procesar el audio. ¿Tal vez el archivo es muy grande?")
    except Exception as e:
        log(f"❌ Error procesando audio: {e}")
        await update.message.reply_text("❌ Error procesando el audio. Intenta de nuevo.")


# =====================================================
# MANEJADOR DE MENSAJES DE TEXTO (CON DEEPSEEK + SQL)
# =====================================================

def _normalizar_comando(s):
    """Quita tildes y pasa a minúsculas para comparar comandos.

    El autocorrector del teclado convierte '/observacion_sap' en
    '/observación_sap'; sin esto el mensaje no se reconoce como comando y cae
    al manejador de lenguaje natural.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    ).lower()


async def responder(update, context):
    """Maneja mensajes de texto"""
    mensaje = update.message.text
    
    if len(mensaje) > MAX_MESSAGE_LENGTH:
        mensaje = mensaje[:MAX_MESSAGE_LENGTH]
    
    await procesar_mensaje(update, context, mensaje)


async def procesar_mensaje(update, context, mensaje):
    """Procesa un mensaje (texto directo o transcrito de audio)"""
    log(f"📩 Mensaje: {mensaje[:80]}...")
    _cmd = _normalizar_comando(mensaje)  # comparación de comandos sin tildes

    # ===== DETECTAR COMANDO /tramite (inline, por si CommandHandler no agarra) =====
    if _cmd.startswith('/tramite'):
        partes = mensaje.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("🔢 Ejemplo: `/tramite 269565885`")
            return
        numero = partes[1].strip()
        context.args = [numero]
        from handlers import tramite_command
        await tramite_command(update, context)
        return
    
    # ===== DETECTAR COMANDO /solicitud (inline) =====
    if _cmd.startswith('/solicitud'):
        partes = mensaje.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("🔢 Ejemplo: `/solicitud 38545078`")
            return
        numero = partes[1].strip()
        context.args = [numero]
        from handlers import solicitud_command
        await solicitud_command(update, context)
        return
    
    # ===== DETECTAR COMANDO /coordenadas (inline) =====
    if _cmd.startswith('/coordenadas'):
        partes = mensaje.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("🔢 Ejemplo: `/coordenadas 201014401131`")
            return
        cuenta = partes[1].strip()
        from config import MAX_RESULTADOS
        from database import buscar_por_cuenta_contrato
        await update.message.reply_text("🗺️ Buscando coordenadas...")
        
        resultados = buscar_por_cuenta_contrato(cuenta, MAX_RESULTADOS)
        
        if not resultados:
            await update.message.reply_text(f"❌ No se encontraron resultados para cuenta contrato: {cuenta}")
            log(f"Comando /coordenadas {cuenta} - sin resultados")
            return
        
        lineas = []
        for i, (tramite, fecha, lon_cnel, lat_cnel, lon_gps, lat_gps) in enumerate(resultados, 1):
            maps_cnel = ""
            if lat_cnel is not None and lon_cnel is not None:
                maps_cnel = f"https://www.google.com/maps?q={lat_cnel},{lon_cnel}"
            
            maps_gps = ""
            if lat_gps is not None and lon_gps is not None:
                maps_gps = f"https://www.google.com/maps?q={lat_gps},{lon_gps}"
            
            linea = (
                f"━━━━━━━━━━━━━ #{i} ━━━━━━━━━━━━━\n"
                f"📋 **Trámite:** {tramite or 'N/A'}\n"
                f"📅 **Ejecución:** {fecha or 'N/A'}\n"
            )
            
            if maps_cnel:
                linea += f"🏛️ **CNEL:** [Ver en Maps]({maps_cnel})\n"
            else:
                linea += f"🏛️ **CNEL:** Sin coordenadas\n"
            
            if maps_gps:
                linea += f"📍 **Ejecución:** [Ver en Maps]({maps_gps})\n"
            else:
                linea += f"📍 **Ejecución:** Sin coordenadas\n"
            
            lineas.append(linea)
        
        respuesta = f"🗺️ **COORDENADAS**\n" + "\n".join(lineas)
        await update.message.reply_text(respuesta, parse_mode='Markdown', disable_web_page_preview=True)
        log(f"Comando /coordenadas {cuenta} - {len(resultados)} resultados")
        return
    
    # ===== DETECTAR COMANDO /orden_sap (inline) =====
    if _cmd.startswith('/orden_sap'):
        partes = mensaje.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text("🔢 Ejemplo: `/orden_sap 21881541`\nBusca en las órdenes SAP por número de solicitud.")
            return
        numero = partes[1].strip()
        await update.message.reply_text("🔍 *Buscando en órdenes SAP...*", parse_mode='Markdown')
        from handlers import orden_sap_command
        # Reutilizar la función del handler con los args
        context.args = [numero]
        await orden_sap_command(update, context)
        return
    
    # ===== DETECTAR COMANDO /observacion_sap (inline) =====
    if _cmd.startswith('/observacion_sap'):
        partes = mensaje.split(maxsplit=1)
        if len(partes) < 2:
            await update.message.reply_text(
                "🔢 Ejemplo: `/observacion_sap 21970326`\n"
                "Trae la Observación de la orden SAP (dirección correcta, teléfono de contacto, etc.).",
                parse_mode='Markdown')
            return
        numero = partes[1].strip()
        from handlers import observacion_sap_command
        context.args = [numero]
        await observacion_sap_command(update, context)
        return
    
    # ===== DETECTAR CONSULTAS DE MEDIDOR EN LENGUAJE NATURAL =====
    match_numero = re.search(r'\b(\d{7,11})\b', mensaje)
    if match_numero and any(p in mensaje.lower() for p in ["medidor", "cuadrilla", "tramite", "quién", "número", "de quién"]):
        numero = match_numero.group(1)
        info = buscar_medidor(numero)
        if info:
            cuadrilla, tramite = info
            await update.message.reply_text(
                f"🔍 **Medidor {numero}**\n"
                f"👥 Cuadrilla: {cuadrilla}\n"
                f"📋 Trámite: {tramite}"
            )
            return
        else:
            await update.message.reply_text(MEDIDOR_NO_ENCONTRADO_MSG.format(numero))
            return
    
    # ===== DETECTAR CONSULTA AL MANUAL CNEL (antes de la BD) =====
    # Va ANTES de es_consulta_bd porque "muéstrame" / "dame" están en
    # PALABRAS_DB y desviaban al SQL cualquier pregunta con figura.
    if es_consulta_manual(mensaje) and not _tiene_lenguaje_bd(mensaje.lower()):
        context.args = mensaje.split()
        await manual_command(update, context)
        return

    # ===== DETECTAR CONSULTA SOBRE LA BASE DE DATOS =====
    if es_consulta_bd(mensaje):
        await update.message.reply_text("🔍 *Consultando la base de datos...*", parse_mode='Markdown')
        
        try:
            respuesta = preguntar_bd(mensaje, cliente)
            if respuesta:
                await update.message.reply_text(respuesta)
                log(f"✅ Respondido con datos de BD")
            else:
                await update.message.reply_text(MENSAJE_ERROR)
        except Exception as e:
            log(f"❌ Error en consulta BD: {e}")
            await update.message.reply_text(MENSAJE_ERROR)
        return
    
    # ===== CONSULTAR A DEEPSEEK (IA GENERAL) =====
    await update.message.reply_text(MENSAJE_DEEPSEEK)
    
    try:
        respuesta = cliente.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
                {"role": "user", "content": mensaje}
            ],
            temperature=DEEPSEEK_TEMPERATURE,
            max_tokens=DEEPSEEK_MAX_TOKENS
        )
        texto = respuesta.choices[0].message.content
        await update.message.reply_text(texto)
        log(f"✅ Respondido con IA")
    except Exception as e:
        log(f"❌ Error en DeepSeek: {str(e)}")
        await update.message.reply_text(MENSAJE_ERROR)


# =====================================================
# NUEVO COMANDO: /preguntar
# =====================================================

async def preguntar_command(update, context):
    """Comando /preguntar - fuerza consulta a la base de datos"""
    if not context.args:
        await update.message.reply_text(
            "🔍 *¿Qué quieres consultar?*\n\n"
            "Ejemplo: `/preguntar cuántos trámites hizo Joel Espinar`",
            parse_mode='Markdown'
        )
        return
    
    mensaje = " ".join(context.args)
    await update.message.reply_text("🔍 *Consultando la base de datos...*", parse_mode='Markdown')
    
    try:
        respuesta = preguntar_bd(mensaje, cliente)
        if respuesta:
            await update.message.reply_text(respuesta)
            log(f"✅ /preguntar respondido")
        else:
            await update.message.reply_text(MENSAJE_ERROR)
    except Exception as e:
        log(f"❌ Error en /preguntar: {e}")
        await update.message.reply_text(MENSAJE_ERROR)


# =====================================================
# COMANDO /manual — RAG del manual CNEL (cláusulas + figuras)
# =====================================================

# Directorio real que sirve el servidor web (corre en Termux, no en PRoot)
_DIR_FIGS_PUBLICADAS = "/data/data/com.termux/files/home/public_html/manual_figs"
_URL_FIGS_BASE = "https://ecosistema.salchipapabot.cc.cd/manual_figs"

# Slugs que no coinciden con el patrón automático fig<n>
_SLUGS_ESPECIALES = {
    "FIG. 33": "fig33_4medidores",
    "FIG. 34": "fig34_8medidores",
    "FIG. 35": "fig35_12medidores",
}


def _url_figura(fid):
    """Reconstruye el link del PNG de una figura, verificando que exista.

    El puente PRoot a veces devuelve la figura con `url: null` aunque el PNG
    esté publicado (timing de _existe_publicada, JSON truncado). Aquí se
    comprueba el archivo REAL en disco y se arma el link con el mismo slug
    que usa el índice. Devuelve None solo si el archivo no está publicado.

    OJO con las variantes: los sufijos van en MAYÚSCULA (fig20-A.png), no en
    minúscula. Por eso se prueban las dos formas antes de rendirse.
    """
    try:
        # Candidatos de slug, en orden de preferencia
        candidatos = []
        esp = _SLUGS_ESPECIALES.get(fid)
        if esp:
            candidatos.append(esp)
        # "FIG. 20-A" -> "fig20-A" (sufijo en mayúscula, como está publicado)
        base = fid.replace(" ", "").replace(".", "")      # FIG20-A
        candidatos.append("fig" + base[3:])               # fig20-A
        candidatos.append(base.lower())                   # fig20-a

        for slug in dict.fromkeys(candidatos):
            ruta = os.path.join(_DIR_FIGS_PUBLICADAS, slug + ".png")
            if os.path.exists(ruta) and os.path.getsize(ruta) > 1000:
                return f"{_URL_FIGS_BASE}/{slug}.png"
    except Exception as e:
        log(f"⚠️ _url_figura({fid}): {e}")
    return None


async def manual_command(update, context):
    """Comando /manual - pregunta al manual MN-COM-AC-001 V3.

    RAG local: 346 cláusulas + 28 figuras del Anexo 1.
    DeepSeek redacta la respuesta pero SOLO con las cláusulas recuperadas.
    """
    if not context.args:
        await update.message.reply_text(
            "📕 *Pregúntale al manual CNEL*\n\n"
            "Ejemplos:\n"
            "`/manual cuántos medidores bornera para 8 en un predio`\n"
            "`/manual hasta cuántos amperios se puede usar una bornera`\n"
            "`/manual qué altura debe tener el medidor`\n\n"
            "_Busca en las 346 cláusulas y en las 28 figuras del Anexo 1, "
            "y responde citando numeral y página._",
            parse_mode='Markdown'
        )
        return

    mensaje = " ".join(context.args)

    # Sin contexto no hay nada que buscar: "muestra lo mismo" huérfano
    # devolvía 4 cláusulas al azar y ninguna figura.
    if len(mensaje.split()) <= 4 and not es_consulta_manual(mensaje):
        await update.message.reply_text(
            "🤔 Necesito que me digas *qué* quieres ver del manual.\n\n"
            "Ejemplos directos:\n"
            "`figura 22`\n"
            "`muéstrame la figura 22`\n"
            "`numeral 4.37`\n"
            "`cuántos medidores bornera para 8 en un predio`",
            parse_mode='Markdown'
        )
        return

    await update.message.reply_text("📕 *Buscando en el manual...*", parse_mode='Markdown')

    try:
        import manual_bot
        r = manual_bot.redactar(mensaje, cliente, DEEPSEEK_MODEL,
                                temperature=DEEPSEEK_TEMPERATURE,
                                max_tokens=DEEPSEEK_MAX_TOKENS)

        # Texto principal (sin parse_mode por si el manual trae asteriscos)
        await update.message.reply_text(r["texto"])

        # Planos: link, NO MEDIA (MEDIA falla desde la tablet a este chat)
        # BLINDAJE 2026-11-11: no confiar en f["url"]. El puente PRoot a veces
        # devuelve la figura SIN url (JSON truncado o _existe_publicada False
        # por timing) aunque el PNG esté publicado. Se reconstruye el link con
        # el mismo criterio del índice y se comprueba que responda HTTP 200.
        enviadas = 0
        for f in r["figuras"][:3]:
            url = f.get("url") or _url_figura(f["id"])
            if url:
                # SIN parse_mode: los títulos del manual traen guiones,
                # paréntesis, asteriscos y guiones bajos sueltos ("_") que
                # rompen el Markdown de Telegram con
                # "can't find end of the entity starting at byte offset N".
                # El link se ve igual en texto plano y Telegram lo hace
                # clickable solo.
                await update.message.reply_text(
                    f"📐 {f['id']} — {f['titulo']}\n"
                    f"{f['dimensiones']}\n{url}"
                )
                enviadas += 1
                log(f"📐 Figura enviada: {f['id']} -> {url}"
                    f"{' (url reconstruida)' if not f.get('url') else ''}")
            else:
                log(f"⚠️ Figura SIN url: {f.get('id')} ({f.get('titulo','')[:40]}) "
                    f"claves={list(f.keys())}")

        # Si el RAG trajo figuras pero ninguna tenía URL, avisarlo en vez de
        # quedarse callado (el silencio parecía "no existe la figura").
        if r["figuras"] and enviadas == 0:
            ids = ", ".join(f.get("id", "?") for f in r["figuras"][:3])
            await update.message.reply_text(
                f"⚠️ Encontré {ids} en el índice, pero sin PNG publicado todavía."
            )
        log(f"✅ /manual respondido ({len(r['fuentes'])} fuentes)")

    except Exception as e:
        log(f"❌ Error en /manual: {e}")
        await update.message.reply_text(f"❌ Error consultando el manual: {e}")


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================

async def registrar_menu_comandos(app):
    """Registra el menú de comandos que Telegram muestra al escribir '/'.
    Se ejecuta una sola vez al arrancar (post_init). Si falla, el bot sigue
    funcionando igual: el menú es cosmético, no afecta a los handlers.
    """
    try:
        comandos = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS.items()]
        await app.bot.set_my_commands(comandos)
        log(f"📋 Menú de comandos registrado en Telegram ({len(comandos)} comandos)")
    except Exception as e:
        log(f"⚠️ No se pudo registrar el menú de comandos: {e}")


def main():
    # Limpiar logs al iniciar
    limpiar_logs_si_es_necesario()
    
    # Crear la aplicación
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(registrar_menu_comandos).build()
    
    # Registrar comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("medidor", medidor_command))
    app.add_handler(CommandHandler("medidor_retirado", medidor_retirado_command))
    app.add_handler(CommandHandler("tramite", tramite_command))
    app.add_handler(CommandHandler("solicitud", solicitud_command))
    app.add_handler(CommandHandler("medidor_avanzado", medidor_avanzado_command))
    app.add_handler(CommandHandler("cuenta_contrato", cuenta_contrato_command))
    app.add_handler(CommandHandler("datos_cuenta_contrato", datos_cuenta_contrato_command))
    app.add_handler(CommandHandler("coordenadas", coordenadas_command))
    app.add_handler(CommandHandler("preguntar", preguntar_command))
    app.add_handler(CommandHandler("manual", manual_command))
    app.add_handler(CommandHandler("resumenayer", resumenayer_command))
    app.add_handler(CommandHandler("resumenmes", resumenmes_command))
    app.add_handler(CommandHandler("resumendia", resumendia_command))
    app.add_handler(CommandHandler("sql", sql_command))
    app.add_handler(CommandHandler("orden_sap", orden_sap_command))
    app.add_handler(CommandHandler("observacion_sap", observacion_sap_command))
    app.add_handler(CommandHandler("resumen_reclamos_dia", resumen_reclamos_dia_command))
    
    # Registrar manejadores de mensajes
    app.add_handler(MessageHandler(filters.VOICE, manejar_voz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    # Iniciar el bot
    # Contexto del arranque: sin esto el log solo dice "iniciado" y no hay
    # forma de saber si fue un reinicio del watchdog, un crash o un deploy.
    # `runsv` reinicia el proceso solo, así que el PID es la pista más útil:
    # PID distinto = proceso nuevo. UP_TIME separa "crash inmediato" de
    # "corrió días y lo mataron".
    try:
        with open("/proc/uptime") as _f:
            _uptime_s = int(float(_f.read().split()[0]))
        _uptime_txt = f"{_uptime_s // 86400}d {(_uptime_s % 86400) // 3600}h {(_uptime_s % 3600) // 60}m"
    except Exception:
        # En PRoot/Android /proc/uptime no siempre es legible. Plan B:
        # /proc/stat trae 'btime' (epoch del arranque) en casi todo Android.
        try:
            import time as _t
            _btime = None
            with open("/proc/stat") as _f:
                for _l in _f:
                    if _l.startswith("btime"):
                        _btime = int(_l.split()[1])
                        break
            if _btime:
                _uptime_s = int(_t.time()) - _btime
                _uptime_txt = f"{_uptime_s // 86400}d {(_uptime_s % 86400) // 3600}h {(_uptime_s % 3600) // 60}m"
            else:
                _uptime_txt = "desconocido"
        except Exception:
            _uptime_txt = "desconocido"
    log(f"🤖 SalchipapaBot iniciado (con SQL natural y audio) · pid={os.getpid()} · equipo arriba {_uptime_txt}")
    log("📊 Base de datos conectada")
    log("🎤 Soporte de audio activado")
    log("🚀 Bot corriendo...")
    
    app.run_polling()

if __name__ == "__main__":
    main()
