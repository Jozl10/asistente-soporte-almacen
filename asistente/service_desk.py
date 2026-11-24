"""Conector con Service Desk, la mesa de ayuda donde soporte da seguimiento a los tickets.

Hay dos versiones con la misma forma de uso, `crear_ticket(ticket)`:

- ServiceDeskSimulado: se comporta como el web service del formulario actual. Valida los
  campos, regresa un folio simulado (SIM-000001) y guarda el ticket en una bitácora. Es el
  que se usa en la página pública, para que nadie levante tickets reales desde ahí.
- ServiceDeskHTTP: manda el mismo ticket a un web service real. Queda listo para cuando haya
  un ambiente de pruebas; solo cambia la dirección.

Los campos son los que ya se conocen del formulario y de su bitácora.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

import requests

CAMPOS_OBLIGATORIOS = ("usuario", "sucursal", "aplicacion", "tipo", "subcategoria", "asunto",
                       "descripcion")
CAMPOS_OPCIONALES = ("articulo", "adjuntos")
TIPOS = ("Incidente", "Solicitud")


class ErrorServiceDesk(Exception):
    """Error al levantar un ticket."""


class TicketInvalido(ErrorServiceDesk):
    """Al ticket le faltan campos o trae alguno mal. `errores` dice cuáles."""

    def __init__(self, errores):
        super().__init__("; ".join(errores))
        self.errores = errores


class ServiceDeskNoDisponible(ErrorServiceDesk):
    """Service Desk no contestó."""


def validar(ticket):
    """Regresa la lista de errores del ticket (vacía si está bien)."""
    if not isinstance(ticket, dict):
        return ["El ticket debe ser un objeto JSON."]
    errores = []
    for campo in CAMPOS_OBLIGATORIOS:
        valor = ticket.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            errores.append(f"Falta el campo '{campo}'.")
    if ticket.get("tipo") not in (None, *TIPOS) and "Falta el campo 'tipo'." not in errores:
        errores.append(f"El tipo debe ser {' o '.join(TIPOS)}.")
    if "articulo" in ticket and ticket["articulo"] is not None and not isinstance(ticket["articulo"], str):
        errores.append("El campo 'articulo' debe ser texto.")
    adjuntos = ticket.get("adjuntos", [])
    if not isinstance(adjuntos, list) or not all(isinstance(a, dict) for a in adjuntos):
        errores.append("El campo 'adjuntos' debe ser una lista.")
    desconocidos = set(ticket) - set(CAMPOS_OBLIGATORIOS) - set(CAMPOS_OPCIONALES)
    if desconocidos:
        errores.append(f"Campos que Service Desk no recibe: {', '.join(sorted(desconocidos))}.")
    return errores


class ServiceDeskSimulado:
    modo = "simulado"

    def __init__(self, ruta_bitacora=None):
        self._candado = threading.Lock()
        self._tickets = []
        self.disponible = True
        self.ruta = Path(ruta_bitacora) if ruta_bitacora else None
        if self.ruta and self.ruta.exists():
            for renglon in self.ruta.read_text(encoding="utf-8").splitlines():
                if renglon.strip():
                    self._tickets.append(json.loads(renglon))

    def crear_ticket(self, ticket):
        if not self.disponible:
            raise ServiceDeskNoDisponible("Service Desk simulado: no disponible (prueba de falla).")
        errores = validar(ticket)
        if errores:
            raise TicketInvalido(errores)
        with self._candado:
            folio = f"SIM-{len(self._tickets) + 1:06d}"
            registro = {"folio": folio, "estatus": "creado",
                        "fecha": datetime.now().isoformat(timespec="seconds"), **ticket}
            self._tickets.append(registro)
            if self.ruta:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                with self.ruta.open("a", encoding="utf-8") as archivo:
                    archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
        return {"folio": folio, "estatus": "creado"}

    def listar(self):
        with self._candado:
            return list(self._tickets)


class ServiceDeskHTTP:
    modo = "real"
    disponible = True

    def __init__(self, url, timeout=10):
        self.url = url
        self.timeout = timeout

    def crear_ticket(self, ticket):
        errores = validar(ticket)
        if errores:
            raise TicketInvalido(errores)
        try:
            respuesta = requests.post(self.url, json=ticket, timeout=self.timeout)
        except requests.RequestException as error:
            raise ServiceDeskNoDisponible(f"Service Desk no contestó: {error}") from error
        if respuesta.status_code >= 500:
            raise ServiceDeskNoDisponible(f"Service Desk respondió {respuesta.status_code}.")
        if respuesta.status_code >= 400:
            raise TicketInvalido([f"Service Desk rechazó el ticket ({respuesta.status_code})."])
        datos = respuesta.json()
        # La bitácora del formulario guarda el folio con otro nombre; aquí se le dice folio.
        folio = datos.get("folio") or datos.get("id_manageengine")
        return {"folio": str(folio), "estatus": datos.get("estatus", "creado")}

    def listar(self):
        return []
