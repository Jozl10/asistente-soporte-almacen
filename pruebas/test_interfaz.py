"""Pruebas de la pantalla de Streamlit con AppTest. La API se simula."""

from pathlib import Path
from unittest import mock

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

PANTALLA = str(Path(__file__).resolve().parent.parent / "interfaz" / "app.py")
SALUD = {"estado": "ok", "service_desk": {"modo": "simulado", "disponible": True}}


class Respuesta:
    def __init__(self, codigo, datos):
        self.status_code = codigo
        self.ok = codigo < 400
        self._datos = datos

    def json(self):
        if self._datos is None:  # una página HTML, como la de error del túnel
            raise ValueError("no es JSON")
        return self._datos


def del_agente(textos, ticket=None):
    return Respuesta(200, {
        "respuestas": textos, "accion": "conversar", "ticket": ticket,
        "estado": {"fase": "esperando_consulta"},
        "traza": [{"paso": "decidir", "capa": "reactiva", "detalle": "conversar: saludo"}],
    })


@pytest.fixture(autouse=True)
def sin_cache():
    st.cache_data.clear()


def textos(at):
    return " ".join(m.value for m in at.markdown)


def abrir(parametros=None, salud=Respuesta(200, SALUD)):
    at = AppTest.from_file(PANTALLA, default_timeout=30)
    for clave, valor in (parametros or {}).items():
        at.query_params[clave] = valor
    with mock.patch("requests.get", return_value=salud):
        return at.run()


def test_pagina_publica(tmp_path):
    at = abrir()
    assert not at.exception
    assert at.title[0].value == "Soporte de almacén"
    assert "Service Desk simulado" in at.info[0].value
    assert "asistente automático" in textos(at)
    assert "Usa una de ejemplo" in at.pills[0].label


def test_dentro_de_la_app_no_pide_datos_ni_muestra_ejemplos():
    at = abrir({"aplicacion": "inventario", "sucursal": "S", "usuario": "u"})
    assert not at.exception
    assert at.caption[0].value == "Asistente de Inventario físico"
    assert not any("Datos de la sesión" in e.label for e in at.expander)
    assert len(at.pills) == 0


def test_aviso_si_la_api_no_esta_disponible():
    at = abrir(salud=Respuesta(503, {}))
    assert "no está disponible" in at.warning[0].value


def test_mensaje_y_respuesta_con_traza():
    at = abrir()
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=del_agente(["Hola, soy el asistente."])) as post:
        at.chat_input[0].set_value("hola").run()
    assert not at.exception
    assert "Hola, soy el asistente." in textos(at)
    assert any(e.label == "Cómo lo decidió el asistente" for e in at.expander)
    enviado = post.call_args.kwargs["data"]
    assert enviado["mensaje"] == "hola"


def test_ticket_levantado_se_muestra_con_su_folio():
    ticket = {"folio": "SIM-000007", "estatus": "creado",
              "campos": {"asunto": "Prueba", "adjuntos": [{"nombre": "c.png", "clase": "captura de la app"}]}}
    at = abrir()
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=del_agente(["Listo."], ticket)):
        at.chat_input[0].set_value("ya, levántalo").run()
    assert at.success[0].value == "Ticket SIM-000007 levantado"


def test_si_la_api_no_contesta_avisa_y_ofrece_reintentar():
    at = abrir()
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", side_effect=requests.ConnectionError("sin red")):
        at.chat_input[0].set_value("no puedo cerrar el inventario").run()
    assert "No pude conectarme con el asistente" in at.error[0].value
    assert any(b.label == "Reintentar" for b in at.button)
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=del_agente(["Ya contesté."])) as post:
        next(b for b in at.button if b.label == "Reintentar").click().run()
    assert "Ya contesté." in textos(at)
    assert post.call_args.kwargs["data"]["mensaje"] == "no puedo cerrar el inventario"
    assert not any(b.label == "Reintentar" for b in at.button)


def test_si_la_api_esta_apagada_detras_del_tunel_dice_que_no_esta_disponible():
    # Lo encontró la prueba funcional F12: con la API apagada, el túnel de Cloudflare contesta
    # 502 con una página HTML, y la pantalla decía "contestó algo que no entendí".
    at = abrir()
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=Respuesta(502, None)):
        at.chat_input[0].set_value("no puedo cerrar el inventario").run()
    assert "no está disponible" in at.error[0].value
    assert any(b.label == "Reintentar" for b in at.button)


def test_error_de_la_api_muestra_su_mensaje():
    at = abrir()
    error = Respuesta(400, {"error": "entrada_invalida", "mensaje": "El mensaje es muy largo."})
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=error):
        at.chat_input[0].set_value("hola").run()
    assert at.error[0].value == "El mensaje es muy largo."


def test_captura_de_ejemplo_se_manda_como_imagen():
    at = abrir()
    with mock.patch("requests.get", return_value=Respuesta(200, SALUD)), \
            mock.patch("requests.post", return_value=del_agente(["Recibí tu imagen."])) as post:
        at.pills[0].set_value("Captura de la app").run()
    nombre, contenido, tipo = post.call_args.kwargs["files"]["imagen"]
    assert nombre == "ejemplo_app.jpg" and tipo == "image/jpeg" and len(contenido) > 1000
    assert "Recibí tu imagen." in textos(at)
    assert at.pills[0].value is None  # queda lista para escoger otra
