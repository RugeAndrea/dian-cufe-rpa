from src.ocr.normalize import normalize_date, normalize_money, normalize_nit, normalize_quantity


def test_normalize_money_thousands_and_decimals():
    assert normalize_money("$ 75.000,00") == 75000.0


def test_normalize_money_no_decimals():
    assert normalize_money("$75.000") == 75000.0


def test_normalize_money_none_and_empty():
    assert normalize_money(None) is None
    assert normalize_money("") is None
    assert normalize_money("no es dinero") is None


def test_normalize_quantity_simple():
    assert normalize_quantity("1,00") == 1.0
    assert normalize_quantity("12,50") == 12.5


def test_normalize_date_ddmmyyyy_to_iso():
    assert normalize_date("05/11/2024") == "2024-11-05"


def test_normalize_date_invalid_format():
    assert normalize_date("2024-11-05") is None
    assert normalize_date(None) is None


def test_normalize_nit_plain_digits():
    assert normalize_nit("800033723") == "800033723"


def test_normalize_nit_strips_check_digit_and_dots():
    assert normalize_nit("800.033.723-7") == "800033723"


def test_normalize_nit_none():
    assert normalize_nit(None) is None
