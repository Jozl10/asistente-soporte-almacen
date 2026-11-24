"""Mide el OCR con las capturas reales de la app (las del Avance 2).

Para cada captura que se etiquetó como de la app:

1. Si el OCR leyó texto útil, su confianza y cuánto tardó.
2. Si el ticket de esa captura tiene intención etiquetada, qué haría el agente si el usuario
   solo mandara la captura: acertar la intención, pedir la descripción o equivocarse.
3. Lo mismo con varios umbrales para lo leído en la captura (UMBRAL_CAPTURA del agente).

Solo guarda e imprime números agregados: ningún texto de las capturas ni de los tickets.
Ojo: el umbral de la captura se escogió con estas mismas capturas, porque son muy pocas para
apartar una parte de prueba.

Uso, desde la carpeta codigo/ (necesita datos-privados/ con las capturas y los tickets):

    venv/bin/python evaluar_ocr.py
"""

import json
import time
from pathlib import Path

import pandas as pd

from asistente.agente import UMBRAL_CAPTURA
from asistente.conocimiento import INTENCIONES
from asistente.ocr import LectorTexto
from asistente.texto import ClasificadorTexto

CARPETA = Path(__file__).resolve().parent
PRIVADOS = CARPETA / "datos-privados"
UMBRALES = (-0.5, -0.25, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5)


def intenciones_por_ticket():
    """id de Service Desk de cada captura → intención etiquetada de su ticket."""
    tickets = pd.concat([pd.read_csv(PRIVADOS / f, dtype=str)[["bitacora_id", "id_manageengine"]]
                         for f in ("tickets_piloto.csv", "tickets_complemento.csv")])
    etiquetas = pd.concat([pd.read_csv(PRIVADOS / f, dtype=str, encoding="utf-8-sig")
                           [["bitacora_id", "intencion"]]
                           for f in ("etiquetas_tickets.csv", "etiquetas_tickets_nuevos.csv")])
    unidos = tickets.drop_duplicates().merge(etiquetas, on="bitacora_id")
    return unidos.drop_duplicates("id_manageengine").set_index("id_manageengine")["intencion"]


def que_haria(puntuacion, predicha, real, umbral):
    if puntuacion < umbral or predicha == "otro" or INTENCIONES[predicha]["atencion"] == "conversacion":
        return "pide_descripcion"
    return "acierta" if predicha == real else "se_equivoca"


def main():
    calibrado = json.loads((CARPETA / "modelos" / "umbral_texto.json").read_text(encoding="utf-8"))
    texto = ClasificadorTexto(CARPETA / "modelos" / "clasificador_intenciones.joblib",
                              calibrado["umbral"])
    lector = LectorTexto(CARPETA / "tessdata")
    if not lector.disponible:
        raise SystemExit("Falta Tesseract con el idioma español.")

    imagenes = pd.read_csv(PRIVADOS / "etiquetas_imagenes.csv", dtype=str)
    imagenes = imagenes[imagenes["clase"].str.startswith("app")]
    intencion_de = intenciones_por_ticket()

    tiempos, confianzas, casos = [], [], []
    for _, fila in imagenes.iterrows():
        contenido = (PRIVADOS / "adjuntos" / fila["archivo"]).read_bytes()
        inicio = time.perf_counter()
        lectura = lector.leer(contenido)
        tiempos.append((time.perf_counter() - inicio) * 1000)
        if not lectura:
            continue
        confianzas.append(lectura["confianza"])
        real = intencion_de.get(fila["id_manageengine"])
        if real and real != "EXCLUIR":
            p = texto.predecir(lectura["texto"])
            predicha = p["intencion"] if p["segura"] else "otro"
            casos.append((p["puntuacion"], predicha, real))

    por_umbral = []
    for umbral in UMBRALES:
        conteo = {"acierta": 0, "pide_descripcion": 0, "se_equivoca": 0}
        for puntuacion, predicha, real in casos:
            conteo[que_haria(puntuacion, predicha, real, umbral)] += 1
        por_umbral.append({"umbral": umbral, **conteo})

    resultado = {
        "capturas_app": int(len(imagenes)),
        "con_texto_util": len(confianzas),
        "confianza_media": round(sum(confianzas) / len(confianzas), 1),
        "ms_promedio": round(sum(tiempos) / len(tiempos)),
        "ms_maximo": round(max(tiempos)),
        "con_ticket_etiquetado": len(casos),
        "umbral_captura": UMBRAL_CAPTURA,
        "solo_con_la_captura": next(m for m in por_umbral if m["umbral"] == UMBRAL_CAPTURA),
        "por_umbral": por_umbral,
    }
    print(f"Capturas de la app: {resultado['capturas_app']}; con texto útil: "
          f"{resultado['con_texto_util']}; confianza media {resultado['confianza_media']}")
    print(f"Tiempo: {resultado['ms_promedio']} ms en promedio, {resultado['ms_maximo']} ms máximo")
    print(f"Solo con la captura ({len(casos)} con ticket etiquetado):")
    for m in por_umbral:
        marca = "  ← umbral del agente" if m["umbral"] == UMBRAL_CAPTURA else ""
        print(f"  {m['umbral']:+.2f}: acierta {m['acierta']:>2}  pide descripción "
              f"{m['pide_descripcion']:>2}  se equivoca {m['se_equivoca']:>2}{marca}")
    (CARPETA / "modelos" / "ocr_evaluacion.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Guardado en modelos/ocr_evaluacion.json")


if __name__ == "__main__":
    main()
