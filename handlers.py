# handlers.py
# =====================================================
# COMANDOS DEL BOT
# =====================================================

import re
import sqlite3
from datetime import datetime, timedelta
import locale
from telegram import Update
from telegram.ext import ContextTypes
from config import (
    MENSAJE_BIENVENIDA, MENSAJE_ERROR, MENSAJE_BUSCANDO,
    MENSAJE_BUSCANDO_MEDIDOR, MEDIDOR_NO_ENCONTRADO_MSG,
    DB_PATH
)
from utils import log
from database import (
    buscar_medidor, buscar_tramite, buscar_solicitud, 
    buscar_medidor_avanzado, buscar_por_cuenta_contrato,
    buscar_por_cuenta_contrato_avanzado, consultar_sqlite,
    buscar_fotos_tramite, buscar_ultimo_por_cuenta_contrato,
    buscar_medidor_retirado
)
import requests
import os

MAX_TELEGRAM_MSG = 4000  # dejar margen de seguridad del límite 4096

async def enviar_en_partes(update, texto, parse_mode='Markdown'):
    """Envía texto largo en múltiples mensajes si excede el límite de Telegram."""
    if len(texto) <= MAX_TELEGRAM_MSG:
        await update.message.reply_text(texto, parse_mode=parse_mode)
        return
    
    partes = []
    parte_actual = []
    largo_actual = 0
    
    for linea in texto.split('\n'):
        # +1 por el '\n'
        if largo_actual + len(linea) + 1 > MAX_TELEGRAM_MSG and parte_actual:
            partes.append('\n'.join(parte_actual))
            parte_actual = [linea]
            largo_actual = len(linea) + 1
        else:
            parte_actual.append(linea)
            largo_actual += len(linea) + 1
    
    if parte_actual:
        partes.append('\n'.join(parte_actual))
    
    for i, parte in enumerate(partes):
        if i == 0:
            await update.message.reply_text(parte, parse_mode=parse_mode)
        else:
            await update.message.reply_text(
                f"📎 *Continuación ({i+1}/{len(partes)})*:\n\n{parte}",
                parse_mode=parse_mode
            )


# Carpeta temporal para fotos
FOTOS_TEMP_DIR = "/data/data/com.termux/files/home/.bot_fotos"
os.makedirs(FOTOS_TEMP_DIR, exist_ok=True)


async def enviar_fotos_tramite(update, numero_tramite):
    """Busca y envía las fotos de un trámite si existen."""
    try:
        fotos = buscar_fotos_tramite(numero_tramite)
        if not fotos:
            return
        
        etiqueta_principal = list(fotos.keys())[0]
        await update.message.reply_text(
            f"📸 *Fotos del trámite {numero_tramite}:*",
            parse_mode='Markdown'
        )
        
        for etiqueta, url in fotos.items():
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    fpath = os.path.join(FOTOS_TEMP_DIR, f"{numero_tramite}_{etiqueta.replace('/', '_').replace(' ', '_')}.jpg")
                    with open(fpath, 'wb') as f:
                        f.write(r.content)
                    with open(fpath, 'rb') as f:
                        await update.message.reply_photo(
                            photo=f,
                            caption=etiqueta
                        )
                    os.remove(fpath)
                else:
                    await update.message.reply_text(f"⚠️ {etiqueta}: no disponible (HTTP {r.status_code})")
            except Exception as e:
                log(f"⚠️ Error al descargar foto {etiqueta} del trámite {numero_tramite}: {e}")
                await update.message.reply_text(f"⚠️ {etiqueta}: error al descargar")
    except Exception as e:
        log(f"⚠️ Error en enviar_fotos_tramite({numero_tramite}): {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - Mensaje de bienvenida"""
    await update.message.reply_text(MENSAJE_BIENVENIDA, parse_mode='Markdown')
    log("Comando /start ejecutado")

async def medidor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /medidor NUMERO - Busca en bodega del Consorcio ART"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/medidor 20230249290`")
        return
    
    nro_serie = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO)
    
    info = buscar_medidor(nro_serie)
    
    if info:
        cuadrilla, tramite = info
        respuesta = (
            f"🔍 **MEDIDOR {nro_serie}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **Cuadrilla:** {cuadrilla or 'No registrada'}\n"
            f"📋 **Número de trámite:** {tramite or 'No registrado'}"
        )
        await update.message.reply_text(respuesta)
    else:
        await update.message.reply_text(MEDIDOR_NO_ENCONTRADO_MSG.format(nro_serie))
    
    log(f"Comando /medidor {nro_serie} ejecutado")

