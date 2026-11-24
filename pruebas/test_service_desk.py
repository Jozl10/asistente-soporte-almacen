"""Pruebas del conector con Service Desk, simulado y real."""

import pytest
import requests

from asistente.service_desk import (ServiceDeskHTTP, ServiceDeskNoDisponible, ServiceDeskSimulado,
                                    TicketInvalido, validar)

TICKET = {
    "usuario": "almacenista.prueba", "sucursal": "Sucursal de prueba",
    "aplicacion": "Inventario físico", "tipo": "Incidente",
    "subcategoria": "Inventario Fisico - Ciclico", "articulo": "Falla en Proceso",
    "asunto": "No se puede cerrar el inventario (Inventario físico)",
    "descripcion": "Número de inventario: 1234",
    "adjuntos": [{"nombre": "captura.png", "clase": "captura de la app"}],
}


def test_ticket_completo_es_valido():
    assert validar(TICKET) == []


def test_faltan_campos():
    errores = validar({"usuario": "x"})
    assert "Falta el campo 'sucursal'." in errores
    assert "Falta el campo 'descripcion'." in errores


def test_tipo_desconocido_y_campos_de_mas():
    errores = validar({**TICKET, "tipo": "Queja", "prioridad": "alta"})
    assert "El tipo debe ser Incidente o Solicitud." in errores
    assert any("prioridad" in e for e in errores)


def test_simulado_da_folios_seguidos_y_los_guarda(tmp_path):
    ruta = tmp_path / "tickets.jsonl"
    sd = ServiceDeskSimulado(ruta)
    assert sd.crear_ticket(TICKET) == {"folio": "SIM-000001", "estatus": "creado"}
    assert sd.crear_ticket(TICKET)["folio"] == "SIM-000002"
    # Al reiniciar la API sigue la numeración de la bitácora.
    assert ServiceDeskSimulado(ruta).crear_ticket(TICKET)["folio"] == "SIM-000003"


def test_simulado_rechaza_ticket_incompleto(tmp_path):
    with pytest.raises(TicketInvalido) as error:
        ServiceDeskSimulado(tmp_path / "t.jsonl").crear_ticket({"usuario": "x"})
    assert "Falta el campo 'tipo'." in error.value.errores


def test_simulado_apagado(tmp_path):
    sd = ServiceDeskSimulado(tmp_path / "t.jsonl")
    sd.disponible = False
    with pytest.raises(ServiceDeskNoDisponible):
        sd.crear_ticket(TICKET)


class Respuesta:
    def __init__(self, codigo, datos=None):
        self.status_code = codigo
        self._datos = datos or {}

    def json(self):
        return self._datos


def test_real_traduce_el_folio(monkeypatch):
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: Respuesta(200, {"id_manageengine": 123456}))
    assert ServiceDeskHTTP("http://sd.prueba").crear_ticket(TICKET)["folio"] == "123456"


@pytest.mark.parametrize("falla, error", [
    (requests.ConnectionError("sin red"), ServiceDeskNoDisponible),
    (Respuesta(503), ServiceDeskNoDisponible),
    (Respuesta(400), TicketInvalido),
])
def test_real_fallas(monkeypatch, falla, error):
    def post(*args, **kwargs):
        if isinstance(falla, Exception):
            raise falla
        return falla
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(error):
        ServiceDeskHTTP("http://sd.prueba").crear_ticket(TICKET)
