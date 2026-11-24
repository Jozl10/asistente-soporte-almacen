"""Agente híbrido que coordina el chat, el clasificador de imágenes y Service Desk.

Sigue los tres pasos que se vieron en clase:

1. percibir: el clasificador de imágenes saca la clase de la captura y, si es de la app,
   el OCR lee el texto de la alerta. El clasificador de texto saca la intención del mensaje
   (o de lo leído, si el usuario solo mandó la captura). Si el usuario escribe desde una
   app, solo cuentan las intenciones de esa app.
2. decidir: dos capas.
   - Reactiva: reglas inmediatas de "si pasa esto, haz aquello" (saludar, dar la solución
     de la documentación, confirmar si se resolvió, cancelar).
   - Deliberativa: cuando hay que levantar un ticket, arma un plan con los datos que
     faltan, lo va cumpliendo pregunta por pregunta, lo rehace si llega algo nuevo (una
     captura a media conversación) y al final usa la herramienta de Service Desk.
3. actuar: contesta, pregunta, guarda el dato o levanta el ticket.

El agente no guarda nada entre mensajes: el estado de la conversación entra y sale en cada
llamada a `atender`. Así la API puede atender a muchos usuarios sin mezclar conversaciones.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

from .conocimiento import (AFIRMATIVAS, APLICACIONES, CAMPOS_SESION, INTENCIONES, NEGATIVAS,
                           NOMBRE_ADJUNTO, PREGUNTAS, SUBCATEGORIA_APP, interpretar_imagen,
                           intenciones_de_app)
from .imagen import ImagenInvalida
from .service_desk import ServiceDeskNoDisponible, TicketInvalido
from .texto import normalizar, tokenizar

FASES = ("esperando_consulta", "confirmando_solucion", "pidiendo_datos", "ticket_pendiente")
MAXIMO_MENSAJES = 20
MAXIMO_ADJUNTOS = 5

# Puntuación mínima para tomar la intención de lo que se leyó en la captura (cuando el
# usuario no escribió nada). Es más exigente que la del mensaje porque el texto de una alerta
# se parece poco a como escriben los usuarios: con las capturas reales del Avance 2, de 0.1 a
# 0.3 quitó todos los errores (evaluar_ocr.py). Abajo de esto, el agente pide la descripción.
UMBRAL_CAPTURA = 0.2


class EstadoInvalido(ValueError):
    """El estado que mandó el cliente no tiene la forma esperada."""


@dataclass
class Estado:
    """Memoria de la conversación."""

    fase: str = "esperando_consulta"
    intencion: str | None = None
    aplicacion: str | None = None
    pendientes: list = field(default_factory=list)
    datos: dict = field(default_factory=dict)
    mensajes: list = field(default_factory=list)
    adjuntos: list = field(default_factory=list)
    no_entendidos: int = 0
    ticket_por_enviar: dict | None = None

    @classmethod
    def desde_dict(cls, valores):
        if not valores:
            return cls()
        if not isinstance(valores, dict):
            raise EstadoInvalido("El estado debe ser un objeto.")
        conocidos = {f.name for f in fields(cls)}
        if set(valores) - conocidos:
            raise EstadoInvalido(f"Campos desconocidos: {sorted(set(valores) - conocidos)}")
        estado = cls(**valores)
        estado._validar()
        return estado

    def _validar(self):
        problemas = []
        if self.fase not in FASES:
            problemas.append("fase")
        if self.intencion is not None and self.intencion not in INTENCIONES:
            problemas.append("intencion")
        if self.aplicacion is not None and self.aplicacion not in APLICACIONES:
            problemas.append("aplicacion")
        if not isinstance(self.pendientes, list) or any(p not in PREGUNTAS for p in self.pendientes):
            problemas.append("pendientes")
        if not isinstance(self.datos, dict) or any(
                k not in PREGUNTAS or not isinstance(v, str) for k, v in self.datos.items()):
            problemas.append("datos")
        if not isinstance(self.mensajes, list) or len(self.mensajes) > MAXIMO_MENSAJES or any(
                not isinstance(m, str) for m in self.mensajes):
            problemas.append("mensajes")
        if not isinstance(self.adjuntos, list) or len(self.adjuntos) > MAXIMO_ADJUNTOS or any(
                not isinstance(a, dict) for a in self.adjuntos):
            problemas.append("adjuntos")
        if not isinstance(self.no_entendidos, int) or self.no_entendidos < 0:
            problemas.append("no_entendidos")
        if self.ticket_por_enviar is not None and not isinstance(self.ticket_por_enviar, dict):
            problemas.append("ticket_por_enviar")
        if self.fase == "pidiendo_datos" and not self.pendientes:
            problemas.append("pendientes vacíos")
        if self.fase in ("pidiendo_datos", "confirmando_solucion") and self.intencion is None:
            problemas.append("falta la intención")
        if self.fase == "ticket_pendiente" and not self.ticket_por_enviar:
            problemas.append("falta el ticket por enviar")
        if problemas:
            raise EstadoInvalido("Estado inválido: " + ", ".join(problemas))

    def a_dict(self):
        return asdict(self)

    def nueva_consulta(self):
        """Olvida la consulta terminada, pero recuerda la app, la sucursal y el usuario."""
        sesion = {k: v for k, v in self.datos.items() if k in CAMPOS_SESION}
        aplicacion = self.aplicacion
        self.__init__(aplicacion=aplicacion, datos=sesion)


@dataclass
class Percepcion:
    mensaje: str
    palabras: set
    texto: dict | None = None
    imagen: dict | None = None
    error_imagen: str | None = None


@dataclass
class Decision:
    accion: str
    capa: str
    motivo: str


def limpiar_sesion(sesion):
    """Datos que la app de Flutter manda en la dirección: usuario, sucursal y aplicación."""
    sesion = sesion or {}
    limpia = {}
    for campo in CAMPOS_SESION:
        valor = sesion.get(campo)
        if isinstance(valor, str) and valor.strip():
            limpia[campo] = valor.strip()[:80]
    if limpia.get("aplicacion") not in (None, *APLICACIONES):
        raise EstadoInvalido(f"Aplicación desconocida: {limpia['aplicacion']}")
    return limpia


def leer_aplicacion(palabras):
    if palabras & {"cliente", "recoge"}:
        return "cliente_recoge"
    if palabras & {"inventario", "fisico", "inventarios"}:
        return "inventario"
    return None


class AgenteSoporte:
    """Agente de soporte. Sus herramientas: los dos clasificadores y Service Desk."""

    def __init__(self, clasificador_texto, clasificador_imagen, service_desk, al_evento=None,
                 lector_ocr=None, umbral_captura=UMBRAL_CAPTURA):
        self.texto = clasificador_texto
        self.imagen = clasificador_imagen
        self.service_desk = service_desk
        self.ocr = lector_ocr if lector_ocr is not None and lector_ocr.disponible else None
        self.umbral_captura = umbral_captura
        # Función que recibe (evento, datos) para el monitoreo; no recibe texto del usuario.
        self.al_evento = al_evento or (lambda evento, datos: None)

    # ------------------------------------------------------------------ entrada
    def atender(self, mensaje="", imagen=None, nombre_imagen=None, sesion=None, estado=None):
        sesion = limpiar_sesion(sesion)
        estado = Estado.desde_dict(estado)
        if estado.aplicacion is None and sesion.get("aplicacion"):
            estado.aplicacion = sesion["aplicacion"]
        traza, respuestas = [], []

        percepcion = self.percibir(mensaje, imagen, nombre_imagen, estado, traza)
        decision = self.decidir(percepcion, estado)
        traza.append({"paso": "decidir", "capa": decision.capa,
                      "detalle": f"{decision.accion}: {decision.motivo}"})
        ticket = self.actuar(decision, percepcion, estado, sesion, respuestas, traza)

        return {"respuestas": respuestas, "accion": decision.accion, "ticket": ticket,
                "estado": estado.a_dict(), "traza": traza}

    # ------------------------------------------------------------------ 1. percibir
    def percibir(self, mensaje, imagen, nombre_imagen, estado, traza):
        mensaje = (mensaje or "").strip()
        palabras = set(normalizar(tokenizar(mensaje))) if mensaje else set()
        percepcion = Percepcion(mensaje=mensaje, palabras=palabras)

        if imagen:
            try:
                resultado = self.imagen.predecir(imagen)
                resultado["nombre"] = (nombre_imagen or "captura")[:80]
                percepcion.imagen = resultado
                estado_clase = "" if resultado["clasificada"] else ", sin clasificar"
                traza.append({"paso": "percibir", "detalle": f"Imagen → {resultado['clase']} "
                              f"(seguridad {resultado['seguridad']:.2f}{estado_clase})"})
                self.al_evento("imagen", {"clase": resultado["clase"],
                                          "clasificada": resultado["clasificada"]})
                self._leer_captura(imagen, resultado, traza)
            except ImagenInvalida as error:
                percepcion.error_imagen = str(error)
                traza.append({"paso": "percibir", "detalle": f"Imagen no válida: {error}"})

        # Lo que se clasifica es el mensaje; si el usuario solo mandó la captura, lo que se
        # leyó en ella.
        leido = percepcion.imagen.get("texto_leido") if percepcion.imagen else None
        texto, fuente = (mensaje, "mensaje") if mensaje else (leido, "captura")
        if texto and estado.fase == "esperando_consulta":
            permitidas = intenciones_de_app(estado.aplicacion) if estado.aplicacion else None
            resultado = self.texto.predecir(texto, permitidas)
            resultado["fuente"] = fuente
            percepcion.texto = resultado
            origen = "Texto" if fuente == "mensaje" else "Texto de la captura"
            detalle = f"{origen} → {resultado['intencion']} (puntuación {resultado['puntuacion']:.2f})"
            if resultado["intencion_modelo"] != resultado["intencion"]:
                detalle += (f"; el modelo prefería {resultado['intencion_modelo']}, que no es de "
                            f"{APLICACIONES[estado.aplicacion]}")
            if not resultado["segura"]:
                detalle += "; abajo del umbral de confianza"
            traza.append({"paso": "percibir", "detalle": detalle})
        elif mensaje:
            traza.append({"paso": "percibir",
                          "detalle": f"Texto: respuesta en la fase {estado.fase} (no se clasifica)"})
        return percepcion

    def _leer_captura(self, contenido, resultado, traza):
        """OCR, solo en las capturas de la app: ahí salen las alertas del escáner."""
        if not self.ocr or not resultado["clasificada"] or resultado["clase"] != "app":
            return
        try:
            lectura = self.ocr.leer(contenido)
        except Exception as error:  # el OCR nunca debe tumbar la conversación
            traza.append({"paso": "percibir", "detalle": f"OCR falló: {type(error).__name__}"})
            return
        if not lectura:
            traza.append({"paso": "percibir", "detalle": "OCR: no se leyó texto útil"})
            return
        resultado["texto_leido"] = lectura["texto"]
        corto = lectura["texto"] if len(lectura["texto"]) <= 80 else lectura["texto"][:77] + "…"
        traza.append({"paso": "percibir", "detalle": f"OCR → “{corto}” ({lectura['palabras']} "
                      f"palabras, confianza {lectura['confianza']:.0f})"})

    # ------------------------------------------------------------------ 2. decidir
    def decidir(self, p, e):
        # Capa reactiva: reglas que se aplican de inmediato.
        if "cancelar" in p.palabras and e.fase != "esperando_consulta":
            return Decision("cancelar", "reactiva", "el usuario pidió cancelar")

        if e.fase == "ticket_pendiente":
            if p.palabras & NEGATIVAS:
                return Decision("cancelar", "reactiva", "no quiere reintentar el envío")
            return Decision("reintentar_ticket", "deliberativa",
                            "hay un ticket que no se pudo enviar y se vuelve a intentar")

        if e.fase == "confirmando_solucion":
            if p.palabras & NEGATIVAS or (p.imagen and not p.palabras & AFIRMATIVAS):
                return Decision("planear_ticket", "deliberativa",
                                "la solución no funcionó: hay que juntar los datos del ticket")
            if p.palabras & AFIRMATIVAS:
                return Decision("cerrar_resuelto", "reactiva", "el usuario confirmó que se resolvió")
            return Decision("repreguntar", "reactiva", "no quedó claro si se resolvió")

        if e.fase == "pidiendo_datos":
            return Decision("recibir_dato", "deliberativa",
                            f"el plan esperaba el dato '{e.pendientes[0]}'")

        # Fase esperando_consulta.
        if not p.texto:
            return Decision("pedir_descripcion", "reactiva", "llegó una imagen sin mensaje")
        intencion = p.texto["intencion"] if p.texto["segura"] else "otro"
        info = INTENCIONES[intencion]
        if p.texto["fuente"] == "captura" and (
                intencion == "otro" or info["atencion"] == "conversacion"
                or p.texto["puntuacion"] < self.umbral_captura):
            return Decision("pedir_descripcion", "reactiva",
                            "lo que se leyó en la captura no alcanza para saber el problema "
                            f"(para la captura se pide puntuación de {self.umbral_captura})")
        if info["atencion"] == "conversacion":
            return Decision("conversar", "reactiva", f"es {intencion}: se contesta directo")
        if intencion == "otro":
            if e.no_entendidos == 0:
                return Decision("pedir_reformular", "reactiva",
                                "no es ninguna intención conocida; se pide decirlo con otras "
                                "palabras antes de levantar un ticket")
            return Decision("planear_ticket", "deliberativa",
                            "segunda vez que no se reconoce: se levanta un ticket general")
        if info["atencion"] == "resolver":
            return Decision("dar_solucion", "reactiva", "la documentación tiene la solución")
        return Decision("planear_ticket", "deliberativa",
                        f"es una {info['atencion']}: hay que levantar un ticket")

    # ------------------------------------------------------------------ 3. actuar
    def actuar(self, d, p, e, sesion, respuestas, traza):
        if p.error_imagen:
            respuestas.append(f"No pude usar la imagen que adjuntaste: {p.error_imagen}")

        if e.fase == "esperando_consulta" and d.accion in (
                "dar_solucion", "planear_ticket", "pedir_reformular"):
            self._recordar_consulta(p, e, traza)

        # La interpretación de la imagen va después de la primera respuesta.
        def interpretar():
            if p.imagen:
                self._adjuntar(p.imagen, e)
                texto = interpretar_imagen(p.imagen["clase"], p.imagen["clasificada"], e.intencion)
                leido = p.imagen.get("texto_leido")
                if leido:
                    corto = leido if len(leido) <= 120 else leido[:117] + "…"
                    texto += f" En ella leí: “{corto}”"
                respuestas.append(texto)

        accion = d.accion
        if accion == "conversar":
            intencion = p.texto["intencion"]
            nombre_app = APLICACIONES.get(e.aplicacion, "las aplicaciones de almacén")
            respuestas.append(INTENCIONES[intencion]["respuesta"].format(app=nombre_app))
            interpretar()
            self.al_evento("consulta", {"intencion": intencion})
            return None

        if accion == "pedir_reformular":
            e.no_entendidos += 1
            interpretar()
            respuestas.append("No te entendí. ¿Me lo puedes decir con otras palabras? Por ejemplo: "
                              "'no puedo cerrar el conteo' o 'no lee el código'.")
            self.al_evento("no_entendido", {})
            return None

        if accion == "pedir_descripcion":
            interpretar()
            respuestas.append("Cuéntame en una frase qué problema tienes.")
            return None

        if accion == "dar_solucion":
            info = INTENCIONES[e.intencion]
            respuesta = info["respuesta"]
            if isinstance(respuesta, dict):
                respuesta = respuesta.get(e.aplicacion) or respuesta["general"]
            respuestas.append(respuesta)
            interpretar()
            respuestas.append("¿Con eso se resolvió? Si no, levanto el ticket.")
            e.fase = "confirmando_solucion"
            self.al_evento("consulta", {"intencion": e.intencion})
            return None

        if accion == "cerrar_resuelto":
            self.al_evento("resuelto_sin_ticket", {"intencion": e.intencion})
            e.nueva_consulta()
            respuestas.append("Qué bueno. ¿Te ayudo con algo más?")
            return None

        if accion == "repreguntar":
            interpretar()
            respuestas.append("¿Se resolvió? Contéstame sí o no.")
            return None

        if accion == "cancelar":
            self.al_evento("cancelado", {"intencion": e.intencion})
            e.nueva_consulta()
            respuestas.append("Listo, cancelé el reporte. ¿Te ayudo con algo más?")
            return None

        if accion == "planear_ticket":
            if e.fase == "confirmando_solucion":
                respuestas.append("Va, entonces levanto el ticket para que soporte lo revise.")
            elif e.intencion == "otro":
                respuestas.append("Sigo sin entenderte, así que levanto un ticket general para "
                                  "que soporte lo revise.")
            else:
                self.al_evento("consulta", {"intencion": e.intencion})
                respuestas.append(INTENCIONES[e.intencion]["respuesta"])
            interpretar()
            e.pendientes = self._planear(e, sesion)
            detalle = "Plan del ticket: " + (" → ".join(e.pendientes) if e.pendientes
                                             else "ya están todos los datos")
            if self.ocr and "captura" in e.pendientes and "mensaje_error" in e.pendientes:
                detalle += " (pide la captura antes que el mensaje: de ahí lo puede leer)"
            traza.append({"paso": "actuar", "detalle": detalle})
            self._replanear(e, traza)
            if not e.pendientes:
                return self._levantar(self._armar_ticket(e, sesion), e, respuestas, traza)
            e.fase = "pidiendo_datos"
            respuestas.append(PREGUNTAS[e.pendientes[0]][1])
            return None

        if accion == "recibir_dato":
            return self._recibir_dato(p, e, sesion, respuestas, traza, interpretar)

        if accion == "reintentar_ticket":
            return self._levantar(e.ticket_por_enviar, e, respuestas, traza)

        raise ValueError(f"Acción desconocida: {accion}")

    # ------------------------------------------------------------------ ayudantes
    def _recordar_consulta(self, p, e, traza):
        intencion = p.texto["intencion"] if p.texto["segura"] else "otro"
        e.intencion = intencion
        if intencion != "otro":
            e.no_entendidos = 0
        if p.mensaje and len(e.mensajes) < MAXIMO_MENSAJES:
            e.mensajes.append(p.mensaje[:500])
        app = INTENCIONES[intencion]["app"]
        if e.aplicacion is None and app != "todas":
            e.aplicacion = app
            traza.append({"paso": "actuar",
                          "detalle": f"Se dedujo la app {APLICACIONES[app]} por la intención"})

    def _adjuntar(self, imagen, e):
        if len(e.adjuntos) >= MAXIMO_ADJUNTOS:
            return
        descripcion = NOMBRE_ADJUNTO[imagen["clase"]] if imagen["clasificada"] else "imagen sin clasificar"
        adjunto = {"nombre": imagen["nombre"], "clase": imagen["clase"],
                   "seguridad": imagen["seguridad"], "descripcion": descripcion}
        if imagen.get("texto_leido"):
            adjunto["texto_leido"] = imagen["texto_leido"]
        e.adjuntos.append(adjunto)

    def _planear(self, e, sesion):
        """Capa deliberativa: la lista de lo que falta para poder levantar el ticket."""
        info = INTENCIONES[e.intencion]
        plan = [dato for dato in info["datos"] if dato not in e.datos]
        # A un incidente le sirve la captura de la pantalla donde pasó, aunque haya empezado
        # como una duda que tenía solución en la documentación.
        if info["ticket"]["tipo"] == "Incidente" and not e.adjuntos:
            plan.append("captura")
            # Con OCR, la captura se pide antes que el mensaje de error: de ahí lo puede leer
            # y ya no hace falta preguntarlo.
            if self.ocr and "mensaje_error" in plan:
                plan.remove("captura")
                plan.insert(plan.index("mensaje_error"), "captura")
        if not e.aplicacion:
            plan.append("aplicacion")
        plan += [c for c in ("sucursal", "usuario") if not sesion.get(c) and c not in e.datos]
        return plan

    def _replanear(self, e, traza):
        """Rehace el plan con lo que ya llegó: la captura y el texto que se leyó en ella."""
        if e.adjuntos and "captura" in e.pendientes:
            e.pendientes.remove("captura")
            traza.append({"paso": "actuar",
                          "detalle": "Se rehizo el plan: la captura ya llegó, no se pide"})
        leido = next((a["texto_leido"] for a in reversed(e.adjuntos) if a.get("texto_leido")), None)
        if leido and "mensaje_error" in e.pendientes:
            e.datos["mensaje_error"] = leido
            e.pendientes.remove("mensaje_error")
            traza.append({"paso": "actuar", "detalle": "Se rehizo el plan: el mensaje en pantalla "
                          "se tomó de la captura (OCR), no se pregunta"})

    def _recibir_dato(self, p, e, sesion, respuestas, traza, interpretar):
        clave = e.pendientes[0]
        _, pregunta, tipo = PREGUNTAS[clave]
        interpretar()
        # La captura (o lo que se leyó en ella) puede cubrir el dato que se preguntó y otros.
        self._replanear(e, traza)
        # Si mandó la captura con un texto y falta el mensaje en pantalla, el texto es eso.
        if (clave not in e.pendientes and p.mensaje and "mensaje_error" in e.pendientes):
            e.datos["mensaje_error"] = p.mensaje[:300]
            e.pendientes.remove("mensaje_error")
            traza.append({"paso": "actuar", "detalle": "El texto que vino con la captura se "
                          "tomó como el mensaje en pantalla"})

        if clave in e.pendientes:
            valor = None
            if tipo == "imagen":
                if p.palabras & NEGATIVAS or p.palabras & {"ninguna", "tengo"}:
                    valor = "sin captura"
                else:
                    respuestas.append("Adjunta la imagen con el botón de adjuntar, o escribe 'no' "
                                      "si no tienes.")
                    return None
            elif tipo == "numero":
                numero = re.search(r"\d+", p.mensaje)
                if not numero:
                    respuestas.append("Necesito el número, por ejemplo 35. " + pregunta)
                    return None
                valor = numero.group()
            elif tipo == "aplicacion":
                app = leer_aplicacion(p.palabras)
                if not app:
                    respuestas.append("No reconocí la app. " + pregunta)
                    return None
                e.aplicacion = app
                valor = APLICACIONES[app]
            else:
                if not p.mensaje:
                    respuestas.append(pregunta)
                    return None
                valor = p.mensaje[:300]

            if tipo not in ("imagen", "aplicacion"):
                e.datos[clave] = valor
            e.pendientes.remove(clave)

        if e.pendientes:
            respuestas.append(PREGUNTAS[e.pendientes[0]][1])
            return None
        return self._levantar(self._armar_ticket(e, sesion), e, respuestas, traza)

    def _armar_ticket(self, e, sesion):
        plantilla = INTENCIONES[e.intencion]["ticket"]
        nombre_app = APLICACIONES[e.aplicacion]
        lineas = []
        if e.mensajes:
            lineas.append("Mensaje del usuario: " + " / ".join(e.mensajes))
        lineas += [f"{PREGUNTAS[c][0]}: {v}" for c, v in e.datos.items() if c not in CAMPOS_SESION]
        for adjunto in e.adjuntos:
            leido = adjunto.get("texto_leido")
            if leido and leido != e.datos.get("mensaje_error"):
                lineas.append(f"Texto leído en {adjunto['nombre']}: {leido}")
        lineas.append(f"Intención que reconoció el asistente: {e.intencion}")
        ticket = {
            "usuario": sesion.get("usuario") or e.datos.get("usuario"),
            "sucursal": sesion.get("sucursal") or e.datos.get("sucursal"),
            "aplicacion": nombre_app,
            "tipo": plantilla["tipo"],
            "subcategoria": plantilla["subcategoria"] or SUBCATEGORIA_APP[e.aplicacion],
            "asunto": f"{plantilla['asunto']} ({nombre_app})",
            "descripcion": "\n".join(lineas),
        }
        if plantilla["articulo"]:
            ticket["articulo"] = plantilla["articulo"]
        if e.adjuntos:
            ticket["adjuntos"] = [{"nombre": a["nombre"], "clase": a["descripcion"]}
                                  for a in e.adjuntos]
        return ticket

    def _levantar(self, ticket, e, respuestas, traza):
        """Usa la herramienta de Service Desk y le avisa al usuario cómo salió."""
        try:
            resultado = self.service_desk.crear_ticket(ticket)
        except ServiceDeskNoDisponible as error:
            traza.append({"paso": "actuar", "detalle": f"Service Desk no respondió: {error}"})
            self.al_evento("ticket_fallido", {"intencion": e.intencion})
            e.ticket_por_enviar = ticket
            e.pendientes = []
            e.fase = "ticket_pendiente"
            respuestas.append("No pude enviar el ticket porque Service Desk no responde. ¿Lo "
                              "intento otra vez? Si es urgente, llama a soporte.")
            return {"estatus": "failed", "campos": ticket}
        except TicketInvalido as error:
            traza.append({"paso": "actuar", "detalle": f"Service Desk rechazó el ticket: {error}"})
            self.al_evento("ticket_fallido", {"intencion": e.intencion})
            e.nueva_consulta()
            respuestas.append("No pude levantar el ticket porque le faltan datos. Por favor llama "
                              "a soporte para reportarlo.")
            return {"estatus": "failed", "campos": ticket, "errores": error.errores}

        modo = getattr(self.service_desk, "modo", "real")
        traza.append({"paso": "actuar",
                      "detalle": f"Service Desk ({modo}) creó el ticket {resultado['folio']}"})
        self.al_evento("ticket_creado", {"intencion": e.intencion, "tipo": ticket["tipo"]})
        e.nueva_consulta()
        respuestas.append(f"Listo, levanté el ticket {resultado['folio']}. Soporte le dará "
                          "seguimiento. ¿Te ayudo con algo más?")
        return {**resultado, "campos": ticket}
