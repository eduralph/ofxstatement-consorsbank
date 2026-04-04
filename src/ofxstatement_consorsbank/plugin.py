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
    r"^(.+?)\s+"  # Verwendungszweck (lazy – stops at first date)
    r"(\d{2}\.\d{2}\.)\s+"  # booking date DD.MM.
    r"(\d+)\s+"  # PNNr
    r"(\d{2}\.\d{2}\.)\s+"  # value date (Wert) DD.MM.
    r"([\d.]+,\d{2}[+\-])"  # amount with sign suffix
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

# Depot/securities statement marker – these documents have no IBAN and are not supported
DEPOT_HDR_RE = re.compile(
    r"\b(Depot(?:auszug|-Nr\.?)|Depotinhaber|Wertpapier)", re.IGNORECASE
)

# ATM cash withdrawal indicators in LASTSCHRIFT continuation lines:
#   BLZ counterparty format  < 760 300 80 >   (digits with spaces, never a BIC)
#   VISA card ATM reference  VISA06254024SB   (SB suffix = Selbstbedienung)
#   ATM terminal number      SB 30 / SB 4     (bank name + SB <terminal-nr>)
ATM_INDICATOR_RE = re.compile(
    r"VISA\d+SB\b"  # VISA card, SB suffix
    r"|<\s*\d{3}\s+\d{3}\s+\d{2}\s*>"  # BLZ counterparty < 760 300 80 >
    r"|\bSB\s+\d+\b"  # ATM terminal: SB 30, SB 4, …
)

# For PNNr 8999 transactions where the payee is a bank (ATM withdrawal),
# not a merchant (POS purchase).  Word-boundary-anchored to avoid false matches
# like "PIGGYBANK".  Only applied when pnnr == "8999".
BANK_PAYEE_RE = re.compile(
    r"\b(BANK|SPARKASSE|RAIFFEISEN|VOLKSBANK|SPARDA|KASSE)\b", re.IGNORECASE
)

# ── OFX transaction type mapping ───────────────────────────────────────────────

