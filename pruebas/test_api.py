"""Pruebas de la API de Flask con los modelos de verdad."""

import json
from pathlib import Path

import pytest

from conftest import png

MODELOS = Path(__file__).resolve().parent.parent / "modelos"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    if not (MODELOS / "clasificador_intenciones.joblib").exists():
        pytest.skip("Faltan los modelos: correr entrenar_texto.py y copiar el de imágenes")
    from api.app import create_app
    return create_app({"RUTA_BITACORA": str(tmp_path_factory.mktemp("bitacora")),
                       "PERMITIR_SIMULAR_FALLAS": True})


@pytest.fixture
def cliente(app):
    return app.test_client()


def test_salud(cliente):
    r = cliente.get("/salud")
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["estado"] == "ok"
    assert datos["modelos"]["texto"]["intenciones"] == 12
    assert datos["modelos"]["imagen"]["clases"] == ["app", "escritorio", "otra"]
    assert datos["service_desk"] == {"modo": "simulado", "disponible": True}
    assert "X-Id-Peticion" in r.headers and "X-Tiempo-ms" in r.headers


def test_usa_el_umbral_calibrado_para_decir_no_se(cliente):
    if not (MODELOS / "umbral_texto.json").exists():
        pytest.skip("Falta correr calibrar_umbral.py")
    umbral = json.loads((MODELOS / "umbral_texto.json").read_text(encoding="utf-8"))["umbral"]
    assert cliente.get("/salud").get_json()["modelos"]["texto"]["umbral"] == umbral
    # En el Avance 2 esta frase salía como despedida ("¡Hasta luego!"); con el umbral el
    # agente pide decirlo con otras palabras.
    datos = cliente.post("/conversacion", json={
        "mensaje": "la pantalla se puso en blanco",
        "sesion": {"aplicacion": "inventario"}}).get_json()
    assert datos["accion"] == "pedir_reformular"
    assert "abajo del umbral" in datos["traza"][0]["detalle"]


def test_con_solo_la_captura_de_ejemplo_el_agente_entiende_la_alerta(cliente):
    if not cliente.get("/salud").get_json()["modelos"]["ocr"]["disponible"]:
        pytest.skip("Tesseract con español no está instalado")
    ejemplo = Path(__file__).resolve().parent.parent / "interfaz" / "ejemplos" / "ejemplo_app.jpg"
    r = cliente.post("/imagen", data={"imagen": (archivo(ejemplo.read_bytes()), "ejemplo_app.jpg")})
    assert "versión más actual" in r.get_json()["texto_leido"]
    datos = cliente.post("/conversacion", data={
        "mensaje": "", "sesion": json.dumps({"aplicacion": "inventario"}),
        "imagen": (archivo(ejemplo.read_bytes()), "ejemplo_app.jpg")}).get_json()
    assert datos["estado"]["intencion"] == "actualizar_version"
    assert datos["accion"] == "planear_ticket"


def test_intencion(cliente):
    r = cliente.post("/intencion", json={"texto": "no puedo cerrar el inventario"})
    assert r.status_code == 200
    assert r.get_json()["intencion"] == "inv_no_cierra"


@pytest.mark.parametrize("cuerpo, fragmento", [
    ({}, "viene vacío"),
    ({"texto": 5}, "debe ser texto"),
    ({"texto": "x" * 1001}, "muy largo"),
    ({"texto": "hola", "aplicacion": "ventas"}, "Aplicación desconocida"),
])
def test_intencion_entrada_invalida(cliente, cuerpo, fragmento):
    r = cliente.post("/intencion", json=cuerpo)
    assert r.status_code == 400
    assert fragmento in r.get_json()["mensaje"]


def test_intencion_sin_json(cliente):
    r = cliente.post("/intencion", data="texto suelto", content_type="text/plain")
    assert r.status_code == 400
    assert r.get_json()["error"] == "entrada_invalida"


def test_imagen(cliente):
    r = cliente.post("/imagen", data={"imagen": (png_archivo(), "captura.png")})
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["clase"] in ("app", "escritorio", "otra")
    assert 0 <= datos["seguridad"] <= 1
    assert set(datos["probabilidades"]) == {"app", "escritorio", "otra"}


def test_imagen_que_no_es_imagen(cliente):
    r = cliente.post("/imagen", data={"imagen": (archivo(b"esto no es una imagen"), "nota.txt")})
    assert r.status_code == 415
    assert r.get_json()["error"] == "imagen_invalida"


def test_imagen_sin_archivo(cliente):
    r = cliente.post("/imagen", data={})
    assert r.status_code == 400


def test_archivo_demasiado_grande(tmp_path):
    from api.app import create_app
    app = create_app({"RUTA_BITACORA": str(tmp_path), "TAMANO_MAXIMO_MB": 0.01})
    r = app.test_client().post("/imagen", data={"imagen": (archivo(b"0" * 20_000), "grande.png")})
    assert r.status_code == 413
    assert "máximo 0.01 MB" in r.get_json()["mensaje"]


