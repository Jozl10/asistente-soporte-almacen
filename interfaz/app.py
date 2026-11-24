"""Pantalla del asistente de soporte, hecha con Streamlit.

La pantalla no clasifica nada: le manda cada mensaje (y la captura, si hay) a la API de Flask
y muestra lo que contesta el agente. La conversación la recuerda la API a través del estado
que va y viene en cada petición.

Dos formas de abrirla:

- Dentro de la app de Flutter (WebView): la dirección trae los datos de la sesión, por
  ejemplo ?embed=true&aplicacion=inventario&sucursal=...&usuario=..., y el asistente no los
  pregunta.
- Como página pública de demostración: sin esos datos. El asistente los pregunta cuando le
  hacen falta, y se ve el detalle de cómo decidió.

La dirección de la API sale del secreto API_URL de Streamlit o de la variable de entorno
API_URL (por omisión, http://127.0.0.1:5000).

En la Mac, desde codigo/:  venv/bin/streamlit run interfaz/app.py
"""

import io
import json
import os
from pathlib import Path

import requests
import streamlit as st
from PIL import Image

CARPETA = Path(__file__).resolve().parent
APLICACIONES = {"cliente_recoge": "Cliente recoge", "inventario": "Inventario físico"}
EJEMPLOS = {
    "Captura de la app": "ejemplo_app.jpg",
    "Sistema de escritorio": "ejemplo_escritorio.jpg",
    "Foto del almacén": "ejemplo_otra.jpg",
}
FORMATOS = ["png", "jpg", "jpeg", "webp"]
TIEMPO_MAXIMO = 30
BIENVENIDA = ("Hola, soy el asistente automático de soporte. Cuéntame qué problema tienes "
              "con la app y te doy la solución o levanto el ticket por ti. Puedes adjuntar una "
              "captura con el clip. Si necesitas a una persona, llama a soporte.")


def direccion_api():
    try:
        valor = st.secrets.get("API_URL")
    except Exception:  # No hay secrets.toml (en la Mac): se usa la variable de entorno.
        valor = None
    return (valor or os.environ.get("API_URL") or "http://127.0.0.1:5000").rstrip("/")


API = direccion_api()


@st.cache_data(ttl=30, show_spinner=False)
def salud_api(direccion):
    try:
        respuesta = requests.get(f"{direccion}/salud", timeout=5)
        return respuesta.json() if respuesta.ok else None
    except (requests.RequestException, ValueError):
        return None


def miniatura(contenido):
    """Copia chica de la imagen para el historial, para no guardar el archivo completo."""
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.thumbnail((240, 240))
        salida = io.BytesIO()
        imagen.convert("RGB").save(salida, format="JPEG", quality=80)
        return salida.getvalue()
    except Exception:
        return None


def sesion_de_la_direccion():
    """Datos que manda la app de Flutter en la dirección; vacío en la página pública."""
    parametros = st.query_params
    sesion = {campo: parametros.get(campo, "").strip()[:80]
              for campo in ("aplicacion", "sucursal", "usuario")}
    if sesion["aplicacion"] not in APLICACIONES:
        sesion["aplicacion"] = ""
    return {k: v for k, v in sesion.items() if v}


def iniciar():
    if "historial" not in st.session_state:
        st.session_state.historial = [{"rol": "assistant", "textos": [BIENVENIDA]}]
        st.session_state.estado = None
        st.session_state.pendiente = None


def nueva_conversacion():
    for clave in ("historial", "estado", "pendiente"):
        st.session_state.pop(clave, None)


# ---------------------------------------------------------------------- llamada a la API
def llamar_api(sesion, mensaje, imagen=None, nombre=None, tipo=None):
    """Regresa (respuesta, error). El error ya viene redactado para el usuario."""
    datos = {"mensaje": mensaje, "sesion": json.dumps(sesion),
             "estado": json.dumps(st.session_state.estado)}
    archivos = {"imagen": (nombre or "captura.jpg", imagen, tipo or "image/jpeg")} if imagen else None
    try:
        respuesta = requests.post(f"{API}/conversacion", data=datos, files=archivos,
                                  timeout=TIEMPO_MAXIMO)
    except requests.Timeout:
        return None, ("El asistente tardó demasiado en contestar. Intenta de nuevo; si es "
                      "urgente, llama a soporte.")
    except requests.RequestException:
        return None, ("No pude conectarme con el asistente. Revisa tu conexión e intenta de "
                      "nuevo; si es urgente, llama a soporte.")
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None, "El asistente contestó algo que no entendí. Intenta de nuevo."
    if respuesta.status_code == 400 and cuerpo.get("error") == "estado_invalido":
        st.session_state.estado = None
    if not respuesta.ok:
        return None, cuerpo.get("mensaje", "No se pudo completar la operación. Intenta de nuevo.")
    return cuerpo, None


# ---------------------------------------------------------------------- dibujo
def dibujar_traza(traza):
    nombres = {"percibir": "Percibir", "decidir": "Decidir", "actuar": "Actuar"}
    with st.expander("Cómo lo decidió el asistente"):
        for paso in traza:
            capa = f" · capa {paso['capa']}" if paso.get("capa") else ""
            st.markdown(f"**{nombres.get(paso['paso'], paso['paso'])}**{capa}  \n{paso['detalle']}")


def dibujar_ticket(ticket):
    if ticket.get("estatus") == "failed":
        st.warning("El ticket no se pudo enviar todavía.")
        return
    st.success(f"Ticket {ticket['folio']} levantado")
    with st.expander("Ver el ticket"):
        campos = ticket["campos"]
        for campo, valor in campos.items():
            if campo == "adjuntos":
                valor = ", ".join(f"{a['nombre']} ({a['clase']})" for a in valor)
            texto = str(valor).replace("\n", "  \n")
            st.markdown(f"**{campo.capitalize()}:** {texto}")


