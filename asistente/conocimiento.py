"""Base de conocimiento del asistente.

Junta lo que venía de los dos avances:

- Avance 1: las respuestas que salen de la documentación de las apps, los datos que hay que
  preguntar para cada ticket y la plantilla del ticket (tipo, subcategoría, artículo, asunto).
- Avance 2: la intención "otro" y la forma de interpretar cada clase de imagen.

Las palabras clave de las reglas del Avance 1 ya no están: la intención ahora la da el SVM.
"""

APLICACIONES = {
    "cliente_recoge": "Cliente recoge",
    "inventario": "Inventario físico",
}

# Subcategoría de Service Desk cuando la intención no trae una propia.
SUBCATEGORIA_APP = {
    "cliente_recoge": ".Inventarios - Cliente recoge",
    "inventario": "Inventario Fisico - Ciclico",
}

# Lo que el asistente puede preguntar para completar el ticket. Cada dato tiene el nombre
# con el que va en el ticket, la pregunta que ve el usuario y qué tipo de respuesta espera:
# "texto", "numero" (se queda solo con el número), "aplicacion" o "imagen".
PREGUNTAS = {
    "numero_ticket": ("Ticket de cliente recoge", "¿Qué número de ticket de cliente recoge es?", "numero"),
    "mensaje_error": ("Mensaje en pantalla", "¿Qué dice el mensaje que te sale? Si no sale ninguno, escribe 'ninguno'.", "texto"),
    "etapa": ("Etapa donde se quedó", "¿En qué parte se quedó: surtir, verificar o entregar?", "texto"),
    "factura": ("Factura", "¿Cuál es la factura?", "texto"),
    "fecha": ("Fecha del inventario", "¿Qué día es el inventario?", "texto"),
    "hora": ("Hora de inicio", "¿A qué hora empiezan?", "texto"),
    "contacto": ("Contacto en sitio", "¿Quién será el contacto en el almacén ese día?", "texto"),
    "numero_inventario": ("Número de inventario", "¿Qué número de inventario es?", "numero"),
    "area": ("Área", "¿Qué área es?", "texto"),
    "codigo": ("Código del artículo", "¿Qué código tiene el artículo? Escríbelo como viene en la etiqueta.", "texto"),
    "ubicacion": ("Ubicación", "¿En qué ubicación está?", "texto"),
    "version": ("Versión que pide la app", "¿Qué versión te pide la app? Si no la sabes, escribe 'no sé'.", "texto"),
    "captura": ("Captura", "Si tienes una captura de la pantalla donde pasa, adjúntala. Si no tienes, escribe 'no'.", "imagen"),
    # Datos de la sesión. Dentro de la app de Flutter llegan solos; en la página pública se
    # preguntan al final.
    "aplicacion": ("Aplicación", "¿Desde qué app me escribes: Cliente recoge o Inventario físico?", "aplicacion"),
    "sucursal": ("Sucursal", "¿En qué sucursal estás?", "texto"),
    "usuario": ("Usuario", "¿Cuál es tu usuario de la app?", "texto"),
}

CAMPOS_SESION = ("aplicacion", "sucursal", "usuario")

