#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seguimiento_ordenes_correo — Ciclo de correos CNEL.

Detecta correos de CNEL que piden "Favor su atención y gestión", extrae
cuenta contrato / número de orden del ASUNTO, y sigue la orden por tres tablas
para emitir un correo en cada hito alcanzado:

  Etapa 1 ASIGNADA      -> recorrido_cuadrillas (cuadrilla llena, sin ejecutar)
  Etapa 2 EJECUTADA     -> gestion_tramites     (fecha_ejecucion llena)
  Etapa 3 CIERRE TECNICO-> ordenes_sap          (estado = CIERRE_TECNICO)

Modo por defecto: --seco (NO envía nada, solo reporta lo que haría).
Para enviar de verdad: --enviar

Diseño completo: skill salchipapabot-ecosistema ->
  references/ciclo-correos-cnel-estados.md
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta

DB = "/data/data/com.termux/files/home/salchipapabot/gestion_medidores.db"
HIMALAYA = "/root/.local/bin/himalaya"
CUENTA = "normalizacionart"
ENV = {"HOME": "/root", "PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin"}

FRASE_GESTION = "favor su atencion y gestion"

# R3 (Miguel, 5-nov-2026): los correos que piden ENTREGA DE MEDIDOR no se
# contestan automáticamente. Son logística de bodega/laboratorio (entregar el
# medidor físico), no avance de orden en ACIIS/SAP — el sistema no puede
# acreditarlos y respondía con datos que no vienen al caso.
# Se detecta por el ASUNTO (es donde CNEL lo pide siempre). Palabras sin
# acento para no depender de la codificación.
RE_ENTREGA_MEDIDOR = re.compile(
    # "entrega de medidor" / "entrega del medidor" / "entregar el medidor"
    # ⚠️ 2026-11-14: faltaba el artículo "el". La frase REAL que usa CNEL es
    # "Favor su atención y gestión para que puedan entregar el medidor a
    # laboratorio" — con "el" el patrón no la capturaba y esos correos
    # entraban al ciclo (casos 7 y 12 de la revisión de Miguel).
    r"(entreg\w*\s+(de\s+|del\s+|el\s+)?medidor)"
    r"|(medidor\w*\s+.{0,20}?\s*entreg)"              # medidor ... entrega
    r"|(entreg\w*\s+a\s+laboratorio)"                 # entregar a laboratorio
    r"|(entrega\w*\s+a\s+laboratorio)",
    re.I)

# EMER / emergencia en el asunto (Miguel, 7-nov-2026): estas órdenes las pide
# CNEL con urgencia y llegan como reenvío del IS-U, SIN la frase de gestión.
# Se procesan igual — son las que más falta hacen atender.
#
# AMPLIADO 2026-11-13 (correo 8877, DOMICILIO SIN SERVICIO ELECTRICO):
# CNEL/ECU 911 despacha emergencias con estos asuntos SIN la frase de gestión y
# SIN la palabra "EMER", así que quedaban descartadas para siempre. Ahora entran
# al ciclo, se registran como NO_ENCONTRADA y se reintentan cada hora hasta que
# ACIIS publique la orden (que puede tardar ~40 min desde que la ingresan).
#
# ⚠️ Solo se aplica al ASUNTO y SOLO si NO es una respuesta ("Re:"). Medido el
# 13-nov: el patrón ancho capturaba 13 correos, de los cuales 12 eran "Re: SIN
# ENERGIA ..." de Antonio respondiendo a avisos que el bot YA había enviado.
# Esos 12 no deben re-procesarse: el anti-Re los deja fuera.
RE_EMER = re.compile(
    r"\bEMER\b|emergenc"
    r"|domicilio\s+sin\s+servicio"
    r"|sin\s+servicio\s+electrico"
    r"|sin\s+energia",
    re.I)

# Asunto que es respuesta a un hilo ya atendido: NO se toma como emergencia.
RE_RESPUESTA = re.compile(r"^\s*(re|rv|fw|fwd)\s*:", re.I)

# --- envío (SMTP Gmail, mismas credenciales que himalaya) ---
REMITENTE = "normalizacionart@gmail.com"
DESTINATARIO = "antonio.morales@cnel.gob.ec"   # Ing. Gustavo Antonio Morales, CNEL

# 🚫 BUZONES MUERTOS — nunca copiar, aunque vengan en el hilo original.
# Miguel (13-nov-2026): "sacala de la lista de destinatarios para siempre".
# `callcentergye@cnel.gob.ec` rebota con 554 5.7.1 Invalid-Recipient: la dirección
# ya no existe en CNEL, pero los hilos viejos la siguen trayendo en Cc y
# `destinatarios_original()` la heredaba → 23 rebotes DSN por cada 80 correos.
# Se filtra en TODAS las salidas (To y Cc). Para retirar una dirección de aquí,
# confirmar antes que el buzón esté operativo: si vuelve a existir, quitarla.
BUZONES_MUERTOS = {
    "callcentergye@cnel.gob.ec",
}
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
_CFG = "config"
_PASSWD_FILE = os.path.join(os.path.expanduser("~"), "." + _CFG, "himalaya", "passwd")


