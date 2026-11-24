"""Calibra el umbral de confianza del clasificador de intenciones (el "no sé" del chat).

El SVM le da una puntuación a cada intención y gana la más alta. Si la más alta es baja, el
modelo está adivinando. Con el umbral, una intención que no llega a ese valor se trata como
"otro": el agente pide decirlo con otras palabras y, si vuelve a pasar, levanta un ticket
general.

Para no hacer trampa, el umbral se escoge solo con la parte de entrenamiento:

1. Validación cruzada de 5 partes: cada ejemplo de entrenamiento se clasifica con un modelo
   que no lo vio, y se guarda su puntuación.
2. Se prueban umbrales de -1.0 a 0.5 y se queda el que da el mejor F1 macro.
3. Con ese umbral fijo se mide el efecto en la parte de prueba, que el modelo nunca vio,
   y en las frases de las pruebas del Avance 1.

Uso, desde la carpeta codigo/ (necesita datos-privados/):

    venv/bin/python calibrar_umbral.py

Guarda el resultado en modelos/umbral_texto.json. La API lo usa con la variable de entorno
UMBRAL_TEXTO.
"""

import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.svm import LinearSVC

from asistente.texto import preparar
from entrenar_texto import PRUEBAS_AVANCE_1, SEMILLA, cargar_datos

CARPETA = Path(__file__).resolve().parent
UMBRALES = np.round(np.arange(-1.0, 0.501, 0.05), 2)


def aplicar_umbral(clases, puntuaciones, umbral):
    """La intención ganadora, o "otro" si su puntuación no llega al umbral."""
    mejores = puntuaciones.argmax(axis=1)
    predichas = clases[mejores].astype(object)
    if umbral is not None:
        predichas[puntuaciones.max(axis=1) < umbral] = "otro"
    return predichas


def medir(reales, clases, puntuaciones, umbral):
    sin = aplicar_umbral(clases, puntuaciones, None)
    con = aplicar_umbral(clases, puntuaciones, umbral)
    cambiadas = (sin != con)
    return {
        "umbral": umbral,
        "accuracy": round(float(accuracy_score(reales, con)), 3),
        "f1_macro": round(float(f1_score(reales, con, average="macro", zero_division=0)), 3),
        # Mandadas a "otro" por el umbral:
        "mandadas_a_otro": int(cambiadas.sum()),
        # ...que estaban bien (el usuario tendría que repetirlo sin necesidad).
        "rechazos_de_mas": int((cambiadas & (sin == reales)).sum()),
        # ...que estaban mal (el umbral evitó una respuesta equivocada).
        "errores_evitados": int((cambiadas & (sin != reales)).sum()),
    }


def main():
    datos = cargar_datos()
    (x_ent, x_pru, y_ent, y_pru, _, origen_pru) = train_test_split(
        datos["texto"].to_numpy(), datos["intencion"].to_numpy(), datos["origen"].to_numpy(),
        test_size=0.25, stratify=datos["intencion"], random_state=SEMILLA)

    # 1. Puntuaciones fuera de la parte con que se entrenó cada modelo.
    svm = make_pipeline(TfidfVectorizer(tokenizer=preparar, lowercase=False, token_pattern=None),
                        LinearSVC(random_state=SEMILLA))
    validacion = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    puntuaciones_cv = cross_val_predict(svm, x_ent, y_ent, cv=validacion,
                                        method="decision_function")
    clases = np.array(sorted(set(y_ent)))

    # 2. Curva en entrenamiento y mejor umbral (si empatan, el más bajo: menos rechazos).
    curva = [medir(y_ent, clases, puntuaciones_cv, float(u)) for u in UMBRALES]
    base_cv = medir(y_ent, clases, puntuaciones_cv, None)
    mejor = max(curva, key=lambda m: (m["f1_macro"], -m["umbral"]))
    umbral = mejor["umbral"]
    print(f"Validación cruzada sin umbral: F1 macro {base_cv['f1_macro']}, "
          f"accuracy {base_cv['accuracy']}")
    print(f"Mejor umbral: {umbral} → F1 macro {mejor['f1_macro']}, accuracy {mejor['accuracy']}, "
          f"{mejor['mandadas_a_otro']} mandadas a otro ({mejor['errores_evitados']} errores "
          f"evitados, {mejor['rechazos_de_mas']} de más)")

    # 3. Efecto en la parte de prueba con el modelo guardado (entrenado con todo el
    #    entrenamiento), y en las frases del Avance 1.
    modelo = joblib.load(CARPETA / "modelos" / "clasificador_intenciones.joblib")
    assert list(modelo.classes_) == list(clases)
    puntuaciones_pru = modelo.decision_function(x_pru)
    reales = origen_pru == "real"
    prueba = {
        "sin_umbral": medir(y_pru, clases, puntuaciones_pru, None),
        "con_umbral": medir(y_pru, clases, puntuaciones_pru, umbral),
        "reales_sin_umbral": medir(y_pru[reales], clases, puntuaciones_pru[reales], None),
        "reales_con_umbral": medir(y_pru[reales], clases, puntuaciones_pru[reales], umbral),
    }
    for nombre, m in prueba.items():
        print(f"Prueba {nombre:<18} accuracy {m['accuracy']:.3f}  F1 macro {m['f1_macro']:.3f}  "
              f"a otro {m['mandadas_a_otro']} (evitados {m['errores_evitados']}, "
              f"de más {m['rechazos_de_mas']})")

    frases = [m for _, m, _ in PRUEBAS_AVANCE_1]
    puntuaciones_frases = modelo.decision_function(frases)
    sin = aplicar_umbral(clases, puntuaciones_frases, None)
    con = aplicar_umbral(clases, puntuaciones_frases, umbral)
    avance_1 = []
    for (prueba_nombre, _, esperada), p, a, b in zip(PRUEBAS_AVANCE_1, puntuaciones_frases, sin, con):
        avance_1.append({"prueba": prueba_nombre, "esperada": esperada, "sin_umbral": a,
                         "con_umbral": b, "puntuacion": round(float(p.max()), 3)})
        marca = "sí" if b == esperada else "NO"
        print(f"  {prueba_nombre:<17} {esperada:<19} {a:<19} → {b:<19} "
              f"({p.max():+.2f}) {marca}")
    print(f"Pruebas del Avance 1: sin umbral {sum(x['sin_umbral'] == x['esperada'] for x in avance_1)}"
          f" de 14, con umbral {sum(x['con_umbral'] == x['esperada'] for x in avance_1)} de 14")

    resultado = {"umbral": umbral, "validacion_cruzada": {"sin_umbral": base_cv, "curva": curva},
                 "prueba": prueba, "pruebas_avance_1": avance_1}
    (CARPETA / "modelos" / "umbral_texto.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Guardado en modelos/umbral_texto.json")


if __name__ == "__main__":
    main()
