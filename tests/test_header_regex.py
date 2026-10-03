import re

from src.ocr.config import OCR_CONFIG
from src.ocr.header import _match, _section

SAMPLE_TEXT = """Código Único de Factura - CUFE :
eccc372e4bdb8125cf2e896b405ddb91918c7c6d92238892e3b36bd3b6a86929a3c47ed18ae8f559a1bf51b89ab560f1

Número de Factura: FEBQ-243564 Forma de pago: Crédito

Fecha de Emisión: 05/11/2024 Medio de Pago: Acuerdo mutuo

Datos del Emisor / Vendedor

Razón Social: SERVICIOS MEDICOS OLIMPUS IPS SAS
Nit del Emisor: 800033723 País: Colombia

Datos del Adquiriente / Comprador

Nombre o Razón Social: NUEVA EMPRESA PROMOTORA DE SALUD S.A
Tipo de Documento: NIT País: Colombia
Número Documento: 900156264 Departamento: Bogotá
"""


def test_numero_factura_regex():
    assert _match(OCR_CONFIG.numero_factura_regex, SAMPLE_TEXT) == "FEBQ-243564"


def test_fecha_emision_regex():
    assert _match(OCR_CONFIG.fecha_emision_regex, SAMPLE_TEXT) == "05/11/2024"


def test_nit_emisor_regex_scoped_to_emisor_section_not_adquiriente():
    emisor_text = _section(
        SAMPLE_TEXT, OCR_CONFIG.emisor_section_marker, OCR_CONFIG.adquiriente_section_marker
    )
    nit = _match(OCR_CONFIG.nit_emisor_regex, emisor_text)
    assert nit == "800033723"
    # the Adquiriente's "Numero Documento" (900156264) must never be picked up
    assert nit != "900156264"


def test_cufe_regex_matches_96_hex_chars():
    collapsed = re.sub(r"\s", "", SAMPLE_TEXT)
    m = re.search(OCR_CONFIG.cufe_regex, collapsed)
    assert m is not None
    assert m.group(0) == (
        "eccc372e4bdb8125cf2e896b405ddb91918c7c6d92238892e3b36bd3b6a86929a3c47ed18"
        "ae8f559a1bf51b89ab560f1"
    )
