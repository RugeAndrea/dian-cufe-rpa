"""Real wrapped-description cases from the 10-invoice sample's PDF text
layer, used to pin down join_wrapped_lines's word-frequency join rule."""
import pytest

from src.ocr.normalize import join_wrapped_lines

CASES = [
    (
        ["MAMOGRAFIA UNILATERA", "L O PIEZA QUIRURGICA"],
        "MAMOGRAFIA UNILATERAL O PIEZA QUIRURGICA",
    ),
    (
        ["ECOGRAFIA OBSTETRICA", "CON DETALLE ANATOMIC", "O PERINATOLOGIA"],
        "ECOGRAFIA OBSTETRICA CON DETALLE ANATOMICO PERINATOLOGIA",
    ),
    (
        ["ECOGRAFIA DOPPLER DE", "ARTERIAS ILIACAS"],
        "ECOGRAFIA DOPPLER DE ARTERIAS ILIACAS",
    ),
    (
        ["CONSULTA DE CONTROL", "POR ESPECIALISTA EN PE", "RINATOLOGIA"],
        "CONSULTA DE CONTROL POR ESPECIALISTA EN PERINATOLOGIA",
    ),
    (
        ["CPN GLUCOSA EN SUERO", "U OTRO FLUIDO DIFEREN", "TE A ORINA"],
        "CPN GLUCOSA EN SUERO U OTRO FLUIDO DIFERENTE A ORINA",
    ),
    (
        ["TRATAMIENTO MENSUAL", "DEL PACIENTE CON IRC E", "N PREDIALISIS"],
        "TRATAMIENTO MENSUAL DEL PACIENTE CON IRC EN PREDIALISIS",
    ),
    (
        [
            "CPN PRUEBA RAPIDA TRE",
            "PONEMA PARA SIFILIS TR",
            "EPONEMA PALLIDUM ANTI",
            "CUERPOS MANUAL O SEM",
            "IAUTOMATIZADA O AUTO",
            "MATIZADA",
        ],
        "CPN PRUEBA RAPIDA TREPONEMA PARA SIFILIS TREPONEMA PALLIDUM "
        "ANTICUERPOS MANUAL O SEMIAUTOMATIZADA O AUTOMATIZADA",
    ),
    (
        ["Consulta De Primera Vez P", "or Medicina General"],
        "Consulta De Primera Vez Por Medicina General",
    ),
]


@pytest.mark.parametrize("lines,expected", CASES)
def test_join_wrapped_lines(lines, expected):
    assert join_wrapped_lines(lines) == expected


def test_empty_input():
    assert join_wrapped_lines([]) == ""
    assert join_wrapped_lines(["", "  "]) == ""


def test_single_line_passthrough():
    assert join_wrapped_lines(["Descuento por copagos"]) == "Descuento por copagos"
