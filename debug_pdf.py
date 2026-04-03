#!/usr/bin/env python3
"""
Debug helper – shows the raw pdfplumber text extraction for the first N pages
and then runs the parser, printing each parsed transaction.

Usage:
    .venv/bin/python debug_pdf.py tests/statement.pdf [pages]
"""

import sys
import logging

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

import pdfplumber
from ofxstatement_consorsbank.plugin import ConsorsPlugin, TXN_ROW_RE, BALANCE_RE

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "tests/statement.pdf"
max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3

print("=" * 72)
print(f"RAW TEXT EXTRACTION (first {max_pages} pages)")
print("=" * 72)

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages[:max_pages]):
        print(f"\n--- Page {i+1} ---")
        text = page.extract_text() or ""
        for j, line in enumerate(text.splitlines(), 1):
            m_txn = TXN_ROW_RE.match(line)
            m_bal = BALANCE_RE.search(line)
            tag = " [TXN]" if m_txn else (" [BAL]" if m_bal else "")
            print(f"  {j:3d}: {line!r}{tag}")

print("\n" + "=" * 72)
print("PARSED TRANSACTIONS")
print("=" * 72)

plugin = ConsorsPlugin(None, {})
parser = plugin.get_parser(pdf_path)
stmt = parser.parse()

print(f"\nAccount: {stmt.account_id}  Bank: {stmt.bank_id}  Type: {stmt.account_type}")
print(f"Total transactions: {len(stmt.lines)}\n")

for sl in stmt.lines:
    sign = "+" if sl.amount and sl.amount > 0 else ""
    print(f"  {sl.date.strftime('%d.%m.%Y')}  {sl.ttype:12s}  {sign}{sl.amount:>12}  {sl.payee or '—'}")
