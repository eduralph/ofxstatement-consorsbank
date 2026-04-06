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
"""
Consorsbank (BNP Paribas Germany) statement parser for ofxstatement.

Supports two input formats:
  PDF  – standard statement PDF exported from the Consorsbank portal
  CSV  – semicolon-separated CSV export from the Consorsbank portal

File format is detected automatically from the file extension.
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
#
# Entries marked ★ are confirmed against real statements (2016–2026).
# Entries marked ○ are best-effort additions for keywords not yet observed
# in the wild — if you encounter a misclassified transaction please open an
# issue at https://github.com/eduralph/ofxstatement-consorsbank/issues
TXN_TYPE_MAP: List[tuple] = [
    # ── Direct debits / transfers ──────────────────────────────────────────
    ("LASTSCHRIFT", "DIRECTDEBIT"),  # ★ direct debit / card via LASTSCHRIFT
    ("RUECKLASTSCHRIFT", "DIRECTDEBIT"),  # ○ returned / bounced direct debit
    ("EURO-UEBERW.", "XFER"),  # ★ SEPA credit transfer
    ("ECHTZEITUEBERW.", "XFER"),  # ○ instant payment (SCT Inst)
    ("SEPA-UEBERW.", "XFER"),  # ○ SEPA transfer (alternate label)
    ("ONLINE-UEBERW.", "XFER"),  # ○ online banking transfer (older label)
    ("UEBERWEISUNG", "XFER"),  # ★ wire transfer (older label)
    ("RUECKUEW", "XFER"),  # ★ Rücküberweisung – return of transfer
    # ── Card payments ──────────────────────────────────────────────────────
    ("GIROCARD", "POS"),  # ★ debit card payment
    ("VISA", "POS"),  # ★ VISA card transaction
    # ── Standing orders ────────────────────────────────────────────────────
    ("DAUERAUFTRAG", "REPEATPMT"),  # ★ standing order (debit)
    ("D-LASTSCHRIFT", "REPEATPMT"),  # ★ standing order debit
    ("D-GUTSCHRIFT", "XFER"),  # ★ standing order credit
    # ── Salary / income ────────────────────────────────────────────────────
    ("GEHALT/RENTE", "DIRECTDEP"),  # ★ salary or pension
    ("BEZUEGE", "DIRECTDEP"),  # ★ salary / benefits (older label)
    # ── Fees and charges ───────────────────────────────────────────────────
    ("GEBUEHREN", "SRVCHG"),  # ★ bank fees
    ("ENTGELT", "SRVCHG"),  # ★ charges
    ("DEPOTGEBUEHREN", "SRVCHG"),  # ○ custody / depot fees
    ("PROVISION", "SRVCHG"),  # ○ brokerage commission
    # ── Credits and reversals ──────────────────────────────────────────────
    ("RETOUREN", "CREDIT"),  # ★ returned goods / refund
    ("STORNO", "CREDIT"),  # ★ reversal (direction from amount sign)
    ("GUTSCHRIFT", "CREDIT"),  # ★ general credit
    # ── Cash ───────────────────────────────────────────────────────────────
    ("BARGELDAUSZ.", "ATM"),  # ○ ATM cash withdrawal
    ("BARAUSZAHLUNG", "ATM"),  # ○ ATM cash withdrawal (alternate label)
    ("BAREINZAHLUNG", "DEP"),  # ○ cash deposit at counter
    ("EINZAHLUNG", "DEP"),  # ○ cash deposit (alternate label)
    # ── Internal transfers ─────────────────────────────────────────────────
    ("UMBUCHUNG", "XFER"),  # ★ internal reclassification / transfer
    # ── Interest and dividends ─────────────────────────────────────────────
    ("ABSCHLUSS", "INT"),  # ★ quarterly settlement (interest / Dispo fees on Girokonto)
    ("SOLLZINSEN", "INT"),  # ○ overdraft (Dispo) interest (alternate label)
    ("KONTOKORRENTZINS", "INT"),  # ○ current account interest settlement
    ("ZINS/DIVID.", "DIV"),  # ★ dividend / interest (Verrechnungskonto)
    ("ZINSEN", "INT"),  # ★ interest
    ("KUPON", "INT"),  # ○ bond coupon payment
    # ── Securities (Verrechnungskonto) ─────────────────────────────────────
    ("EFFEKTEN", "DEBIT"),  # ★ securities purchase
    ("WERTPAPIERKAUF", "DEBIT"),  # ○ securities purchase (alternate label)
    ("WERTPAPIERVERKAUF", "CREDIT"),  # ○ securities sale proceeds
    ("TILGUNG", "CREDIT"),  # ○ bond redemption
]


def _txn_type(text: str) -> str:
    upper = text.upper()
    for prefix, ttype in TXN_TYPE_MAP:
        if upper.startswith(prefix):
            return ttype
    return "OTHER"


# ── Consorsbank account constants ─────────────────────────────────────────────

CONSORSBANK_BLZ = "76030080"
CONSORSBANK_BIC = "CSDBDE71XXX"

# ── CSV transaction type map ───────────────────────────────────────────────────
#
# Maps the Buchungstext column from the CSV export to OFX ttype.
# Labels differ from the PDF keywords (German full text vs. uppercase abbreviations).

CSV_TXN_TYPE_MAP: List[tuple] = [
    ("Lastschrift", "DIRECTDEBIT"),  # direct debit / card via Lastschrift
    ("Dauerauftrag", "REPEATPMT"),  # standing order
    ("D-Lastschrift", "REPEATPMT"),  # standing order debit (alternate)
    ("D-Gutschrift", "XFER"),  # standing order credit
    ("ECHTZEIT EURO-UEBERW.", "XFER"),  # instant payment (SCT Inst)
    ("EURO-Überweisung", "XFER"),  # SEPA credit transfer
    ("SEPA-Überweisung", "XFER"),  # SEPA transfer (alternate label)
    ("Überweisung", "XFER"),  # wire transfer
    ("Gutschrift", "CREDIT"),  # general credit
    ("Retouren", "CREDIT"),  # returned goods / refund
    ("Storno", "CREDIT"),  # reversal
    ("Gehalt/Rente", "DIRECTDEP"),  # salary or pension
    ("Bezüge", "DIRECTDEP"),  # salary / benefits (alternate)
    ("Gebühren", "SRVCHG"),  # bank fees
    ("Entgelt", "SRVCHG"),  # charges / fees (alternate)
    ("Abschluss", "INT"),  # quarterly settlement / interest
    ("Zinsen", "INT"),  # interest
    ("Zins/Divid.", "DIV"),  # dividend / interest
    ("Effekten", "DEBIT"),  # securities purchase
    ("Umbuchung", "XFER"),  # internal transfer
    ("Barauszahlung", "ATM"),  # cash withdrawal
    ("Bareinzahlung", "DEP"),  # cash deposit
]


def _csv_txn_type(buchungstext: str) -> str:
    lower = buchungstext.lower()
    for prefix, ttype in CSV_TXN_TYPE_MAP:
        if lower.startswith(prefix.lower()):
            return ttype
    return "OTHER"


# ── Amount / date helpers ──────────────────────────────────────────────────────


def _parse_amount(raw: str) -> Decimal:
    """Parse German-locale amount string, e.g. '1.234,56-' → Decimal('-1234.56')."""
    sign = Decimal(1) if raw.endswith("+") else Decimal(-1)
    normalised = raw[:-1].replace(".", "").replace(",", ".")
    return sign * Decimal(normalised)


def _parse_csv_amount(raw: str) -> Decimal:
    """Parse CSV amount with leading sign, e.g. '-272,50' or '2.838,23'."""
    raw = raw.strip()
    if raw.startswith("-"):
        sign, raw = Decimal(-1), raw[1:]
    else:
        sign = Decimal(1)
    return sign * Decimal(raw.replace(".", "").replace(",", "."))


def _make_iban(account_number: str) -> str:
    """Compute German IBAN for a Consorsbank account number."""
    ktnr = account_number.strip().zfill(10)
    bban = CONSORSBANK_BLZ + ktnr  # 18 digits
    # Append country code as digits (DE=1314) with 00 placeholder check digits
    remainder = int(bban + "131400") % 97
    check = 98 - remainder
    return f"DE{check:02d}{bban}"


def _make_id(date: datetime, amount: Decimal, memo: str, ref: str) -> str:
    """Stable 16-hex-char transaction ID derived from key fields."""
    raw = f"{date.isoformat()}|{amount}|{ref}|{memo}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
    """Consorsbank (BNP Paribas) statement plugin — supports PDF and CSV"""

    def get_parser(self, filename: str) -> "StatementParser":
        if filename.lower().endswith(".csv"):
            return ConsorsCSVParser(filename)
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
        try:
            pdf_cm = pdfplumber.open(self.fin)
        except Exception as exc:
            raise ValueError(
                f"{self.fin!r} could not be opened as a PDF. "
                "If this is a CSV export, rename it to .csv."
            ) from exc
        with pdf_cm as pdf:
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

        self._apply_balances(stmt, all_lines)

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

    def parse_record(self, _: str) -> Optional[StatementLine]:  # required by base class
        return None

    # ── Balance population ─────────────────────────────────────────────────────

    def _apply_balances(self, stmt: Statement, lines: List[str]) -> None:
        """Set start/end balance and dates on the statement from balance checkpoints."""
        checkpoints = []
        for line in lines:
            m = BALANCE_RE.search(line)
            if m:
                try:
                    date = _parse_date(m.group(1), self.stmt_year, self.stmt_month)
                    amount = _parse_amount(m.group(2))
                    checkpoints.append((date, amount))
                except (ValueError, Exception):
                    pass

        if not checkpoints:
            return

        checkpoints.sort(key=lambda x: x[0])
        stmt.start_date = checkpoints[0][0]
        stmt.start_balance = checkpoints[0][1]
        stmt.end_date = checkpoints[-1][0]
        stmt.end_balance = checkpoints[-1][1]
        logger.debug(
            "Balances: start=%s end=%s",
            stmt.start_date.strftime("%d.%m.%Y"),
            stmt.end_date.strftime("%d.%m.%Y"),
        )

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

        # Payee: "KEYWORD – Counterparty name" so each row is uniquely
        # identifiable in GnuCash's single-line register view even when
        # multiple transactions share the same counterparty on the same date.
        counterparty = cont_lines[0] if cont_lines else ""
        payee = f"{desc_text} – {counterparty}" if counterparty else desc_text

        memo_parts = cont_lines[1:]
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
        sl.id = _make_id(date, amount, memo, pnnr)

        return sl


# ── CSV Parser ─────────────────────────────────────────────────────────────────


class ConsorsCSVParser(StatementParser[str]):
    """Parse Consorsbank CSV export (semicolon-separated) into OFX.

    CSV column layout (row 6+):
        Buchung ; Valuta ; Sender/Empfänger ; IBAN ; BIC ;
        Buchungstext ; Verwendungszweck ; Betrag ; Währung
    """

    fin: str

    def __init__(self, filename: str) -> None:
        super().__init__()
        self.fin = filename

    def parse(self) -> Statement:
        logger.info("Parsing CSV %s", self.fin)
        # utf-8-sig strips the UTF-8 BOM that Consorsbank prepends to CSV exports
        with open(self.fin, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()

        if not lines or not lines[0].startswith("Konto;"):
            raise ValueError(
                f"{self.fin!r} does not look like a Consorsbank CSV export "
                "(expected first line to start with 'Konto;'). "
                "If this is a PDF, rename it to .pdf."
            )

        # Row 1: account number ; holder ; export date
        account_number = lines[1].split(";")[0].strip() if len(lines) > 1 else ""
        iban = _make_iban(account_number) if account_number else "UNKNOWN"
        masked = iban[:4] + "…" + iban[-4:] if len(iban) > 8 else iban
        logger.debug("CSV: account=%s → IBAN %s", account_number, masked)

        # Row 4: balance ; currency ; date ; ...
        end_balance: Optional[Decimal] = None
        end_date: Optional[datetime] = None
        if len(lines) > 4:
            parts = lines[4].split(";")
            try:
                end_balance = _parse_csv_amount(parts[0])
                end_date = datetime.strptime(parts[2].strip(), "%d.%m.%Y")
            except (ValueError, InvalidOperation):
                pass

        stmt = Statement(
            account_id=iban,
            bank_id=CONSORSBANK_BIC,
            currency="EUR",
            account_type="CHECKING",
        )
        stmt.end_balance = end_balance
        stmt.end_date = end_date

        # Rows 6+: transactions (row index passed to break ID ties for duplicate rows)
        for row_idx, raw_line in enumerate(lines[6:]):
            sl = self._parse_row(raw_line, row_idx)
            if sl is not None:
                stmt.lines.append(sl)

        logger.info("CSV done: %d transaction(s)", len(stmt.lines))
        return stmt

    def _parse_row(self, raw_line: str, row_idx: int = 0) -> Optional[StatementLine]:
        parts = raw_line.split(";")
        if len(parts) < 9:
            return None
        booking_str = parts[0].strip()
        valuta_str = parts[1].strip()
        counterparty = parts[2].strip()
        buchungstext = parts[5].strip()
        verwendungszweck = parts[6].strip()
        betrag_str = parts[7].strip()

        if not booking_str or not betrag_str:
            return None

        try:
            date = datetime.strptime(booking_str, "%d.%m.%Y")
            date_user = (
                datetime.strptime(valuta_str, "%d.%m.%Y") if valuta_str else date
            )
            amount = _parse_csv_amount(betrag_str)
        except (ValueError, InvalidOperation):
            logger.warning(
                "CSV row parse failed: booking=%r betrag=%r — skipped",
                booking_str,
                betrag_str,
            )
            return None

        ttype = _csv_txn_type(buchungstext)

        # ATM detection: same VISA…SB / BLZ / SB-terminal heuristics as PDF
        if ttype == "DIRECTDEBIT":
            if ATM_INDICATOR_RE.search(verwendungszweck) or ATM_INDICATOR_RE.search(
                counterparty
            ):
                ttype = "ATM"
                logger.debug("ATM detected (CSV) on %s", date.strftime("%d.%m"))

        if ttype == "OTHER":
            logger.warning(
                "Unknown CSV Buchungstext: %r on %s — add to CSV_TXN_TYPE_MAP?",
                buchungstext,
                date.strftime("%d.%m.%Y"),
            )

        payee = f"{buchungstext} – {counterparty}" if counterparty else buchungstext
        memo = verwendungszweck

        sl = StatementLine(id=None, date=date, memo=memo, amount=amount)
        sl.date_user = date_user
        sl.trntype = ttype
        sl.payee = payee
        sl.id = _make_id(date, amount, memo, f"{buchungstext}:{row_idx}")
        return sl

    def split_records(self) -> Iterator[str]:
        return iter([])

    def parse_record(self, _: str) -> Optional[StatementLine]:  # required by base class
        return None
