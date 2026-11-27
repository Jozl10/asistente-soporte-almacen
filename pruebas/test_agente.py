"""Pruebas del agente híbrido: cada regla de la capa reactiva y el plan de la deliberativa."""

import pytest

from asistente.agente import EstadoInvalido

DESDE_LA_APP = {"aplicacion": "inventario", "sucursal": "Sucursal de prueba",
                "usuario": "almacenista.prueba"}
DESDE_CLIENTE_RECOGE = {**DESDE_LA_APP, "aplicacion": "cliente_recoge"}


def test_saludo_se_contesta_directo_con_el_nombre_de_la_app(charla):
    c = charla(DESDE_LA_APP)
    r = c.decir("hola, buenos días")
    assert r["accion"] == "conversar"
    assert "Inventario físico" in r["respuestas"][0]
    assert r["traza"][1]["capa"] == "reactiva"


def test_solucion_de_la_documentacion_y_el_usuario_confirma(charla, eventos):
    c = charla(DESDE_LA_APP)
    r = c.decir("no puedo cerrar el inventario")
    assert r["accion"] == "dar_solucion"
    assert c.estado["fase"] == "confirmando_solucion"
    assert r["respuestas"][-1].startswith("¿Con eso se resolvió?")
    r = c.decir("sí, ya quedó")
    assert r["accion"] == "cerrar_resuelto"
    assert c.estado["fase"] == "esperando_consulta"
    assert c.estado["aplicacion"] == "inventario"
    assert "resuelto_sin_ticket" in eventos


def test_si_no_se_resuelve_planea_el_ticket_y_lo_levanta(charla, service_desk):
    c = charla()  # página pública: no llegan los datos de la sesión
    c.decir("no puedo cerrar el inventario")
    r = c.decir("no, sigue igual")
    assert r["accion"] == "planear_ticket"
    assert r["traza"][1]["capa"] == "deliberativa"
    # Sin sesión: además de los datos de la intención pide la captura, la sucursal y el
    # usuario. La app no la pide porque la dedujo de la intención.
    assert c.estado["pendientes"] == ["numero_inventario", "mensaje_error", "captura",
                                      "sucursal", "usuario"]
    c.decir("es el inventario 1234")
    c.decir("dice que hay áreas en revisión")
    c.decir("no tengo")
    c.decir("Sucursal de prueba")
    r = c.decir("almacenista.prueba")
    assert r["accion"] == "recibir_dato"
    assert r["ticket"]["folio"] == "SIM-000001"
    campos = r["ticket"]["campos"]
    assert campos["aplicacion"] == "Inventario físico"
    assert campos["tipo"] == "Incidente"
    assert campos["articulo"] == "Falla en Proceso"
    assert "Número de inventario: 1234" in campos["descripcion"]
    assert "adjuntos" not in campos
    assert len(service_desk.listar()) == 1
    assert c.estado["fase"] == "esperando_consulta"


def test_dentro_de_la_app_no_pregunta_usuario_sucursal_ni_app(charla):
    c = charla(DESDE_LA_APP)
    r = c.decir("necesitamos guardia el sábado")
    assert r["accion"] == "planear_ticket"
    # La guardia es solicitud: no pide captura.
    assert c.estado["pendientes"] == ["fecha", "hora", "contacto"]


def test_pide_el_numero_si_la_respuesta_no_trae_uno(charla):
    c = charla(DESDE_LA_APP)
    c.decir("no puedo cerrar el inventario")
    c.decir("no")
    r = c.decir("el de hoy")
    assert r["respuestas"][-1].startswith("Necesito el número")
    assert c.estado["pendientes"][0] == "numero_inventario"
    c.decir("el 77")
    assert c.estado["datos"]["numero_inventario"] == "77"


def test_una_captura_a_media_conversacion_rehace_el_plan(charla):
    c = charla(DESDE_CLIENTE_RECOGE)
    c.decir("la firma no deja cerrar el ticket")
    assert "captura" in c.estado["pendientes"]
    r = c.decir("es el 35", imagen=b"app")
    assert "captura" not in c.estado["pendientes"]
    assert any("Se rehizo el plan" in paso["detalle"] for paso in r["traza"])
    assert any("captura de la app" in texto for texto in r["respuestas"])
    r = c.decir("ORA-01722")
    assert r["ticket"]["campos"]["adjuntos"] == [{"nombre": "captura.png",
                                                  "clase": "captura de la app"}]


def test_imagen_con_poca_seguridad_va_sin_clasificar(charla):
    c = charla(DESDE_LA_APP)
    c.decir("no lee el codigo de la etiqueta")
    c.decir("no")
    c.decir("ESS-0000")
    c.decir("pasillo 3")
    r = c.decir("", imagen=b"dudosa")
    assert "No alcanzo a distinguir" in r["respuestas"][0]
    assert r["ticket"]["campos"]["adjuntos"][0]["clase"] == "imagen sin clasificar"