def test_conversacion_completa_con_imagen_hasta_el_ticket(cliente):
    sesion = json.dumps({"aplicacion": "inventario", "sucursal": "Sucursal de prueba",
                         "usuario": "almacenista.prueba"})
    estado = None

    def decir(mensaje, imagen=None):
        nonlocal estado
        datos = {"mensaje": mensaje, "sesion": sesion, "estado": json.dumps(estado)}
        if imagen:
            datos["imagen"] = (archivo(imagen), "captura.png")
        r = cliente.post("/conversacion", data=datos)
        assert r.status_code == 200, r.get_json()
        estado = r.get_json()["estado"]
        return r.get_json()

    assert decir("no puedo cerrar el inventario, me marca áreas en revisión")["accion"] == "dar_solucion"
    assert decir("no, sigue igual")["accion"] == "planear_ticket"
    decir("el 1234")
    r = decir("dice que hay áreas en revisión", imagen=png())
    assert r["ticket"]["folio"].startswith("SIM-")
    assert r["ticket"]["campos"]["adjuntos"][0]["nombre"] == "captura.png"
    tickets = cliente.get("/service-desk/tickets").get_json()
    assert tickets[-1]["folio"] == r["ticket"]["folio"]


def test_conversacion_por_json_sin_imagen(cliente):
    r = cliente.post("/conversacion", json={"mensaje": "hola"})
    assert r.status_code == 200
    assert r.get_json()["accion"] == "conversar"


@pytest.mark.parametrize("cuerpo, codigo, error", [
    ({"mensaje": ""}, 400, "entrada_invalida"),
    ({"mensaje": "hola", "estado": {"fase": "inventada"}}, 400, "estado_invalido"),
    ({"mensaje": "hola", "sesion": "no soy objeto"}, 400, "entrada_invalida"),
    ({"mensaje": "hola", "sesion": {"aplicacion": "ventas"}}, 400, "estado_invalido"),
])
def test_conversacion_entrada_invalida(cliente, cuerpo, codigo, error):
    r = cliente.post("/conversacion", json=cuerpo)
    assert r.status_code == codigo
    assert r.get_json()["error"] == error


def test_service_desk_caido_y_de_vuelta(cliente):
    assert cliente.post("/service-desk/disponibilidad",
                        json={"disponible": False}).status_code == 200
    r = cliente.post("/service-desk/tickets", json={"usuario": "x"})
    assert r.status_code == 503
    estado = None
    for mensaje in ["necesitamos guardia de soporte el sábado para el inventario",
                    "sábado 6", "9 am", "el jefe de almacén"]:
        datos = cliente.post("/conversacion", json={
            "mensaje": mensaje, "estado": estado,
            "sesion": {"aplicacion": "inventario", "sucursal": "S", "usuario": "u"}}).get_json()
        estado = datos["estado"]
    assert datos["ticket"]["estatus"] == "failed"
    assert estado["fase"] == "ticket_pendiente"
    cliente.post("/service-desk/disponibilidad", json={"disponible": True})
    datos = cliente.post("/conversacion", json={"mensaje": "sí", "estado": estado}).get_json()
    assert datos["ticket"]["estatus"] == "creado"


def test_lista_de_tickets_y_prueba_de_falla_piden_clave_si_hay(tmp_path):
    from api.app import create_app
    app = create_app({"RUTA_BITACORA": str(tmp_path), "PERMITIR_SIMULAR_FALLAS": True,
                      "CLAVE_ADMIN": "clave-de-prueba"})
    cliente = app.test_client()
    assert cliente.get("/service-desk/tickets").status_code == 403
    assert cliente.post("/service-desk/disponibilidad",
                        json={"disponible": False}).status_code == 403
    r = cliente.get("/service-desk/tickets", headers={"X-Clave-Admin": "clave-de-prueba"})
    assert r.status_code == 200


def test_service_desk_simulado_valida_campos(cliente):
    r = cliente.post("/service-desk/tickets", json={"usuario": "x"})
    assert r.status_code == 422
    assert "Falta el campo 'sucursal'." in r.get_json()["campos"]


def test_rutas_y_metodos_que_no_existen_contestan_json(cliente):
    r = cliente.get("/no-existe")
    assert r.status_code == 404 and r.get_json()["mensaje"] == "Esa ruta no existe."
    r = cliente.get("/conversacion")
    assert r.status_code == 405 and r.get_json()["error"] == "method_not_allowed"


def test_error_inesperado_contesta_json_sin_detalles_internos(app, monkeypatch):
    agente = app.extensions["asistente"]["agente"]

    def falla(*args, **kwargs):
        raise RuntimeError("detalle interno")
    monkeypatch.setattr(agente, "atender", falla)
    r = app.test_client().post("/conversacion", json={"mensaje": "hola"})
    assert r.status_code == 500
    datos = r.get_json()
    assert datos["error"] == "error_interno"
    assert "detalle interno" not in json.dumps(datos)


def test_metricas_cuentan_peticiones_y_eventos(cliente):
    cliente.post("/conversacion", json={"mensaje": "hola"})
    cliente.post("/intencion", json={})
    metricas = cliente.get("/metricas").get_json()
    assert metricas["rutas"]["POST /conversacion"]["peticiones"] >= 1
    assert metricas["rutas"]["POST /intencion"]["errores_cliente"] >= 1
    assert metricas["agente"]["intenciones"].get("saludo", 0) >= 1


def test_la_bitacora_no_guarda_lo_que_escribe_el_usuario(app, cliente):
    cliente.post("/conversacion", json={"mensaje": "mensaje secreto de prueba"})
    carpeta = Path(app.config["RUTA_BITACORA"])
    assert "mensaje secreto" not in (carpeta / "api.log").read_text(encoding="utf-8")


def archivo(contenido):
    import io
    return io.BytesIO(contenido)


def png_archivo():
    return archivo(png())