def dibujar_mensaje(entrada, mostrar_detalle):
    with st.chat_message(entrada["rol"]):
        if entrada.get("imagen"):
            st.image(entrada["imagen"], width=160)
        for texto in entrada.get("textos", []):
            if texto:
                st.markdown(texto)
        if entrada.get("error"):
            st.error(entrada["error"])
        if entrada.get("ticket"):
            dibujar_ticket(entrada["ticket"])
        if mostrar_detalle and entrada.get("traza"):
            dibujar_traza(entrada["traza"])


def enviar(contenedor, sesion, mostrar_detalle, mensaje, imagen=None, nombre=None, tipo=None,
           repetir=False):
    """Muestra el mensaje del usuario, llama a la API y vuelve a dibujar la página."""
    with contenedor:
        if not repetir:
            usuario = {"rol": "user", "textos": [mensaje],
                       "imagen": miniatura(imagen) if imagen else None}
            st.session_state.historial.append(usuario)
            dibujar_mensaje(usuario, mostrar_detalle)
        with st.spinner("Revisando…"):
            cuerpo, error = llamar_api(sesion, mensaje, imagen, nombre, tipo)
    if error:
        st.session_state.pendiente = (mensaje, imagen, nombre, tipo)
        entrada = {"rol": "assistant", "error": error}
    else:
        st.session_state.pendiente = None
        st.session_state.estado = cuerpo["estado"]
        entrada = {"rol": "assistant", "textos": cuerpo["respuestas"], "ticket": cuerpo["ticket"],
                   "traza": cuerpo["traza"]}
    st.session_state.historial.append(entrada)
    st.rerun()


# ---------------------------------------------------------------------- página
st.set_page_config(page_title="Soporte de almacén", page_icon="🧰", layout="centered")
st.markdown("""
<style>
  .block-container {padding-top: 1.5rem; padding-bottom: 5rem; max-width: 720px;}
  .stButton button {min-height: 2.75rem; width: 100%;}
</style>
""", unsafe_allow_html=True)

iniciar()
sesion_app = sesion_de_la_direccion()
dentro_de_la_app = bool(sesion_app)
mostrar_detalle = not dentro_de_la_app or st.query_params.get("detalle") == "1"
salud = salud_api(API)

st.title("Soporte de almacén")
if dentro_de_la_app and sesion_app.get("aplicacion"):
    st.caption(f"Asistente de {APLICACIONES[sesion_app['aplicacion']]}")
else:
    st.caption("Asistente de Cliente recoge e Inventario físico")

if salud is None:
    st.warning("El asistente no está disponible en este momento. Puedes intentar de nuevo en "
               "unos minutos; si es urgente, llama a soporte.")
elif salud["service_desk"]["modo"] == "simulado" and not dentro_de_la_app:
    st.info("Demostración: los tickets se levantan en un Service Desk simulado (folios SIM-), "
            "no en el real.")

if dentro_de_la_app:
    sesion = sesion_app
else:
    with st.expander("Datos de la sesión (prueba)"):
        st.caption("Dentro de la app de Flutter estos datos llegan solos. Si los dejas "
                   "vacíos, el asistente los pregunta cuando le hagan falta.")
        opcion = st.selectbox("Aplicación", ["Que la pregunte el asistente", *APLICACIONES.values()])
        sucursal = st.text_input("Sucursal", max_chars=80)
        usuario = st.text_input("Usuario", max_chars=80)
    sesion = {"sucursal": sucursal.strip(), "usuario": usuario.strip()}
    sesion["aplicacion"] = next((k for k, v in APLICACIONES.items() if v == opcion), "")
    sesion = {k: v for k, v in sesion.items() if v}

if len(st.session_state.historial) > 1 and st.button("Nueva conversación"):
    nueva_conversacion()
    st.rerun()

# La conversación va al final, pegada a la caja de texto: en la pantalla chica del Zebra el
# último mensaje del asistente queda siempre a la vista.
conversacion = st.container()
with conversacion:
    for entrada in st.session_state.historial:
        dibujar_mensaje(entrada, mostrar_detalle)
    if st.session_state.pendiente and st.button("Reintentar"):
        enviar(conversacion, sesion, mostrar_detalle, *st.session_state.pendiente, repetir=True)


def elegir_ejemplo():
    st.session_state.ejemplo_por_enviar = st.session_state.ejemplo
    st.session_state.ejemplo = None


if not dentro_de_la_app:
    # Capturas de ejemplo para quien prueba la página sin tener una del escáner. Van en una
    # sola línea justo arriba de la caja de texto, para no tener que subir a buscarlas.
    st.pills("¿Sin captura a la mano? Usa una de ejemplo", list(EJEMPLOS), key="ejemplo",
             on_change=elegir_ejemplo)
    elegido = st.session_state.pop("ejemplo_por_enviar", None)
    if elegido:
        archivo = EJEMPLOS[elegido]
        enviar(conversacion, sesion, mostrar_detalle, "",
               (CARPETA / "ejemplos" / archivo).read_bytes(), archivo, "image/jpeg")

entrada = st.chat_input("Escribe tu problema o adjunta una captura", accept_file=True,
                        file_type=FORMATOS, max_chars=1000, max_upload_size=8)
if entrada:
    if isinstance(entrada, str):  # algunas versiones regresan solo el texto
        texto, archivo = entrada.strip(), None
    else:
        archivo = entrada.files[0] if entrada.files else None
        texto = (entrada.text or "").strip()
    if texto or archivo:
        enviar(conversacion, sesion, mostrar_detalle, texto,
               archivo.getvalue() if archivo else None,
               archivo.name if archivo else None,
               archivo.type if archivo else None)