def test_dos_veces_sin_entender_levanta_ticket_general(charla):
    c = charla(DESDE_LA_APP)
    r = c.decir("la pantalla se puso en blanco")
    assert r["accion"] == "pedir_reformular"
    r = c.decir("se queda toda blanca")
    assert r["accion"] == "planear_ticket"
    assert r["respuestas"][0].startswith("Sigo sin entenderte")
    assert c.estado["pendientes"] == ["captura"]
    r = c.decir("no")
    campos = r["ticket"]["campos"]
    assert campos["asunto"].startswith("Problema que el asistente no reconoció")
    assert "la pantalla se puso en blanco / se queda toda blanca" in campos["descripcion"]


def test_si_se_explica_bien_despues_de_no_entender_se_reinicia_el_contador(charla):
    c = charla(DESDE_LA_APP)
    c.decir("la pantalla se puso en blanco")
    assert c.estado["no_entendidos"] == 1
    c.decir("no puedo cerrar el inventario")
    assert c.estado["no_entendidos"] == 0


def test_cancelar_a_media_conversacion(charla, eventos):
    c = charla(DESDE_CLIENTE_RECOGE)
    c.decir("la firma no deja cerrar")
    r = c.decir("cancelar")
    assert r["accion"] == "cancelar"
    assert c.estado["fase"] == "esperando_consulta"
    assert "cancelado" in eventos


def test_si_service_desk_no_responde_avisa_y_reintenta(charla, service_desk):
    c = charla(DESDE_LA_APP)
    c.decir("necesitamos guardia el sábado")
    c.decir("sábado 6 de diciembre")
    c.decir("9 am")
    service_desk.disponible = False
    r = c.decir("el jefe de almacén")
    assert r["ticket"]["estatus"] == "failed"
    assert "Service Desk no responde" in r["respuestas"][-1]
    assert c.estado["fase"] == "ticket_pendiente"
    service_desk.disponible = True
    r = c.decir("sí, intenta otra vez")
    assert r["accion"] == "reintentar_ticket"
    assert r["ticket"]["folio"] == "SIM-000001"


def test_imagen_que_no_se_puede_leer_se_avisa_y_sigue_la_conversacion(charla):
    c = charla(DESDE_LA_APP)
    r = c.decir("no puedo cerrar el inventario", imagen=b"roto")
    assert r["respuestas"][0].startswith("No pude usar la imagen")
    assert r["accion"] == "dar_solucion"


def test_solo_cuentan_las_intenciones_de_la_app_del_usuario(charla):
    # Desde Cliente recoge, "cerrar el inventario" no puede ser inv_no_cierra.
    c = charla({"aplicacion": "cliente_recoge"})
    r = c.decir("no puedo cerrar el inventario")
    assert c.estado["intencion"] != "inv_no_cierra"
    assert "no es de Cliente recoge" in r["traza"][0]["detalle"]


def test_deduce_la_app_por_la_intencion(charla):
    c = charla()
    r = c.decir("la firma no deja cerrar")
    assert c.estado["aplicacion"] == "cliente_recoge"
    assert any("Se dedujo la app" in paso["detalle"] for paso in r["traza"])


def test_pregunta_la_app_cuando_la_intencion_es_de_las_dos(charla):
    c = charla()
    c.decir("no me llegó la version nueva")
    assert c.estado["pendientes"] == ["version", "aplicacion", "sucursal", "usuario"]
    c.decir("no sé")
    r = c.decir("desde la de pedidos")
    assert r["respuestas"][-1].startswith("No reconocí la app")
    c.decir("inventario")
    assert c.estado["aplicacion"] == "inventario"


def test_recuerda_sucursal_y_usuario_para_el_siguiente_ticket(charla):
    c = charla()
    c.decir("necesitamos guardia el sábado")
    for respuesta in ["sábado", "9 am", "el jefe", "Sucursal de prueba", "almacenista.prueba"]:
        c.decir(respuesta)
    c.decir("necesitamos guardia el domingo")
    assert c.estado["pendientes"] == ["fecha", "hora", "contacto"]


def test_umbral_de_confianza_trata_la_intencion_dudosa_como_otro(service_desk):
    from asistente.agente import AgenteSoporte
    from conftest import ImagenFalsa, TextoFalso
    agente = AgenteSoporte(TextoFalso(umbral=0.95), ImagenFalsa(), service_desk)
    r = agente.atender("no puedo cerrar el inventario", sesion=DESDE_LA_APP)
    assert r["accion"] == "pedir_reformular"
    assert "abajo del umbral" in r["traza"][0]["detalle"]


# ---------------------------------------------------------------- OCR

