# SalchipapaBot 🤖

Bot de Telegram para gestión de **medidores eléctricos** del Consorcio ART. Consulta, administra y visualiza datos de trámites, cuadrillas y fotos desde una base SQLite.

## 🧩 Funcionalidades

- **Consulta de trámites** por número — muestra estado, fecha, cuadrilla, observaciones
- **Fotos de trámites** — descarga y envía hasta 10 fotos por trámite desde los servidores de Altura
- **Resumen de reclamos diarios** — trámites tipo RECL ejecutados en el día
- **Ruteo inteligente** — entiende lenguaje natural: "medidor", "asignados", "ejecutados"
- **Comando `/sql`** — consultas SQL directas a la base de datos

## 🏗️ Arquitectura

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐
│   Telegram    │────▶│  Python Bot  │────▶│ gestion_medidores.db │
│  (usuario)    │◀────│ (PTB v20.x)  │◀────│      (SQLite)        │
└──────────────┘     └──────┬───────┘     └──────────────────────┘
                            │
                    ┌───────▼────────┐
                    │   DeepSeek API  │
                    │  (ruteo NL)     │
                    └────────────────┘
```

## 🚀 Despliegue

El bot corre en **Termux + PRoot** sobre Android, gestionado con **runsv** (auto-reinicio).

```bash
# Iniciar
sv start salchipapabot

# Ver logs
sv status salchipapabot
tail -f /data/data/com.termux/files/usr/var/log/salchipapabot.log
```

### Requisitos

- Python 3.10+
- `python-telegram-bot[job-queue]`
- `openai` (cliente DeepSeek)
- `pandas`, `openpyxl`, `requests`

### Configuración

Crear `.env` en la raíz del proyecto:

```
TELEGRAM_TOKEN=tu_token_aqui
DEEPSEEK_API_KEY=tu_key_aqui
```

## 📦 Datos

- **BD principal:** `gestion_medidores.db` (~174 MB)
- **Tablas:** `gestion_tramites`, `recorrido_cuadrillas`, `gestion_fotos`, `medidores`, `materiales_tramite`
- **Origen de datos:** Reportes descargados del portal ACIIS (Altura) en formato Excel
- **Scripts de carga:** `cargar_*_sqlite.py` procesan los Excel y hacen upsert en SQLite

## 🔄 Alimentación de datos

Los datos se cargan automáticamente vía cron usando `no_agent` scripts (sin consumo de tokens LLM):

| Reporte | Archivo | Frecuencia |
|---------|---------|------------|
| Gestión (915) | `cargar_gestion_sqlite.py` | Cada 2h |
| Recorrido (911) | `cargar_recorrido_sqlite.py` | Cada 2h |
| Retirado (932) | `cargar_retirado_sqlite.py` | Cada 2h |
| Fotos (942) | `cargar_fotos_sqlite.py` | Diario |

## 🌐 Ecosistema

- **Web dashboard:** `ecosistema.salchipapabot.cc.cd`
- **Web server repo:** [`salchipapabot-web`](https://github.com/mikelecuadorian/salchipapabot-web)
- **Infraestructura:** Tablet Android + Termux + Cloudflare Tunnel

---

*Proyecto personal — gestión de medidores LaCostaTech 🇪🇨*