async def medidor_retirado_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /medidor_retirado NUMERO - Busca en tabla medidores_retirados"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/medidor_retirado 20230249290`")
        return
    
    medidor = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO)
    
    info = buscar_medidor_retirado(medidor)
    
    if info:
        num, marca, lugar = info
        respuesta = (
            f"🔍 **MEDIDOR RETIRADO {medidor}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔢 **Medidor:** {num or 'No registrado'}\n"
            f"🏷️ **Marca:** {marca or 'No registrada'}\n"
            f"📦 **Lugar de entrega:** {lugar or 'No registrado'}"
        )
        await update.message.reply_text(respuesta)
    else:
        await update.message.reply_text(MEDIDOR_NO_ENCONTRADO_MSG.format(medidor))
    
    log(f"Comando /medidor_retirado {medidor} ejecutado")

async def tramite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /tramite NUMERO - Busca en recorrido_cuadrillas y gestion_tramites"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/tramite 269565885`")
        return
    
    numero = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO)
    
    recorrido, observacion = buscar_tramite(numero)
    
    if not recorrido:
        await update.message.reply_text(f"❌ No se encontró el trámite {numero}")
        return
    
    r = recorrido[0]
    obs = observacion[0][0] if observacion else "No registrada"
    cuenta_raw = r[4]
    if cuenta_raw:
        try:
            cuenta = str(int(float(cuenta_raw)))
        except (ValueError, TypeError):
            cuenta = str(cuenta_raw).strip()
    else:
        cuenta = "No registrada"
    
    respuesta = (
        f"📋 **TRÁMITE {numero}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 **Número de solicitud:** {r[0] or 'No registrado'}\n"
        f"🏷️ **Tipo:** {r[1] or 'No registrado'}\n"
        f"👥 **Cuadrilla:** {r[2] or 'No registrada'}\n"
        f"📅 **Fecha ejecución:** {r[3] or 'No registrada'}\n"
        f"🔢 **Cuenta contrato:** {cuenta}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **Observación:**\n{obs}"
    )
    await update.message.reply_text(respuesta)
    log(f"Comando /tramite {numero} ejecutado")
    
    # Enviar fotos si existen
    await enviar_fotos_tramite(update, numero)


async def solicitud_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /solicitud NUMERO - Busca en recorrido_cuadrillas y gestion_tramites"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/solicitud 38545078`")
        return
    
    numero = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO)
    
    recorrido, observacion = buscar_solicitud(numero)
    
    if not recorrido:
        await update.message.reply_text(f"❌ No se encontró la solicitud {numero}")
        return
    
    r = recorrido[0]
    obs = observacion[0][0] if observacion else "No registrada"
    cuenta_raw = r[4]
    if cuenta_raw:
        try:
            cuenta = str(int(float(cuenta_raw)))
        except (ValueError, TypeError):
            cuenta = str(cuenta_raw).strip()
    else:
        cuenta = "No registrada"
    
    respuesta = (
        f"📄 **SOLICITUD {numero}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **Número de trámite:** {r[0] or 'No registrado'}\n"
        f"🏷️ **Tipo:** {r[1] or 'No registrado'}\n"
        f"👥 **Cuadrilla:** {r[2] or 'No registrada'}\n"
        f"📅 **Fecha ejecución:** {r[3] or 'No registrada'}\n"
        f"🔢 **Cuenta contrato:** {cuenta}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **Observación:**\n{obs}"
    )
    await update.message.reply_text(respuesta)
    log(f"Comando /solicitud {numero} ejecutado")
    
    # Enviar fotos del trámite asociado si existen
    if recorrido:
        tramite_num = str(recorrido[0][0])
        await enviar_fotos_tramite(update, tramite_num)


async def medidor_avanzado_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /medidor_avanzado NUMERO - Búsqueda en cualquier campo"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/medidor_avanzado 20230249290`")
        return
    
    numero = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO_MEDIDOR)
    
    datos, recorrido = buscar_medidor_avanzado(numero)
    
    if not datos:
        await update.message.reply_text(f"❌ No se encontró el número {numero} en ningún registro")
        return
    
    respuesta = (
        f"🔍 **BÚSQUEDA AVANZADA: {numero}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **Trámite:** {datos[0] or 'No registrado'}\n"
        f"📄 **Solicitud:** {datos[1] or 'No registrado'}\n"
        f"👥 **Cuadrilla:** {datos[2] or 'No registrada'}\n"
        f"📅 **Ejecución:** {datos[3] or 'No registrada'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **Observación:**\n{datos[4] or 'No registrada'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧰 **Nota materiales:** {datos[5] or 'No registrada'}\n"
        f"❌ **Motivo no ejecución:** {datos[6] or 'No registrado'}\n"
        f"📋 **Detalle:** {datos[7] or 'No registrado'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📟 **Medidor actual:** {datos[8] or '?'} / {datos[9] or '?'}\n"
        f"📊 **Contador:** {datos[10] or '?'} / {datos[11] or '?'}\n"
        f"🆕 **Medidor nuevo:** {datos[12] or '?'} / {datos[13] or '?'}\n"
        f"🔄 **Medidor retirado:** {datos[14] or '?'} / {datos[15] or '?'}"
    )
    
    if recorrido:
        r = recorrido[0]
        respuesta += (
            f"\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Cuenta contrato:** {_fmt_sin_decimales(r[0]) or 'No registrada'}\n"
            f"📋 **Motivo solicitud:** {r[1] or 'No registrado'}"
        )
    
    await update.message.reply_text(respuesta)
    log(f"Comando /medidor_avanzado {numero} ejecutado")

