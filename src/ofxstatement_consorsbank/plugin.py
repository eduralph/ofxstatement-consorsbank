"""
Consorsbank (BNP Paribas Germany) PDF statement parser for ofxstatement.

Parses the standard Girokonto / Tagesgeldkonto PDF exported from the
Consorsbank online portal and produces OFX output suitable for GnuCash.

Transaction row format in the PDF:
    Text/Verwendungszweck  |  Datum  |  PNNr  |  Wert  |  Soll  |  Haben

Two flavours:
  SEPA  – starts with a known keyword (LASTSCHRIFT, EURO-UEBERW., …)
          second line: counterparty BIC + IBAN
  VISA  – PNNr 8999; second line: merchant / amount EUR date txnid
"""

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator, List, Optional

import pdfplumber

from ofxstatement.plugin import Plugin
from ofxstatement.parser import StatementParser
from ofxstatement.statement import Statement, StatementLine

logger = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

# Full transaction row: description  DD.MM.  PNNr  DD.MM.  amount(+/-)
# The amount at end-of-line is the reliable anchor; the description can be
# separated from the date by any whitespace (pdfplumber column gap varies).
TXN_ROW_RE = re.compile(
    r"^(.+?)\s+"              # Verwendungszweck (lazy – stops at first date)
    r"(\d{2}\.\d{2}\.)\s+"   # booking date DD.MM.
    r"(\d+)\s+"              # PNNr
    r"(\d{2}\.\d{2}\.)\s+"   # value date (Wert) DD.MM.
    r"([\d.]+,\d{2}[+\-])"   # amount with sign suffix
    r"\s*$"
)

# Balance checkpoint: *** Kontostand zum DD.MM.[YY] *** amount
BALANCE_RE = re.compile(
    r"\*{3}\s*Kontostand\s+zum\s+"
    r"(\d{2}\.\d{2}\.(?:\d{2,4})?)"
    r"\s*\*{3}\s*"
    r"([\d.]+,\d{2}[+\-])"
)

# Statement header date: first occurrence of DD.MM.YY or DD.MM.YYYY
STMT_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{2}|\d{4})\b")

# IBAN (DE + 20 digits, possibly space-separated)
IBAN_RE = re.compile(r"\b(DE\d{2}(?:\s?\d{4}){4}\s?\d{2})\b")

# BIC (4 letters + DE + 2 alphanum + optional 3 alphanum)
BIC_RE = re.compile(r"\b([A-Z]{4}DE[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")

# Kontotyp line
KONTO_TYPE_RE = re.compile(r"Kontotyp[:\s]+(\S+)", re.IGNORECASE)

# Repeating page header – skip these lines
PAGE_HDR_RE = re.compile(r"^Kontoauszug\s+\d+\s+Konto-Nr\.")

# Column-header row – skip these lines
COL_HDR_RE = re.compile(r"Text/Verwendungszweck")

# ── OFX transaction type mapping ───────────────────────────────────────────────

# Maps the start of the Verwendungszweck text to an OFX ttype string.
TXN_TYPE_MAP: List[tuple] = [
    ("LASTSCHRIFT",   "DIRECTDEBIT"),
    ("EURO-UEBERW.",  "XFER"),
    ("GIROCARD",      "POS"),
    ("DAUERAUFTRAG",  "REPEATPMT"),
    ("GEHALT/RENTE",  "DIRECTDEP"),
    ("GEBUEHREN",     "SRVCHG"),
    ("ENTGELT",       "SRVCHG"),
    ("GUTSCHRIFT",    "CREDIT"),
    ("UMBUCHUNG",     "XFER"),
    ("ZINSEN",        "INT"),
    ("VISA",          "POS"),
]


def _txn_type(text: str) -> str:
    upper = text.upper()
    for prefix, ttype in TXN_TYPE_MAP:
        if upper.startswith(prefix):
            return ttype
    return "OTHER"


