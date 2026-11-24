"""API REST del asistente de soporte (Flask).

Rutas:

    GET  /salud                     ¿está arriba?, qué modelos cargó y en qué modo está Service Desk
    POST /intencion                 servicio de inferencia de texto: {"texto", "aplicacion"?}
    POST /imagen                    servicio de inferencia de imágenes: archivo "imagen"
    POST /conversacion              el agente: mensaje, imagen opcional, sesión y estado
    GET  /metricas                  monitoreo: peticiones, errores, tiempos y eventos del agente
    POST /service-desk/tickets      Service Desk simulado: recibe un ticket como el web service
    GET  /service-desk/tickets      Service Desk simulado: tickets levantados
    POST /service-desk/disponibilidad  prueba de falla: apaga o prende el Service Desk simulado

Los errores siempre regresan JSON con "error" (código corto) y "mensaje" (para mostrarle al
usuario). La bitácora guarda método, ruta, código y tiempo de cada petición, pero no lo que
escribe el usuario.

En la Mac:      venv/bin/python -m api.app
En el servidor: venv/bin/gunicorn -w 1 --threads 4 -b 0.0.0.0:5000 "api.app:create_app()"
"""

import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from asistente.agente import AgenteSoporte, EstadoInvalido
from asistente.conocimiento import APLICACIONES, NOMBRE_ADJUNTO, intenciones_de_app
from asistente.imagen import ClasificadorImagen, ImagenInvalida
from asistente.ocr import LectorTexto
from asistente.service_desk import (ServiceDeskHTTP, ServiceDeskNoDisponible, ServiceDeskSimulado,
                                    TicketInvalido)
from asistente.texto import ClasificadorTexto

CODIGO = Path(__file__).resolve().parent.parent
VERSION = "1.0"
MAXIMO_CARACTERES = 1000

MENSAJES_HTTP = {
    400: "La petición no se pudo leer.",
    403: "No tienes permiso para usar esta ruta.",
    404: "Esa ruta no existe.",
    405: "Esa ruta no acepta ese método.",
    413: "El archivo es demasiado grande.",
    415: "Ese tipo de contenido no se acepta.",
}


class ErrorDeEntrada(Exception):
    """Lo que mandó el cliente no sirve. Se contesta con `codigo` (400 por omisión)."""

    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.codigo = codigo


def configuracion_por_omision():
    """Se configura con variables de entorno, para no tocar el código en el servidor."""
    umbral = os.environ.get("UMBRAL_TEXTO")
    return {
        "RUTA_MODELOS": os.environ.get("RUTA_MODELOS", str(CODIGO / "modelos")),
        "RUTA_BITACORA": os.environ.get("RUTA_BITACORA", str(CODIGO / "bitacora")),
        "SERVICE_DESK_MODO": os.environ.get("SERVICE_DESK_MODO", "simulado"),
        "SERVICE_DESK_URL": os.environ.get("SERVICE_DESK_URL", ""),
        "PERMITIR_SIMULAR_FALLAS": os.environ.get("PERMITIR_SIMULAR_FALLAS", "0") == "1",
        "UMBRAL_TEXTO": float(umbral) if umbral else None,
        "TAMANO_MAXIMO_MB": float(os.environ.get("TAMANO_MAXIMO_MB", "8")),
        # Si se define, la lista de tickets simulados y la prueba de falla piden el
        # encabezado X-Clave-Admin. En el servidor público siempre se define.
        "CLAVE_ADMIN": os.environ.get("CLAVE_ADMIN", ""),
        # OCR con Tesseract; si no está instalado, el agente sigue sin él.
        "USAR_OCR": os.environ.get("USAR_OCR", "1") == "1",
        "RUTA_TESSDATA": os.environ.get("RUTA_TESSDATA", str(CODIGO / "tessdata")),
    }


# Los modelos se cargan una sola vez por proceso, aunque se cree la app varias veces.
@lru_cache(maxsize=4)
def _cargar_texto(ruta, umbral):
    return ClasificadorTexto(ruta, umbral)


@lru_cache(maxsize=4)
def _cargar_imagen(ruta_modelo, ruta_clases):
    return ClasificadorImagen(ruta_modelo, ruta_clases)


