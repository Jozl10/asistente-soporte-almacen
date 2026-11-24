"""Entrena el clasificador de intenciones y lo guarda en modelos/.

Repite el entrenamiento del Avance 2 (clasificador_intenciones.ipynb) con los mismos datos,
la misma separación y la misma semilla, pero toma `preparar` del paquete `asistente`. Así el
modelo guardado se puede cargar desde la API.

Uso, desde la carpeta codigo/:

    venv/bin/python entrenar_texto.py

Necesita los tickets etiquetados en datos-privados/, que no se suben al repositorio.
"""

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

from asistente.texto import preparar

CARPETA = Path(__file__).resolve().parent
SEMILLA = 42

# Frases de las pruebas del Avance 1, para comparar contra las versiones anteriores.
PRUEBAS_AVANCE_1 = [
    ("Conversación 1", "Hola, buenos días", "saludo"),
    ("Conversación 1", "El sábado tenemos inventario, vamos a requerir de su apoyo", "inv_guardia"),
    ("Conversación 2", "no puedo iniciar mi inventario en el ipad", "inv_no_inicia"),
    ("Conversación 3", "no puedo cerrar el inventario, me marca áreas en revisión", "inv_no_cierra"),
    ("Conversación 4", "me aparece un mensaje diciendo que el inventario está siendo ocupado por un usuario", "inv_area_ocupada"),
    ("Conversación 5", "la etiqueta del artículo no se lee", "codigo_no_se_lee"),
    ("Conversación 6", "el cliente ya firmó y no me deja terminar el ticket, sale un error", "cr_firma_no_cierra"),
    ("Conversación 7", "el ticket se quedó pasmado y no avanza", "cr_ticket_atorado"),
    ("Conversación 8", "le doy salida al material pero no genera la remisión", "cr_remision"),
    ("Conversación 9", "no puedo escanear el código del artículo", "codigo_no_se_lee"),
    ("Conversación 10", "me dice que no tengo la versión más actual de la app", "actualizar_version"),
    ("Extraordinaria 1", "tenemos inventario la próxima semana y necesitamos apoyo", "inv_guardia"),
    ("Extraordinaria 2", "se cerró sola", "otro"),
    ("Extraordinaria 3", "la pantalla se puso en blanco", "otro"),
]


def cargar_datos():
    privados = CARPETA / "datos-privados"
    reales = pd.concat([pd.read_csv(privados / "etiquetas_tickets.csv"),
                        pd.read_csv(privados / "etiquetas_tickets_nuevos.csv")], ignore_index=True)
    reales = reales[reales["intencion"] != "EXCLUIR"]
    generados = pd.read_csv(CARPETA / "datos" / "ejemplos_generados.csv")
    return pd.concat([
        pd.DataFrame({"texto": reales["asunto"] + ". " + reales["texto"],
                      "intencion": reales["intencion"], "origen": "real"}),
        pd.DataFrame({"texto": generados["texto"],
                      "intencion": generados["intencion"], "origen": "generado"}),
    ], ignore_index=True)


def main():
    datos = cargar_datos()
    print(f"Ejemplos: {len(datos)} ({(datos['origen'] == 'real').sum()} reales, "
          f"{(datos['origen'] == 'generado').sum()} generados)")

    (x_ent, x_pru, y_ent, y_pru, _, origen_pru) = train_test_split(
        datos["texto"].to_numpy(), datos["intencion"].to_numpy(), datos["origen"].to_numpy(),
        test_size=0.25, stratify=datos["intencion"], random_state=SEMILLA)
    print(f"Entrenamiento: {len(x_ent)}  Prueba: {len(x_pru)}")

    def tfidf():
        return TfidfVectorizer(tokenizer=preparar, lowercase=False, token_pattern=None)

    modelos = {
        "Naive Bayes": make_pipeline(tfidf(), MultinomialNB()),
        "SVM": make_pipeline(tfidf(), LinearSVC(random_state=SEMILLA)),
    }
    validacion = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    f1 = {}
    for nombre, modelo in modelos.items():
        f1[nombre] = cross_val_score(modelo, x_ent, y_ent, cv=validacion, scoring="f1_macro")
        print(f"{nombre:<12} F1 macro: {f1[nombre].mean():.3f} (± {f1[nombre].std():.3f})")
    mejor = max(f1, key=lambda nombre: f1[nombre].mean())
    print("Se queda:", mejor)

    modelo = modelos[mejor].fit(x_ent, y_ent)
    predichas = modelo.predict(x_pru)
    reales = origen_pru == "real"
    exactitud = accuracy_score(y_pru, predichas)
    exactitud_reales = accuracy_score(y_pru[reales], predichas[reales])
    print(f"Accuracy prueba: {exactitud:.3f}   solo reales: {exactitud_reales:.3f}")
    print(classification_report(y_pru, predichas, digits=3, zero_division=0))

    aciertos = modelo.predict([m for _, m, _ in PRUEBAS_AVANCE_1])
    total = sum(p == e for (_, _, e), p in zip(PRUEBAS_AVANCE_1, aciertos))
    print(f"Pruebas del Avance 1: {total} de {len(PRUEBAS_AVANCE_1)}")

    carpeta_modelos = CARPETA / "modelos"
    carpeta_modelos.mkdir(exist_ok=True)
    joblib.dump(modelo, carpeta_modelos / "clasificador_intenciones.joblib")
    resumen = {
        "ejemplos": int(len(datos)),
        "f1_validacion_cruzada": {n: round(float(v.mean()), 3) for n, v in f1.items()},
        "modelo": mejor,
        "accuracy_prueba": round(float(exactitud), 3),
        "accuracy_reales": round(float(exactitud_reales), 3),
        "pruebas_avance_1": f"{total}/{len(PRUEBAS_AVANCE_1)}",
        "por_intencion": classification_report(y_pru, predichas, output_dict=True,
                                               zero_division=0),
    }
    (carpeta_modelos / "metricas_texto.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print("Guardado en modelos/clasificador_intenciones.joblib y modelos/metricas_texto.json")


if __name__ == "__main__":
    main()