# ── Amount / date helpers ──────────────────────────────────────────────────────

def _parse_amount(raw: str) -> Decimal:
    """Parse German-locale amount string, e.g. '1.234,56-' → Decimal('-1234.56')."""
    sign = Decimal(1) if raw.endswith("+") else Decimal(-1)
    normalised = raw[:-1].replace(".", "").replace(",", ".")
    return sign * Decimal(normalised)


def _parse_date(ddmm: str, stmt_year: int, stmt_month: int) -> datetime:
    """
    Convert 'DD.MM.' to a datetime, inferring the year.

    Transactions with month > stmt_month belong to the previous year
    (e.g. December entries that appear at the top of a January statement).
    """
    parts = ddmm.rstrip(".").split(".")
    day, month = int(parts[0]), int(parts[1])
    year = stmt_year if month <= stmt_month else stmt_year - 1
    return datetime(year, month, day)


# ── Plugin ─────────────────────────────────────────────────────────────────────

class ConsorsPlugin(Plugin):
    """Consorsbank (BNP Paribas) PDF statement plugin"""

    def get_parser(self, filename: str) -> "ConsorsParser":
        return ConsorsParser(filename)


# ── Parser ─────────────────────────────────────────────────────────────────────

class ConsorsParser(StatementParser[str]):
    """Parse Consorsbank PDF bank statements into OFX."""

    fin: str
    iban: str
    bic: str
    account_type: str  # 'CHECKING' | 'SAVINGS'
    stmt_year: int
    stmt_month: int

    def __init__(self, filename: str) -> None:
        super().__init__()
        self.fin = filename
        self.iban = ""
        self.bic = ""
        self.account_type = "CHECKING"
        self.stmt_year = 0
        self.stmt_month = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def parse(self) -> Statement:
        with pdfplumber.open(self.fin) as pdf:
            all_lines: List[str] = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                all_lines.extend(text.splitlines())

        self._parse_header(all_lines)

        stmt = Statement(
            account_id=self.iban or "UNKNOWN",
            bank_id=self.bic or None,
            currency="EUR",
            account_type=self.account_type,
        )
        self.statement = stmt

        for sl in self._parse_transactions(all_lines):
            stmt.lines.append(sl)

        logger.info(
            "Parsed %d transactions from %s (IBAN %s)",
            len(stmt.lines),
            self.fin,
            self.iban,
        )
        return stmt

    # Required by base class; not used because we override parse() entirely.
    def split_records(self) -> Iterator[str]:
        return iter([])

    def parse_record(self, line: str) -> Optional[StatementLine]:
        return None

    # ── Header parsing ─────────────────────────────────────────────────────────

    def _parse_header(self, lines: List[str]) -> None:
        """Extract IBAN, BIC, account type and statement date from the first page."""
        for line in lines[:60]:
            if not self.iban:
                m = IBAN_RE.search(line)
                if m:
                    self.iban = re.sub(r"\s+", "", m.group(1))
                    logger.debug("Found IBAN: %s", self.iban)

            if not self.bic:
                m = BIC_RE.search(line)
                if m:
                    self.bic = m.group(1)
                    logger.debug("Found BIC: %s", self.bic)

            if not self.stmt_year:
                m = STMT_DATE_RE.search(line)
                if m:
                    year_raw = int(m.group(3))
                    year = year_raw + 2000 if year_raw < 100 else year_raw
                    self.stmt_year = year
                    self.stmt_month = int(m.group(2))
                    logger.debug(
                        "Statement date: %s.%s (year=%d)",
                        m.group(1), m.group(2), year,
                    )

            m = KONTO_TYPE_RE.search(line)
            if m:
                kt = m.group(1).lower()
                if "tagesgeld" in kt:
                    self.account_type = "SAVINGS"
                else:
                    self.account_type = "CHECKING"

        if not self.stmt_year:
            now = datetime.now()
            self.stmt_year = now.year
            self.stmt_month = now.month
            logger.warning("Could not determine statement year; using %d", self.stmt_year)

    # ── Transaction parsing ────────────────────────────────────────────────────

    def _parse_transactions(self, lines: List[str]) -> Iterator[StatementLine]:
        """
        Walk all lines using a simple state machine.

        A *transaction block* starts on a line that matches TXN_ROW_RE and
        continues until the next such line, a page-header, or a balance
        checkpoint.  The block is then handed to _emit() for conversion.
        """
        current_block: List[str] = []

        for raw_line in lines:
            line = raw_line.rstrip()

            # ── Lines to discard ──────────────────────────────────────────────
            if not line:
                continue
            if PAGE_HDR_RE.match(line):
                yield from self._flush(current_block)
                current_block = []
                continue
            if COL_HDR_RE.search(line):
                yield from self._flush(current_block)
                current_block = []
                continue

            # ── Balance checkpoint ────────────────────────────────────────────
            m = BALANCE_RE.search(line)
            if m:
                yield from self._flush(current_block)
                current_block = []
                bal_date = m.group(1)
                bal_amount = _parse_amount(m.group(2))
                logger.debug(
                    "Balance checkpoint %s → %s EUR", bal_date, bal_amount
                )
                continue

            # ── New transaction row? ──────────────────────────────────────────
            if TXN_ROW_RE.match(line):
                yield from self._flush(current_block)
                current_block = [line]
            elif current_block:
                # Continuation line belonging to the current transaction
                current_block.append(line)
            # else: preamble / unrecognised line before first transaction — ignore

        yield from self._flush(current_block)

    def _flush(self, block: List[str]) -> Iterator[StatementLine]:
        if block:
            sl = self._emit(block)
            if sl is not None:
                yield sl

    def _emit(self, block: List[str]) -> Optional[StatementLine]:
        """Convert a raw transaction block to a StatementLine."""
        if not block:
            return None

        m = TXN_ROW_RE.match(block[0])
        if not m:
            logger.debug("Unmatched block: %r", block[0])
            return None

        desc_text   = m.group(1).strip()
        booking_str = m.group(2)          # DD.MM.
        pnnr        = m.group(3)
        wert_str    = m.group(4)          # DD.MM. (value date)
        amount_str  = m.group(5)

        try:
            amount = _parse_amount(amount_str)
        except InvalidOperation:
            logger.warning("Cannot parse amount %r in line %r", amount_str, block[0])
            return None

        try:
            date = _parse_date(booking_str, self.stmt_year, self.stmt_month)
            date_user = _parse_date(wert_str, self.stmt_year, self.stmt_month)
        except ValueError as exc:
            logger.warning("Cannot parse date in line %r: %s", block[0], exc)
            return None

        ttype = _txn_type(desc_text)

        # PNNr 8999 = VISA card transaction processed as LASTSCHRIFT
        # Override the generic DIRECTDEBIT type to POS for card purchases.
        if pnnr == "8999" and ttype == "DIRECTDEBIT":
            ttype = "POS"

        # ── Build memo and payee from all lines ───────────────────────────────
        cont_lines = [ln.strip() for ln in block[1:] if ln.strip()]

        # First continuation line is the counterparty / merchant name for all
        # transaction types (SEPA counterparty, VISA merchant, etc.)
        payee = cont_lines[0] if cont_lines else desc_text

        memo_parts = [desc_text] + cont_lines
        memo = " | ".join(memo_parts)

        sl = StatementLine(
            id=None,
            date=date,
            memo=memo,
            amount=amount,
        )
        sl.date_user = date_user
        sl.ttype = ttype
        sl.payee = payee
        sl.id = self._make_id(date, amount, memo, pnnr)

        return sl

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_id(date: datetime, amount: Decimal, memo: str, pnnr: str) -> str:
        """Stable 16-hex-char transaction ID derived from key fields."""
        raw = f"{date.isoformat()}|{amount}|{pnnr}|{memo}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
