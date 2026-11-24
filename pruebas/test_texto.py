"""Pruebas del módulo de NLP con el modelo entrenado."""

from pathlib import Path

import pytest

from asistente.conocimiento import INTENCIONES, intenciones_de_app
from asistente.texto import ClasificadorTexto, preparar
from entrenar_texto import PRUEBAS_AVANCE_1

MODELO = Path(__file__).resolve().parent.parent / "modelos" / "clasificador_intenciones.joblib"


@pytest.fixture(scope="module")
def clasificador():
    if not MODELO.exists():
        pytest.skip("Falta el modelo: correr entrenar_texto.py")
    return ClasificadorTexto(MODELO)


def test_preparar_quita_acentos_signos_numeros_y_stopwords_pero_no_el_no():
    raices = preparar("Buenas tardes, ya conté todo y NO puedo cerrar el inventario 1234")
    assert "no" in raices
    assert "1234" not in raices
    assert "el" not in raices and "ya" not in raices
    assert all(r == r.lower() and r.isalpha() for r in raices)
    assert "cerr" in raices


def test_las_clases_del_modelo_son_las_de_la_base_de_conocimiento(clasificador):
    assert set(clasificador.clases) == set(INTENCIONES)


def test_pruebas_del_avance_1_dan_lo_mismo_que_en_el_avance_2(clasificador):
    fallas = [prueba for prueba, mensaje, esperada in PRUEBAS_AVANCE_1
              if clasificador.predecir(mensaje)["intencion"] != esperada]
    # En el Avance 2 fallaron las mismas dos: "se cerró sola" y "la pantalla se puso en blanco".
    assert fallas == ["Extraordinaria 2", "Extraordinaria 3"]


def test_restringir_a_la_app_cambia_la_intencion(clasificador):
    libre = clasificador.predecir("no puedo cerrar el inventario")
    assert libre["intencion"] == "inv_no_cierra"
    restringida = clasificador.predecir("no puedo cerrar el inventario",
                                        intenciones_de_app("cliente_recoge"))
    assert restringida["intencion"] in intenciones_de_app("cliente_recoge")
    assert restringida["intencion_modelo"] == "inv_no_cierra"