def _fmt_sin_decimales(valor):
    """Limpia valores numéricos tipo '12345.0' para mostrarlos sin decimales"""
    if valor is None or str(valor).strip() == '':
        return valor
    texto = str(valor).strip()
    if texto.endswith('.0'):
        return texto[:-2]
    return valor


async def cuenta_contrato_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cuenta_contrato NUMERO - Busca por cuenta contrato y muestra med_numero"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/cuenta_contrato 201014401131`")
        return
    
    from config import MAX_RESULTADOS
    
    numero = context.args[0]
    await update.message.reply_text(MENSAJE_BUSCANDO)
    
    resultados = buscar_por_cuenta_contrato_avanzado(numero, MAX_RESULTADOS)
    
    if not resultados:
        await update.message.reply_text(f"❌ No se encontraron resultados para cuenta contrato: {numero}")
        log(f"Comando /cuenta_contrato {numero} - sin resultados")
        return
    
    for i, (datos, cuenta_cto, motivo, rc_extra) in enumerate(resultados, 1):
        # Enviar cada resultado como mensaje individual
        if datos is None:
            # Trámite asignado en recorrido pero SIN gestión registrada
            linea = (
                f"━━━━━━━━━━━━━ #{i} ━━━━━━━━━━━━━\n"
                f"⚠️ **NO GESTIONADO**\n"
                f"📋 **Trámite:** {rc_extra['numero_tramite'] or 'No registrado'}\n"
                f"📄 **Solicitud:** {rc_extra['numero_solicitud'] or 'No registrada'}\n"
                f"👥 **Cuadrilla:** {rc_extra['cuadrilla'] or 'No asignada'}\n"
                f"📅 **Fecha solicitud:** {rc_extra['fecha_solicitud'] or 'No registrada'}\n"
                f"🗓️ **Planificación:** {rc_extra['fecha_planificacion'] or 'No registrada'}\n"
                f"📊 **Estado inspección:** {rc_extra['estado_insp'] or 'No registrado'}\n"
                f"📋 **Tipo solicitud:** {rc_extra['tipo_solicitud'] or 'No registrado'}\n"
                f"👤 **Cliente:** {rc_extra['cliente'] or 'No registrado'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 **Motivo solicitud:** {motivo or 'No registrado'}\n"
                f"ℹ️ Asignado en recorrido pero sin registro en gestion_tramites"
            )
        else:
            linea = (
                f"━━━━━━━━━━━━━ #{i} ━━━━━━━━━━━━━\n"
                f"📋 **Trámite:** {datos[0] or 'No registrado'}\n"
                f"📄 **Solicitud:** {datos[1] or 'No registrado'}\n"
                f"👥 **Cuadrilla:** {datos[2] or 'No registrada'}\n"
                f"📅 **Ejecución:** {datos[3] or 'No registrada'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 **Observación:**\n{datos[4] or 'No registrada'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🧰 **Nota materiales:** {datos[5] or 'No registrada'}\n"
                f"❌ **Motivo no ejecución:** {datos[6] or 'No registrado'}\n"
                f"📋 **Detalle:** {datos[7] or 'No registrado'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📟 **Medidor actual:** {datos[8] or '?'} / {datos[9] or '?'}\n"
                f"📊 **Contador:** {datos[10] or '?'} / {datos[11] or '?'}\n"
                f"🆕 **Medidor nuevo:** {_fmt_sin_decimales(datos[12]) or '?'} / {_fmt_sin_decimales(datos[13]) or '?'}\n"
                f"🔄 **Medidor retirado:** {_fmt_sin_decimales(datos[14]) or '?'} / {_fmt_sin_decimales(datos[15]) or '?'}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📟 **Medidor N°:** {datos[8] or 'No registrado'}\n"
                f"📋 **Motivo solicitud:** {motivo or 'No registrado'}"
            )
        if i == 1:
            await update.message.reply_text(f"🔍 **CUENTA CONTRATO: {numero}**\n\n{linea}")
        else:
            await update.message.reply_text(linea)
    log(f"Comando /cuenta_contrato {numero} - {len(resultados)} resultados")

