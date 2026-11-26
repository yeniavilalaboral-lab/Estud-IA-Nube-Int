"""
Villa Nube · API de Trámites (contexto Radicación)
Caso de práctica ficticio del curso EstudIA · Nube Intermedio.

La configuración llega SIEMPRE desde variables de entorno (nada de valores
de un equipo concreto dentro del código ni de la imagen):

  APP_VERSION                versión que se informa en /salud (la fija la imagen)
  PORT                       puerto en el que escucha gunicorn (por defecto 8080)
  DB_HOST                    si existe, usa PostgreSQL; si no, SQLite en DATA_DIR
  DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
  DATA_DIR                   carpeta del archivo SQLite (por defecto /data)
  DIAS_VIGENCIA_DOCUMENTO    regla de negocio: antigüedad máxima del documento (90)
"""
import logging
import os
import sqlite3
import uuid
from datetime import date, datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tramites")

VERSION = os.getenv("APP_VERSION", "sin-version")
DIAS_VIGENCIA = int(os.getenv("DIAS_VIGENCIA_DOCUMENTO", "90"))
USA_POSTGRES = bool(os.getenv("DB_HOST"))
TIPOS_VALIDOS = {"certificado_residencia"}


# ---------------------------------------------------------------- persistencia
def conectar():
    """Abre una conexión según la configuración recibida."""
    if USA_POSTGRES:
        import psycopg  # solo se necesita cuando hay PostgreSQL

        return psycopg.connect(
            host=os.environ["DB_HOST"],
            port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME", "tramites"),
            user=os.getenv("DB_USER", "tramites"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=3,
        )
    carpeta = os.getenv("DATA_DIR", "/data")
    os.makedirs(carpeta, exist_ok=True)
    return sqlite3.connect(os.path.join(carpeta, "tramites.db"))


def sql(consulta):
    """PostgreSQL usa %s como marcador; SQLite usa ?."""
    return consulta.replace("?", "%s") if USA_POSTGRES else consulta


def crear_tabla():
    with conectar() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS solicitudes (
                id TEXT PRIMARY KEY,
                ciudadano TEXT NOT NULL,
                tipo TEXT NOT NULL,
                fecha_documento TEXT NOT NULL,
                estado TEXT NOT NULL,
                radicada_en TEXT NOT NULL
            )
            """
        )


def motor():
    return "postgresql" if USA_POSTGRES else "sqlite"


# ---------------------------------------------------------------- endpoints
@app.get("/salud")
def salud():
    """Liveness: el proceso está vivo. No consulta dependencias."""
    return jsonify(estado="ok", version=VERSION)


@app.get("/listo")
def listo():
    """Readiness: el servicio puede atender porque su base de datos responde."""
    try:
        crear_tabla()
        with conectar() as con:
            con.execute("SELECT 1")
        return jsonify(estado="listo", version=VERSION, base_de_datos=motor())
    except Exception as error:  # noqa: BLE001 - se informa al operador
        log.warning("Base de datos no disponible: %s", error)
        return jsonify(estado="no_listo", motivo="base de datos no disponible"), 503


@app.post("/solicitudes")
def radicar():
    """Comando RadicarSolicitud → evento SolicitudRadicada."""
    datos = request.get_json(silent=True) or {}
    ciudadano = (datos.get("ciudadano") or "").strip()
    tipo = datos.get("tipo", "certificado_residencia")
    fecha_txt = datos.get("fecha_documento", "")

    if not ciudadano:
        return jsonify(error="El campo 'ciudadano' es obligatorio"), 400
    if tipo not in TIPOS_VALIDOS:
        return jsonify(error=f"Tipo de trámite no soportado: {tipo}"), 400
    try:
        fecha_doc = date.fromisoformat(fecha_txt)
    except ValueError:
        return jsonify(error="'fecha_documento' debe tener formato AAAA-MM-DD"), 400

    antiguedad = (date.today() - fecha_doc).days
    if antiguedad > DIAS_VIGENCIA:
        # Regla del negocio vista en la Clase 2: documentos vencidos se rechazan.
        return (
            jsonify(
                error="Documento vencido",
                regla=f"El documento no puede tener más de {DIAS_VIGENCIA} días",
                antiguedad_dias=antiguedad,
            ),
            422,
        )

    solicitud = {
        "id": str(uuid.uuid4()),
        "ciudadano": ciudadano,
        "tipo": tipo,
        "fecha_documento": fecha_doc.isoformat(),
        "estado": "RADICADA",
        "radicada_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    crear_tabla()
    with conectar() as con:
        con.execute(
            sql(
                "INSERT INTO solicitudes (id, ciudadano, tipo, fecha_documento, estado, radicada_en) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            tuple(solicitud.values()),
        )
    log.info("SolicitudRadicada id=%s version=%s", solicitud["id"], VERSION)
    return jsonify(solicitud), 201


@app.get("/solicitudes/<id_solicitud>")
def consultar(id_solicitud):
    crear_tabla()
    with conectar() as con:
        fila = con.execute(
            sql("SELECT id, ciudadano, tipo, fecha_documento, estado, radicada_en FROM solicitudes WHERE id = ?"),
            (id_solicitud,),
        ).fetchone()
    if not fila:
        return jsonify(error="Solicitud no encontrada"), 404
    campos = ["id", "ciudadano", "tipo", "fecha_documento", "estado", "radicada_en"]
    return jsonify(dict(zip(campos, fila)))


@app.get("/solicitudes")
def listar():
    crear_tabla()
    with conectar() as con:
        total = con.execute("SELECT COUNT(*) FROM solicitudes").fetchone()[0]
    return jsonify(total=total, version=VERSION, base_de_datos=motor())


if __name__ == "__main__":
    # Solo para desarrollo sin contenedor. En la imagen se usa gunicorn.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
