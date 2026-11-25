"""Pruebas funcionales de la aplicación publicada, de punta a punta.

Abre la pantalla pública de Streamlit en Chrome, como un usuario, y recorre cada escenario:
manda los mensajes, contesta lo que pregunte el asistente y revisa que la respuesta sea la
esperada. Toma una captura en cada paso y mide cuánto tarda en contestar.

Además prueba la API directo (sin la pantalla) con entradas inválidas, y al final guarda las
métricas de /metricas.

No va con las pruebas de pytest porque necesita la app publicada, Chrome y Playwright:

    pip install playwright        # usa el Chrome instalado en la máquina
    CLAVE_ADMIN=... python pruebas_funcionales.py --api https://<túnel> \
        --salida ../reporte/evidencias [--ssh ubuntu@<ip-de-la-ec2>] [--solo F01,F02]

- CLAVE_ADMIN (variable de entorno) se necesita para simular la caída de Service Desk.
- --ssh se necesita para apagar y prender la API en la prueba de "API caída".
Sin alguno de los dos, esa prueba se salta y se dice.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.sync_api import TimeoutError as Agotado
from playwright.sync_api import sync_playwright

CARPETA = Path(__file__).resolve().parent
sys.path.insert(0, str(CARPETA))
from asistente.conocimiento import PREGUNTAS  # noqa: E402  (solo textos, sin modelos)

APP = "https://asistente-soporte-almacen.streamlit.app"
EJEMPLOS = CARPETA / "interfaz" / "ejemplos"
ESCRITORIO = {"viewport": {"width": 1280, "height": 900}}
ZEBRA = {"viewport": {"width": 360, "height": 640}, "device_scale_factor": 2,
         "is_mobile": True, "has_touch": True}
SESION_ZEBRA = {"embed": "true", "aplicacion": "inventario",
                "sucursal": "Sucursal de prueba", "usuario": "almacenista.prueba"}

# Lo que contesta el "usuario" a cada pregunta del asistente.
RESPUESTAS = {
    "numero_ticket": "1234",
    "mensaje_error": "dice que no hay un inventario activo",
    "etapa": "en surtir",
    "factura": "F-12345",
    "fecha": "el sábado 29 de noviembre",
    "hora": "a las 7 de la mañana",
    "contacto": "el jefe de almacén",
    "numero_inventario": "35",
    "area": "3",
    "codigo": "ABC-123",
    "ubicacion": "A-03",
    "version": "3.3",
    "captura": "no",
    "aplicacion": "Inventario físico",
    "sucursal": "Sucursal de prueba",
    "usuario": "almacenista.prueba",
}


# ---------------------------------------------------------------------- la pantalla
class Pantalla:
    """Una conversación en la app publicada, con su propia sesión de navegador."""

    def __init__(self, navegador, carpeta, nombre, perfil=ESCRITORIO, parametros=None):
        self.contexto = navegador.new_context(**perfil, locale="es-MX")
        self.pagina = self.contexto.new_page()
        self.carpeta, self.nombre, self.capturas, self.tiempos = carpeta, nombre, 0, []
        url = APP + "/~/+/" + (("?" + urlencode(parametros)) if parametros else "")
        self.pagina.goto(url, wait_until="domcontentloaded", timeout=90000)
        try:  # Streamlit Cloud duerme las apps que nadie abre.
            self.pagina.get_by_role("button", name="get this app back up", exact=False).click(timeout=4000)
            print("   (la app estaba dormida; se despertó)")
        except Agotado:
            pass
        self.pagina.get_by_text("Hola, soy el asistente automático", exact=False).wait_for(timeout=180000)
        self.pagina.wait_for_timeout(1500)

    def mensajes(self):
        return self.pagina.get_by_test_id("stChatMessage")

    def ultimo(self):
        return self.mensajes().last.inner_text().strip()

    def esperar_respuesta(self, antes, limite=90):
        """Espera el mensaje del usuario y la respuesta, y que se quite el 'Revisando…'.

        El tiempo se toma en cuanto la respuesta aparece en pantalla (lo que espera el
        usuario), revisando cada 50 ms; después se dan 600 ms para que la página termine de
        dibujarse antes de seguir, y eso ya no cuenta.
        """
        inicio = time.perf_counter()
        while time.perf_counter() - inicio < limite:
            if (self.mensajes().count() >= antes + 2
                    and not self.pagina.get_by_test_id("stSpinner").is_visible()):
                ms = round((time.perf_counter() - inicio) * 1000)
                self.pagina.wait_for_timeout(600)
                if self.mensajes().count() >= antes + 2:
                    self.tiempos.append(ms)
                    return ms
            self.pagina.wait_for_timeout(50)
        raise TimeoutError("El asistente no contestó a tiempo.")

    def escribir(self, texto, archivo=None):
        antes = self.mensajes().count()
        if archivo:
            self.pagina.locator('[data-testid="stChatInput"] input[type="file"]').set_input_files(str(archivo))
            self.pagina.wait_for_timeout(800)
        caja = self.pagina.get_by_test_id("stChatInputTextArea")
        caja.fill(texto)
        self.pagina.get_by_test_id("stChatInputSubmitButton").click()
        return self.esperar_respuesta(antes)

    def ejemplo(self, nombre):
        antes = self.mensajes().count()
        self.pagina.get_by_text(nombre, exact=True).click()
        return self.esperar_respuesta(antes)

    def boton(self, nombre):
        antes = self.mensajes().count()
        self.pagina.get_by_role("button", name=nombre).click()
        return self.esperar_respuesta(antes - 1)  # "Reintentar" no agrega mensaje del usuario

    def abrir(self, texto):
        """Abre el último desplegable con ese título (el del ticket o el de la traza)."""
        self.pagina.get_by_text(texto, exact=True).last.click()
        self.pagina.wait_for_timeout(700)

    def foto(self, etiqueta, completa=False):
        self.capturas += 1
        ruta = self.carpeta / f"{self.nombre}_{self.capturas:02d}_{etiqueta}.png"
        if not completa:
            self.mensajes().last.scroll_into_view_if_needed()
        self.pagina.screenshot(path=str(ruta), full_page=completa)
        return ruta.name

    def contestar_hasta_el_ticket(self, respuestas=RESPUESTAS, maximo=10):
        """Contesta cada pregunta del plan hasta que el asistente levante el ticket."""
        preguntas = {clave: texto for clave, (_, texto, _) in PREGUNTAS.items()}
        for _ in range(maximo):
            ultimo = self.ultimo()
            if "levanté el ticket" in ultimo or "No pude enviar el ticket" in ultimo:
                return ultimo
            clave = next((c for c, t in preguntas.items() if t in ultimo), None)
            if clave is None:
                raise AssertionError(f"Pregunta que no se esperaba: {ultimo[-160:]}")
            self.escribir(respuestas[clave])
        raise AssertionError("El asistente no terminó de pedir datos.")

    def cerrar(self):
        self.contexto.close()


# ---------------------------------------------------------------------- escenarios
def verificar(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)


def f01_solucion_directa(p):
    """Problema con solución en la documentación: contesta directo y cierra."""
    p.escribir("no puedo cerrar el inventario, me dice que hay ubicaciones pendientes")
    verificar("ubicaciones pendientes" in p.ultimo() and "¿Con eso se resolvió?" in p.ultimo(),
              "no dio la solución de la documentación")
    p.foto("solucion")
    p.escribir("sí, ya quedó")
    verificar("Qué bueno" in p.ultimo(), "no cerró la consulta")
    p.abrir("Cómo lo decidió el asistente")
    p.foto("resuelto_con_traza")
    return "Dio la solución de la documentación y cerró cuando el usuario confirmó."


def f02_solucion_no_funciona(p):
    """La solución no funcionó: el plan pide los datos uno por uno y levanta el ticket."""
    p.escribir("no me deja iniciar el inventario")
    verificar("inventario activo" in p.ultimo(), "no dio la solución de iniciar")
    p.escribir("no, sigue igual")
    p.foto("pide_datos")
    final = p.contestar_hasta_el_ticket()
    verificar("levanté el ticket SIM-" in final, "no levantó el ticket")
    p.abrir("Ver el ticket")
    p.foto("ticket", completa=True)
    return "Pidió los datos que faltaban, uno por uno, y levantó el ticket simulado."


def f03_solicitud_guardia(p):
    """Solicitud: no hay nada que resolver, va directo al plan del ticket."""
    p.escribir("necesito guardia de soporte para el inventario del sábado")
    verificar("guardia" in p.ultimo().lower(), "no reconoció la guardia")
    final = p.contestar_hasta_el_ticket()
    verificar("levanté el ticket SIM-" in final, "no levantó la solicitud")
    p.foto("solicitud_levantada")
    return "Reconoció la solicitud y levantó el ticket con fecha, hora y contacto."


def f04_solo_captura(p):
    """Solo la captura de la app: el OCR lee la alerta y de ahí sale la intención."""
    p.ejemplo("Captura de la app")
    ultimo = p.ultimo()
    verificar("En ella leí" in ultimo and "versión" in ultimo, "no usó el texto de la captura")
    p.abrir("Cómo lo decidió el asistente")
    p.foto("ocr_y_traza")
    final = p.contestar_hasta_el_ticket()
    verificar("levanté el ticket SIM-" in final, "no levantó el ticket")
    p.abrir("Ver el ticket")
    p.foto("ticket_con_adjunto", completa=True)
    verificar("captura de la app" in p.pagina.inner_text("body"), "el ticket no trae el adjunto")
    return "Clasificó la captura como de la app, leyó la alerta y levantó el ticket con el adjunto."


def f05_captura_escritorio(p):
    """Captura del sistema de escritorio sin mensaje: avisa y pide la descripción."""
    p.ejemplo("Sistema de escritorio")
    ultimo = p.ultimo()
    verificar("sistema de escritorio" in ultimo and "Cuéntame en una frase" in ultimo,
              "no interpretó la captura del escritorio")
    p.foto("escritorio")
    p.escribir("el área está ocupada por otro usuario")
    verificar("Áreas pendientes" in p.ultimo(), "no dio la solución del área ocupada")
    p.foto("solucion_area")
    return "Reconoció la captura de escritorio, pidió la descripción y dio la solución."


def f06_foto_etiqueta(p):
    """Foto del almacén junto con el mensaje: la interpreta según la intención."""
    p.escribir("no se puede escanear el código del artículo", archivo=EJEMPLOS / "ejemplo_otra.jpg")
    ultimo = p.ultimo()
    verificar("a mano" in ultimo and "foto" in ultimo.lower(), "no relacionó la foto con la etiqueta")
    p.foto("foto_etiqueta")
    return "Dio la solución del código ilegible e interpretó la imagen como foto de la etiqueta."


def f07_no_se(p):
    """Algo fuera de lo que atiende: dice que no entendió y, a la segunda, ticket general."""
    p.escribir("quiero pedir mis vacaciones de diciembre")
    verificar("No te entendí" in p.ultimo(), "no dijo que no entendió")
    p.foto("no_entendi")
    p.escribir("son unos días que me deben")
    verificar("Sigo sin entenderte" in p.ultimo(), "no pasó al ticket general")
    final = p.contestar_hasta_el_ticket()
    verificar("levanté el ticket SIM-" in final, "no levantó el ticket general")
    p.foto("ticket_general")
    return "Pidió decirlo con otras palabras y a la segunda levantó un ticket general."


def f08_conversacion(p):
    """Saludo y despedida: se contestan directo, sin ticket."""
    p.escribir("hola, buenos días")
    verificar("soy el asistente de soporte" in p.ultimo(), "no saludó")
    p.escribir("gracias, hasta luego")
    verificar("Hasta luego" in p.ultimo(), "no se despidió")
    p.foto("saludo_despedida")
    return "Saludó y se despidió sin levantar ticket."


def f09_cancelar(p):
    """El usuario se arrepiente a media lista de preguntas."""
    p.escribir("el ticket de cliente recoge no avanza, se quedó atorado")
    verificar("número de ticket" in p.ultimo(), "no empezó a pedir datos")
    p.escribir("mejor cancelar")
    verificar("cancelé el reporte" in p.ultimo(), "no canceló")
    p.foto("cancelado")
    return "Canceló el reporte a media lista de preguntas."


def f10_imagen_danada(p, carpeta):
    """Un archivo con extensión de imagen que no es imagen: lo dice y sigue la conversación."""
    falsa = carpeta / "captura_danada.png"
    falsa.write_bytes(b"esto no es una imagen")
    p.escribir("no puedo cerrar el inventario", archivo=falsa)
    ultimo = p.ultimo()
    verificar("No pude usar la imagen" in ultimo and "ubicaciones pendientes" in ultimo,
              "no manejó la imagen dañada")
    p.foto("imagen_danada")
    falsa.unlink()
    return "Avisó que no pudo usar la imagen y aun así contestó el mensaje."


def f11_service_desk_caido(p, api, clave):
    """Service Desk no responde: avisa, guarda el ticket y lo reintenta cuando el usuario dice."""
    def disponible(valor):
        r = requests.post(f"{api}/service-desk/disponibilidad", json={"disponible": valor},
                          headers={"X-Clave-Admin": clave}, timeout=15)
        r.raise_for_status()
    disponible(False)
    try:
        p.escribir("necesito guardia de soporte para el inventario del sábado")
        final = p.contestar_hasta_el_ticket()
        verificar("No pude enviar el ticket" in final, "no avisó la falla de Service Desk")
        p.foto("service_desk_caido")
    finally:
        disponible(True)
    p.escribir("sí, inténtalo otra vez")
    verificar("levanté el ticket SIM-" in p.ultimo(), "no reintentó el ticket")
    p.foto("reintento_ok")
    return "Avisó que Service Desk no respondía, guardó el ticket y lo levantó al reintentar."


def f12_api_caida(p, api, ssh):
    """La API se cae: la pantalla lo dice con palabras claras y ofrece reintentar."""
    def servicio(accion):
        subprocess.run(["ssh", "-i", os.path.expanduser("~/.ssh/asistente-soporte.pem"), ssh,
                        f"sudo systemctl {accion} asistente-api"], check=True, timeout=60)
    servicio("stop")
    try:
        antes = p.mensajes().count()
        p.pagina.get_by_test_id("stChatInputTextArea").fill("no puedo cerrar el inventario")
        p.pagina.get_by_test_id("stChatInputSubmitButton").click()
        p.esperar_respuesta(antes)
        error = p.ultimo()
        p.foto("api_caida")
        verificar("no está disponible" in error, f"el mensaje no es claro: {error}")
        verificar(p.pagina.get_by_role("button", name="Reintentar").is_visible(),
                  "no ofreció reintentar")
    finally:
        servicio("start")
    for _ in range(60):  # TensorFlow tarda en cargar.
        try:
            if requests.get(f"{api}/salud", timeout=5).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    p.boton("Reintentar")
    verificar("ubicaciones pendientes" in p.ultimo(), "el reintento no funcionó")
    p.foto("reintento_api")
    return f"Mostró «{error.splitlines()[-1]}» y el reintento funcionó al volver la API."


def f13_zebra_webview(p):
    """Dentro de la app de Flutter, en la pantalla del Zebra: no pregunta los datos de sesión."""
    cuerpo = p.pagina.inner_text("body")
    verificar("Datos de la sesión" not in cuerpo and "Sin captura a la mano" not in cuerpo,
              "en modo WebView no debe salir lo de la página de demostración")
    verificar("Asistente de Inventario físico" in cuerpo, "no tomó la app de la dirección")
    p.foto("inicio")
    p.escribir("no puedo cerrar el inventario")
    p.foto("solucion")
    p.escribir("no, sigue igual")
    final = p.contestar_hasta_el_ticket()
    verificar("levanté el ticket SIM-" in final, "no levantó el ticket")
    p.foto("ticket")
    preguntado = " ".join(p.mensajes().all_inner_texts())
    verificar(PREGUNTAS["sucursal"][1] not in preguntado and PREGUNTAS["usuario"][1] not in preguntado,
              "preguntó datos que ya venían en la dirección")
    return "En 360×640 y con los datos en la dirección, levantó el ticket sin preguntar sucursal ni usuario."


ESCENARIOS = [
    ("F01", "Solución de la documentación", f01_solucion_directa, {}),
    ("F02", "La solución no funciona: ticket", f02_solucion_no_funciona, {}),
    ("F03", "Solicitud de guardia", f03_solicitud_guardia, {}),
    ("F04", "Solo la captura de la app (OCR)", f04_solo_captura, {}),
    ("F05", "Captura del sistema de escritorio", f05_captura_escritorio, {}),
    ("F06", "Foto de la etiqueta con mensaje", f06_foto_etiqueta, {}),
    ("F07", "Fuera de lo que atiende (no sé)", f07_no_se, {}),
    ("F08", "Saludo y despedida", f08_conversacion, {}),
    ("F09", "Cancelar a media conversación", f09_cancelar, {}),
    ("F10", "Imagen dañada", f10_imagen_danada, {"carpeta": True}),
    ("F11", "Service Desk no responde", f11_service_desk_caido, {"api": True, "clave": True}),
    ("F12", "API caída", f12_api_caida, {"api": True, "ssh": True}),
    ("F13", "WebView en la pantalla del Zebra", f13_zebra_webview, {"zebra": True}),
]


# ---------------------------------------------------------------------- API directo
def pruebas_api(api, clave):
    """Entradas inválidas directo a la API: cada una debe dar su código y un JSON claro."""
    casos = [
        ("Ruta que no existe", "GET", "/no-existe", {}, 404),
        ("Método equivocado", "GET", "/conversacion", {}, 405),
        ("Mensaje vacío", "POST", "/conversacion", {"json": {"mensaje": ""}}, 400),
        ("Mensaje de más de 1000 caracteres", "POST", "/conversacion",
         {"json": {"mensaje": "a" * 1001}}, 400),
        ("Estado de conversación inválido", "POST", "/conversacion",
         {"json": {"mensaje": "hola", "estado": {"fase": "inventada"}}}, 400),
        ("Archivo que no es imagen", "POST", "/imagen",
         {"files": {"imagen": ("nota.txt", b"hola", "text/plain")}}, 415),
        ("Imagen de más de 8 MB", "POST", "/imagen",
         {"files": {"imagen": ("grande.png", b"0" * (9 * 1024 * 1024), "image/png")}}, 413),
        ("Ticket sin campos obligatorios", "POST", "/service-desk/tickets",
         {"json": {"asunto": "solo asunto"}}, 422),
        ("Lista de tickets sin la clave", "GET", "/service-desk/tickets", {}, 403),
        ("Lista de tickets con la clave", "GET", "/service-desk/tickets",
         {"headers": {"X-Clave-Admin": clave}} if clave else None, 200),
    ]
    resultados = []
    for nombre, metodo, ruta, opciones, esperado in casos:
        if opciones is None:
            resultados.append({"prueba": nombre, "esperado": esperado, "resultado": "saltada"})
            continue
        r = requests.request(metodo, api + ruta, timeout=60, **opciones)
        try:
            cuerpo = r.json()
            mensaje = cuerpo.get("mensaje") if isinstance(cuerpo, dict) else f"{len(cuerpo)} tickets"
        except ValueError:
            mensaje = "(no regresó JSON)"
        ok = r.status_code == esperado and mensaje != "(no regresó JSON)"
        resultados.append({"prueba": nombre, "esperado": esperado, "obtenido": r.status_code,
                           "mensaje": mensaje, "resultado": "pasa" if ok else "falla"})
        print(f"   {'✓' if ok else '✗'} {nombre}: {r.status_code} {mensaje}")
    return resultados


# ---------------------------------------------------------------------- principal
def main():
    argumentos = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    argumentos.add_argument("--api", required=True, help="dirección pública de la API (el túnel)")
    argumentos.add_argument("--salida", required=True, type=Path, help="carpeta para capturas y resultados")
    argumentos.add_argument("--ssh", help="ubuntu@<ip> de la EC2, para la prueba de API caída")
    argumentos.add_argument("--solo", help="códigos de escenarios separados por coma, p. ej. F01,F04")
    a = argumentos.parse_args()
    api, clave = a.api.rstrip("/"), os.environ.get("CLAVE_ADMIN", "")
    a.salida.mkdir(parents=True, exist_ok=True)
    elegidos = set(a.solo.split(",")) if a.solo else None

    resultados = []
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(channel="chrome", headless=True)
        for codigo, titulo, funcion, necesita in ESCENARIOS:
            if elegidos and codigo not in elegidos:
                continue
            faltan = [n for n, falta in (("CLAVE_ADMIN", necesita.get("clave") and not clave),
                                         ("--ssh", necesita.get("ssh") and not a.ssh)) if falta]
            if faltan:
                print(f"{codigo} {titulo}: saltada (falta {', '.join(faltan)})")
                resultados.append({"codigo": codigo, "escenario": titulo, "resultado": "saltada"})
                continue
            print(f"{codigo} {titulo}")
            zebra = necesita.get("zebra")
            p = Pantalla(navegador, a.salida, codigo, ZEBRA if zebra else ESCRITORIO,
                         SESION_ZEBRA if zebra else None)
            extra = {k: v for k, v in (("carpeta", a.salida), ("api", api), ("clave", clave),
                                       ("ssh", a.ssh)) if necesita.get(k)}
            fila = {"codigo": codigo, "escenario": titulo, "pantalla": "Zebra 360×640" if zebra
                    else "escritorio 1280×900"}
            try:
                fila["observado"] = funcion(p, **extra)
                fila["resultado"] = "pasa"
            except Exception as error:  # una prueba que falla no detiene las demás
                fila["resultado"] = "falla"
                fila["observado"] = f"{type(error).__name__}: {error}"
                p.foto("falla")
            fila["mensajes"] = len(p.tiempos)
            fila["ms_respuesta"] = p.tiempos
            fila["capturas"] = p.capturas
            print(f"   {'✓' if fila['resultado'] == 'pasa' else '✗'} {fila['observado']}")
            print(f"   tiempos de respuesta (ms): {p.tiempos}")
            resultados.append(fila)
            p.cerrar()
        navegador.close()

    print("API directo")
    api_directo = pruebas_api(api, clave)
    metricas = requests.get(f"{api}/metricas", timeout=30).json()

    tiempos = sorted(t for f in resultados for t in f.get("ms_respuesta", []))
    resumen = {
        "escenarios": len([f for f in resultados if f["resultado"] != "saltada"]),
        "pasan": len([f for f in resultados if f["resultado"] == "pasa"]),
        "api_directo_pasan": len([c for c in api_directo if c["resultado"] == "pasa"]),
        "api_directo": len(api_directo),
        "respuestas_medidas": len(tiempos),
        "ms_mediana": tiempos[len(tiempos) // 2] if tiempos else None,
        "ms_p90": tiempos[int(len(tiempos) * 0.9)] if tiempos else None,
        "ms_maximo": tiempos[-1] if tiempos else None,
    }
    salida = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "app": APP, "resumen": resumen,
              "escenarios": resultados, "api_directo": api_directo, "metricas_api": metricas}
    (a.salida / "pruebas_funcionales.json").write_text(
        json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False))


if __name__ == "__main__":
    main()