def test_con_solo_la_captura_saca_la_intencion_de_lo_que_lee(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    r = c.decir("", imagen=b"app_version")
    assert c.estado["intencion"] == "actualizar_version"
    assert r["accion"] == "planear_ticket"
    detalles = [paso["detalle"] for paso in r["traza"]]
    assert any(d.startswith("OCR → “No cuenta con la versión") for d in detalles)
    assert any(d.startswith("Texto de la captura → actualizar_version") for d in detalles)
    assert any("En ella leí" in texto for texto in r["respuestas"])


def test_lo_leido_con_puntuacion_baja_no_basta(service_desk, lector):
    from asistente.agente import AgenteSoporte
    from conftest import ImagenFalsa, TextoFalso
    agente = AgenteSoporte(TextoFalso(), ImagenFalsa(), service_desk, lector_ocr=lector,
                           umbral_captura=0.95)  # el falso da 0.9
    r = agente.atender("", imagen=b"app_version", sesion=DESDE_LA_APP)
    assert r["accion"] == "pedir_descripcion"
    # El mismo puntaje sí basta si viene del mensaje del usuario.
    r = agente.atender("no me llego la version", sesion=DESDE_LA_APP)
    assert r["accion"] == "planear_ticket"


def test_si_el_mensaje_no_dice_el_problema_usa_lo_leido_en_la_captura(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    r = c.decir("me sale este aviso al entrar", imagen=b"app_version")
    assert c.estado["intencion"] == "actualizar_version"
    assert r["accion"] == "planear_ticket"
    assert c.estado["no_entendidos"] == 0
    assert any(paso["detalle"].endswith("se usa lo leído") for paso in r["traza"])
    assert c.estado["mensajes"] == ["me sale este aviso al entrar"]  # va en la descripción


def test_si_lo_leido_tampoco_dice_el_problema_pide_otras_palabras(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    r = c.decir("me sale esto", imagen=b"app_menu")
    assert r["accion"] == "pedir_reformular"
    assert c.estado["no_entendidos"] == 1


def test_si_el_mensaje_se_entiende_manda_el_mensaje_y_no_lo_leido(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    c.decir("no puedo cerrar el inventario", imagen=b"app_version")
    assert c.estado["intencion"] == "inv_no_cierra"


def test_si_lo_leido_no_dice_el_problema_pide_descripcion(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    r = c.decir("", imagen=b"app_menu")
    assert r["accion"] == "pedir_descripcion"
    assert c.estado["no_entendidos"] == 0  # no cuenta como "no te entendí"


def test_con_ocr_pide_la_captura_antes_y_ya_no_pregunta_el_mensaje(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    c.decir("no puedo cerrar el inventario")
    r = c.decir("no, sigue igual")
    assert c.estado["pendientes"] == ["numero_inventario", "captura", "mensaje_error"]
    assert "pide la captura antes que el mensaje" in r["traza"][-1]["detalle"]
    c.decir("el 1234")
    r = c.decir("", imagen=b"app_cierre")
    assert any("se tomó de la captura (OCR)" in paso["detalle"] for paso in r["traza"])
    descripcion = r["ticket"]["campos"]["descripcion"]
    assert "Mensaje en pantalla: No se puede cerrar el inventario" in descripcion
    assert "Texto leído en" not in descripcion  # no se repite


def test_sin_texto_util_en_la_captura_si_pregunta_el_mensaje(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    c.decir("no puedo cerrar el inventario")
    c.decir("no")
    c.decir("el 1234")
    r = c.decir("", imagen=b"app")
    assert c.estado["pendientes"] == ["mensaje_error"]
    assert r["respuestas"][-1].startswith("¿Qué dice el mensaje")


def test_el_ocr_solo_lee_capturas_de_la_app(charla_ocr, lector):
    c = charla_ocr(DESDE_LA_APP)
    c.decir("no puedo cerrar el inventario", imagen=b"escritorio")
    c.decir("hola", imagen=b"dudosa")
    assert lector.leidas == []


def test_lo_leido_en_otra_captura_va_en_la_descripcion(charla_ocr):
    c = charla_ocr(DESDE_LA_APP)
    c.decir("necesitamos guardia el sábado", imagen=b"app_version")
    for respuesta in ["sábado", "9 am"]:
        c.decir(respuesta)
    r = c.decir("el jefe")
    assert "Texto leído en captura.png: No cuenta con la versión" in r["ticket"]["campos"]["descripcion"]


@pytest.mark.parametrize("estado", [
    {"fase": "inventada"},
    {"fase": "pidiendo_datos", "intencion": "inv_no_cierra", "pendientes": []},
    {"pendientes": ["dato_que_no_existe"]},
    {"campo_raro": 1},
    {"intencion": "no_existe"},
    "no soy un objeto",
])
def test_estado_invalido(agente, estado):
    with pytest.raises(EstadoInvalido):
        agente.atender("hola", estado=estado)


def test_aplicacion_desconocida_en_la_sesion(agente):
    with pytest.raises(EstadoInvalido):
        agente.atender("hola", sesion={"aplicacion": "ventas"})