async def datos_cuenta_contrato_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /datos_cuenta_contrato CUENTA - Datos del cliente del último trámite"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/datos_cuenta_contrato 200046107518`")
        return
    
    numero = context.args[0]
    await update.message.reply_text("👤 Buscando datos del cliente...")
    
    datos = buscar_ultimo_por_cuenta_contrato(numero)
    
    if not datos:
        await update.message.reply_text(f"❌ No se encontraron datos para cuenta contrato: {numero}")
        log(f"Comando /datos_cuenta_contrato {numero} - sin resultados")
        return
    
    # Formatear coordenadas como enlaces Google Maps
    lon, lat = datos[7], datos[8]
    maps_link = ""
    if lon and lat:
        try:
            maps_link = f"\n🗺️ [Ver en Google Maps](https://www.google.com/maps?q={lat},{lon})"
        except:
            pass
    
    respuesta = (
        f"👤 **DATOS DEL CLIENTE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **Identificación:** {datos[0] or 'No registrada'}\n"
        f"👤 **Cliente:** {datos[1] or 'No registrado'}\n"
        f"🏷️ **Tarifa:** {datos[2] or 'No registrada'}\n"
        f"📍 **Dirección:** {datos[3] or 'No registrada'}\n"
        f"📋 **MRU:** {datos[4] or 'No registrado'}\n"
        f"📟 **Medidor N°:** {datos[5] or 'No registrado'}\n"
        f"🔢 **Serie medidor:** {datos[6] or 'No registrada'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 **Coordenadas CNEL:**\n"
        f"   Lon: {lon or 'N/A'} | Lat: {lat or 'N/A'}{maps_link}"
    )
    await update.message.reply_text(respuesta)
    log(f"Comando /datos_cuenta_contrato {numero} ejecutado")

async def coordenadas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /coordenadas CUENTA_CONTRATO - Muestra coordenadas con Google Maps"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/coordenadas 201014401131`")
        return
    
    cuenta = context.args[0]
    from config import MAX_RESULTADOS
    await update.message.reply_text("🗺️ Buscando coordenadas...")
    
    resultados = buscar_por_cuenta_contrato(cuenta, MAX_RESULTADOS)
    
    if not resultados:
        await update.message.reply_text(f"❌ No se encontraron resultados para cuenta contrato: {cuenta}")
        log(f"Comando /coordenadas {cuenta} - sin resultados")
        return
    
    lineas = []
    for i, (tramite, fecha, lon_cnel, lat_cnel, lon_gps, lat_gps) in enumerate(resultados, 1):
        # Google Maps links solo si hay coordenadas válidas
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


