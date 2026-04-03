"""Tests for ofxstatement-consorsbank plugin."""

import os
import pytest
from decimal import Decimal
from datetime import datetime

from ofxstatement.plugin import Plugin
from ofxstatement.ui import UI

from ofxstatement_consorsbank.plugin import (
    ConsorsPlugin,
    _parse_amount,
    _parse_date,
    TXN_ROW_RE,
)

HERE = os.path.dirname(__file__)
STATEMENT_PDF = os.path.join(HERE, "statement.pdf")


# ── Unit tests ─────────────────────────────────────────────────────────────────

def test_parse_amount_credit():
    assert _parse_amount("25,47+") == Decimal("25.47")

def test_parse_amount_debit():
    assert _parse_amount("349,91-") == Decimal("-349.91")

def test_parse_amount_thousands():
    assert _parse_amount("3.300,23-") == Decimal("-3300.23")

def test_parse_amount_large():
    assert _parse_amount("9.000,00+") == Decimal("9000.00")

def test_parse_date_same_month():
    dt = _parse_date("02.01.", 2026, 1)
    assert dt == datetime(2026, 1, 2)

def test_parse_date_year_boundary():
    # December transaction in a January statement → previous year
    dt = _parse_date("31.12.", 2026, 1)
    assert dt == datetime(2025, 12, 31)


def test_txn_row_re_sepa():
    line = "LASTSCHRIFT 02.01. 8421 02.01. 349,91-"
    m = TXN_ROW_RE.match(line)
    assert m is not None
    assert m.group(1).strip() == "LASTSCHRIFT"
    assert m.group(2) == "02.01."
    assert m.group(3) == "8421"
    assert m.group(4) == "02.01."
    assert m.group(5) == "349,91-"

def test_txn_row_re_euro_ueberw_with_nr():
    line = "EURO-UEBERW. NR.0000155 14.01. 8422 14.01. 489,72-"
    m = TXN_ROW_RE.match(line)
    assert m is not None
    assert "NR.0000155" in m.group(1)
    assert m.group(5) == "489,72-"

def test_txn_row_re_dauerauftrag_with_nr():
    line = "DAUERAUFTRAG NR.0000009 30.01. 8422 30.01. 450,00-"
    m = TXN_ROW_RE.match(line)
    assert m is not None
    assert "NR.0000009" in m.group(1)

def test_txn_row_re_thousands():
    line = "EURO-UEBERW. 07.01. 8420 07.01. 3.300,23-"
    m = TXN_ROW_RE.match(line)
    assert m is not None
    assert m.group(5) == "3.300,23-"

def test_txn_row_re_credit():
    line = "EURO-UEBERW. 02.01. 8420 01.01. 25,47+"
    m = TXN_ROW_RE.match(line)
    assert m is not None
    assert m.group(5) == "25,47+"

def test_txn_row_re_no_false_positive_on_description_text():
    # A typical continuation line must NOT match
    assert TXN_ROW_RE.match("SIGNAL IDUNA Gruppe") is None
    assert TXN_ROW_RE.match("<GENODEM1DOR> DE22441600142502511600") is None
    assert TXN_ROW_RE.match("52,20 EUR 01.01. 10357372") is None
    assert TXN_ROW_RE.match("26,39 USD 1,1617000 11.01. 22,72 10355968") is None


# ── Integration test (requires statement.pdf in tests/) ───────────────────────

@pytest.mark.skipif(
    not os.path.exists(STATEMENT_PDF),
    reason="statement.pdf not found in tests/ — copy it there to run",
)
class TestRealStatement:
    @pytest.fixture(scope="class")
    def statement(self):
        plugin = ConsorsPlugin(UI(), {})
        parser = plugin.get_parser(STATEMENT_PDF)
        return parser.parse()

    def test_account_id_is_iban(self, statement):
        assert statement.account_id == "DE50760300800200041041"

    def test_bank_id_is_bic(self, statement):
        assert statement.bank_id == "CSDBDE71XXX"

    def test_account_type(self, statement):
        assert statement.account_type == "CHECKING"

    def test_currency(self, statement):
        assert statement.currency == "EUR"

    def test_transaction_count(self, statement):
        # January 2026 statement has ~100+ transactions
        assert len(statement.lines) > 50

    def test_first_transaction(self, statement):
        # EURO-UEBERW. 02.01. 8420 01.01. 25,47+
        txn = statement.lines[0]
        assert txn.date == datetime(2026, 1, 2)
        assert txn.amount == Decimal("25.47")
        assert txn.ttype == "XFER"

    def test_visa_transaction_type(self, statement):
        # Any PNNr 8999 transaction should be POS, not DIRECTDEBIT
        visa_txns = [sl for sl in statement.lines if sl.ttype == "POS"]
        assert len(visa_txns) > 0

    def test_directdebit_type(self, statement):
        # Regular LASTSCHRIFT (PNNr != 8999) should be DIRECTDEBIT
        dd_txns = [sl for sl in statement.lines if sl.ttype == "DIRECTDEBIT"]
        assert len(dd_txns) > 0

    def test_all_have_ids(self, statement):
        for sl in statement.lines:
            assert sl.id is not None and len(sl.id) == 16

    def test_all_have_amounts(self, statement):
        for sl in statement.lines:
            assert sl.amount is not None

    def test_year_assignment(self, statement):
        # All transactions should be in 2026 (January statement, no Dec entries here)
        for sl in statement.lines:
            assert sl.date.year in (2025, 2026), f"Unexpected year: {sl.date}"