class Monitor:
    """Métricas en memoria desde que arrancó la API."""

    def __init__(self):
        self._candado = threading.Lock()
        self.inicio = datetime.now()
        self.rutas = defaultdict(lambda: {"peticiones": 0, "errores_cliente": 0,
                                          "errores_servidor": 0, "total_ms": 0.0, "maximo_ms": 0.0})
        self.eventos = Counter()
        self.intenciones = Counter()
        self.imagenes = Counter()

    def registrar_peticion(self, ruta, codigo, ms):
        with self._candado:
            r = self.rutas[ruta]
            r["peticiones"] += 1
            r["total_ms"] += ms
            r["maximo_ms"] = max(r["maximo_ms"], ms)
            if 400 <= codigo < 500:
                r["errores_cliente"] += 1
            elif codigo >= 500:
                r["errores_servidor"] += 1

    def registrar_evento(self, evento, datos):
        with self._candado:
            self.eventos[evento] += 1
            if evento == "consulta":
                self.intenciones[datos.get("intencion")] += 1
            if evento == "imagen":
                clase = datos.get("clase") if datos.get("clasificada") else "sin clasificar"
                self.imagenes[clase] += 1

    def resumen(self):
        with self._candado:
            rutas = {
                ruta: {"peticiones": r["peticiones"],
                       "errores_cliente": r["errores_cliente"],
                       "errores_servidor": r["errores_servidor"],
                       "tiempo_promedio_ms": round(r["total_ms"] / r["peticiones"], 1),
                       "tiempo_maximo_ms": round(r["maximo_ms"], 1)}
                for ruta, r in sorted(self.rutas.items())
            }
            return {
                "activo_desde": self.inicio.isoformat(timespec="seconds"),
                "peticiones": sum(r["peticiones"] for r in rutas.values()),
                "rutas": rutas,
                "agente": {"eventos": dict(self.eventos), "intenciones": dict(self.intenciones),
                           "imagenes": dict(self.imagenes)},
            }


def _bitacora(ruta):
    registro = logging.getLogger(f"asistente.api.{ruta}")
    if not registro.handlers:
        ruta.mkdir(parents=True, exist_ok=True)
        manejador = RotatingFileHandler(ruta / "api.log", maxBytes=2_000_000, backupCount=3,
                                        encoding="utf-8")
        manejador.setFormatter(logging.Formatter("%(message)s"))
        registro.addHandler(manejador)
        registro.setLevel(logging.INFO)
        registro.propagate = False
    return registro


