"""Pure CSV validation. No database or file access here, so it is easy to unit test.

Rules (dataset schema v1):
  transaction_id   required, 1-100 chars, letters/digits/_/-, unique within the file
  transaction_date required, YYYY-MM-DD, not in the future
  product_code     required, 1-50 chars
  quantity         required, whole number 1..1,000,000
  unit_price       required, non-negative, max 2 decimal places, below 10,000,000,000
  country          required, 2-letter code (stored upper-case)
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

REQUIRED_COLUMNS = (
    "transaction_id",
    "transaction_date",
    "product_code",
    "quantity",
    "unit_price",
    "country",
)

_TXN_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_QTY = re.compile(r"^\d{1,7}$")
_PRICE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class ValidRow:
    line_number: int
    transaction_id: str
    transaction_date: date
    product_code: str
    quantity: int
    unit_price: Decimal
    country: str

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(_CENT)


@dataclass(frozen=True)
class RowError:
    line_number: int | None
    column_name: str | None
    error_code: str
    message: str
    raw_value: str | None = None


@dataclass
class ValidationResult:
    valid_rows: list[ValidRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    total_rows: int = 0
    invalid_rows: int = 0
    # Set when the whole file is unusable (bad encoding, missing columns...).
    file_error: RowError | None = None


def _clip(value: str | None, limit: int = 255) -> str | None:
    return None if value is None else value[:limit]


def validate_csv(content: bytes, today: date | None = None) -> ValidationResult:
    """Validate raw CSV bytes and split them into valid rows and row-level errors."""
    today = today or date.today()
    result = ValidationResult()

    try:
        text = content.decode("utf-8-sig")  # -sig strips an Excel byte-order mark
    except UnicodeDecodeError:
        result.file_error = RowError(None, None, "ENCODING_ERROR", "File is not valid UTF-8 text")
        return result

    if not text.strip():
        result.file_error = RowError(None, None, "FILE_EMPTY", "File contains no data")
        return result

    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        result.file_error = RowError(None, None, "MALFORMED_CSV", f"CSV could not be parsed: {exc}")
        return result

    header = [h.strip().lower() for h in rows[0]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        result.file_error = RowError(
            1,
            None,
            "SCHEMA_MISSING_COLUMNS",
            "Missing required column(s): " + ", ".join(missing),
            _clip(",".join(rows[0])),
        )
        return result

    index = {name: header.index(name) for name in REQUIRED_COLUMNS}
    seen_ids: set[str] = set()

    for offset, raw in enumerate(rows[1:]):
        line = offset + 2  # line 1 is the header
        if not raw or all(not cell.strip() for cell in raw):
            continue  # ignore blank lines
        result.total_rows += 1

        if len(raw) != len(header):
            result.errors.append(
                RowError(
                    line,
                    None,
                    "COLUMN_COUNT_MISMATCH",
                    f"Expected {len(header)} values but found {len(raw)}",
                    _clip(",".join(raw)),
                )
            )
            result.invalid_rows += 1
            continue

        values = {name: raw[i].strip() for name, i in index.items()}
        row_errors = _validate_row(line, values, today)

        txn_id = values["transaction_id"]
        if not row_errors and txn_id in seen_ids:
            row_errors.append(
                RowError(
                    line,
                    "transaction_id",
                    "DUPLICATE_IN_FILE",
                    "transaction_id appears earlier in this file",
                    txn_id,
                )
            )

        if row_errors:
            result.errors.extend(row_errors)
            result.invalid_rows += 1
            continue

        seen_ids.add(txn_id)
        result.valid_rows.append(
            ValidRow(
                line_number=line,
                transaction_id=txn_id,
                transaction_date=date.fromisoformat(values["transaction_date"]),
                product_code=values["product_code"],
                quantity=int(values["quantity"]),
                unit_price=Decimal(values["unit_price"]).quantize(_CENT),
                country=values["country"].upper(),
            )
        )

    if result.total_rows == 0:
        result.file_error = RowError(None, None, "FILE_EMPTY", "File has a header but no data rows")

    return result


def _validate_row(line: int, v: dict[str, str], today: date) -> list[RowError]:
    errors: list[RowError] = []

    def err(column: str, code: str, message: str) -> None:
        errors.append(RowError(line, column, code, message, _clip(v.get(column))))

    for column in REQUIRED_COLUMNS:
        if v[column] == "":
            err(column, "MISSING_VALUE", f"{column} is required")
    if errors:
        return errors

    if not _TXN_ID.match(v["transaction_id"]):
        err("transaction_id", "INVALID_FORMAT", "Use 1-100 letters, digits, '_' or '-'")

    if not _DATE.match(v["transaction_date"]):
        err("transaction_date", "INVALID_DATE", "Use the format YYYY-MM-DD")
    else:
        try:
            parsed = date.fromisoformat(v["transaction_date"])
            if parsed > today:
                err("transaction_date", "DATE_IN_FUTURE", "Date cannot be in the future")
        except ValueError:
            err("transaction_date", "INVALID_DATE", "Not a real calendar date")

    if len(v["product_code"]) > 50:
        err("product_code", "TOO_LONG", "Maximum 50 characters")

    if not _QTY.match(v["quantity"]) or int(v["quantity"]) == 0:
        err("quantity", "INVALID_QUANTITY", "Must be a whole number greater than 0")

    if not _PRICE.match(v["unit_price"]):
        err("unit_price", "INVALID_PRICE", "Must be a non-negative amount with max 2 decimals")

    if not _COUNTRY.match(v["country"]):
        err("country", "INVALID_COUNTRY", "Use a 2-letter country code, e.g. GB")

    return errors
