"""Módulo de visión: clasifica la captura que adjunta el usuario con EfficientNetB0.

El modelo es el del Avance 2, con tres clases: app, escritorio y otra. La preparación de la
imagen es la misma del entrenamiento: se endereza según el EXIF, se pasa a RGB y se ajusta a
224 × 224 rellenando con negro para no deformarla.
"""

import io
from pathlib import Path

import keras
import numpy as np
import tensorflow as tf
from PIL import Image, ImageOps, UnidentifiedImageError

TAMANO = 224

# Seguridad mínima para confiar en la clase. Abajo de esto la imagen va "sin clasificar".
UMBRAL = 0.6

FORMATOS = {"PNG", "JPEG", "WEBP"}


class ImagenInvalida(ValueError):
    """El archivo no se puede usar como imagen. `codigo` es el código HTTP que le toca."""

    def __init__(self, mensaje, codigo=422):
        super().__init__(mensaje)
        self.codigo = codigo


def preparar_imagen(contenido):
    """Convierte los bytes del archivo en el arreglo de 224 × 224 × 3 que espera el modelo."""
    if not contenido:
        raise ImagenInvalida("El archivo de la imagen viene vacío.")
    try:
        imagen = Image.open(io.BytesIO(contenido))
        formato = imagen.format
        imagen.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ImagenInvalida("El archivo no es una imagen que se pueda leer.", 415) from error
    if formato not in FORMATOS:
        raise ImagenInvalida(f"El formato {formato} no se acepta. Usa una imagen PNG o JPG.", 415)
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    return tf.image.resize_with_pad(np.asarray(imagen), TAMANO, TAMANO).numpy()


class ClasificadorImagen:
    """Carga la red entrenada y regresa la clase de una imagen con su seguridad."""

    def __init__(self, ruta_modelo, ruta_clases):
        self.modelo = keras.models.load_model(Path(ruta_modelo))
        texto = Path(ruta_clases).read_text(encoding="utf-8")
        self.clases = [c for c in texto.splitlines() if c]

    def predecir(self, contenido):
        lote = preparar_imagen(contenido)[np.newaxis]
        probabilidades = self.modelo(lote, training=False).numpy()[0]
        mejor = int(probabilidades.argmax())
        seguridad = float(probabilidades[mejor])
        return {
            "clase": self.clases[mejor],
            "seguridad": round(seguridad, 3),
            "clasificada": seguridad >= UMBRAL,
            "probabilidades": {c: round(float(p), 3) for c, p in zip(self.clases, probabilidades)},
        }
