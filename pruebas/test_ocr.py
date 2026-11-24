"""Pruebas del OCR con Tesseract de verdad (se saltan si no está instalado)."""

from pathlib import Path

import pytest

from asistente.ocr import LectorTexto

CODIGO = Path(__file__).resolve().parent.parent
EJEMPLOS = CODIGO / "interfaz" / "ejemplos"


@pytest.fixture(scope="module")
def lector():
    lector = LectorTexto(CODIGO / "tessdata")
    if not lector.disponible:
        pytest.skip("Tesseract con español no está instalado")
    return lector


def test_lee_la_alerta_de_la_captura_de_la_app(lector):
    lectura = lector.leer((EJEMPLOS / "ejemplo_app.jpg").read_bytes())
    assert "No cuenta con la versión más actual de la aplicación" in lectura["texto"]
    assert lectura["confianza"] > 80


def test_en_una_foto_sin_texto_no_regresa_nada(lector):
    assert lector.leer((EJEMPLOS / "ejemplo_otra.jpg").read_bytes()) is None


def test_sin_tesseract_queda_apagado(monkeypatch):
    import asistente.ocr as ocr
    monkeypatch.setattr(ocr, "pytesseract", None)
    lector = ocr.LectorTexto()
    assert not lector.disponible
    assert lector.leer(b"lo que sea") is None