async def resumenayer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resumenayer - Resumen de ejecutados del día anterior"""
    await update.message.reply_text("📊 *Generando resumen del día anterior...*", parse_mode='Markdown')

    try:
        # Calcular fecha de ayer
        ayer = datetime.now() - timedelta(days=1)
        fecha_ayer = ayer.strftime("%Y-%m-%d")
        # Intentar nombre del día en español
        try:
            locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
        except:
            pass
        dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        nombre_dia = dias_semana[ayer.weekday()]
        nombre_mes = meses[ayer.month - 1]
        fecha_legible = f"{nombre_dia} {ayer.day} de {nombre_mes} de {ayer.year}"

        # ===== 1. TOTAL EJECUTADOS =====
        total_result, _ = consultar_sqlite(f"""
            SELECT COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime') = DATE('now', 'localtime', '-1 day')
        """)
        total = total_result[0]["total"] if total_result else 0

        # ===== 2. POR TIPO DE TRÁMITE =====
        tipo_result, _ = consultar_sqlite(f"""
            SELECT codigo_cliente, COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime') = DATE('now', 'localtime', '-1 day')
              AND codigo_cliente IS NOT NULL AND codigo_cliente != ''
            GROUP BY codigo_cliente
            ORDER BY total DESC
        """)
        tipos = tipo_result or []

        # ===== 3. POR CUADRILLA =====
        cuadrilla_result, _ = consultar_sqlite(f"""
            SELECT cuadrilla, COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime') = DATE('now', 'localtime', '-1 day')
              AND cuadrilla IS NOT NULL AND cuadrilla != ''
            GROUP BY cuadrilla
            ORDER BY total DESC
        """)
        cuadrillas = cuadrilla_result or []

        # ===== ARMAR RESPUESTA =====
        lineas = []
        lineas.append(f"📊 *RESUMEN DEL DÍA ANTERIOR*")
        lineas.append(f"📅 {fecha_legible}")
        lineas.append("")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append(f"📈 *TOTAL EJECUTADOS:* **{total}**")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append("")

        if tipos:
            lineas.append("🏷️  *POR TIPO DE TRÁMITE:*")
            for row in tipos[:20]:
                codigo = row["codigo_cliente"].strip() if row["codigo_cliente"] else "Sin código"
                cant = row["total"]
                barra = "█" * min(cant, 15) + (" ▸" + str(cant) if cant > 15 else f" {cant}")
                lineas.append(f"▫️ `{codigo}` — {barra}")
            resto = len(tipos) - 20
            if resto > 0:
                lineas.append(f"   *… y {resto} tipos de trámite más*")
            lineas.append("")

        if cuadrillas:
            lineas.append("👥 *POR CUADRILLA:*")
            for row in cuadrillas:
                cuad = row["cuadrilla"].strip() if row["cuadrilla"] else "Sin asignar"
                cant = row["total"]
                barra = "█" * min(cant, 15) + (" ▸" + str(cant) if cant > 15 else f" {cant}")
                lineas.append(f"▫️ {cuad} — {barra}")
            lineas.append("")

        if total == 0:
            lineas.append("😴 No se registraron ejecuciones el día anterior.")
            lineas.append("")

        lineas.append("━━━━━━━━━━━━━━━━━━━━━")

        respuesta = "\n".join(lineas)
        await enviar_en_partes(update, respuesta)
        log(f"Comando /resumenayer - {total} ejecutados")

    except Exception as e:
        log(f"❌ Error en /resumenayer: {e}")
        await update.message.reply_text(f"❌ Error al generar el resumen: {e}")


async def resumendia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resumendia dd/mm/aaaa - Resumen de ejecutados de una fecha específica"""
    
    # Validar argumento
    if not context.args:
        await update.message.reply_text(
            "📅 *Uso correcto:* `/resumendia dd/mm/aaaa`\n\n"
            "Ejemplos:\n"
            "`/resumendia 15/06/2026`\n"
            "`/resumendia 30/06/2026`\n"
            "`/resumendia 01/06/2026`",
            parse_mode='Markdown'
        )
        return

    # Parsear fecha
    match = re.match(r'^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$', context.args[0])
    if not match:
        await update.message.reply_text(
            "❌ *Formato inválido.* Usa: `/resumendia dd/mm/aaaa`\n"
            "Ejemplo: `/resumendia 15/06/2026`",
            parse_mode='Markdown'
        )
        return

    dia, mes, anio = int(match.group(1)), int(match.group(2)), int(match.group(3))
    
    try:
        fecha = datetime(anio, mes, dia)
    except ValueError:
        await update.message.reply_text(f"❌ Fecha inválida: {context.args[0]}")
        return
    
    fecha_str = fecha.strftime("%Y-%m-%d")
    
    # Intentar nombre del día en español
    try:
        locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
    except:
        pass
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    nombre_dia = dias_semana[fecha.weekday()]
    nombre_mes = meses[fecha.month - 1]
    fecha_legible = f"{nombre_dia} {fecha.day} de {nombre_mes} de {fecha.year}"

    await update.message.reply_text(f"📊 *Generando resumen de {fecha_legible}...*", parse_mode='Markdown')

    try:
        # ===== 1. TOTAL EJECUTADOS =====
        total_result, _ = consultar_sqlite(f"""
            SELECT COUNT(*) as total
            FROM gestion_tramites
            WHERE fecha_ejecucion LIKE '{fecha_str}%'
        """)
        total = total_result[0]["total"] if total_result else 0

        # ===== 2. POR TIPO DE TRÁMITE =====
        tipo_result, _ = consultar_sqlite(f"""
            SELECT codigo_cliente, COUNT(*) as total
            FROM gestion_tramites
            WHERE fecha_ejecucion LIKE '{fecha_str}%'
              AND codigo_cliente IS NOT NULL AND codigo_cliente != ''
            GROUP BY codigo_cliente
            ORDER BY total DESC
        """)
        tipos = tipo_result or []

        # ===== 3. POR CUADRILLA =====
        cuadrilla_result, _ = consultar_sqlite(f"""
            SELECT cuadrilla, COUNT(*) as total
            FROM gestion_tramites
            WHERE fecha_ejecucion LIKE '{fecha_str}%'
              AND cuadrilla IS NOT NULL AND cuadrilla != ''
            GROUP BY cuadrilla
            ORDER BY total DESC
        """)
        cuadrillas = cuadrilla_result or []

        # ===== ARMAR RESPUESTA =====
        lineas = []
        lineas.append(f"📊 *RESUMEN DEL DÍA*")
        lineas.append(f"📅 {fecha_legible}")
        lineas.append("")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append(f"📈 *TOTAL EJECUTADOS:* **{total}**")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append("")

        if tipos:
            lineas.append("🏷️  *POR TIPO DE TRÁMITE:*")
            for row in tipos[:20]:
                codigo = row["codigo_cliente"].strip() if row["codigo_cliente"] else "Sin código"
                cant = row["total"]
                barra = "█" * min(cant, 15) + (" ▸" + str(cant) if cant > 15 else f" {cant}")
                lineas.append(f"▫️ `{codigo}` — {barra}")
            resto = len(tipos) - 20
            if resto > 0:
                lineas.append(f"   *… y {resto} tipos de trámite más*")
            lineas.append("")

        if cuadrillas:
            lineas.append("👥 *POR CUADRILLA:*")
            for row in cuadrillas:
                cuad = row["cuadrilla"].strip() if row["cuadrilla"] else "Sin asignar"
                cant = row["total"]
                barra = "█" * min(cant, 15) + (" ▸" + str(cant) if cant > 15 else f" {cant}")
                lineas.append(f"▫️ {cuad} — {barra}")
            lineas.append("")

        if total == 0:
            lineas.append("😴 No se registraron ejecuciones en esta fecha.")
            lineas.append("")

        lineas.append("━━━━━━━━━━━━━━━━━━━━━")

        respuesta = "\n".join(lineas)
        await enviar_en_partes(update, respuesta)
        log(f"Comando /resumendia {context.args[0]} - {total} ejecutados")

    except Exception as e:
        log(f"❌ Error en /resumendia: {e}")
        await update.message.reply_text(f"❌ Error al generar el resumen: {e}")


