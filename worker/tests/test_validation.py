from datetime import date
from decimal import Decimal

from pulseops_worker.validation import validate_csv

TODAY = date(2026, 11, 10)
HEADER = "transaction_id,transaction_date,product_code,quantity,unit_price,country\n"


def run(body: str, header: str = HEADER):
    return validate_csv((header + body).encode("utf-8"), today=TODAY)


def codes(result):
    return [e.error_code for e in result.errors]


def test_valid_file_produces_rows_and_exact_totals():
    r = run("T1,2026-10-01,P-100,3,19.99,gb\nT2,2026-10-02,P-200,1,0.10,IN\n")
    assert r.file_error is None
    assert r.total_rows == 2 and r.invalid_rows == 0 and r.errors == []
    assert r.valid_rows[0].country == "GB"  # normalised to upper case
    assert r.valid_rows[0].line_total == Decimal("59.97")  # exact decimal, no float drift
    assert sum(v.line_total for v in r.valid_rows) == Decimal("60.07")


def test_missing_required_column_fails_whole_file():
    r = run(
        "T1,2026-10-01,P-100,3,GB\n",
        header="transaction_id,transaction_date,product_code,quantity,country\n",
    )
    assert r.file_error is not None
    assert r.file_error.error_code == "SCHEMA_MISSING_COLUMNS"
    assert "unit_price" in r.file_error.message
    assert r.valid_rows == []


def test_header_only_file_is_empty():
    r = run("")
    assert r.file_error.error_code == "FILE_EMPTY"


def test_non_utf8_file_is_rejected():
    r = validate_csv(b"\xff\xfe\x00bad", today=TODAY)
    assert r.file_error.error_code == "ENCODING_ERROR"


def test_invalid_values_are_reported_per_column_with_line_numbers():
    r = run(
        "T1,2026-13-01,P-100,0,12.345,GBR\n"  # bad date, qty, price, country
        "T2,2026-10-01,P-100,-2,5.00,GB\n"  # negative quantity
        "T3,2026-10-01,P-100,2,abc,GB\n"  # non-numeric price
    )
    assert r.total_rows == 3 and r.invalid_rows == 3 and r.valid_rows == []
    line2 = {e.column_name: e.error_code for e in r.errors if e.line_number == 2}
    assert line2 == {
        "transaction_date": "INVALID_DATE",
        "quantity": "INVALID_QUANTITY",
        "unit_price": "INVALID_PRICE",
        "country": "INVALID_COUNTRY",
    }
    assert ("quantity", "INVALID_QUANTITY") in {
        (e.column_name, e.error_code) for e in r.errors if e.line_number == 3
    }
    assert ("unit_price", "INVALID_PRICE") in {
        (e.column_name, e.error_code) for e in r.errors if e.line_number == 4
    }


def test_missing_values_are_flagged():
    r = run("T1,,P-100,1,1.00,GB\n")
    assert codes(r) == ["MISSING_VALUE"]
    assert r.errors[0].column_name == "transaction_date"


def test_future_date_is_rejected():
    r = run("T1,2026-11-11,P-100,1,1.00,GB\n")
    assert codes(r) == ["DATE_IN_FUTURE"]


def test_duplicate_transaction_id_in_file_keeps_first_rejects_second():
    r = run("T1,2026-10-01,P-100,1,1.00,GB\nT1,2026-10-02,P-200,1,2.00,GB\n")
    assert [v.line_number for v in r.valid_rows] == [2]
    assert codes(r) == ["DUPLICATE_IN_FILE"] and r.errors[0].line_number == 3


def test_wrong_number_of_values_in_a_row():
    r = run("T1,2026-10-01,P-100,1,1.00\n")
    assert codes(r) == ["COLUMN_COUNT_MISMATCH"]


def test_blank_lines_and_excel_bom_are_tolerated():
    content = ("﻿" + HEADER + "T1,2026-10-01,P-100,1,1.00,GB\n\n").encode("utf-8")
    r = validate_csv(content, today=TODAY)
    assert r.file_error is None and r.total_rows == 1 and len(r.valid_rows) == 1


def test_column_order_does_not_matter_and_extra_columns_are_ignored():
    r = run(
        "GB,1.50,2,P-1,2026-10-01,T9,ignored\n",
        header="country,unit_price,quantity,product_code,transaction_date,transaction_id,notes\n",
    )
    assert r.errors == [] and r.valid_rows[0].transaction_id == "T9"