def create_app(config=None):
    app = Flask(__name__)
    cfg = configuracion_por_omision()
    cfg.update(config or {})
    app.config.update(cfg)
    app.config["MAX_CONTENT_LENGTH"] = int(cfg["TAMANO_MAXIMO_MB"] * 1024 * 1024)
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    carpeta_bitacora = Path(cfg["RUTA_BITACORA"])
    registro = _bitacora(carpeta_bitacora)
    monitor = Monitor()

    modelos = Path(cfg["RUTA_MODELOS"])
    # El umbral del "no sé" sale de calibrar_umbral.py, salvo que la variable de entorno lo
    # reemplace.
    umbral = cfg["UMBRAL_TEXTO"]
    calibrado = modelos / "umbral_texto.json"
    if umbral is None and calibrado.exists():
        umbral = json.loads(calibrado.read_text(encoding="utf-8"))["umbral"]
    texto = _cargar_texto(str(modelos / "clasificador_intenciones.joblib"), umbral)
    imagen = _cargar_imagen(str(modelos / "clasificador_imagenes.keras"),
                            str(modelos / "clases_imagenes.txt"))
    if cfg["SERVICE_DESK_MODO"] == "real":
        if not cfg["SERVICE_DESK_URL"]:
            raise RuntimeError("SERVICE_DESK_MODO=real necesita SERVICE_DESK_URL.")
        service_desk = ServiceDeskHTTP(cfg["SERVICE_DESK_URL"])
    else:
        service_desk = ServiceDeskSimulado(carpeta_bitacora / "tickets_simulados.jsonl")
    lector = LectorTexto(cfg["RUTA_TESSDATA"]) if cfg["USAR_OCR"] else None
    if cfg["USAR_OCR"] and not lector.disponible:
        app.logger.warning("OCR apagado: no se encontró Tesseract con el idioma español.")
    agente = AgenteSoporte(texto, imagen, service_desk, al_evento=monitor.registrar_evento,
                           lector_ocr=lector)
    app.extensions["asistente"] = {"agente": agente, "monitor": monitor,
                                   "service_desk": service_desk}

    # ------------------------------------------------------------- monitoreo
    @app.before_request
    def _antes():
        g.inicio = time.perf_counter()
        g.id = uuid.uuid4().hex[:8]

    @app.after_request
    def _despues(respuesta):
        ms = (time.perf_counter() - g.get("inicio", time.perf_counter())) * 1000
        ruta = f"{request.method} {request.url_rule.rule if request.url_rule else '(sin ruta)'}"
        monitor.registrar_peticion(ruta, respuesta.status_code, ms)
        respuesta.headers["X-Id-Peticion"] = g.get("id", "-")
        respuesta.headers["X-Tiempo-ms"] = f"{ms:.1f}"
        registro.info(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                                  "id": g.get("id", "-"), "ruta": ruta,
                                  "codigo": respuesta.status_code, "ms": round(ms, 1)}))
        return respuesta

    # ------------------------------------------------------------- errores
    def error(codigo, clave, mensaje, **extra):
        return jsonify({"error": clave, "mensaje": mensaje, **extra}), codigo

    @app.errorhandler(ErrorDeEntrada)
    def _entrada(e):
        return error(e.codigo, "entrada_invalida", str(e))

    @app.errorhandler(EstadoInvalido)
    def _estado(e):
        return error(400, "estado_invalido", "La conversación no se pudo continuar. Empieza una "
                     "conversación nueva.", detalle=str(e))

    @app.errorhandler(ImagenInvalida)
    def _imagen(e):
        return error(e.codigo, "imagen_invalida", str(e))

    @app.errorhandler(TicketInvalido)
    def _ticket(e):
        return error(422, "ticket_invalido", "El ticket no se pudo levantar.", campos=e.errores)

    @app.errorhandler(ServiceDeskNoDisponible)
    def _service_desk(e):
        return error(503, "service_desk_no_disponible",
                     "Service Desk no responde. Intenta más tarde o llama a soporte.")

    @app.errorhandler(HTTPException)
    def _http(e):
        mensaje = MENSAJES_HTTP.get(e.code, e.description)
        if e.code == 413:
            mensaje = f"El archivo es demasiado grande (máximo {cfg['TAMANO_MAXIMO_MB']:g} MB)."
        return error(e.code, e.name.lower().replace(" ", "_"), mensaje)

    @app.errorhandler(Exception)
    def _inesperado(e):
        app.logger.exception("Error inesperado en la petición %s", g.get("id", "-"))
        registro.info(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                                  "id": g.get("id", "-"), "error": type(e).__name__}))
        return error(500, "error_interno", "Ocurrió un error inesperado. Intenta de nuevo.",
                     id_peticion=g.get("id", "-"))

    # ------------------------------------------------------------- lectura de la entrada
    def leer_json():
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict):
            raise ErrorDeEntrada("El cuerpo debe ser un objeto JSON.")
        return datos

    def leer_texto(valor, campo, obligatorio):
        if valor is None:
            valor = ""
        if not isinstance(valor, str):
            raise ErrorDeEntrada(f"El campo '{campo}' debe ser texto.")
        if obligatorio and not valor.strip():
            raise ErrorDeEntrada(f"El campo '{campo}' viene vacío.")
        if len(valor) > MAXIMO_CARACTERES:
            raise ErrorDeEntrada(f"El mensaje es muy largo (máximo {MAXIMO_CARACTERES:,} caracteres).")
        return valor

    def exigir_clave_admin():
        clave = cfg["CLAVE_ADMIN"]
        if clave and not hmac.compare_digest(request.headers.get("X-Clave-Admin", ""), clave):
            raise ErrorDeEntrada("Esta ruta necesita la clave de administración.", 403)

    def leer_objeto(valor, campo):
        if isinstance(valor, str):
            try:
                valor = json.loads(valor) if valor.strip() else None
            except json.JSONDecodeError as e:
                raise ErrorDeEntrada(f"El campo '{campo}' no es JSON válido.") from e
        if valor is not None and not isinstance(valor, dict):
            raise ErrorDeEntrada(f"El campo '{campo}' debe ser un objeto.")
        return valor

    # ------------------------------------------------------------- rutas
    @app.get("/salud")
    def salud():
        return jsonify({
            "estado": "ok",
            "version": VERSION,
            "modelos": {"texto": {"intenciones": len(texto.clases), "umbral": texto.umbral},
                        "imagen": {"clases": imagen.clases},
                        "ocr": {"disponible": agente.ocr is not None}},
            "service_desk": {"modo": service_desk.modo, "disponible": service_desk.disponible},
        })

    @app.post("/intencion")
    def intencion():
        datos = leer_json()
        mensaje = leer_texto(datos.get("texto"), "texto", obligatorio=True)
        aplicacion = datos.get("aplicacion")
        if aplicacion is not None and aplicacion not in APLICACIONES:
            raise ErrorDeEntrada(f"Aplicación desconocida. Usa {' o '.join(APLICACIONES)}.")
        permitidas = intenciones_de_app(aplicacion) if aplicacion else None
        return jsonify(texto.predecir(mensaje, permitidas))

    @app.post("/imagen")
    def clasificar_imagen():
        archivo = request.files.get("imagen")
        if archivo is None:
            raise ErrorDeEntrada("Falta el archivo 'imagen' (multipart/form-data).")
        contenido = archivo.read()
        resultado = imagen.predecir(contenido)
        resultado["descripcion"] = (NOMBRE_ADJUNTO[resultado["clase"]] if resultado["clasificada"]
                                    else "imagen sin clasificar")
        # El OCR solo se corre en las capturas de la app, igual que en el agente.
        if agente.ocr and resultado["clasificada"] and resultado["clase"] == "app":
            lectura = agente.ocr.leer(contenido)
            resultado["texto_leido"] = lectura["texto"] if lectura else None
        return jsonify(resultado)

    @app.post("/conversacion")
    def conversacion():
        contenido = nombre = None
        if request.is_json:
            datos = leer_json()
            mensaje, sesion, estado = datos.get("mensaje"), datos.get("sesion"), datos.get("estado")
        else:
            mensaje = request.form.get("mensaje")
            sesion, estado = request.form.get("sesion"), request.form.get("estado")
            archivo = request.files.get("imagen")
            if archivo is not None:
                contenido, nombre = archivo.read(), archivo.filename
        mensaje = leer_texto(mensaje, "mensaje", obligatorio=False)
        if not mensaje.strip() and not contenido:
            raise ErrorDeEntrada("Escribe un mensaje o adjunta una imagen.")
        resultado = agente.atender(mensaje, contenido, nombre, leer_objeto(sesion, "sesion"),
                                   leer_objeto(estado, "estado"))
        return jsonify(resultado)

    @app.get("/metricas")
    def metricas():
        resumen = monitor.resumen()
        resumen["service_desk"] = {"modo": service_desk.modo,
                                   "disponible": service_desk.disponible,
                                   "tickets": len(service_desk.listar())}
        return jsonify(resumen)

    if service_desk.modo == "simulado":
        @app.post("/service-desk/tickets")
        def crear_ticket():
            return jsonify(service_desk.crear_ticket(leer_json())), 201

        @app.get("/service-desk/tickets")
        def listar_tickets():
            # Trae lo que escribieron los usuarios: solo con la clave de administración.
            exigir_clave_admin()
            return jsonify(service_desk.listar())

        if cfg["PERMITIR_SIMULAR_FALLAS"]:
            @app.post("/service-desk/disponibilidad")
            def disponibilidad():
                exigir_clave_admin()
                valor = leer_json().get("disponible")
                if not isinstance(valor, bool):
                    raise ErrorDeEntrada("Manda {\"disponible\": true} o {\"disponible\": false}.")
                service_desk.disponible = valor
                return jsonify({"disponible": valor})

    return app


if __name__ == "__main__":
    puerto = int(os.environ.get("PUERTO", "5000"))
    create_app().run(host="127.0.0.1", port=puerto)
