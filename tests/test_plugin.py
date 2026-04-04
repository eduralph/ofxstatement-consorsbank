"""Tests for ofxstatement-consorsbank plugin."""

from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ofxstatement_consorsbank.plugin import (
    ConsorsParser,
    _parse_amount,
    _parse_date,
    TXN_ROW_RE,
)

# ── Synthetic statement text ───────────────────────────────────────────────────
#
# Two-page minimal statement covering all supported transaction types.
# Formatted to match the real pdfplumber text extraction layout.

PAGE_1 = """\
Kontonummer 0000099999
Kontotyp Girokonto
Kontoinhaber Test User
Dispokredit 5.000,00
Soll Haben
Buchungssaldo alt 1.500,00+
Buchungssaldo neu 843,42+
Kontostand zum 31.01.26
843,42+
Kontoauszug 1 Konto-Nr. 0000099999 Blatt 1 / 2
Datum 31.01.26 Bankleitzahl 123 456 78 Kontowährung EUR
BIC TESTDE71XXX
IBAN DE00123456780000099999
Text/Verwendungszweck Datum PNNr Wert Soll Haben
EURO-UEBERW. 02.01. 8420 01.01. 25,47+
Counterparty Name
<TESTDE71XXX> DE00111122223333444455
*** Kontostand zum 01.01. *** 1.525,47+
LASTSCHRIFT 02.01. 8421 02.01. 349,91-
Muster Versicherung AG
<TESTDEM1XXX> DE00222233334444555566
Mandate 12345678 Beitrag 01/2026
DE00ZZZ00000000001
12345678MU00001
*** Kontostand zum 02.01. *** 1.175,56+
LASTSCHRIFT 05.01. 8999 05.01. 52,20-
EXAMPLE*CLOUD SERVICE CC G
VISA 00000001 CC EXAMPLE.CO
52,20 EUR 01.01. 10000001
LASTSCHRIFT 06.01. 8999 06.01. 14,99-
EXAMPLE*SUBSCRIPTION ms
VISA 00000001 EXAMPLE.INFO
14,99 EUR 03.01. 10000002
4100000000000001
*** Kontostand zum 06.01. *** 1.108,37+
"""

PAGE_2 = """\
Kontoauszug 1 Konto-Nr. 0000099999 Blatt 2 / 2
Datum 31.01.26 Bankleitzahl 123 456 78 Kontowährung EUR
BIC TESTDE71XXX
IBAN DE00123456780000099999
Text/Verwendungszweck Datum PNNr Wert Soll Haben
GIROCARD 10.01. 8421 10.01. 22,50-
Muster Verlag GmbH
<TESTDEFF370> DE00333344445555666677
BVx000000x0000000001
EURO-UEBERW. NR.0000155 14.01. 8422 14.01. 489,72-
Dentist Practice
<TESTDE81XXX> DE00444455556666777788
X000000001
DAUERAUFTRAG NR.0000009 31.01. 8422 31.01. 450,00-
Landlord Name
<TESTDEB2XXX> DE00555566667777888899
Monthly rent
GEHALT/RENTE 31.01. 8420 31.01. 3.126,41+
Musterkasse Testland
<TESTDEMMXXX> DE00666677778888999900
BEZUEGE F. 00000001/202601
GEBUEHREN 31.01. 8999 31.01. 0,48-
2.1% Auslandseinsatzentgelt
VISA 00000001 EXAMPLE.SHOP
26,39 USD 12.01. 10000003
*** Kontostand zum 31.01. *** 843,42+
"""


def _make_mock_pdf(page_texts):
    """Return a mock pdfplumber PDF context manager."""
    pages = []
    for text in page_texts:
        p = MagicMock()
        p.extract_text.return_value = text
        pages.append(p)
    mock_pdf = MagicMock()
    mock_pdf.pages = pages
    mock_pdf.__enter__ = lambda s: s
    mock_pdf.__exit__ = MagicMock(return_value=False)
    return mock_pdf


@pytest.fixture(scope="module")
def statement():
    with patch("pdfplumber.open", return_value=_make_mock_pdf([PAGE_1, PAGE_2])):
        parser = ConsorsParser("fake.pdf")
        return parser.parse()


# ── Amount parsing ─────────────────────────────────────────────────────────────

def test_parse_amount_credit():
    assert _parse_amount("25,47+") == Decimal("25.47")

def test_parse_amount_debit():
    assert _parse_amount("349,91-") == Decimal("-349.91")

def test_parse_amount_thousands():
    assert _parse_amount("3.300,23-") == Decimal("-3300.23")

def test_parse_amount_large():
    assert _parse_amount("9.000,00+") == Decimal("9000.00")


# ── Date parsing ───────────────────────────────────────────────────────────────

def test_parse_date_same_month():
    assert _parse_date("02.01.", 2026, 1) == datetime(2026, 1, 2)

def test_parse_date_year_boundary():
    # December transaction in a January statement → previous year
    assert _parse_date("31.12.", 2026, 1) == datetime(2025, 12, 31)


# ── Transaction row regex ──────────────────────────────────────────────────────

