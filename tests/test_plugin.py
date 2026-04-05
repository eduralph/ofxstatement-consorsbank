# ofxstatement-consorsbank - Consorsbank PDF statement plugin for ofxstatement
# Copyright (C) 2026  Eduard Ralph
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
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
    assert txn.trntype == "XFER"
    assert txn.payee == "EURO-UEBERW. – Counterparty Name"


def test_lastschrift_sepa(statement):
    txn = statement.lines[1]
    assert txn.date == datetime(2026, 1, 2)
    assert txn.amount == Decimal("-349.91")
    assert txn.trntype == "DIRECTDEBIT"
    assert txn.payee == "LASTSCHRIFT – Muster Versicherung AG"


def test_visa_card_typed_as_pos(statement):
    # PNNr 8999 LASTSCHRIFT transactions must become POS, not DIRECTDEBIT
    txn = statement.lines[2]
    assert txn.amount == Decimal("-52.20")
    assert txn.trntype == "POS"
    assert txn.payee == "LASTSCHRIFT – EXAMPLE*CLOUD SERVICE CC G"


def test_visa_card_with_card_number_line(statement):
    txn = statement.lines[3]
    assert txn.amount == Decimal("-14.99")
    assert txn.trntype == "POS"
    assert txn.payee == "LASTSCHRIFT – EXAMPLE*SUBSCRIPTION ms"


def test_girocard(statement):
    txn = statement.lines[4]
    assert txn.trntype == "POS"
    assert txn.amount == Decimal("-22.50")


def test_euro_ueberw_with_nr(statement):
    txn = statement.lines[5]
    assert txn.amount == Decimal("-489.72")
    assert txn.trntype == "XFER"
    assert "NR.0000155" in txn.payee


def test_dauerauftrag(statement):
    txn = statement.lines[6]
    assert txn.trntype == "REPEATPMT"
    assert txn.amount == Decimal("-450.00")


def test_gehalt_rente(statement):
    txn = statement.lines[7]
    assert txn.trntype == "DIRECTDEP"
    assert txn.amount == Decimal("3126.41")


def test_gebuehren_fee(statement):
    txn = statement.lines[8]
    assert txn.trntype == "SRVCHG"
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


# ── ATM cash withdrawal detection ─────────────────────────────────────────────
#
# Cash withdrawals at another bank's ATM appear as LASTSCHRIFT but use:
#   - BLZ counterparty format  < 760 300 80 >  instead of  <BIC>
#   - VISA card reference with SB suffix (Selbstbedienung = ATM)
# The parser must override DIRECTDEBIT → ATM for these.

PAGE_ATM = """\
Kontonummer 0000099999
Kontotyp Girokonto
Kontoinhaber Test User
Soll Haben
Buchungssaldo alt 1.000,00+
Buchungssaldo neu 950,00+
Kontostand zum 31.07.16
950,00+
Kontoauszug 7 Konto-Nr. 0000099999 Blatt 1 / 1
Datum 31.07.16 Bankleitzahl 123 456 78 Kontowährung EUR
BIC TESTDE71XXX
IBAN DE00123456780000099999
Text/Verwendungszweck Datum PNNr Wert Soll Haben
LASTSCHRIFT 06.07. 8421 06.07. 50,00-
MUSTER SPARKASSE TESTSTADT
< 123 456 78 > 000000001
VISA00000001SB TESTPLATZ
50,00EUR0,0000000000 04.07.
50,00 10316011
*** Kontostand zum 06.07. *** 950,00+
"""


def test_atm_withdrawal_typed_as_atm():
    with patch("pdfplumber.open", return_value=_make_mock_pdf([PAGE_ATM])):
        stmt = ConsorsParser("fake_atm.pdf").parse()
    assert len(stmt.lines) == 1
    txn = stmt.lines[0]
    assert txn.trntype == "ATM"
    assert txn.amount == Decimal("-50.00")
    assert txn.payee == "LASTSCHRIFT – MUSTER SPARKASSE TESTSTADT"


def test_atm_withdrawal_sb_terminal_number():
    # ATM where the only indicator is "SB <n>" in the payee name (no BLZ line, no VISA...SB)
    page = PAGE_ATM.replace(
        "MUSTER SPARKASSE TESTSTADT\n< 123 456 78 > 000000001\nVISA00000001SB TESTPLATZ\n50,00EUR0,0000000000 04.07.\n50,00 10316011",
        "MUSTER VR BANK TESTSTADT SB 30",
    )
    with patch("pdfplumber.open", return_value=_make_mock_pdf([page])):
        stmt = ConsorsParser("fake_atm_sb.pdf").parse()
    assert len(stmt.lines) == 1
    assert stmt.lines[0].trntype == "ATM"


def test_atm_withdrawal_pnnr8999_bank_payee():
    # PNNr 8999 + bank name payee → ATM, not POS
    # "VR BANK A-OAL VRB-A-OAL 276" style: no SB terminal, no BLZ format
    page = PAGE_ATM.replace(
        "LASTSCHRIFT 06.07. 8421 06.07. 50,00-\nMUSTER SPARKASSE TESTSTADT\n< 123 456 78 > 000000001\nVISA00000001SB TESTPLATZ\n50,00EUR0,0000000000 04.07.\n50,00 10316011",
        "LASTSCHRIFT 27.01. 8999 27.01. 100,00-\nTEST VR BANK TESTORT VRB-T-OAL 276\nVISA 00000001 VRB-T-OAL\n100,00 EUR 24.01. 10316011",
    )
    with patch("pdfplumber.open", return_value=_make_mock_pdf([page])):
        stmt = ConsorsParser("fake_atm_bank.pdf").parse()
    assert len(stmt.lines) == 1
    txn = stmt.lines[0]
    assert txn.trntype == "ATM"
    assert txn.amount == Decimal("-100.00")