async def resumenmes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resumenmes - Resumen de ejecutados del mes hasta ayer"""
    await update.message.reply_text("📊 *Generando resumen del mes...*", parse_mode='Markdown')

    try:
        # Calcular fechas
        hoy = datetime.now()
        ayer = hoy - timedelta(days=1)
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        nombre_mes = meses[ayer.month - 1]
        fecha_legible = f"{nombre_mes.capitalize()} {ayer.year} (del 1 al {ayer.day})"

        # ===== 1. TOTAL EJECUTADOS DEL MES =====
        total_result, _ = consultar_sqlite("""
            SELECT COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime')
              BETWEEN DATE('now', 'localtime', 'start of month')
              AND DATE('now', 'localtime', '-1 day')
        """)
        total = total_result[0]["total"] if total_result else 0

        # ===== 2. POR TIPO DE TRÁMITE =====
        tipo_result, _ = consultar_sqlite("""
            SELECT codigo_cliente, COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime')
              BETWEEN DATE('now', 'localtime', 'start of month')
              AND DATE('now', 'localtime', '-1 day')
              AND codigo_cliente IS NOT NULL AND codigo_cliente != ''
            GROUP BY codigo_cliente
            ORDER BY total DESC
        """)
        tipos = tipo_result or []

        # ===== 3. POR CUADRILLA =====
        cuadrilla_result, _ = consultar_sqlite("""
            SELECT cuadrilla, COUNT(*) as total
            FROM gestion_tramites
            WHERE DATE(fecha_ejecucion, 'localtime')
              BETWEEN DATE('now', 'localtime', 'start of month')
              AND DATE('now', 'localtime', '-1 day')
              AND cuadrilla IS NOT NULL AND cuadrilla != ''
            GROUP BY cuadrilla
            ORDER BY total DESC
        """)
        cuadrillas = cuadrilla_result or []

        # ===== ARMAR RESPUESTA =====
        lineas = []
        lineas.append(f"📊 *RESUMEN DEL MES*")
        lineas.append(f"📅 {fecha_legible}")
        lineas.append("")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append(f"📈 *TOTAL EJECUTADOS:* **{total}**")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append("")

        TOP_LIMIT = 20

        if tipos:
            lineas.append("🏷️  *POR TIPO DE TRÁMITE (Top 20):*")
            max_bar = min(max(r["total"] for r in tipos[:TOP_LIMIT]), 20)
            for row in tipos[:TOP_LIMIT]:
                codigo = row["codigo_cliente"].strip() if row["codigo_cliente"] else "Sin código"
                cant = row["total"]
                barra = "█" * min(cant, max_bar) + (f" ▸{cant}" if cant > max_bar else f" {cant}")
                lineas.append(f"▫️ `{codigo}` — {barra}")
            resto = len(tipos) - TOP_LIMIT
            if resto > 0:
                lineas.append(f"   *… y {resto} tipos de trámite más*")
            lineas.append("")

        if cuadrillas:
            lineas.append("👥 *POR CUADRILLA:*")
            max_bar = min(max(r["total"] for r in cuadrillas), 20)
            for row in cuadrillas:
                cuad = row["cuadrilla"].strip() if row["cuadrilla"] else "Sin asignar"
                cant = row["total"]
                barra = "█" * min(cant, max_bar) + (f" ▸{cant}" if cant > max_bar else f" {cant}")
                lineas.append(f"▫️ {cuad} — {barra}")
            lineas.append("")

        if total == 0:
            lineas.append("😴 No se registraron ejecuciones este mes aún.")
            lineas.append("")

        lineas.append("━━━━━━━━━━━━━━━━━━━━━")

        respuesta = "\n".join(lineas)
        await enviar_en_partes(update, respuesta)
        log(f"Comando /resumenmes - {total} ejecutados (mes)")

    except Exception as e:
        log(f"❌ Error en /resumenmes: {e}")
        await update.message.reply_text(f"❌ Error al generar el resumen del mes: {e}")


async def sql_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /sql SQL_QUERY - Ejecuta SQL directo en la base de datos"""
    from database import ejecutar_sql_seguro

    if not context.args:
        await update.message.reply_text(
            "🗄️ *Ejecutar SQL directo*\n\n"
            "Ejemplo: `/sql SELECT COUNT(*) FROM recorrido_cuadrillas`\n"
            "Ejemplo: `/sql SELECT * FROM medidores LIMIT 5`\n\n"
            "⚠️ Solo SELECT permitido.",
            parse_mode='Markdown'
        )
        return

    sql = " ".join(context.args).strip()
    await update.message.reply_text("🗄️ *Ejecutando consulta...*", parse_mode='Markdown')

    try:
        resultados, columnas = ejecutar_sql_seguro(sql)

        if resultados is None:
            # columnas trae el mensaje de error
            msg = columnas if columnas else "❌ Error ejecutando la consulta"
            await update.message.reply_text(f"❌ {msg}")
            return

        if len(resultados) == 0:
            await update.message.reply_text("🔍 La consulta no devolvió resultados.")
            log(f"/sql: 0 resultados")
            return

        # Formatear resultados
        if len(resultados) == 1 and len(resultados[0]) == 1:
            # Un solo valor
            valor = resultados[0][0]
            nombre_col = columnas[0] if columnas else "Resultado"
            await update.message.reply_text(f"📊 *{nombre_col}:* **{valor}**", parse_mode='Markdown')
            log(f"/sql: {valor}")
            return

        # Múltiples resultados - formato tabla simple
        header = " | ".join(columnas) if columnas else ""
        filas = []
        for fila in resultados[:20]:
            vals = [str(v) if v is not None else "-" for v in fila]
            filas.append(" | ".join(vals))

        texto_filas = "\n".join(filas)
        total = len(resultados)

        respuesta = (
            f"🗄️ *Resultados ({total} filas)*\n"
            f"`{header}`\n"
            f"`{texto_filas}`"
        )
        if total > 20:
            respuesta += f"\n\n📌 Mostrando 20 de {total} filas"

        await update.message.reply_text(respuesta, parse_mode='Markdown')
        log(f"/sql: {total} filas")

    except Exception as e:
        log(f"❌ Error en /sql: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def orden_sap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /orden_sap NUMERO - Busca en ordenes_sap por numero_solicitud"""
    if not context.args:
        await update.message.reply_text("🔢 Ejemplo: `/orden_sap 21881541`\nBusca en las órdenes SAP por número de solicitud.")
        return

    numero = context.args[0].strip()
    await update.message.reply_text("🔍 *Buscando en órdenes SAP...*", parse_mode='Markdown')

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT * FROM ordenes_sap 
            WHERE numero_solicitud = ? OR numero_solicitud LIKE ?
            LIMIT 5
        """, (numero, f"%{numero}%"))
        filas = c.fetchall()
        conn.close()

        if not filas:
            await update.message.reply_text(f"❌ No se encontró ninguna orden con solicitud: {numero}")
            log(f"Comando /orden_sap {numero} - sin resultados")
            return

        for fila in filas:
            respuesta = (
                f"🔍 *ORDEN SAP*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 **N° Orden:** `{fila['numero_orden_sap']}`\n"
                f"📄 **N° Solicitud:** `{fila['numero_solicitud']}`\n"
                f"🏷️ **Estado:** `{fila['estado']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 **Distrito:** {fila['distrito'] or 'No registrado'}\n"
                f"🏠 **Calle/No.:** {fila['calle_no'] or 'No registrada'}\n"
                f"👤 **Usuario:** `{fila['usuario'] or 'No registrado'}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏘️ **Cantón:** {fila['canton'] or 'No registrado'}\n"
                f"🔧 **Actividad:** {fila['actividad_pm'] or 'No registrada'}\n"
                f"📅 **Fecha inicio:** {fila['fecha_inicio'] or 'No registrada'}"
            )
            await update.message.reply_text(respuesta, parse_mode='Markdown')

        if len(filas) == 1:
            log(f"Comando /orden_sap {numero} - 1 resultado")
        else:
            await update.message.reply_text(f"📌 *Mostrando {len(filas)} de {len(filas)} resultados*", parse_mode='Markdown')
            log(f"Comando /orden_sap {numero} - {len(filas)} resultados")

    except Exception as e:
        log(f"❌ Error en /orden_sap: {e}")
        await update.message.reply_text(f"❌ Error al buscar la orden: {e}")


async def resumen_reclamos_dia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /resumen_reclamos_dia - Reclamos RECL del día con detalle de no ejecutados"""
    await update.message.reply_text("📊 *Generando resumen de reclamos del día...*", parse_mode='Markdown')

    try:
        # Determinar fecha: argumento opcional (dd/mm/aaaa) o hoy por defecto
        if context.args:
            match = re.match(r'^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$', context.args[0])
            if not match:
                await update.message.reply_text(
                    "❌ *Formato inválido.* Usa: `/resumen_reclamos_dia dd/mm/aaaa`\n"
                    "Ejemplo: `/resumen_reclamos_dia 15/06/2026`\n"
                    "O sin parámetro para usar la fecha de hoy.",
                    parse_mode='Markdown'
                )
                return
            dia, mes, anio = int(match.group(1)), int(match.group(2)), int(match.group(3))
            try:
                fecha = datetime(anio, mes, dia)
            except ValueError:
                await update.message.reply_text(f"❌ Fecha inválida: {context.args[0]}")
                return
            fecha_str = fecha.strftime("%Y-%m-%d")
        else:
            fecha = datetime.now()
            fecha_str = fecha.strftime("%Y-%m-%d")

        dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        nombre_dia = dias_semana[fecha.weekday()]
        fecha_legible = f"{nombre_dia} {fecha.day} de {meses[fecha.month - 1]} de {fecha.year}"

        # Keywords de no ejecución
        keywords = [
            "no permite", "no conoce", "no responde", "no contesta",
            "no hay nadie", "no se encuentra", "se reprograma",
            "nadie en el predio", "no localizado"
        ]

        # ===== 1. TOTAL RECL ATENDIDOS =====
        total_result, _ = consultar_sqlite(f"""
            SELECT COUNT(*) as total
            FROM gestion_tramites
            WHERE codigo_cliente = 'RECL'
              AND fecha_ejecucion LIKE '{fecha_str}%'
        """)
        total = total_result[0]["total"] if total_result else 0

        # ===== 2. NO EJECUTADOS (por keywords en observacion_gestion) =====
        # Construir condiciones LIKE para cada keyword
        like_conditions = " OR ".join([f"LOWER(observacion_gestion) LIKE '%{kw}%'" for kw in keywords])

        no_ejec_result, _ = consultar_sqlite(f"""
            SELECT COUNT(*) as total
            FROM gestion_tramites
            WHERE codigo_cliente = 'RECL'
              AND fecha_ejecucion LIKE '{fecha_str}%'
              AND ({like_conditions})
        """)
        no_ejecutados = no_ejec_result[0]["total"] if no_ejec_result else 0

        # ===== 2b. TRÁMITES NO EJECUTADOS (con detalle) =====
        tramites_no_ejec, _ = consultar_sqlite(f"""
            SELECT numero_tramite, numero_solicitud, cuadrilla, observacion_gestion
            FROM gestion_tramites
            WHERE codigo_cliente = 'RECL'
              AND fecha_ejecucion LIKE '{fecha_str}%'
              AND ({like_conditions})
            ORDER BY cuadrilla
        """)
        no_ejec_detalle = tramites_no_ejec or []

        # ===== 3. POR CUADRILLA (top 5) =====
        cuadrilla_result, _ = consultar_sqlite(f"""
            SELECT cuadrilla, COUNT(*) as total
            FROM gestion_tramites
            WHERE codigo_cliente = 'RECL'
              AND fecha_ejecucion LIKE '{fecha_str}%'
              AND cuadrilla IS NOT NULL AND cuadrilla != ''
            GROUP BY cuadrilla
            ORDER BY total DESC
        """)
        cuadrillas = cuadrilla_result or []

        # ===== ARMAR RESPUESTA =====
        ejecutados = total - no_ejecutados
        porcentaje_no_ejec = round((no_ejecutados / total * 100), 1) if total > 0 else 0

        lineas = []
        lineas.append(f"📊 *RESUMEN DE RECLAMOS DEL DÍA*")
        lineas.append(f"📅 {fecha_legible}")
        lineas.append("")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")
        lineas.append(f"📈 *TOTAL RECL ATENDIDOS:* **{total}**")
        lineas.append(f"✅ *Ejecutados:* **{ejecutados}**")
        lineas.append(f"⚠️ *No ejecutados:* **{no_ejecutados}** ({porcentaje_no_ejec}%)")
        lineas.append("━━━━━━━━━━━━━━━━━━━━━")

        if no_ejec_detalle:
            lineas.append("")
            lineas.append("🚫 *TRÁMITES NO EJECUTADOS:*")
            for t in no_ejec_detalle:
                tramite = t["numero_tramite"]
                solicitud = t["numero_solicitud"] or "—"
                cuad = t["cuadrilla"] or "?"
                obs = (t["observacion_gestion"] or "")[:60].replace("\n", " ")
                lineas.append(f"▫️ (ACIIS `{tramite}`)(SAP `{solicitud}`) — {cuad}")
                lineas.append(f"   _{obs}_")
            lineas.append("")

        if cuadrillas:
            lineas.append("👥 *POR CUADRILLA:*")
            max_cuad = cuadrillas[0]["total"]
            for row in cuadrillas:
                cuad = row["cuadrilla"].strip()
                cant = row["total"]
                barra = "█" * max(1, int(cant / max_cuad * 12)) if max_cuad > 0 else "▎"
                lineas.append(f"▫️ {cuad} — {barra} {cant}")
            lineas.append("")

        if total == 0:
            lineas.append("😴 No se registraron reclamos RECL atendidos hoy.")
            lineas.append("")

        lineas.append("━━━━━━━━━━━━━━━━━━━━━")

        respuesta = "\n".join(lineas)
        await enviar_en_partes(update, respuesta)
        log(f"Comando /resumen_reclamos_dia - {total} RECL, {no_ejecutados} no ejecutados")

    except Exception as e:
        log(f"❌ Error en /resumen_reclamos_dia: {e}")
        await update.message.reply_text(f"❌ Error al generar el resumen de reclamos: {e}")