def test_txn_row_re_sepa_debit():
    m = TXN_ROW_RE.match("LASTSCHRIFT 02.01. 8421 02.01. 349,91-")
    assert m and m.group(1).strip() == "LASTSCHRIFT"
    assert m.group(2) == "02.01."
    assert m.group(3) == "8421"
    assert m.group(4) == "02.01."
    assert m.group(5) == "349,91-"

def test_txn_row_re_sepa_credit():
    m = TXN_ROW_RE.match("EURO-UEBERW. 02.01. 8420 01.01. 25,47+")
    assert m and m.group(5) == "25,47+"

def test_txn_row_re_with_nr_suffix():
    m = TXN_ROW_RE.match("EURO-UEBERW. NR.0000155 14.01. 8422 14.01. 489,72-")
    assert m and "NR.0000155" in m.group(1)
    m = TXN_ROW_RE.match("DAUERAUFTRAG NR.0000009 30.01. 8422 30.01. 450,00-")
    assert m and "NR.0000009" in m.group(1)

def test_txn_row_re_thousands():
    m = TXN_ROW_RE.match("EURO-UEBERW. 07.01. 8420 07.01. 3.300,23-")
    assert m and m.group(5) == "3.300,23-"

def test_txn_row_re_no_false_positives():
    assert TXN_ROW_RE.match("Signal Versicherung AG") is None
    assert TXN_ROW_RE.match("<GENODEM1DOR> DE22441600142502511600") is None
    assert TXN_ROW_RE.match("52,20 EUR 01.01. 10357372") is None
    assert TXN_ROW_RE.match("26,39 USD 1,1617000 11.01. 22,72 10355968") is None
    assert TXN_ROW_RE.match("VISA 06254016 msbill.info") is None


# ── Header parsing ─────────────────────────────────────────────────────────────

def test_iban(statement):
    assert statement.account_id == "DE00123456780000099999"

def test_bic(statement):
    assert statement.bank_id == "TESTDE71XXX"

def test_account_type_girokonto(statement):
    assert statement.account_type == "CHECKING"

def test_currency(statement):
    assert statement.currency == "EUR"


# ── Transaction parsing ────────────────────────────────────────────────────────

def test_transaction_count(statement):
    assert len(statement.lines) == 9

def test_euro_ueberw_credit(statement):
    txn = statement.lines[0]
    assert txn.date == datetime(2026, 1, 2)
    assert txn.amount == Decimal("25.47")
    assert txn.ttype == "XFER"
    assert txn.payee == "Counterparty Name"

def test_lastschrift_sepa(statement):
    txn = statement.lines[1]
    assert txn.date == datetime(2026, 1, 2)
    assert txn.amount == Decimal("-349.91")
    assert txn.ttype == "DIRECTDEBIT"
    assert txn.payee == "Muster Versicherung AG"

def test_visa_card_typed_as_pos(statement):
    # PNNr 8999 LASTSCHRIFT transactions must become POS, not DIRECTDEBIT
    txn = statement.lines[2]
    assert txn.amount == Decimal("-52.20")
    assert txn.ttype == "POS"
    assert txn.payee == "EXAMPLE*CLOUD SERVICE CC G"

def test_visa_card_with_card_number_line(statement):
    txn = statement.lines[3]
    assert txn.amount == Decimal("-14.99")
    assert txn.ttype == "POS"
    assert txn.payee == "EXAMPLE*SUBSCRIPTION ms"

def test_girocard(statement):
    txn = statement.lines[4]
    assert txn.ttype == "POS"
    assert txn.amount == Decimal("-22.50")

def test_euro_ueberw_with_nr(statement):
    txn = statement.lines[5]
    assert txn.amount == Decimal("-489.72")
    assert txn.ttype == "XFER"
    assert "NR.0000155" in txn.memo

def test_dauerauftrag(statement):
    txn = statement.lines[6]
    assert txn.ttype == "REPEATPMT"
    assert txn.amount == Decimal("-450.00")

def test_gehalt_rente(statement):
    txn = statement.lines[7]
    assert txn.ttype == "DIRECTDEP"
    assert txn.amount == Decimal("3126.41")

def test_gebuehren_fee(statement):
    txn = statement.lines[8]
    assert txn.ttype == "SRVCHG"
    assert txn.amount == Decimal("-0.48")

def test_all_have_unique_ids(statement):
    ids = [sl.id for sl in statement.lines]
    assert len(set(ids)) == len(ids), "Duplicate transaction IDs"

def test_all_have_amounts(statement):
    for sl in statement.lines:
        assert sl.amount is not None

def test_all_dates_in_range(statement):
    for sl in statement.lines:
        assert sl.date.year in (2025, 2026)
        assert 1 <= sl.date.month <= 12


# ── Tagesgeldkonto account type ────────────────────────────────────────────────

def test_account_type_tagesgeldkonto():
    page = PAGE_1.replace("Kontotyp Girokonto", "Kontotyp Tagesgeldkonto")
    with patch("pdfplumber.open", return_value=_make_mock_pdf([page, PAGE_2])):
        parser = ConsorsParser("fake.pdf")
        stmt = parser.parse()
    assert stmt.account_type == "SAVINGS"