def _leer_password():
    with open(_PASSWD_FILE, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


SMTP_PASS = _leer_password()

FIRMA = (
    "Notificación automática de estado de la orden\n"
    "Consorcio Normalización ART · Contrato CNEL-GLR-AJ-2024-024\n"
    "Datos según ACIIS y SAP de CNEL al {fecha}"
)

SEP = "━" * 21


# ---------------------------------------------------------------- utilidades

def parse_fecha(valor):
    """Acepta 'DD/MM/YYYY [HH:MM]' (himalaya) o 'YYYY-MM-DD [HH:MM:SS]' (BD).

    IMPORTANTE: probar primero los formatos CON hora. Si se prueba
    '%d/%m/%Y' antes, trunca '05/11/2026 14:52' a '05/11/2026' y pierde
    la hora -> las horas transcurridas salen mal por el offset del día.
    """
    valor = (valor or "").strip()
    if not valor:
        return datetime.now()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor[:len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    return datetime.now()


def avisar_miguel(texto):
    """Envía un aviso interno a Miguel por Telegram (bot Salchipapabot)."""
    env_file = "/data/data/com.termux/files/home/salchipapabot/" + ".env"
    token = None
    try:
        with open(env_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                key = "TELEGRAM_TOKEN_" + "SALCHIPABOT"
                if line.startswith(key):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception as e:
        print(f"     no pude leer el token: {e}")
        return False

    if not token:
        return False

    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({
        "chat_id": "917073696",
        "text": texto,
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            import json as _j
            return bool(_j.load(r).get("ok"))
    except Exception as e:
        print(f"     telegram falló: {e}")
        return False


def destinatarios_original(correo_id):
    """Lee To y Cc del correo original y devuelve (to_list, cc_list).

    Responder a TODOS: To = remitente original; Cc = resto del To + todo el Cc.
    Se excluye la propia cuenta y se deduplica.
    """
    import email
    from email import policy
    from email.utils import getaddresses

    try:
        mime = himalaya(["message", "export", correo_id, "--full", "-a", CUENTA])
    except Exception:
        return [], []

    msg = email.message_from_string(mime, policy=policy.default)
    yo = REMITENTE.lower()

    frm = [a for _, a in getaddresses([msg.get("From", "")]) if a]
    to_raw = [a for _, a in getaddresses([msg.get("To", "")]) if a]
    cc_raw = [a for _, a in getaddresses([msg.get("Cc", "")]) if a]

    # To = quien escribio
    to_final = [a for a in frm if a.lower() != yo] or [a for a in to_raw if a.lower() != yo]

    # Cc = el resto del To + todo el Cc original
    vistos = {a.lower() for a in to_final} | {yo}
    cc_final = []
    for a in to_raw + cc_raw:
        al = a.lower()
        if al in vistos:
            continue
        vistos.add(al)
        cc_final.append(a)

    # 🚫 Nunca escribir a un buzón muerto: rebota y ensucia la bandeja con DSN.
    # Se filtra al final, sobre To y Cc, para cubrir también el caso de que el
    # remitente original sea esa misma dirección.
    to_final = [a for a in to_final if a.lower() not in BUZONES_MUERTOS]
    cc_final = [a for a in cc_final if a.lower() not in BUZONES_MUERTOS]

    return to_final, cc_final


def ultimo_del_hilo(correo_id):
    """Devuelve el ID del CORREO MÁS RECIENTE del hilo, en la carpeta INBOX.

    Regla de Miguel (5-nov-2026): si el correo es parte de un hilo, no se
    responde al correo viejo — se responde al ÚLTIMO mensaje del hilo, para
    que la respuesta quede al final y no se pierda en medio de la cadena.

    ⚠️ Los IDs de himalaya son POR CARPETA. Se busca en [Gmail]/Todos para
    encontrar el hilo completo, pero se DEVUELVE el ID de INBOX, porque el
    resto del script (destinatarios_original, etc.) lee con --folder INBOX.

    Devuelve (id_inbox, asunto) o (correo_id, None) si no hay hilo.
    """
    import email
    from email import policy

    try:
        mime = himalaya(["message", "export", correo_id, "--full", "-a", CUENTA])
        msg = email.message_from_string(mime, policy=policy.default)
    except Exception:
        return correo_id, None

    asunto = (msg.get("Subject") or "").strip()
    if not asunto:
        return correo_id, None

    base = re.sub(r"^\s*(re|rv|fw|fwd)\s*:\s*", "", asunto, flags=re.I).strip()
    if not base:
        return correo_id, None

    def _norm(s):
        return re.sub(r"^\s*(re|rv|fw|fwd)\s*:\s*", "", (s or ""), flags=re.I).strip().lower()

    objetivo = base.lower()
    # palabra clave sin acento para la búsqueda (himalaya no maneja acentos)
    clave = re.sub(r"[^A-Za-z0-9]", " ", base).split()
    clave = clave[0] if clave else None
    if not clave:
        return correo_id, None

    # 1) buscar el hilo completo en Todos (cubre INBOX + Enviados + etiquetas)
    def _buscar(folder):
        try:
            raw = himalaya(["envelope", "list", "-a", CUENTA, "--folder", folder,
                            "-s", "100", "--output", "json", "subject", clave])
            return json.loads(raw)
        except Exception:
            return []

    vistos_todos = _buscar("[Gmail]/Todos")
    cands = [m for m in vistos_todos if _norm(m.get("subject")) == objetivo]
    if not cands:
        return correo_id, None

    # 2) de esos, solo los que existen en INBOX (para tener su ID de INBOX)
    inbox = _buscar("INBOX")
    idx_inbox = {_norm(m.get("subject")) + "|" + (m.get("date") or ""): str(m.get("id"))
                 for m in inbox}
    elegibles = []
    for m in cands:
        k = _norm(m.get("subject")) + "|" + (m.get("date") or "")
        id_inbox = idx_inbox.get(k)
        if id_inbox:
            elegibles.append((parse_fecha(m.get("date") or ""), id_inbox, m.get("subject")))

    if not elegibles:
        return correo_id, None

    elegibles.sort(reverse=True)
    return elegibles[0][1], elegibles[0][2]


def _es_de_antonio(correo_id):
    """True si el ÚLTIMO correo del hilo lo envió antonio.morales@cnel.gob.ec."""
    import email
    from email import policy
    from email.utils import getaddresses

    try:
        mime = himalaya(["message", "export", correo_id, "--full", "-a", CUENTA])
        msg = email.message_from_string(mime, policy=policy.default)
    except Exception:
        return False
    frm = [a for _, a in getaddresses([msg.get("From", "")]) if a]
    return bool(frm) and frm[0].lower() == DESTINATARIO.lower()


def enviar_correo(asunto_original, cuerpo, correo_id=None):
    """Responde a TODOS (To + Cc del original) en el hilo. Devuelve (ok, detalle).

    Reglas de Miguel (5-nov-2026):
      R1. Si el correo pertenece a un hilo, se responde al ÚLTIMO del hilo.
      R2. Solo se envía automáticamente si ese último correo lo mandó
          antonio.morales@cnel.gob.ec. En cualquier otro caso NO se envía.
    """
    asunto = asunto_original if asunto_original.lower().startswith("re:") \
        else f"Re: {asunto_original}"

    # R3: pedidos de entrega de medidor NUNCA se contestan automáticamente
    if RE_ENTREGA_MEDIDOR.search(asunto) or RE_ENTREGA_MEDIDOR.search(asunto.replace("Re:", "")):
        return False, "OMITIDO: pide entrega de medidor (R3)"

    from email.message import EmailMessage
    import smtplib
    from email.utils import formatdate, make_msgid

    # R1: apuntar al último mensaje del hilo
    objetivo = correo_id
    if correo_id:
        ultimo, asunto_ultimo = ultimo_del_hilo(correo_id)
        if ultimo and ultimo != str(correo_id):
            print(f"     🔗 hilo: se responde al último ({ultimo}) en vez de {correo_id}")
            objetivo = ultimo
            if asunto_ultimo:
                asunto = asunto_ultimo if asunto_ultimo.lower().startswith("re:") \
                    else f"Re: {asunto_ultimo}"

    # R2: filtro — solo si el último del hilo es de Antonio Morales
    if objetivo and not _es_de_antonio(objetivo):
        return False, "OMITIDO: el último correo del hilo no es de antonio.morales@cnel.gob.ec"

    to_list, cc_list = ([], [])
    if objetivo:
        to_list, cc_list = destinatarios_original(objetivo)
    if not to_list:
        to_list = [DESTINATARIO]
        print(f"     ⚠️ no pude leer destinatarios del último — fallback a {DESTINATARIO}")

    msg = EmailMessage()
    msg["From"] = REMITENTE
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = asunto
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="normalizacionart.com")

    # Encadenar al hilo real (In-Reply-To / References del último mensaje)
    if objetivo:
        try:
            import email
            from email import policy
            _m = email.message_from_string(
                himalaya(["message", "export", objetivo, "--full", "-a", CUENTA]),
                policy=policy.default)
            mid = (_m.get("Message-ID") or "").strip()
            refs = (_m.get("References") or "").strip()
            if mid:
                msg["In-Reply-To"] = mid
                msg["References"] = (refs + " " + mid).strip() if refs else mid
        except Exception:
            pass

    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
            s.starttls()
            s.login(REMITENTE, SMTP_PASS)
            s.send_message(msg)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def horario_laboral():
    """Lunes-viernes, 08:00-18:00 (avisos internos a Miguel)."""
    now = datetime.now()
    return now.weekday() < 5 and 8 <= now.hour < 18


def _uid(mensaje):
    """Extrae el UID de IMAP de un envelope de himalaya (viene como str)."""
    try:
        return int(str(mensaje.get("id", 0)).strip())
    except (TypeError, ValueError):
        return 0


def norm(s):
    """Minúsculas sin acentos, para comparar la frase de gestión."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def dia(valor):
    """'2026-11-05 10:01:29' -> '2026-11-05' (o '' si vacío)."""
    return (valor or "")[:10]


def himalaya(args, timeout=120):
    r = subprocess.run([HIMALAYA] + args, capture_output=True, text=True,
                       timeout=timeout, env={**os.environ, **ENV})
    if r.returncode != 0:
        raise RuntimeError(f"himalaya falló: {r.stderr[:300]}")
    return r.stdout


# ------------------------------------------------------- extracción del asunto

RE_ORDEN = re.compile(
    r"(?:ORDEN(?:\s+SERVICIO|\s+DE\s+SERVICIO)?|IS-U\s*:\s*ORDEN\s+SERVICIO)\s*:?\s*(\d{6,12})",
    re.I)
# ⚠️ Formatos REALES de CNEL que el patrón de arriba no cubría (auditoría 2026-11-13):
#   "Re: EMER#  90005227 / CC# 1701742725 -  200060437932 /"
#   "Re: EMER 90008987 CC 200047644311  / GLR DURAN CENTRO"
#   "Re: APP# 15910063 /  EMER#  90009747 / CC# 0901808183 - 200045607484 /"
# El '#' y el prefijo EMER/APP dejaban el asunto como ignorado_sin_datos, con
# órdenes de EMERGENCIA mudas. Se prueban DESPUÉS del patrón canónico.
RE_ORDEN_ALT = re.compile(
    r"\b(?:EMER|APP|ORDEN)\s*#?\s*:?\s*(\d{6,12})", re.I)
RE_CUENTA = re.compile(
    r"(?:CTA|CT|AC|CC|CLTE|CUENTA(?:\s+CONTRATO)?)\s*[-#]?[:°º]?\s*:?(\d{8,13})", re.I)

# Orden CNEL "desnuda": sin ninguna etiqueta delante (Fix A, 2026-11-13).
# Caso real: "Re: DOMICILIIO S/E 90014210". Las órdenes del contrato arrancan
# con 8 o 9 y tienen 8 dígitos (verificado en ordenes_sap: 90012757, 80136678,
# 90014210). Se excluyen los prefijos de cuenta contrato (2000/2010, 12-13 díg)
# y los trámites (32xxxxxxxxx, 9 díg) porque el patrón exige exactamente 8.
RE_ORDEN_DESNUDA = re.compile(r"(?<!\d)([89]\d{7})(?!\d)")


def extraer(subject):
    """Devuelve (orden, cuenta). Cualquiera puede ser None."""
    s = subject or ""
    orden = cuenta = None

    m = RE_ORDEN.search(s)
    if m:
        orden = m.group(1)
    else:
        # Fallback para formatos CNEL con '#' o prefijo EMER/APP (ver RE_ORDEN_ALT).
        # El primer número de 6-12 dígitos tras la etiqueta se toma como orden.
        m = RE_ORDEN_ALT.search(s)
        if m:
            orden = m.group(1)

    # ⚠️ FALLBACK NÚMERO DESNUDO (Fix A, 2026-11-13).
    # Caso real: "Re: DOMICILIIO S/E 90014210" — la orden viene sin NINGUNA
    # etiqueta (ni ORDEN, ni EMER, ni CTA), así que los patrones de arriba no la
    # veían y el correo caía en `ignorado_sin_datos`. Era una emergencia por
    # falta de servicio que nunca entró al ciclo.
    #
    # Rango real de órdenes CNEL, verificado en ordenes_sap:
    #   solicitudes 900xxxxx -> orden 0000900xxxxx
    #   solicitudes 801xxxxx -> orden 0000801xxxxx
    # Los trámites son 32xxxxxxxxx (9 díg, otro rango) y las cuentas 2000/2010
    # (12-13 díg), así que no se pisan con este patrón.
    # Solo se aplica si aún no hay orden Y no hay cuenta: si el asunto trae una
    # cuenta 2000/2010, esa manda (es la clave de seguimiento preferida).
    if not orden:
        m = RE_ORDEN_DESNUDA.search(s)
        if m:
            orden = m.group(1)

    # ⚠️ REGLA DE NEGOCIO (Miguel, 2026-11-13): las cuentas contrato del
    # Consorcio ART empiezan con 2000 o 2010 desde oct-2024. Números como
    # 1701742725 (CC) o 15910063 (APP) NO son cuentas. En asuntos que traen
    # varios números ("EMER# 90005227 / CC# 1701742725 - 200060437932 /")
    # hay que quedarse con el que empieza 2000/2010, no con el primero.
    candidatas = []
    for m in RE_CUENTA.finditer(s):
        val = m.group(1)
        if orden and val == orden:
            continue
        candidatas.append(val)
    # Candidatas sueltas de 12-13 dígitos con prefijo 2000/2010 aunque no lleven
    # etiqueta delante ("EMER 90008987 ... - 200060437932").
    for m in re.finditer(r"(?<!\d)(20(?:00|10)\d{8,11})(?!\d)", s):
        if m.group(1) not in candidatas:
            candidatas.append(m.group(1))
    if candidatas:
        propias = [v for v in candidatas if v.startswith(("2000", "2010"))]
        cuenta = (propias or candidatas)[0]

    return orden, cuenta


def cuerpo_sin_cita(raw_mime):
    """Extrae el texto plano del cuerpo, cortando antes de la cita del correo anterior."""
    import email
    from email import policy

    try:
        msg = email.message_from_string(raw_mime, policy=policy.default)
    except Exception:
        return ""

    texto = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                texto = part.get_content()
                break
            except Exception:
                continue
    if not texto:
        # fallback HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_content()
                    texto = re.sub(r"<[^>]+>", " ", html)
                    break
                except Exception:
                    continue

    # cortar la cita
    cortes = [
        r"\nEl .{5,80} escribi[óo]:",
        r"\nDe:\s",
        r"\n-{2,}\s*Mensaje original",
        r"\nOn .{5,80} wrote:",
        r"\n_{10,}",
    ]
    for pat in cortes:
        m = re.search(pat, texto)
        if m:
            texto = texto[:m.start()]
    return texto


# ------------------------------------------------------------- acceso a datos

def conexion():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Blidaje del modo seco: tabla de ensayos (no toca el estado real).
    con.execute("""CREATE TABLE IF NOT EXISTS seco_ensayos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clave_orden TEXT NOT NULL,
        etapa TEXT NOT NULL,
        correo_id TEXT,
        asunto TEXT,
        ensayado_en TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(clave_orden, etapa))""")
    con.commit()
    return con


def buscar_orden(con, numero_orden, dias_atras=2, dias_adelante=2, fecha_correo=None):
    """Busca la solicitud en recorrido_cuadrillas con ventana +-2 días.

    ±2 y no ±1 (2026-11-14): ver la nota de buscar_por_cuenta() — el correo
    llega días después de que el trámite entró a ACIIS, no el mismo día.
    """
    base = parse_fecha(fecha_correo)
    desde = (base - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
    hasta = (base + timedelta(days=dias_adelante)).strftime("%Y-%m-%d")

    return con.execute("""
        SELECT numero_solicitud, numero_tramite, cuenta_contrato, cliente,
               cuadrilla, tipo_solicitud, fecha_analisis, fecha_planificacion,
               fecha_ejecucion, accion_ejecutada
          FROM recorrido_cuadrillas
         WHERE numero_solicitud = ?
           AND substr(fecha_analisis,1,10) BETWEEN ? AND ?
         ORDER BY fecha_analisis DESC
         LIMIT 1
    """, (numero_orden, desde, hasta)).fetchone()


# Desfase máximo (en días, valor absoluto) entre la fecha del correo y la de la
# orden para aceptarla en el fallback por cuenta. 21 = el mayor desfase
# legítimo observado en la BD (16 días, cuenta 201018245120) + margen.
# Por encima de esto se asume que la orden del correo AÚN NO llegó a la BD.
LIMITE_DIAS_FALLBACK = 21


def _dias_desde(iso_fecha, base):
    """Días entre `iso_fecha` y `base` (datetime). None si no se puede parsear.

    Puede ser negativo: la orden puede nacer DESPUÉS del correo.
    """
    if not iso_fecha:
        return None
    try:
        f = datetime.fromisoformat(str(iso_fecha)[:19])
    except Exception:
        try:
            f = datetime.strptime(str(iso_fecha)[:10], "%Y-%m-%d")
        except Exception:
            return None
    try:
        return (base - f).days
    except Exception:
        return None


def buscar_por_cuenta(con, cuenta, fecha_correo=None, dias_atras=2, dias_adelante=2):
    """Busca por cuenta contrato. Devuelve (fila, es_ambiguo).

    Ventana ±2 días (antes ±1). Medido el 14-nov sobre las 19 filas en
    NO_ENCONTRADA: el desfase real entre la fecha del correo y fecha_analisis
    de la orden no es de horas, es de DÍAS — el correo llega cuando el trámite
    ya lleva días en ACIIS. Casos verificados por Miguel:
      · 200049629641 -> correo 13-nov, analisis 11-nov (2 d)
      · 201020955476 -> correo 13-nov, analisis 07-nov (6 d); la orden se
        EJECUTÓ 1 h después del correo, pero la fila ya existía desde el 7.
    Con ±1 los dos casos se perdían. ±2 cubre el caso de 2 días completo y
    parte del de 6 (ese sigue necesitando otra vía). No se amplía más a
    propósito: con cuentas de muchas filas, una ventana ancha trae la orden
    equivocada (ver 201017959556, cuyo análisis es 238 días anterior).
    """
    base = parse_fecha(fecha_correo)
    desde = (base - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
    hasta = (base + timedelta(days=dias_adelante)).strftime("%Y-%m-%d")

    filas = con.execute("""
        SELECT numero_solicitud, numero_tramite, cuenta_contrato, cliente,
               cuadrilla, tipo_solicitud, fecha_analisis, fecha_planificacion,
               fecha_ejecucion, accion_ejecutada
          FROM recorrido_cuadrillas
         WHERE cuenta_contrato = ?
           AND substr(fecha_analisis,1,10) BETWEEN ? AND ?
         ORDER BY fecha_analisis DESC
    """, (cuenta, desde, hasta)).fetchall()

    if not filas:
        # ── Fallback: orden reciente por PLANIFICACIÓN (2026-11-14) ────────
        # Caso real 201020955476 (verificado por Miguel): el correo llegó el
        # 13-nov y la orden se ejecutó 1 h después, pero su fecha_analisis es
        # del 7-nov — 6 días antes. Con la ventana (aunque sea ±2) nunca entra.
        #
        # Se descartó ampliar la ventana: hay órdenes legítimamente viejas sin
        # ejecutar (201018245120, 16 días) y otras de hace meses que NO son la
        # orden del correo (201017959556, 237 días → traería la equivocada).
        #
        # El criterio NO es la fecha de nacimiento sino: "¿es la orden MÁS
        # RECIENTE de esta cuenta y es razonablemente contemporánea?". Se mira
        # fecha_planificacion (cuando se asignó a cuadrilla), que es lo que el
        # correo de CNEL está comunicando.
        #
        # LÍMITE_DIAS_FALLBACK = 21: el mayor desfase legítimo visto en la BD
        # es 16 días (201018245120) + margen. Por encima se considera que la
        # orden del correo NO ha llegado a la BD (ORDEN_NO_LLEGO).
        filas = con.execute("""
            SELECT numero_solicitud, numero_tramite, cuenta_contrato, cliente,
                   cuadrilla, tipo_solicitud, fecha_analisis, fecha_planificacion,
                   fecha_ejecucion, accion_ejecutada
              FROM recorrido_cuadrillas
             WHERE cuenta_contrato = ?
               AND COALESCE(NULLIF(TRIM(fecha_planificacion), ''),
                            fecha_analisis) IS NOT NULL
             ORDER BY COALESCE(NULLIF(TRIM(fecha_planificacion), ''),
                               fecha_analisis) DESC
        """, (cuenta,)).fetchall()

        if not filas:
            return None, False

        reciente = filas[0]
        ref = (reciente["fecha_planificacion"] or "").strip() or (reciente["fecha_analisis"] or "")
        d = _dias_desde(ref, base)
        # d puede ser negativo (la orden nació DESPUÉS del correo): es válido,
        # el correo pide y ACIIS crea la orden días más tarde (visto: -3 días).
        if d is None or abs(d) > LIMITE_DIAS_FALLBACK:
            return None, False
        return reciente, False

    if len(filas) == 1:
        return filas[0], False

    # desambiguar: solo las abiertas
    abiertas = [f for f in filas
                if not (f["fecha_ejecucion"] or "").strip()
                and (f["accion_ejecutada"] or "").upper() != "SI"]
    if len(abiertas) == 1:
        return abiertas[0], False
    return None, True


def tramite_ejecutado(con, numero_tramite):
    """Etapa 2: gestion_tramites con fecha_ejecucion llena."""
    if not numero_tramite:
        return None
    return con.execute("""
        SELECT numero_tramite, numero_solicitud, cuenta_contrato, estado,
               cuadrilla, fecha_ejecucion, observacion_gestion,
               motivo_no_ejecucion, detalle_no_ejecucion
          FROM gestion_tramites
         WHERE numero_tramite = ?
           AND fecha_ejecucion IS NOT NULL AND TRIM(fecha_ejecucion) <> ''
         LIMIT 1
    """, (numero_tramite,)).fetchone()


def cierre_tecnico(con, numero_solicitud):
    """Etapa 3: ordenes_sap en CIERRE_TECNICO.

    numero_orden_sap viene con 12 dígitos; numero_solicitud con 8.
    Se busca por numero_solicitud (sin padding) y por padding como respaldo.
    """
    if not numero_solicitud:
        return None
    row = con.execute("""
        SELECT s.numero_orden_sap, s.numero_solicitud, s.estado, s.distrito,
               s.calle_no, s.canton, s.actividad_pm, s.fecha_cambio_estado,
               COALESCE(u.nombre, s.usuario) AS usuario
          FROM ordenes_sap s
          LEFT JOIN usuarios_sap u ON u.codigo = s.usuario
         WHERE s.numero_solicitud = ?
           AND s.estado = 'CIERRE_TECNICO'
         LIMIT 1
    """, (numero_solicitud,)).fetchone()
    if row:
        return row
    pad = str(numero_solicitud).zfill(12)
    return con.execute("""
        SELECT s.numero_orden_sap, s.numero_solicitud, s.estado, s.distrito,
               s.calle_no, s.canton, s.actividad_pm, s.fecha_cambio_estado,
               COALESCE(u.nombre, s.usuario) AS usuario
          FROM ordenes_sap s
          LEFT JOIN usuarios_sap u ON u.codigo = s.usuario
         WHERE s.numero_orden_sap = ?
           AND s.estado = 'CIERRE_TECNICO'
         LIMIT 1
    """, (pad,)).fetchone()


# --------------------------------------------------------- composición correos

def correo_asignacion(rec, ahora):
    return (
        f"📄 SOLICITUD {rec['numero_solicitud']}\n"
        f"{SEP}\n"
        f"📋 Número de trámite: {rec['numero_tramite'] or '-'}\n"
        f"🏷 Tipo: {rec['tipo_solicitud'] or '-'}\n"
        f"👥 Cuadrilla: {rec['cuadrilla'] or '-'}\n"
        f"📅 Fecha planificación: {rec['fecha_planificacion'] or '-'}\n"
        f"🔢 Cuenta contrato: {rec['cuenta_contrato'] or '-'}\n"
        f"{SEP}\n\n"
        + FIRMA.format(fecha=ahora)
    )


def correo_ejecutada(rec, gt, ahora, nota_origen=None):
    """Correo de EJECUTADA.

    `gt` puede ser None cuando la ejecución está en recorrido_cuadrillas pero
    gestión de trámites aún no la registra (2026-11-14). En ese caso se usa la
    fecha de ejecución del propio recorrido y se añade `nota_origen` para que
    CNEL sepa de dónde sale el dato.
    """
    if gt:
        obs = (gt["observacion_gestion"] or "").strip()
        fecha_ej = gt["fecha_ejecucion"]
        cuenta_ej = gt["cuenta_contrato"] or (rec["cuenta_contrato"] or "-")
        if not obs:
            partes = [x.strip() for x in ((gt["motivo_no_ejecucion"] or "") or "",
                                          (gt["detalle_no_ejecucion"] or "") or "") if x and x.strip()]
            obs = " - ".join(partes) if partes else "SIN OBSERVACION"
    else:
        # Ejecución solo en recorrido_cuadrillas: NO se inventa observación.
        obs = "SIN OBSERVACION"
        fecha_ej = rec["fecha_ejecucion"]
        cuenta_ej = rec["cuenta_contrato"] or "-"
    if nota_origen:
        obs = f"{obs}\n({nota_origen})" if obs != "SIN OBSERVACION" else f"SIN OBSERVACION\n({nota_origen})"
    return (
        f"📄 SOLICITUD {rec['numero_solicitud']}\n"
        f"{SEP}\n"
        f"📋 Número de trámite: {rec['numero_tramite'] or '-'}\n"
        f"🏷 Tipo: {rec['tipo_solicitud'] or '-'}\n"
        f"👥 Cuadrilla: {rec['cuadrilla'] or '-'}\n"
        f"📅 Fecha ejecución: {fecha_ej}\n"
        f"🔢 Cuenta contrato: {cuenta_ej}\n"
        f"{SEP}\n"
        f"📝 Observación:\n{obs}\n\n"
        + FIRMA.format(fecha=ahora)
    )


def correo_en_proceso(rec, ahora):
    """Respuesta de cortesía para una orden REAL en BD que aún no se ejecuta.

    Miguel, 7-nov-2026: Antonio pide "confirmar gestión" de órdenes que están en
    tratamiento. El bot callaba (no hay avance que reportar) y CNEL quedaba sin
    respuesta. Esta plantilla NO inventa avance: reporta estado real de la BD.

    La frase de cierre se ajusta al estado REAL (Miguel, 7-nov-2026):
    - sin ejecución  → falta ejecución Y cierre técnico
    - ya ejecutada   → falta SOLO el cierre técnico (no prometer la ejecución)
    """
    sin_ejecucion = not (rec["fecha_ejecucion"] or "").strip()
    if sin_ejecucion:
        estado = "EN TRATAMIENTO — sin ejecución registrada"
        pendiente = ("Se notificará automáticamente al registrarse la ejecución "
                     "y el cierre técnico en ACIIS/SAP.")
    else:
        estado = f"EJECUTADA el {rec['fecha_ejecucion']} (pendiente cierre técnico)"
        pendiente = ("Se notificará automáticamente al registrarse el cierre "
                     "técnico en ACIIS/SAP.")
    return (
        f"📄 SOLICITUD {rec['numero_solicitud']}\n"
        f"{SEP}\n"
        f"📋 Número de trámite: {rec['numero_tramite'] or '-'}\n"
        f"🏷 Tipo: {rec['tipo_solicitud'] or '-'}\n"
        f"👥 Cuadrilla: {rec['cuadrilla'] or 'SIN ASIGNAR'}\n"
        f"📅 Fecha planificación: {rec['fecha_planificacion'] or '-'}\n"
        f"🔢 Cuenta contrato: {rec['cuenta_contrato'] or '-'}\n"
        f"{SEP}\n"
        f"📌 Estado: {estado}\n"
        f"{pendiente}\n\n"
        + FIRMA.format(fecha=ahora)
    )


def correo_cierre(sap, ahora):
    return (
        "🔍 ORDEN SAP\n"
        f"{SEP}\n"
        f"📋 N° Orden: {sap['numero_orden_sap']}\n"
        f"📄 N° Solicitud: {sap['numero_solicitud']}\n"
        f"🏷 Estado: {sap['estado']}\n"
        f"{SEP}\n"
        f"📍 Distrito: {sap['distrito'] or '-'}\n"
        f"🏠 Calle/No.: {sap['calle_no'] or '-'}\n"
        f"👤 Usuario: {sap['usuario'] or '-'}\n"
        f"{SEP}\n"
        f"🏘 Cantón: {sap['canton'] or '-'}\n"
        f"🔧 Actividad: {sap['actividad_pm'] or '-'}\n"
        f"📅 Fecha cierre técnico: {sap['fecha_cambio_estado'] or '-'}\n"
        f"{SEP}\n"
        f"✅ Cierre técnico registrado en SAP. Con esto queda concluido el "
        f"seguimiento de esta orden; no se enviarán más notificaciones.\n\n"
        + FIRMA.format(fecha=ahora)
    )


def historial_cuenta(con, cuenta):
    """Devuelve (numero_de_solicitudes_previas, fecha_ultima) para una cuenta."""
    if not cuenta:
        return (0, None)
    row = con.execute("""
        SELECT COUNT(*), MAX(fecha_analisis)
          FROM recorrido_cuadrillas
         WHERE cuenta_contrato = ?""", (cuenta,)).fetchone()
    if not row:
        return (0, None)
    n = row[0] or 0
    ult = (row[1] or "")[:10] or None
    return (n, ult)


def correo_no_encontrada(numero_orden, ahora):
    return (
        f"Estimados, respecto a la orden {numero_orden}, al momento no hemos registrado "
        f"su ingreso en nuestro sistema de gestión. Agradecemos nos confirmen el estado "
        f"o nos indiquen si requiere alguna acción de nuestra parte.\n\n"
        + FIRMA.format(fecha=ahora)
    )


# ------------------------------------------------------------------- principal

def enviar_resumen_diario():
    """Resumen del día para Miguel: correos enviados, pendientes, sin orden."""
    con = conexion()
    hoy = datetime.now().strftime("%Y-%m-%d")

    enviados = con.execute("""
        SELECT numero_orden, etapa_actual, cuadrilla_asignada,
               COALESCE(enviado_asignacion, enviado_en_proceso,
                        enviado_ejecutada, enviado_cierre) AS cuando
          FROM seguimiento_orden_correo
         WHERE substr(COALESCE(enviado_asignacion, enviado_en_proceso,
                               enviado_ejecutada, enviado_cierre),1,10) = ?
         ORDER BY cuando""", (hoy,)).fetchall()

    pendientes = con.execute("""
        SELECT numero_orden, estado, notas FROM seguimiento_orden_correo
         WHERE estado IN ('SIN_CUADRILLA','NO_ENCONTRADA','ORDEN_NO_LLEGO','AMBIGUO',
                          'PENDIENTE','PENDIENTE_REVISION')
         ORDER BY creado_en DESC LIMIT 20""").fetchall()

    lineas = [f"📬 *Seguimiento correos CNEL — {hoy}*", ""]
    lineas.append(f"*Enviados hoy:* {len(enviados)}")
    for e in enviados:
        lineas.append(f"  • {e['numero_orden']} — {e['etapa_actual']} — {e['cuadrilla_asignada'] or '-'}")

    if pendientes:
        lineas.append("")
        lineas.append(f"*Pendientes:* {len(pendientes)}")
        for x in pendientes:
            lineas.append(f"  • {x['numero_orden']} — {x['estado']}")

    if not enviados and not pendientes:
        lineas.append("")
        lineas.append("Sin novedades.")

    texto = "\n".join(lineas)
    print(texto)
    ok = avisar_miguel(texto)
    print("\nTelegram:", "enviado" if ok else "falló")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true", default=True,
                    help="no envía nada (por defecto)")
    ap.add_argument("--enviar", action="store_true",
                    help="envía los correos de verdad")
    ap.add_argument("--limite", type=int, default=60,
                    help="cuántos correos del INBOX revisar (default 60). "
                         "⚠️ NO subir mucho: con 200 tardaba ~6 min y se solapaba con "
                         "la corrida horaria siguiente (colisión 2026-11-13).")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--resumen", action="store_true",
                    help="solo enviar el resumen diario a Miguel (no barre correos)")
    args = ap.parse_args()

    if args.resumen:
        enviar_resumen_diario()
        return

    enviar_de_verdad = args.enviar
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    hoy = datetime.now().strftime("%Y-%m-%d")

    print("=" * 62)
    print(f"SEGUIMIENTO ORDENES CORREO — {'ENVÍO REAL' if enviar_de_verdad else 'MODO SECO'}")
    print(f"Fecha: {ahora}")
    print("=" * 62)

    # 1. traer correos
    # ── PUNTERO v2 (Jefe, 13-nov-2026) ──────────────────────────────────────
    # Antes se traían SIEMPRE los últimos `--limite` correos y se volvían a
    # evaluar todos: trabajo repetido y, peor, dos oportunidades de reprocesar.
    # Ahora se recuerda hasta qué UID ya se leyó (tabla puntero_imap) y solo se
    # miran los NUEVOS. Si el puntero no existe (primera vez) se usa --limite.
    #
    # ⚠️ El puntero es solo EFICIENCIA; la seguridad contra duplicados la da el
    # candado por (orden, etapa) en la BD. Por eso el puntero avanza SIEMPRE al
    # final, incluso si un envío falló: el estado real vive en la tabla.
    con = conexion()
    uid_previo = None
    try:
        fila_ptr = con.execute(
            "SELECT uid_ultimo FROM puntero_imap WHERE carpeta='INBOX'").fetchone()
        if fila_ptr:
            uid_previo = fila_ptr[0]
    except sqlite3.OperationalError:
        uid_previo = None   # tabla aún no creada (BD vieja) -> modo ventana

    # ── RECONCILIACIÓN DE HUÉRFANOS (Fix B, 13-nov-2026) ────────────────────
    # Problema real (orden 90012757, EMER persona de la tercera edad): el correo
    # se anotó en `correos_vistos` (línea ~923) y la corrida murió ANTES de crear
    # su fila en seguimiento_orden_correo. Resultado: el puntero ya lo había
    # pasado y `correos_vistos` lo daba por procesado -> quedaba invisible para
    # siempre, sin que CNEL recibiera ningún aviso.
    #
    # Regla: todo correo que esté en `correos_vistos` y NO tenga fila en
    # seguimiento_orden_correo es un huérfano. Se rebobina el puntero hasta
    # justo antes del huérfano más antiguo para que la próxima pasada lo relea.
    #
    # Es SEGURO rebobinar: el puntero es solo eficiencia (línea 809). El candado
    # contra duplicados es el estado por etapa en la tabla, así que los correos
    # que ya tienen fila se saltan solos.
    try:
        huerfanos = con.execute("""
            SELECT cv.uid, cv.numero_orden, cv.asunto
              FROM correos_vistos cv
              LEFT JOIN seguimiento_orden_correo s
                     ON s.numero_orden = cv.numero_orden
             WHERE s.numero_orden IS NULL
               AND cv.numero_orden IS NOT NULL
               AND cv.numero_orden <> ''
             ORDER BY cv.uid""").fetchall()
    except sqlite3.OperationalError:
        huerfanos = []

    if huerfanos:
        uid_min = min(int(h[0]) for h in huerfanos)
        print(f"\n♻️  HUÉRFANOS: {len(huerfanos)} correo(s) visto(s) sin fila en seguimiento")
        for h in huerfanos:
            print(f"     uid {h[0]} · orden {h[1]} · {(h[2] or '')[:50]}")
        nuevo_ptr = max(uid_min - 1, 0)
        if uid_previo is None or nuevo_ptr < uid_previo:
            con.execute("""INSERT INTO puntero_imap (carpeta, uid_ultimo, actualizado_en)
                           VALUES ('INBOX', ?, datetime('now','localtime'))
                           ON CONFLICT(carpeta) DO UPDATE SET
                             uid_ultimo=excluded.uid_ultimo,
                             actualizado_en=excluded.actualizado_en""", (nuevo_ptr,))
            con.commit()
            print(f"♻️  Puntero rebobinado {uid_previo} → {nuevo_ptr} para releerlos")
            uid_previo = nuevo_ptr

    raw = himalaya(["envelope", "list", "-a", CUENTA, "--folder", "INBOX",
                    "-s", str(args.limite), "--output", "json"])
    correos = json.loads(raw)

    # ── DETECCIÓN DE HUECOS DE UID (Fix B2, 13-nov-2026) ────────────────────
    # Problema real (orden 90014210, uid 8837): el puntero decía "leído hasta
    # 8878" pero la ventana de lectura solo traía los últimos N correos. Los que
    # quedaron entre el UID más viejo de la ventana y el puntero NUNCA se
    # evaluaron -- ni siquiera entraron a la lista. Al avanzar el puntero, esos
    # correos se perdían para siempre.
    #
    # Regla: si el UID más viejo de la ventana es MAYOR que (puntero + 1), hay un
    # hueco: existen correos con UID <= puntero que esta ventana no cubre.
    # Se rebobina el puntero al UID más viejo disponible - 1 y se procesa por
    # lotes: cada corrida lee una ventana completa nueva, y el puntero solo
    # avanza cuando el hueco queda cerrado.
    #
    # Como el puntero sigue siendo solo eficiencia (el candado real es el estado
    # por etapa en la tabla), rebobinar no puede duplicar correos.
    if correos:
        uids = sorted(_uid(m) for m in correos)
        uid_min_ventana = uids[0]
        if uid_previo is not None and uid_min_ventana > uid_previo + 1:
            hueco = uid_min_ventana - uid_previo - 1
            print(f"\n🕳️  HUECO DE UID: la ventana arranca en {uid_min_ventana} "
                  f"pero el puntero está en {uid_previo} (~{hueco} correo(s) sin evaluar)")
            nuevo_ptr = uid_min_ventana - 1
            con.execute("""INSERT INTO puntero_imap (carpeta, uid_ultimo, actualizado_en)
                           VALUES ('INBOX', ?, datetime('now','localtime'))
                           ON CONFLICT(carpeta) DO UPDATE SET
                             uid_ultimo=excluded.uid_ultimo,
                             actualizado_en=excluded.actualizado_en""", (nuevo_ptr,))
            con.commit()
            print(f"🕳️  Puntero rebobinado {uid_previo} → {nuevo_ptr} para cubrir el hueco")
            uid_previo = nuevo_ptr

    if uid_previo is not None:
        antes = len(correos)
        correos = [m for m in correos if _uid(m) > uid_previo]
        print(f"\nCorreos NUEVOS desde UID {uid_previo}: {len(correos)} "
              f"(ventana leída: {antes})")
    else:
        print(f"\nCorreos revisados: {len(correos)} (sin puntero — primera pasada)")
    stats = {"ignorado_sin_datos": 0, "sin_frase": 0, "sin_orden_bd": 0,
             "ambiguo": 0, "sin_cuadrilla": 0, "aviso_sin_cuadrilla": 0,
             "aviso_24h": 0, "aviso_48h": 0,
             "ya_enviado": 0, "candidatos": 0}
    # Órdenes ya atendidas EN ESTA CORRIDA: evita duplicados cuando dos correos
    # distintos del mismo hilo resuelven a la misma orden (ver guard anti-reenvío).
    ya_respondidas_ciclo = set()

    for m in correos:
        cid = str(m.get("id"))
        asunto = m.get("subject") or ""
        fecha_correo = (m.get("date") or "").strip()
        frm = m.get("from") or {}
        remitente = (frm.get("addr") or "") if isinstance(frm, dict) else ""

        # ⚠️ v2 (2026-11-13): la llave de control es SOLO la orden, no el correo.
        # Antes se consultaba por (correo_id, asunto_original); como CNEL responde
        # en hilos con asuntos distintos ("Re: EMER 90009992 / AC ..." vs
        # "Re: SIN ENERGIA AC- ..."), cada correo estrenaba fila con las columnas
        # enviado_* vacías y el candado por etapa nunca veía lo que la otra fila
        # ya había avisado. Caso real: 90009992 recibió EJECUTADA por una fila y
        # CIERRE por otra — dos correos a CNEL por la misma orden.
        #
        # Ahora hay UNA fila por orden. Todo se decide contra ella.
        # La extracción de orden/cuenta se hace AQUÍ (antes estaba más abajo) para
        # poder consultar el estado antes de gastar tiempo leyendo el cuerpo.
        orden, cuenta = extraer(asunto)
        clave_orden = orden or cuenta
        # ⚠️ Fix C (13-nov-2026): buscar la fila por ORDEN **o** por CUENTA.
        # El correo puede traer solo la cuenta mientras la fila se guardó con el
        # número de orden (o al revés). Si se consulta por una sola clave, el
        # candado queda ciego y la etapa se reenvía (así se duplicó la 90012757).
        ya = con.execute(
            "SELECT numero_orden, estado, aviso_48h_enviado, enviado_asignacion,"
            " enviado_en_proceso, enviado_ejecutada, enviado_cierre"
            " FROM seguimiento_orden_correo"
            " WHERE numero_orden = ?"
            "    OR (? IS NOT NULL AND numero_orden = ?)"
            "    OR (? IS NOT NULL AND cuenta_contrato = ?)"
            " ORDER BY (numero_orden = ?) DESC"
            " LIMIT 1",
            (clave_orden, orden, orden, cuenta, cuenta, clave_orden)).fetchone()
        if ya:
            # A partir de aquí la clave buena es la que ya está en la fila.
            clave_orden = ya["numero_orden"]
            # Los casos NO_ENCONTRADA y ORDEN_NO_LLEGO deben seguir evaluándose
            # cada hora hasta que aparezcan en la BD o se dispare el aviso de 48 h.
            # ORDEN_NO_LLEGO (2026-11-14) es el mismo comportamiento: la orden
            # puede publicarse en ACIIS más tarde y hay que reevaluar.
            pendiente_revision = (ya["estado"] in ("NO_ENCONTRADA", "ORDEN_NO_LLEGO")
                                  and not ya["aviso_48h_enviado"])
            # ⚠️ BUG ARREGLADO 2026-11-13 (orden 90012490): antes, cualquier fila ya
            # registrada hacía `continue` aquí, ANTES de llegar al guard por etapa de
            # la línea ~931. Como CNEL responde en el MISMO hilo (mismo correo_id y
            # mismo asunto "Re: ..."), la orden se respondía una sola vez (EN_PROCESO)
            # y quedaba congelada para siempre: 24 órdenes ejecutadas en campo nunca
            # recibieron el correo de EJECUTADA ni el de CIERRE.
            #
            # Ahora las filas EN_CICLO también se re-evalúan. El guard anti-reenvío de
            # más abajo consulta la columna de la ETAPA REAL, así que no hay riesgo de
            # duplicados: si la etapa no avanzó, `previo[0]` tiene fecha y se salta.
            if not pendiente_revision:
                etapas_previas = ("ASIGNADA", "EN_PROCESO", "EJECUTADA", "CERRADA")
                if ya["estado"] == "EN_CICLO":
                    # re-evaluar: la orden pudo avanzar de etapa en ACIIS/SAP
                    if args.verbose:
                        print(f"  [{cid}] EN_CICLO — reevaluando etapa real")
                elif ya["estado"] in etapas_previas:
                    if args.verbose:
                        print(f"  [{cid}] ya registrado ({ya['estado']}) — skip")
                    stats["ya_enviado"] += 1
                    continue
                else:
                    if args.verbose:
                        print(f"  [{cid}] ya registrado ({ya['estado']}) — skip")
                    stats["ya_enviado"] += 1
                    continue
            # ⚠️ Aunque NO_ENCONTRADA, si ya se envió una etapa NO_ASIGNADA no se
            # reenvía: basta un aviso por etapa (Miguel, 7-nov-2026: la 90002487
            # se respondió EJECUTADA el 6-nov 19:07 y se seguía respondiendo cada
            # hora porque el estado quedó NO_ENCONTRADA). Solo se permite reenviar
            # si la etapa NUEVA es distinta y superior (ASIGNADA→EJECUTADA→CERRADA).
            if args.verbose:
                print(f"  [{cid}] NO_ENCONTRADA aún abierta — reevaluando")

        # ── v2: REGISTRAR SIEMPRE ANTES DE RESOLVER ──────────────────────────
        # Decisión del Jefe (13-nov-2026): la búsqueda en gestion_medidores NO es
        # el juez que decide si un correo entra a nuestra tabla. Primero se
        # registra el correo (queda memoria de que lo vimos); después se intenta
        # resolver. Así un correo cuya orden aún no está en ACIIS no se pierde:
        # queda pendiente y se reintenta en los ciclos siguientes.
        #
        # Único requisito de entrada: tener orden (8 díg) o cuenta (2000/2010).
        # Eso filtra solo los rebotes (mailer-daemon), el spam y los listados,
        # que no traen ninguno de los dos — sin necesidad de listas de remitentes.
        if not orden and not cuenta:
            stats["ignorado_sin_datos"] += 1
            if args.verbose:
                print(f"  [{cid}] sin orden ni cuenta — ignorado ({asunto[:60]})")
            continue

        # Se anota el correo en la memoria por UID (para no reprocesarlo nunca
        # más, y para poder reintentarlo aunque salga de la ventana de lectura).
        if enviar_de_verdad:
            con.execute("""INSERT OR IGNORE INTO correos_vistos
                (uid, carpeta, numero_orden, cuenta, asunto, fecha_correo, es_respuesta)
                VALUES (?,?,?,?,?,?,?)""",
                (cid, "INBOX", clave_orden, cuenta, asunto, fecha_correo,
                 1 if RE_RESPUESTA.match(asunto) else 0))

        # R3: pedidos de entrega de medidor -> se ignoran (no se responde)
        # 2026-11-14: además del ASUNTO se mira el CUERPO. Casos 7 y 12 de la
        # revisión de Miguel: el asunto era genérico ("MEDIDOR INSTALADO ...")
        # y la petición real venía dentro: "Favor su atención y gestión para
        # que puedan entregar el medidor a laboratorio". Con solo el asunto
        # pasaban al ciclo y quedaban como NO_ENCONTRADA para siempre.
        try:
            mime_r3 = himalaya(["message", "export", cid, "--full", "-a", CUENTA])
            cuerpo_r3 = cuerpo_sin_cita(mime_r3)
        except Exception:
            cuerpo_r3 = ""
        if (RE_ENTREGA_MEDIDOR.search(asunto)
                or RE_ENTREGA_MEDIDOR.search(asunto.replace("Re:", ""))
                or RE_ENTREGA_MEDIDOR.search(cuerpo_r3)):
            stats["ignorado_entrega_medidor"] = stats.get("ignorado_entrega_medidor", 0) + 1
            print(f"  🚫 [{cid}] pide ENTREGA DE MEDIDOR — ignorado ({asunto[:60]})")
            continue

        # 2. revisar la frase en el cuerpo
        try:
            mime = himalaya(["message", "export", cid, "--full", "-a", CUENTA])
        except Exception as e:
            print(f"  [{cid}] no se pudo leer el cuerpo: {e}")
            continue
        cuerpo = cuerpo_sin_cita(mime)
        # EMER/emergencia: se procesa aunque el correo no traiga la frase de
        # gestión (Miguel, 7-nov-2026). Antonio reenvía el IS-U tal cual, sin la
        # frase, y esas son justamente las que pide atender urgente: exigir la
        # frase dejaba sin respuesta TODOS los EMER del día (verificado: 12 de
        # 17 correos de la mañana nunca se evaluaron).
        # ⚠️ El EMER puede venir SOLO en el cuerpo, no en el asunto: la orden
        # 90001175 llegó como "Fwd: IS-U: Orden servicio: …" (asunto limpio) y
        # el "ORDEN EMER" estaba dentro. Buscar en los dos.
        #
        # 2026-11-13: las frases eléctricas ("sin energia", "domicilio sin
        # servicio") SOLO cuentan en el ASUNTO y SOLO si el correo NO es una
        # respuesta. Si no, los "Re: SIN ENERGIA ..." de Antonio —que son
        # respuestas a avisos que YA enviamos— se reprocesarían.
        es_respuesta = bool(RE_RESPUESTA.match(asunto))
        if es_respuesta and not re.search(r"\bEMER\b|emergenc", asunto + " " + (cuerpo or ""), re.I):
            es_emer = False
        else:
            es_emer = bool(RE_EMER.search(asunto) or RE_EMER.search(cuerpo or ""))

        # ── v2 (13-nov-2026): la frase YA NO bloquea la entrada ──────────────
        # Antes, "sin frase de gestión y sin EMER" hacía continue y el correo se
        # descartaba para siempre. Fue lo que perdió el 8877 (DOMICILIO SIN
        # SERVICIO ELECTRICO, orden ingresada 40 min después): cuando ACIIS
        # publicó la orden, el correo ya nadie lo volvía a mirar.
        #
        # Ahora el correo YA quedó registrado arriba. Lo que decide la frase es
        # si RESPONDEMOS automáticamente, no si lo guardamos:
        #   • trae la frase        -> flujo normal, responde
        #   • es EMER              -> responde igual (urgencia manda)
        #   • ni frase ni EMER     -> queda registrado como PENDIENTE_REVISION y
        #                             NO se responde (evita contestar correos que
        #                             no pedían gestión); aparece en el informe.
        tiene_frase = FRASE_GESTION in norm(cuerpo)
        if not tiene_frase and not es_emer:
            stats["sin_frase"] += 1
            if enviar_de_verdad:
                # ⚠️ v2.1 (13-nov-2026): ANTES esta rama solo escribía en
                # correos_vistos y hacía continue, así que la ORDEN nunca se
                # registraba en seguimiento_orden_correo. Fue justo lo que pasó
                # con el 8877 (DOMICILIO SIN SERVICIO ELECTRICO, cta
                # 200048766238): quedó como "correo visto" pero sin fila de
                # seguimiento, así que cuando ACIIS publicó el trámite nadie la
                # re-evaluó y el bot quedó ciego a la orden.
                # Ahora la fila de seguimiento se crea IGUAL, en estado
                # PENDIENTE_REVISION: no se responde solo, pero la orden queda
                # en el radar y su avance (cuadrilla, ejecución) sí se vigila.
                con.execute("""INSERT OR IGNORE INTO seguimiento_orden_correo
                    (numero_orden, cuenta_contrato, estado, notas, correo_id_origen,
                     asunto_origen, creado_en)
                    VALUES (?,?,?,?,?,?,datetime('now','localtime'))""",
                    (clave_orden, cuenta, "PENDIENTE_REVISION",
                     "registrado sin frase de gestión — no se responde solo",
                     cid, asunto))
                con.commit()
            if args.verbose:
                print(f"  📝 [{cid}] sin frase de gestión — registrado, sin respuesta ({asunto[:55]})")
            continue
        if es_emer and not tiene_frase:
            print(f"  🚨 [{cid}] EMER sin frase de gestión — se procesa igual ({asunto[:55]})")

        # 3. ubicar la orden
        rec = None
        if orden:
            rec = buscar_orden(con, orden, fecha_correo=fecha_correo)
        if rec is None and cuenta:
            rec, ambiguo = buscar_por_cuenta(con, cuenta, fecha_correo=fecha_correo)
            if ambiguo:
                stats["ambiguo"] += 1
                print(f"  ⚠️ [{cid}] AMBIGUO — cuenta {cuenta} con varias solicitudes abiertas")
                if enviar_de_verdad:
                    con.execute("""INSERT OR IGNORE INTO seguimiento_orden_correo
                        (numero_orden, cuenta_contrato, estado, notas, correo_id_origen,
                         asunto_origen, creado_en)
                        VALUES (?,?,?,?,?,?,datetime('now','localtime'))""",
                        (clave_orden, cuenta, "AMBIGUO",
                         "varias solicitudes abiertas para la misma cuenta", cid, asunto))
                    con.commit()
                continue

        # ── ALINEACIÓN DE LA CLAVE (Fix C, 13-nov-2026) ────────────────────
        # ⚠️ BUG GRAVE que este bloque corrige (orden 56342231, duplicado real):
        #
        # `clave_orden` se calculaba como `orden or cuenta` ANTES de resolver la
        # solicitud. Cuando el correo solo traía CUENTA (p.ej. "Re: DESCONEXION
        # DEFINITIVA 200048065235"), clave_orden quedaba en la cuenta. Pero al
        # resolver por cuenta, `rec["numero_solicitud"]` da la ORDEN real
        # (56342231), y la fila se guardaba con numero_orden='56342231'.
        #
        # Resultado: el guard anti-reenvío consultaba
        #     WHERE numero_orden = '200048065235'   -> no encontraba nada
        # y volvía a enviar la misma etapa en cada barrido. Así se duplicó el
        # correo de la 90012757 a CNEL (dos mensajes idénticos, 21:05).
        #
        # La clave de PERSISTENCIA y de CANDADO debe ser la ORDEN real, siempre.
        # Se reasigna aquí, una sola vez, antes de cualquier INSERT/UPDATE/guard.
        if rec is not None:
            clave_orden = str(rec["numero_solicitud"] or clave_orden)

        if rec is None:
            stats["sin_orden_bd"] += 1
            tipo = "orden" if orden else "cuenta"
            base_f = parse_fecha(fecha_correo) if fecha_correo else datetime.now()
            horas = (datetime.now() - base_f).total_seconds() / 3600

            # v2: UNA fila por orden. Si ya existe (por otro correo del hilo o de
            # un ciclo anterior), se conserva; solo se crea si es nueva.
            existente = con.execute(
                "SELECT * FROM seguimiento_orden_correo WHERE numero_orden=?",
                (clave_orden,)).fetchone()

            if not existente:
                # ── ORDEN_NO_LLEGO vs NO_ENCONTRADA (2026-11-14) ──────────
                # Antes todo caía en NO_ENCONTRADA, que mezclaba dos cosas muy
                # distintas y hacía ilegible el informe:
                #
                #  · ORDEN_NO_LLEGO: el correo es legítimo (trae orden o cuenta
                #    real) pero esa orden NO está en recorrido_cuadrillas. No es
                #    un fallo del bot: ACIIS todavía no la publicó. Casos 2 y 4
                #    de la revisión de Miguel: "nunca llegó la orden a nosotros".
                #  · NO_ENCONTRADA: la cuenta SÍ tiene filas, pero ninguna que
                #    corresponda al correo (fuera del fallback de 21 días). Eso
                #    sí puede ser un desfase que hay que mirar.
                #
                # Se distingue con una consulta barata: ¿existe la cuenta?
                if cuenta and con.execute(
                        "SELECT 1 FROM recorrido_cuadrillas WHERE cuenta_contrato=? LIMIT 1",
                        (cuenta,)).fetchone():
                    estado_nuevo = "NO_ENCONTRADA"
                    nota_nueva = "cuenta existe pero sin orden en la ventana ni en el fallback de 21 d"
                else:
                    estado_nuevo = "ORDEN_NO_LLEGO"
                    nota_nueva = "la orden aún no está en recorrido_cuadrillas (ACIIS no la publicó)"

                if enviar_de_verdad:
                    con.execute("""INSERT OR IGNORE INTO seguimiento_orden_correo
                        (numero_orden, cuenta_contrato, estado, notas, correo_id_origen,
                         asunto_origen, creado_en)
                        VALUES (?,?,?,?,?,?,datetime('now','localtime'))""",
                        (clave_orden, cuenta, estado_nuevo, nota_nueva, cid, asunto))
                    con.commit()
                existente = con.execute(
                    "SELECT * FROM seguimiento_orden_correo WHERE numero_orden=?",
                    (clave_orden,)).fetchone()

            # AVISOS DE ÓRDENES NO ENCONTRADAS
            # Política (5-nov-2026, decisión de Miguel):
            #   24 h -> NO se escribe a CNEL. Solo marca interna, sin envío.
            #   48 h -> aviso interno a Miguel por Telegram; él decide la acción.
            env48 = existente["aviso_48h_enviado"] if existente else None

            if horas >= 48 and not env48:
                stats["aviso_48h"] += 1
                hist = historial_cuenta(con, cuenta) if cuenta else (0, None)
                print(f"  🔔 [{cid}] {tipo} {clave_orden} — {horas:.0f} h SIN APARECER "
                      f"→ AVISO INTERNO A MIGUEL (historial: {hist[0]} previas)")
                if enviar_de_verdad:
                    ok = avisar_miguel(
                        f"🔔 Orden sin aparecer (48 h)\n"
                        f"Orden/Cuenta: {clave_orden}\n"
                        f"Asunto: {asunto[:120]}\n"
                        f"Correo recibido: {fecha_correo}\n"
                        f"Historial de la cuenta: {hist[0]} solicitudes previas"
                        + (f" (última {hist[1]})" if hist[1] else "") + "\n"
                        f"No está en recorrido_cuadrillas ni en gestión de trámites.\n"
                        f"Decide tú: responder a CNEL o esperar.")
                    if ok:
                        con.execute("""UPDATE seguimiento_orden_correo
                                       SET aviso_48h_enviado=datetime('now','localtime'),
                                           actualizado_en=datetime('now','localtime')
                                     WHERE numero_orden=?""", (clave_orden,))
                        con.commit()
                else:
                    print("     (modo seco: no se avisa)")
            elif horas >= 24 and not env48:
                print(f"  ⏳ [{cid}] {tipo} {clave_orden} — {horas:.1f} h. Sin aviso a CNEL "
                      f"(política: solo aviso interno a las 48 h). Faltan {48 - horas:.1f} h.")
            else:
                print(f"  ⏳ [{cid}] {tipo} {clave_orden} — {horas:.1f} h, sin aviso (umbral 48 h)")
            continue

        # 4. sin cuadrilla -> nada (pero avisar a las 5 h en horario laboral)
        if not (rec["cuadrilla"] or "").strip():
            stats["sin_cuadrilla"] += 1
            base_f = parse_fecha(fecha_correo) if fecha_correo else datetime.now()
            horas = (datetime.now() - base_f).total_seconds() / 3600
            if horas >= 5 and horario_laboral():
                stats["aviso_sin_cuadrilla"] += 1
                print(f"  ⏰ [{cid}] SIN CUADRILLA hace {horas:.0f} h — AVISAR A MIGUEL "
                      f"(solicitud {rec['numero_solicitud']}, cta {rec['cuenta_contrato']})")
                if enviar_de_verdad:
                    con.execute("""INSERT OR IGNORE INTO seguimiento_orden_correo
                        (numero_orden, cuenta_contrato, estado, notas, correo_id_origen,
                         asunto_origen, creado_en)
                        VALUES (?,?,'SIN_CUADRILLA',?,?,?,datetime('now','localtime'))""",
                        (clave_orden, rec["cuenta_contrato"],
                         f"aviso 5h {datetime.now():%Y-%m-%d %H:%M}", cid, asunto))
                    con.commit()
            elif args.verbose:
                print(f"  [{cid}] sin cuadrilla hace {horas:.1f} h — espera")
            continue

        # 5. decidir etapa (solo la actual)
        etapa = None
        cuerpo_mail = None
        gt = None
        sap = cierre_tecnico(con, rec["numero_solicitud"])
        if sap:
            etapa, cuerpo_mail = "CERRADA", correo_cierre(sap, ahora)
        else:
            gt = tramite_ejecutado(con, rec["numero_tramite"])
            if gt:
                etapa, cuerpo_mail = "EJECUTADA", correo_ejecutada(rec, gt, ahora)
            elif not (rec["fecha_ejecucion"] or "").strip():
                # Orden REAL en BD pero sin ejecución: antes se callaba y CNEL
                # quedaba sin respuesta. Se contesta "en proceso" (una sola vez).
                # Si el asunto es EMER se usa la misma plantilla (misma etapa).
                etapa, cuerpo_mail = "EN_PROCESO", correo_en_proceso(rec, ahora)
            else:
                # ── EJECUTADA EN CAMPO, NO EN gestion_tramites (2026-11-14) ──
                # Hueco real: `fecha_ejecucion` de recorrido_cuadrillas tiene
                # fecha, pero gestion_tramites NO registra el trámite. Antes
                # caía en el `if not etapa: continue` de abajo y el correo a
                # CNEL NUNCA salía — silencio total aunque el trabajo estaba
                # hecho en campo.
                #
                # Decisión de Miguel: en ese caso SÍ se avisa ("b) Avisar").
                # Casos 5 y 6 de la revisión del 14-nov: "la orden sí fue
                # atendida días atrás" / "la orden sí fue atendida hoy".
                #
                # La etapa es EJECUTADA y usa la MISMA plantilla, con una
                # observación que aclara el origen del dato, para que CNEL no
                # crea que viene de gestión de trámites.
                etapa = "EJECUTADA"
                cuerpo_mail = correo_ejecutada(
                    rec, gt, ahora,
                    nota_origen=("ejecución registrada en el recorrido de "
                                 "cuadrillas; gestion_tramites aún sin registro"),
                )

        if not etapa:
            if args.verbose:
                print(f"  [{cid}] sin etapa determinable — skip")
            continue

        # 🚫 GUARD ANTI-REENVÍO (Miguel, 7-nov-2026): una sola respuesta por
        # etapa y por orden. Si esa etapa ya se envió, no se repite nunca más
        # (basta un aviso hasta el cierre técnico). Esto cortó el bucle de la
        # 90002487, que se respondía EJECUTADA cada hora porque su fila quedó
        # con estado NO_ENCONTRADA y por eso se reevaluaba en cada ciclo.
        col_etapa = {"ASIGNADA": "enviado_asignacion",
                     "EN_PROCESO": "enviado_en_proceso",
                     "EJECUTADA": "enviado_ejecutada",
                     "CERRADA": "enviado_cierre"}[etapa]
        # v2: la llave es SOLO la orden. Una fila por orden, sin importar cuántos
        # correos del hilo hayan llegado con asuntos distintos.
        previo = con.execute(
            f"SELECT {col_etapa} FROM seguimiento_orden_correo"
            f" WHERE numero_orden = ?", (clave_orden,)
        ).fetchone()
        # 🚫 DUPLICADO DENTRO DEL MISMO CICLO (auditoría 2026-11-13):
        # el sello en BD se escribe DESPUÉS de enviar, así que dos correos del
        # mismo hilo que resuelven a la MISMA orden (p.ej. [8793] por cuenta y
        # [8778] por número) pasaban ambos el guard y generaban dos correos
        # idénticos a CNEL. Se lleva un set en memoria por corrida.
        clave_ciclo = (clave_orden, etapa)
        if clave_ciclo in ya_respondidas_ciclo:
            if args.verbose:
                print(f"  [{cid}] {etapa} ya respondida en ESTE ciclo — sin reenvío")
            stats["ya_enviado"] += 1
            continue
        if previo and previo[0]:
            if args.verbose:
                print(f"  [{cid}] {etapa} ya respondida ({previo[0]}) — sin reenvío")
            con.execute(
                "UPDATE seguimiento_orden_correo SET estado = 'EN_CICLO',"
                " notas = 'respondida; sin reenvío por etapa',"
                " actualizado_en = datetime('now','localtime')"
                " WHERE numero_orden = ?", (clave_orden,))
            con.commit()
            stats["ya_enviado"] += 1
            continue

        stats["candidatos"] += 1
        # Se registra también en MODO SECO: el seco debe predecir exactamente lo
        # que haría el envío real (si no, muestra duplicados que nunca se enviarían
        # y esconde los que sí — el seco dejaría de ser una prueba fiable).
        ya_respondidas_ciclo.add(clave_ciclo)
        print(f"\n  ✅ [{cid}] {etapa} — solicitud {rec['numero_solicitud']} "
              f"| cuadrilla {rec['cuadrilla']}")
        print("  " + "-" * 58)
        for linea in cuerpo_mail.splitlines():
            print("  " + linea)
        print("  " + "-" * 58)

        # ── BLINDAJE DEL MODO SECO (14-nov-2026) ────────────────────────────
        # Problema: en seco NINGUNA escritura se ejecutaba, así que el ensayo no
        # dejaba rastro. Dos corridas en seco seguidas mostraban el MISMO
        # candidato como si fuera nuevo, y el estado del sistema quedaba listo
        # para repetir. (El duplicado real de la 90012757 tuvo como causa el
        # Fix C, ya corregido, pero el seco "mudo" impedía verlo venir.)
        #
        # Ahora el seco deja constancia en `seco_ensayos`, tabla SEPARADA que no
        # toca el estado real. Así:
        #   • correr el seco dos veces muestra el 2º como "ya ensayado" (idempotente)
        #   • la BD de seguimiento queda intacta
        #   • un envío real borra el ensayo de esa (orden, etapa): el seco vuelve
        #     a ser limpio después de enviar
        if not enviar_de_verdad:
            ensayado = con.execute(
                "SELECT ensayado_en FROM seco_ensayos WHERE clave_orden=? AND etapa=?",
                (clave_orden, etapa)).fetchone()
            if ensayado:
                print(f"  🧪 ya ENSAYADO en seco ({ensayado[0]}) — no cuenta como nuevo")
            else:
                con.execute("""INSERT OR IGNORE INTO seco_ensayos
                    (clave_orden, etapa, correo_id, asunto)
                    VALUES (?,?,?,?)""", (clave_orden, etapa, cid, asunto))
                con.commit()
            continue

        # Envío real: se limpia el ensayo previo de esta etapa para que un seco
        # posterior la vuelva a mostrar como candidata si hiciera falta.
        con.execute("DELETE FROM seco_ensayos WHERE clave_orden=? AND etapa=?",
                    (clave_orden, etapa))

        if enviar_de_verdad:
            # v2: UPSERT sobre la orden (una sola fila). No hay correo_id en la
            # llave; el correo que originó la fila se guarda aparte si es nueva.
            con.execute("""INSERT INTO seguimiento_orden_correo
                (numero_orden, cuenta_contrato, correo_id_origen, asunto_origen,
                 etapa_actual, cuadrilla_asignada, fecha_planificacion, fecha_ejecucion,
                 fecha_cierre_sap, observacion_gestion, estado, creado_en, actualizado_en)
                VALUES (?,?,?,?,?,?,?,?,?,?,'EN_CICLO',
                        datetime('now','localtime'), datetime('now','localtime'))
                ON CONFLICT(numero_orden) DO UPDATE SET
                    etapa_actual=excluded.etapa_actual,
                    cuadrilla_asignada=excluded.cuadrilla_asignada,
                    fecha_ejecucion=COALESCE(excluded.fecha_ejecucion, fecha_ejecucion),
                    fecha_cierre_sap=COALESCE(excluded.fecha_cierre_sap, fecha_cierre_sap),
                    observacion_gestion=COALESCE(excluded.observacion_gestion, observacion_gestion),
                    estado='EN_CICLO',
                    actualizado_en=datetime('now','localtime')""",
                (clave_orden, rec["cuenta_contrato"], cid, asunto,
                 etapa, rec["cuadrilla"], rec["fecha_planificacion"],
                 rec["fecha_ejecucion"],
                 sap["fecha_cambio_estado"] if sap else None,
                 (gt["observacion_gestion"] if gt else None)))
            con.commit()
            ok, detalle = enviar_correo(asunto, cuerpo_mail, correo_id=cid)
            if ok:
                # registro para el guard intra-ciclo (ver arriba)
                ya_respondidas_ciclo.add(clave_ciclo)
                col = {"ASIGNADA": "enviado_asignacion",
                       "EN_PROCESO": "enviado_en_proceso",
                       "EJECUTADA": "enviado_ejecutada",
                       "CERRADA": "enviado_cierre"}[etapa]
                con.execute(f"""UPDATE seguimiento_orden_correo
                                SET {col} = datetime('now','localtime'),
                                    actualizado_en = datetime('now','localtime')
                              WHERE numero_orden = ?""",
                            (clave_orden,))
                con.commit()
                _to, _cc = destinatarios_original(cid)
                print(f"  📤 ENVIADO → To: {', '.join(_to)}"
                      + (f" | Cc: {', '.join(_cc)}" if _cc else ""))
            else:
                con.execute("""UPDATE seguimiento_orden_correo SET notas = ?,
                               actualizado_en = datetime('now','localtime')
                               WHERE numero_orden = ?""",
                            (f"ENVIO FALLIDO: {detalle[:200]}", clave_orden))
                con.commit()
                print(f"  ❌ FALLÓ EL ENVÍO: {detalle[:200]}")

    # ── Avance del puntero ──────────────────────────────────────────────────
    # Se avanza al UID más alto visto en esta corrida. Se hace SIEMPRE, incluso
    # si algún envío falló: el estado real (qué etapa ya se avisó) vive en la
    # tabla, y el candado por (orden, etapa) impide reenviar. El puntero es solo
    # para no releer lo mismo cada hora.
    if enviar_de_verdad and correos:
        max_uid = max((_uid(m) for m in correos), default=0)
        if max_uid > 0 and (uid_previo is None or max_uid > uid_previo):
            con.execute("""INSERT INTO puntero_imap (carpeta, uid_ultimo, actualizado_en)
                           VALUES ('INBOX', ?, datetime('now','localtime'))
                           ON CONFLICT(carpeta) DO UPDATE SET
                             uid_ultimo=excluded.uid_ultimo,
                             actualizado_en=excluded.actualizado_en""", (max_uid,))
            con.commit()
            print(f"\n🔖 Puntero INBOX → UID {max_uid}")

    print("\n" + "=" * 62)
    print("RESUMEN")
    for k, v in stats.items():
        print(f"  {k:<20} {v}")
    print("=" * 62)
    con.close()


if __name__ == "__main__":
    main()