# Una entrada por cada intención que reconoce el SVM.
#   app:      en qué aplicación aplica ("todas" si aplica en las dos).
#   atencion: conversacion, resolver (el asistente da la solución), solicitud o incidente
#             (se levanta el ticket).
#   respuesta: lo que contesta en cuanto la reconoce.
#   datos:    lo que pregunta, uno por uno, si hay que levantar el ticket.
#   ticket:   lo que ya se sabe del ticket sin preguntar nada.
INTENCIONES = {
    "saludo": {
        "app": "todas", "atencion": "conversacion", "datos": [], "ticket": None,
        "respuesta": "Hola, soy el asistente de soporte de {app}. Cuéntame qué problema tienes "
                     "y te ayudo a resolverlo, o levanto el ticket por ti.",
    },
    "despedida": {
        "app": "todas", "atencion": "conversacion", "datos": [], "ticket": None,
        "respuesta": "Con gusto. Si necesitas ayuda de nuevo, solo escríbeme. ¡Hasta luego!",
    },
    # ---------------- Cliente recoge ----------------
    "cr_firma_no_cierra": {
        "app": "cliente_recoge", "atencion": "incidente",
        "respuesta": "Si el ticket no se cierra al capturar la firma, lo tiene que revisar "
                     "soporte. Levanto el reporte por ti.",
        "datos": ["numero_ticket", "mensaje_error"],
        "ticket": {"tipo": "Incidente", "subcategoria": ".Inventarios - Cliente recoge",
                   "articulo": "QR - Entrega Cliente",
                   "asunto": "No permite cerrar el ticket al capturar la firma del cliente"},
    },
    "cr_ticket_atorado": {
        "app": "cliente_recoge", "atencion": "incidente",
        "respuesta": "Un ticket de cliente recoge que no avanza o que no aparece lo tiene que "
                     "revisar soporte. Levanto el reporte por ti.",
        "datos": ["numero_ticket", "etapa"],
        "ticket": {"tipo": "Incidente", "subcategoria": ".Inventarios - Cliente recoge",
                   "articulo": None, "asunto": "Ticket de cliente recoge atorado o que no aparece"},
    },
    "cr_remision": {
        "app": "cliente_recoge", "atencion": "incidente",
        "respuesta": "Si la remisión no se genera o no se imprime, lo tiene que revisar "
                     "soporte. Levanto el reporte por ti.",
        "datos": ["factura", "mensaje_error"],
        "ticket": {"tipo": "Incidente", "subcategoria": ".Inventarios - Cliente recoge",
                   "articulo": None, "asunto": "No genera o no imprime la remisión"},
    },
    # ---------------- Inventario físico ----------------
    "inv_guardia": {
        "app": "inventario", "atencion": "solicitud",
        "respuesta": "Claro, levanto la solicitud de guardia de soporte para tu inventario.",
        "datos": ["fecha", "hora", "contacto"],
        "ticket": {"tipo": "Solicitud", "subcategoria": "Inventario Fisico - Ciclico",
                   "articulo": "Guardia de Soporte",
                   "asunto": "Guardia de soporte para inventario físico"},
    },
    "inv_area_ocupada": {
        "app": "inventario", "atencion": "resolver",
        "respuesta": "El área se bloquea mientras otro usuario la tiene abierta, y se libera "
                     "cuando esa persona regresa a la pantalla de Áreas pendientes. Pídele que "
                     "lo haga y vuelve a intentar.",
        "datos": ["numero_inventario", "area"],
        "ticket": {"tipo": "Incidente", "subcategoria": "Inventario Fisico - Ciclico",
                   "articulo": "Falla en Proceso",
                   "asunto": "Área de inventario ocupada por otro usuario"},
    },
    "inv_no_inicia": {
        "app": "inventario", "atencion": "resolver",
        "respuesta": "La app solo te deja entrar cuando hay un inventario activo para tu "
                     "almacén. Confirma con quien dio de alta el inventario que ya esté activo "
                     "y vuelve a entrar.",
        "datos": ["numero_inventario", "mensaje_error"],
        "ticket": {"tipo": "Incidente", "subcategoria": "Inventario Fisico - Ciclico",
                   "articulo": "Falla en Proceso", "asunto": "No se puede iniciar el inventario"},
    },
    "inv_no_cierra": {
        "app": "inventario", "atencion": "resolver",
        "respuesta": "La app no deja terminar un área mientras tenga ubicaciones pendientes. "
                     "Revisa que todas las ubicaciones del área estén escaneadas y contadas, y "
                     "que no tengas revisiones pendientes.",
        "datos": ["numero_inventario", "mensaje_error"],
        "ticket": {"tipo": "Incidente", "subcategoria": "Inventario Fisico - Ciclico",
                   "articulo": "Falla en Proceso", "asunto": "No se puede cerrar el inventario"},
    },
    # ---------------- Las dos aplicaciones ----------------
    "codigo_no_se_lee": {
        "app": "todas", "atencion": "resolver",
        # La respuesta cambia según la app; "general" es para cuando todavía no se sabe.
        "respuesta": {
            "cliente_recoge": "Puedes escribir el código a mano en la pantalla de escaneo en "
                              "lugar de escanearlo, o escanearlo con la cámara del dispositivo.",
            "inventario": "Puedes escribir el código de barras a mano, o usar la opción de "
                          "código ilegible para escoger el artículo de la lista.",
            "general": "Puedes escribir el código a mano en lugar de escanearlo. En Inventario "
                       "físico también puedes usar la opción de código ilegible para escoger "
                       "el artículo de la lista.",
        },
        "datos": ["codigo", "ubicacion"],
        "ticket": {"tipo": "Incidente", "subcategoria": None, "articulo": None,
                   "asunto": "No se puede leer el código del artículo"},
    },
    "actualizar_version": {
        "app": "todas", "atencion": "solicitud",
        "respuesta": "Las actualizaciones se instalan solas en los dispositivos. Si a tu "
                     "dispositivo no le llegó, levanto la solicitud para que te la instalen.",
        "datos": ["version"],
        "ticket": {"tipo": "Solicitud", "subcategoria": ".Dispositivos", "articulo": "Aplicaciones",
                   "asunto": "La actualización de la app no llegó al dispositivo"},
    },
    "otro": {
        "app": "todas", "atencion": "incidente",
        "respuesta": "No tengo una solución para eso, así que levanto un ticket general para "
                     "que soporte lo revise.",
        "datos": [],
        "ticket": {"tipo": "Incidente", "subcategoria": None, "articulo": None,
                   "asunto": "Problema que el asistente no reconoció"},
    },
}

# Respuestas cortas a "¿se resolvió?".
AFIRMATIVAS = {"si", "ya", "listo", "funciono", "quedo", "pude", "resuelto", "gracias", "sirvio"}
NEGATIVAS = {"no", "sigue", "igual", "tampoco", "nada"}

NOMBRE_ADJUNTO = {
    "app": "captura de la app",
    "escritorio": "captura del sistema de escritorio",
    "otra": "otra imagen",
}


def intenciones_de_app(app):
    """Intenciones que tienen sentido si el usuario escribe desde `app`."""
    return {nombre for nombre, info in INTENCIONES.items() if info["app"] in ("todas", app)}


def interpretar_imagen(clase, clasificada, intencion):
    """Convierte la clase de la imagen en lo que el asistente le dice al usuario.

    Es la misma interpretación del Avance 2: depende de la clase y de la intención.
    """
    if not clasificada:
        return ("No alcanzo a distinguir qué muestra tu imagen, así que va en el ticket tal cual "
                "para que soporte la revise.")
    if clase == "app":
        return "Tu imagen es una captura de la app y va en el ticket, así soporte ve la pantalla donde pasó."
    if clase == "escritorio" and intencion == "otro":
        return "Tu imagen es del sistema de escritorio, que este chat no atiende, y va en el ticket."
    if clase == "escritorio":
        return ("Tu imagen es del sistema de escritorio y no de la app. La agrego al ticket; si "
                "tienes una captura de la app, mándamela también.")
    if intencion == "codigo_no_se_lee":
        return "Tu imagen es una foto, seguramente de la etiqueta, y va en el ticket para que revisen el código."
    return ("Tu imagen no es una pantalla de la app. La agrego al ticket; si el problema es en "
            "la app, mándame también una captura de la pantalla donde pasa.")
