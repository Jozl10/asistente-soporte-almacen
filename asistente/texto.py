"""Módulo de NLP: los cuatro pasos de preparación del texto y el clasificador de intenciones.

Los pasos son los mismos del Avance 2. Viven en este módulo, y no en un notebook, porque el
modelo guardado guarda una referencia a `preparar`: así se puede cargar desde la API sin
tener que volver a definir la función.
"""

from pathlib import Path

import joblib
import nltk
from nltk.corpus import stopwords
from nltk.stem import SnowballStemmer

# NLTK necesita dos paquetes: uno para separar en palabras y la lista de stopwords.
for paquete, ruta in [("punkt_tab", "tokenizers/punkt_tab"), ("stopwords", "corpora/stopwords")]:
    try:
        nltk.data.find(ruta)
    except LookupError:
        nltk.download(paquete, quiet=True)

SIN_ACENTOS = str.maketrans("áéíóúü", "aeiouu")

# Se quitan las stopwords de NLTK menos "no" y "sin", que cambian el sentido del mensaje
# ("no puedo cerrar").
STOPWORDS = {p.translate(SIN_ACENTOS) for p in stopwords.words("spanish")} - {"no", "sin"}

_raiz = SnowballStemmer("spanish")


# 1. Tokenización: separa el mensaje en palabras y signos.
def tokenizar(texto):
    return nltk.word_tokenize(texto, language="spanish")


# 2. Normalización: minúsculas, sin acentos y solo palabras (fuera signos, folios y códigos).
def normalizar(tokens):
    tokens = [t.lower().translate(SIN_ACENTOS) for t in tokens]
    return [t for t in tokens if t.isalpha()]


# 3. Stopwords: quita las palabras que no ayudan a distinguir una intención de otra.
def quitar_stopwords(tokens):
    return [t for t in tokens if t not in STOPWORDS]


# 4. Stemming: deja cada palabra en su raíz.
def raices(tokens):
    return [_raiz.stem(t) for t in tokens]


# Junta los cuatro pasos. Es la función que usa TF-IDF para cada mensaje.
def preparar(texto):
    return raices(quitar_stopwords(normalizar(tokenizar(texto))))


class ClasificadorTexto:
    """Carga el SVM entrenado y regresa la intención de un mensaje con su puntuación."""

    def __init__(self, ruta_modelo, umbral=None):
        self.modelo = joblib.load(Path(ruta_modelo))
        self.clases = [str(c) for c in self.modelo.classes_]
        # Puntuación mínima para confiar en la intención. None = siempre se confía (así
        # estaba el Avance 2); el umbral se calibra con las pruebas.
        self.umbral = umbral

    def predecir(self, texto, permitidas=None):
        """Clasifica el texto.

        `permitidas` es el conjunto de intenciones que tienen sentido en el contexto (por
        ejemplo, las de la app desde la que escribe el usuario). Si se da, gana la mejor
        de esas; la que habría escogido el modelo solo se reporta en `intencion_modelo`.
        """
        puntuaciones = self.modelo.decision_function([texto])[0]
        ranking = sorted(zip(self.clases, puntuaciones), key=lambda par: par[1], reverse=True)
        candidatas = [par for par in ranking if permitidas is None or par[0] in permitidas]
        intencion, puntuacion = candidatas[0] if candidatas else ranking[0]
        puntuacion = float(puntuacion)  # numpy no se puede pasar a JSON
        return {
            "intencion": intencion,
            "puntuacion": round(puntuacion, 3),
            "segura": self.umbral is None or puntuacion >= self.umbral,
            "intencion_modelo": ranking[0][0],
            "alternativas": [{"intencion": c, "puntuacion": round(float(p), 3)}
                             for c, p in ranking[:3]],
        }
