"""Piezas comunes de las pruebas.

Las pruebas del agente usan clasificadores falsos: así se prueba cada regla del agente con
la intención y la clase de imagen que se quiera, sin depender de lo que conteste el modelo.
Las pruebas de la API usan los modelos de verdad.
"""

import io
import json

import pytest
from PIL import Image

from asistente.agente import AgenteSoporte
from asistente.imagen import ImagenInvalida
from asistente.service_desk import ServiceDeskSimulado


class TextoFalso:
    """Regresa la intención según palabras del mensaje, en el orden de `reglas`."""

    reglas = [
        ("hola", "saludo"),
        ("gracias", "despedida"),
        ("cerrar el inventario", "inv_no_cierra"),
        ("firma", "cr_firma_no_cierra"),
        ("guardia", "inv_guardia"),
        ("codigo", "codigo_no_se_lee"),
        ("version", "actualizar_version"),
    ]

    def __init__(self, umbral=None):
        self.umbral = umbral
        self.clases = sorted({i for _, i in self.reglas} | {"otro"})

    def predecir(self, texto, permitidas=None):
        texto = texto.lower().translate(str.maketrans("áéíóú", "aeiou"))
        ranking = [i for palabra, i in self.reglas if palabra in texto] + ["otro"]
        candidatas = [i for i in ranking if permitidas is None or i in permitidas]
        intencion = candidatas[0]
        puntuacion = 0.9 if intencion != "otro" else -0.2
        return {"intencion": intencion, "puntuacion": puntuacion,
                "segura": self.umbral is None or puntuacion >= self.umbral,
                "intencion_modelo": ranking[0], "alternativas": []}


class ImagenFalsa:
    """El contenido dice la clase: b"app…" (p. ej. b"app_version"), b"escritorio", b"otra"
    o b"dudosa"."""

    clases = ["app", "escritorio", "otra"]

    def predecir(self, contenido):
        if contenido == b"roto":
            raise ImagenInvalida("El archivo no es una imagen que se pueda leer.", 415)
        if contenido == b"dudosa":
            return {"clase": "otra", "seguridad": 0.44, "clasificada": False, "probabilidades": {}}
        clase = "app" if contenido.startswith(b"app") else contenido.decode()
        return {"clase": clase, "seguridad": 0.97, "clasificada": True, "probabilidades": {}}


class LectorFalso:
    """OCR falso: el texto que "lee" depende del contenido de la imagen."""

    disponible = True
    textos = {
        b"app_version": "No cuenta con la versión más actual de la aplicación",
        b"app_cierre": "No se puede cerrar el inventario hay áreas en revisión",
        b"app_menu": "Aceptar Cancelar Menú principal",
    }

    def __init__(self):
        self.leidas = []

    def leer(self, contenido):
        self.leidas.append(contenido)
        texto = self.textos.get(contenido)
        return {"texto": texto, "palabras": len(texto.split()), "confianza": 95.0} if texto else None


@pytest.fixture
def service_desk(tmp_path):
    return ServiceDeskSimulado(tmp_path / "tickets.jsonl")


@pytest.fixture
def eventos():
    return []


@pytest.fixture
def agente(service_desk, eventos):
    return AgenteSoporte(TextoFalso(), ImagenFalsa(), service_desk,
                         al_evento=lambda evento, datos: eventos.append(evento))


class Charla:
    """Lleva una conversación turno por turno, pasando el estado por JSON como lo hace la API."""

    def __init__(self, agente, sesion=None):
        self.agente = agente
        self.sesion = sesion
        self.estado = None
        self.ultimo = None

    def decir(self, mensaje="", imagen=None):
        self.ultimo = self.agente.atender(mensaje, imagen, "captura.png", self.sesion, self.estado)
        self.estado = json.loads(json.dumps(self.ultimo["estado"]))
        return self.ultimo


@pytest.fixture
def charla(agente):
    return lambda sesion=None: Charla(agente, sesion)


@pytest.fixture
def lector():
    return LectorFalso()


@pytest.fixture
def charla_ocr(service_desk, lector):
    agente = AgenteSoporte(TextoFalso(), ImagenFalsa(), service_desk, lector_ocr=lector)
    return lambda sesion=None: Charla(agente, sesion)


def png(ancho=320, alto=640, color="white"):
    archivo = io.BytesIO()
    Image.new("RGB", (ancho, alto), color).save(archivo, format="PNG")
    return archivo.getvalue()
