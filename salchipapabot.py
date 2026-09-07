# bot.py
# =====================================================
# CÓDIGO PRINCIPAL DEL BOT
# =====================================================

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from openai import OpenAI
import re
import os
import json
import requests as http_requests

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
    validar_configuracion
)
from utils import log, limpiar_logs_si_es_necesario
from handlers import (
    start, medidor_command, tramite_command, 
    solicitud_command, medidor_avanzado_command, 
    cuenta_contrato_command, coordenadas_command,
    resumenayer_command, resumenmes_command, resumendia_command,
    sql_command, orden_sap_command,
    resumen_reclamos_dia_command, datos_cuenta_contrato_command,
    medidor_retirado_command
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
    
    # Detectar menciones a números grandes (patrón de medidores/tramites)
    tiene_numero = bool(re.search(r'\b\d{7,12}\b', m))
    
    # Contar cuántas palabras clave de DB aparecen
    palabras_encontradas = sum(1 for p in PALABRAS_DB if p in m)
    
    return tiene_numero or palabras_encontradas >= 1


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

async def responder(update, context):
    """Maneja mensajes de texto"""
    mensaje = update.message.text
    
    if len(mensaje) > MAX_MESSAGE_LENGTH:
        mensaje = mensaje[:MAX_MESSAGE_LENGTH]
    
    await procesar_mensaje(update, context, mensaje)


async def procesar_mensaje(update, context, mensaje):
    """Procesa un mensaje (texto directo o transcrito de audio)"""
    log(f"📩 Mensaje: {mensaje[:80]}...")
    
    # ===== DETECTAR COMANDO /tramite (inline, por si CommandHandler no agarra) =====
    if mensaje.startswith('/tramite'):
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
    if mensaje.startswith('/solicitud'):
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
    if mensaje.startswith('/coordenadas'):
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
    if mensaje.startswith('/orden_sap'):
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
# FUNCIÓN PRINCIPAL
# =====================================================

def main():
    # Limpiar logs al iniciar
    limpiar_logs_si_es_necesario()
    
    # Crear la aplicación
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
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
    app.add_handler(CommandHandler("resumenayer", resumenayer_command))
    app.add_handler(CommandHandler("resumenmes", resumenmes_command))
    app.add_handler(CommandHandler("resumendia", resumendia_command))
    app.add_handler(CommandHandler("sql", sql_command))
    app.add_handler(CommandHandler("orden_sap", orden_sap_command))
    app.add_handler(CommandHandler("resumen_reclamos_dia", resumen_reclamos_dia_command))
    
    # Registrar manejadores de mensajes
    app.add_handler(MessageHandler(filters.VOICE, manejar_voz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, responder))
    
    # Iniciar el bot
    log("🤖 SalchipapaBot iniciado (con SQL natural y audio)")
    log("📊 Base de datos conectada")
    log("🎤 Soporte de audio activado")
    log("🚀 Bot corriendo...")
    
    app.run_polling()

if __name__ == "__main__":
    main()