def test_atm_blz_pattern_not_matched_by_regular_lastschrift(statement):
    # Regular LASTSCHRIFT (direct debit with BIC) must stay DIRECTDEBIT
    txn = statement.lines[1]  # Signal Versicherung — uses <BIC> IBAN format
    assert txn.trntype == "DIRECTDEBIT"


# ── Tagesgeldkonto account type ────────────────────────────────────────────────


def test_account_type_tagesgeldkonto():
    page = PAGE_1.replace("Kontotyp Girokonto", "Kontotyp Tagesgeldkonto")
    with patch("pdfplumber.open", return_value=_make_mock_pdf([page, PAGE_2])):
        parser = ConsorsParser("fake.pdf")
        stmt = parser.parse()
    assert stmt.account_type == "SAVINGS"


# ── Tagesgeldkonto single-page statement (mirrors real PDF layout) ─────────────
#
# D-GUTSCHRIFT is a standing-order credit between own accounts — ttype XFER.
# The real PDF has BIC/IBAN on separate lines below the "Kontoauszug N" header,
# which pdfplumber emits as individual text lines.

PAGE_TAGESGELD = """\
Kontonummer 0000088888
Kontotyp Tagesgeldkonto
Kontoinhaber Test User
Soll Haben
Buchungssaldo alt 28.366,88+
Buchungssaldo neu 28.816,88+
Kontostand zum 31.10.25
28.816,88+
Kontoauszug 10 Konto-Nr. 0000088888 Blatt 1 / 1
Datum 31.10.25 Bankleitzahl 123 456 78 Kontowährung EUR
BIC TESTDE71XXX
IBAN DE00123456780000088888
Text/Verwendungszweck Datum PNNr Wert Soll Haben
D-GUTSCHRIFT NR.0000009 31.10. 8422 31.10. 450,00+
Test User
<TESTDE71XXX> DE00123456780000099999
Ruecklagen jaehrliche Ausgaben
*** Kontostand zum 31.10. *** 28.816,88+
"""


@pytest.fixture(scope="module")
def tagesgeld_statement():
    with patch("pdfplumber.open", return_value=_make_mock_pdf([PAGE_TAGESGELD])):
        parser = ConsorsParser("fake_tagesgeld.pdf")
        return parser.parse()


def test_tagesgeld_account_type(tagesgeld_statement):
    assert tagesgeld_statement.account_type == "SAVINGS"


def test_tagesgeld_iban(tagesgeld_statement):
    assert tagesgeld_statement.account_id == "DE00123456780000088888"


def test_tagesgeld_transaction_count(tagesgeld_statement):
    assert len(tagesgeld_statement.lines) == 1


def test_tagesgeld_d_gutschrift_type(tagesgeld_statement):
    txn = tagesgeld_statement.lines[0]
    assert txn.trntype == "XFER"
    assert txn.amount == Decimal("450.00")
    assert txn.date == datetime(2025, 10, 31)
    assert "NR.0000009" in txn.payee


# ── Verrechnungskonto (securities settlement account) ─────────────────────────
#
# ZINS/DIVID. = dividend/interest credit (ttype DIV)
# EFFEKTEN    = securities purchase debit (ttype DEBIT)
# Kontotyp Verrechnungskonto → account_type MONEYMRKT

PAGE_VERRECHNUNGSKONTO = """\
Kontonummer 0000077777
Kontotyp Verrechnungskonto
Kontoinhaber Test User
Soll Haben
Buchungssaldo alt 0,00+
Buchungssaldo neu 0,00+
Kontostand zum 31.12.25
0,00+
Kontoauszug 11 Konto-Nr. 0000077777 Blatt 1 / 1
Datum 31.12.25 Bankleitzahl 123 456 78 Kontowährung EUR
BIC TESTDE71XXX
IBAN DE00123456780000077777
Text/Verwendungszweck Datum PNNr Wert Soll Haben
ZINS/DIVID. 04.12. 8809 04.12. 42,49+
TEST.INDEX FUND 1D
WKN: TEST01
*** Kontostand zum 04.12. *** 42,49+
EFFEKTEN NR.0000000000001 05.12. 8808 09.12. 42,49-
SPARPLAN 0000000000001
Kauf WKN: TEST01
TEST.INDEX FUND 1D
*** Kontostand zum 09.12. *** 0,00+
"""


@pytest.fixture(scope="module")
def verrechnungskonto_statement():
    with patch(
        "pdfplumber.open", return_value=_make_mock_pdf([PAGE_VERRECHNUNGSKONTO])
    ):
        parser = ConsorsParser("fake_verrechnungskonto.pdf")
        return parser.parse()


def test_verrechnungskonto_account_type(verrechnungskonto_statement):
    assert verrechnungskonto_statement.account_type == "MONEYMRKT"


def test_verrechnungskonto_iban(verrechnungskonto_statement):
    assert verrechnungskonto_statement.account_id == "DE00123456780000077777"


def test_verrechnungskonto_transaction_count(verrechnungskonto_statement):
    assert len(verrechnungskonto_statement.lines) == 2


def test_verrechnungskonto_zins_divid(verrechnungskonto_statement):
    txn = verrechnungskonto_statement.lines[0]
    assert txn.trntype == "DIV"
    assert txn.amount == Decimal("42.49")
    assert txn.date == datetime(2025, 12, 4)


def test_verrechnungskonto_effekten(verrechnungskonto_statement):
    txn = verrechnungskonto_statement.lines[1]
    assert txn.trntype == "DEBIT"
    assert txn.amount == Decimal("-42.49")
    assert txn.date == datetime(2025, 12, 5)
    assert "NR.0000000000001" in txn.payee