# Maps the start of the Verwendungszweck text to an OFX ttype string.
TXN_TYPE_MAP: List[tuple] = [
    ("LASTSCHRIFT", "DIRECTDEBIT"),
    ("EURO-UEBERW.", "XFER"),
    ("UEBERWEISUNG", "XFER"),  # older label for wire transfer
    ("RUECKUEW", "XFER"),  # Rücküberweisung – return/reversal of transfer
    ("GIROCARD", "POS"),
    ("DAUERAUFTRAG", "REPEATPMT"),
    ("D-LASTSCHRIFT", "REPEATPMT"),  # standing-order debit
    ("GEHALT/RENTE", "DIRECTDEP"),
    ("BEZUEGE", "DIRECTDEP"),  # older label for salary/benefits
    ("GEBUEHREN", "SRVCHG"),
    ("ENTGELT", "SRVCHG"),
    ("RETOUREN", "CREDIT"),  # returned goods / refund credit
    ("STORNO", "CREDIT"),  # reversal; direction is carried by amount sign
    ("D-GUTSCHRIFT", "XFER"),
    ("GUTSCHRIFT", "CREDIT"),
    ("UMBUCHUNG", "XFER"),
    ("ABSCHLUSS", "INT"),
    ("ZINS/DIVID.", "DIV"),
    ("ZINSEN", "INT"),
    ("EFFEKTEN", "DEBIT"),
    ("VISA", "POS"),
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
        logger.info("Parsing %s", self.fin)
        all_lines: List[str] = []
        with pdfplumber.open(self.fin) as pdf:
            n_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                page_lines = text.splitlines()
                logger.debug("  Page %d/%d: %d lines", i, n_pages, len(page_lines))
                all_lines.extend(page_lines)
        logger.info("PDF: %d page(s), %d lines total", n_pages, len(all_lines))

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
            "Done: %d transaction(s), account_type=%s, statement=%02d/%d",
            len(stmt.lines),
            self.account_type,
            self.stmt_month,
            self.stmt_year,
        )
        return stmt

    # Required by base class; not used because we override parse() entirely.
    def split_records(self) -> Iterator[str]:
        return iter([])

    def parse_record(self, line: str) -> Optional[StatementLine]:
        return None

    # ── Header parsing ─────────────────────────────────────────────────────────

    def _parse_header(self, lines: List[str]) -> None:
        """Extract IBAN, BIC, account type and statement date from the first page.

        Scans all lines but exits as soon as all four fields are found, so large
        PDFs are not penalised. Own IBAN always appears in the page header before
        any transaction continuation lines, so the first IBAN hit is correct.
        """
        found_kontotyp = False

        for line_num, line in enumerate(lines, 1):
            if not self.iban:
                m = IBAN_RE.search(line)
                if m:
                    self.iban = re.sub(r"\s+", "", m.group(1))
                    # Mask all but the country code and last 4 chars
                    masked = self.iban[:4] + "…" + self.iban[-4:]
                    logger.debug(
                        "Line %d: IBAN found: %s (%d chars)",
                        line_num,
                        masked,
                        len(self.iban),
                    )

            if not self.bic:
                m = BIC_RE.search(line)
                if m:
                    self.bic = m.group(1)
                    logger.debug("Line %d: BIC found: %s", line_num, self.bic)

            if not self.stmt_year:
                m = STMT_DATE_RE.search(line)
                if m:
                    year_raw = int(m.group(3))
                    year = year_raw + 2000 if year_raw < 100 else year_raw
                    self.stmt_year = year
                    self.stmt_month = int(m.group(2))
                    logger.debug(
                        "Line %d: statement date: month=%s year=%d",
                        line_num,
                        m.group(2),
                        year,
                    )

            if not found_kontotyp:
                m = KONTO_TYPE_RE.search(line)
                if m:
                    found_kontotyp = True
                    kt = m.group(1).lower()
                    if "tagesgeld" in kt:
                        self.account_type = "SAVINGS"
                    elif "verrechnungskonto" in kt:
                        self.account_type = "MONEYMRKT"
                    else:
                        self.account_type = "CHECKING"
                    logger.debug(
                        "Line %d: Kontotyp: %r → %s",
                        line_num,
                        m.group(1),
                        self.account_type,
                    )

            if self.iban and self.bic and self.stmt_year and found_kontotyp:
                logger.debug("All header fields found by line %d", line_num)
                break

        if not self.iban:
            if any(DEPOT_HDR_RE.search(line) for line in lines[:100]):
                logger.warning(
                    "This file appears to be a depot/securities statement "
                    "(Quartalsdepotauszug / Jahresdepotauszug) — not supported by this plugin. "
                    "Only Konto statements (Girokonto, Tagesgeldkonto, Verrechnungskonto) are parsed. "
                    "Output will contain 0 transactions."
                )
            else:
                logger.warning(
                    "IBAN not found in %d lines — output will use account_id=UNKNOWN",
                    len(lines),
                )
        if not self.bic and self.iban:
            # Only warn about missing BIC separately if IBAN was found (depot warning covers both)
            logger.warning(
                "BIC not found in %d lines — bank_id will be unset", len(lines)
            )
        if not self.stmt_year:
            now = datetime.now()
            self.stmt_year = now.year
            self.stmt_month = now.month
            logger.warning(
                "Statement date not found in %d lines; falling back to %02d/%d",
                len(lines),
                self.stmt_month,
                self.stmt_year,
            )

    # ── Transaction parsing ────────────────────────────────────────────────────

    def _parse_transactions(self, lines: List[str]) -> Iterator[StatementLine]:
        """
        Walk all lines using a simple state machine.

        A *transaction block* starts on a line that matches TXN_ROW_RE and
        continues until the next such line, a page-header, or a balance
        checkpoint.  The block is then handed to _emit() for conversion.
        """
        current_block: List[str] = []
        txn_count = 0

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.rstrip()

            # ── Lines to discard ──────────────────────────────────────────────
            if not line:
                continue
            if PAGE_HDR_RE.match(line):
                yield from self._flush(current_block)
                current_block = []
                logger.debug("Line %d: page header — flushed block", line_num)
                continue
            if COL_HDR_RE.search(line):
                yield from self._flush(current_block)
                current_block = []
                logger.debug("Line %d: column header — flushed block", line_num)
                continue

            # ── Balance checkpoint ────────────────────────────────────────────
            m = BALANCE_RE.search(line)
            if m:
                yield from self._flush(current_block)
                current_block = []
                logger.debug("Line %d: balance checkpoint %s", line_num, m.group(1))
                continue

            # ── New transaction row? ──────────────────────────────────────────
            m_txn = TXN_ROW_RE.match(line)
            if m_txn:
                yield from self._flush(current_block)
                txn_count += 1
                current_block = [line]
                keyword = m_txn.group(1).split()[0]
                sign = "+" if m_txn.group(5).endswith("+") else "-"
                logger.debug(
                    "Line %d: txn #%d — keyword=%r date=%s PNNr=%s sign=%s",
                    line_num,
                    txn_count,
                    keyword,
                    m_txn.group(2),
                    m_txn.group(3),
                    sign,
                )
            elif current_block:
                # Continuation line belonging to the current transaction
                current_block.append(line)
                logger.debug(
                    "Line %d: continuation (block now %d lines)",
                    line_num,
                    len(current_block),
                )
            else:
                # Preamble / header material before the first transaction
                logger.debug(
                    "Line %d: skipped (pre-transaction, len=%d)", line_num, len(line)
                )

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
            # Should not happen: the block was opened because TXN_ROW_RE matched.
            # If it fires, the regex or the block-assembly logic has a bug.
            logger.warning(
                "Unexpected: block first line no longer matches TXN_ROW_RE "
                "(block_lines=%d, first_line_len=%d) — transaction skipped",
                len(block),
                len(block[0]),
            )
            return None

        desc_text = m.group(1).strip()
        booking_str = m.group(2)  # DD.MM.
        pnnr = m.group(3)
        wert_str = m.group(4)  # DD.MM. (value date)
        amount_str = m.group(5)

        try:
            amount = _parse_amount(amount_str)
        except InvalidOperation:
            # Mask all digits so no amount value is exposed.
            masked = re.sub(r"\d", "X", amount_str)
            logger.warning(
                "Amount parse failed: masked_raw=%r len=%d — "
                "expected German-locale format e.g. X.XXX,XX+ — transaction skipped",
                masked,
                len(amount_str),
            )
            return None

        try:
            date = _parse_date(booking_str, self.stmt_year, self.stmt_month)
            date_user = _parse_date(wert_str, self.stmt_year, self.stmt_month)
        except ValueError as exc:
            # booking_str / wert_str are DD.MM. — safe to log (no personal data).
            logger.warning(
                "Date parse failed: booking=%r value=%r inferred_year=%d — %s "
                "— transaction skipped",
                booking_str,
                wert_str,
                self.stmt_year,
                exc,
            )
            return None

        ttype = _txn_type(desc_text)
        keyword = desc_text.split()[0]

        # PNNr 8999 = VISA card transaction processed as LASTSCHRIFT
        # Override the generic DIRECTDEBIT type to POS for card purchases.
        if pnnr == "8999" and ttype == "DIRECTDEBIT":
            ttype = "POS"
            logger.debug("PNNr 8999 on %s: DIRECTDEBIT → POS", date.strftime("%d.%m"))

        if ttype == "OTHER":
            logger.warning(
                "Unknown transaction type: keyword=%r date=%s — add to TXN_TYPE_MAP?",
                keyword,
                date.strftime("%d.%m.%Y"),
            )
        else:
            logger.debug(
                "Type: keyword=%r → %s (date=%s)",
                keyword,
                ttype,
                date.strftime("%d.%m"),
            )

        if date != date_user:
            logger.debug(
                "Value date differs: booked=%s valued=%s",
                date.strftime("%d.%m"),
                date_user.strftime("%d.%m"),
            )

        # ── Build memo and payee from all lines ───────────────────────────────
        cont_lines = [ln.strip() for ln in block[1:] if ln.strip()]
        logger.debug(
            "Block %s: %d continuation line(s)", date.strftime("%d.%m"), len(cont_lines)
        )

        # ATM cash withdrawal detection.  Two flavours:
        #
        # 1. LASTSCHRIFT (non-8999): BLZ counterparty / VISA...SB / SB terminal
        #    → ttype is still DIRECTDEBIT at this point.
        # 2. LASTSCHRIFT PNNr 8999 (already flipped to POS): bank-name payee
        #    (VR BANK …, SPARKASSE …) rather than a merchant.
        #
        # Both are checked here, after cont_lines is available.
        if ttype in ("DIRECTDEBIT", "POS"):
            atm = any(ATM_INDICATOR_RE.search(ln) for ln in cont_lines)
            if not atm and ttype == "POS" and cont_lines:
                atm = bool(BANK_PAYEE_RE.search(cont_lines[0]))
            if atm:
                prev = ttype
                ttype = "ATM"
                logger.debug(
                    "ATM detected on %s: %s → ATM", date.strftime("%d.%m"), prev
                )

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
        sl.trntype = ttype
        sl.payee = payee
        sl.id = self._make_id(date, amount, memo, pnnr)

        return sl

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_id(date: datetime, amount: Decimal, memo: str, pnnr: str) -> str:
        """Stable 16-hex-char transaction ID derived from key fields."""
        raw = f"{date.isoformat()}|{amount}|{pnnr}|{memo}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
