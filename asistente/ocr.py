"""Lectura del texto de la captura (OCR) con Tesseract.

Lee el texto de la alerta que sale en la pantalla del escáner, para que el agente lo use:
sacar la intención cuando el usuario solo manda la imagen, llenar el dato "Mensaje en
pantalla" del ticket sin preguntarlo y agregar lo leído a la descripción.

Tesseract corre en el mismo servidor: las capturas no se mandan a ningún servicio externo.
Si no está instalado, el lector queda apagado y el agente sigue funcionando sin OCR.

Solo se guardan las palabras que Tesseract leyó con buena confianza, y si quedan muy pocas
la lectura no se usa: así no se llena el ticket con texto revuelto.
"""

import io
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError:  # sin pytesseract, el OCR queda apagado
    pytesseract = None

CONFIANZA_MINIMA = 60       # por palabra, de 0 a 100
PALABRAS_MINIMAS = 4        # palabras con letras para que la lectura cuente
MAXIMO_CARACTERES = 300


class LectorTexto:
    def __init__(self, ruta_tessdata=None, idioma="spa"):
        self.idioma = idioma
        self.configuracion = "--psm 3"
        if ruta_tessdata and Path(ruta_tessdata).exists():
            self.configuracion += f' --tessdata-dir "{ruta_tessdata}"'
        self.disponible = self._revisar()

    def _revisar(self):
        if pytesseract is None:
            return False
        try:
            pytesseract.get_tesseract_version()
            return self.idioma in pytesseract.get_languages(config=self.configuracion)
        except Exception:
            return False

    def leer(self, contenido):
        """Regresa {"texto", "palabras", "confianza"} o None si no hay texto útil."""
        if not self.disponible:
            return None
        imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido)))
        imagen = ImageOps.grayscale(imagen)
        if max(imagen.size) < 1600:  # el texto chico se lee mejor al doble de tamaño
            imagen = imagen.resize((imagen.width * 2, imagen.height * 2))
        datos = pytesseract.image_to_data(imagen, lang=self.idioma, config=self.configuracion,
                                          output_type=pytesseract.Output.DICT)
        buenas = [(palabra.strip(), float(confianza))
                  for palabra, confianza in zip(datos["text"], datos["conf"])
                  if palabra.strip() and float(confianza) >= CONFIANZA_MINIMA]
        con_letras = [p for p, _ in buenas if sum(c.isalpha() for c in p) >= 2]
        if len(con_letras) < PALABRAS_MINIMAS:
            return None
        texto = " ".join(p for p, _ in buenas)[:MAXIMO_CARACTERES]
        return {"texto": texto, "palabras": len(con_letras),
                "confianza": round(sum(c for _, c in buenas) / len(buenas), 1)}
