# Consorsbank plugin for ofxstatement

Converts **Consorsbank** (BNP Paribas Germany) bank statements to OFX
format for import into GnuCash or other personal finance software.

[ofxstatement](https://github.com/kedder/ofxstatement) is a tool to convert proprietary bank statements to OFX format.

Two input formats are supported — the format is detected automatically from the file extension:

| Extension | Format | How to export |
|-----------|--------|---------------|
| `.pdf`    | PDF statement | Consorsbank portal → Kontoauszüge → Download |
| `.csv`    | CSV transaction export | Consorsbank portal → Umsätze → Export → CSV |


## Supported account types

- **Girokonto** (current account, `account_type=CHECKING`)
- **Tagesgeldkonto** (savings account, `account_type=SAVINGS`)
- **Verrechnungskonto** (securities settlement account, `account_type=MONEYMRKT`)

### PDF format

The plugin parses the standard PDF exported from the Consorsbank online portal.
The PDF must have a text layer (i.e. not a scanned image); all PDFs downloaded
directly from the portal qualify.

Quarterly and annual depot statements (`QUARTALSDEPOTAUSZUG`,
`JAHRESDEPOTAUSZUG`) have a different format and are not supported — the
plugin will log a clear warning and produce no transactions for those files.

### Transaction types handled

Entries marked ★ are confirmed against real statements (2016–2026).
Entries marked ○ are best-effort additions for keywords not yet observed —
see [Caveats](#caveats) below.

| Keyword              | Description                                   | OFX type    |   |
|----------------------|-----------------------------------------------|-------------|---|
| LASTSCHRIFT          | Direct debit / SEPA debit                     | DIRECTDEBIT | ★ |
| LASTSCHRIFT (8999)   | VISA card purchase (PNNr 8999)                | POS         | ★ |
| LASTSCHRIFT (ATM)    | Cash withdrawal at another bank's ATM         | ATM         | ★ |
| RUECKLASTSCHRIFT     | Returned / bounced direct debit               | DIRECTDEBIT | ○ |
| EURO-UEBERW.         | SEPA credit transfer                          | XFER        | ★ |
| ECHTZEITUEBERW.      | Instant payment (SCT Inst)                    | XFER        | ○ |
| SEPA-UEBERW.         | SEPA transfer (alternate label)               | XFER        | ○ |
| ONLINE-UEBERW.       | Online banking transfer (older label)         | XFER        | ○ |
| UEBERWEISUNG         | Wire transfer (older label)                   | XFER        | ★ |
| RUECKUEW             | Return transfer (Rücküberweisung)             | XFER        | ★ |
| GIROCARD             | Debit card payment                            | POS         | ★ |
| VISA                 | VISA card transaction                         | POS         | ★ |
| DAUERAUFTRAG         | Standing order (debit)                        | REPEATPMT   | ★ |
| D-LASTSCHRIFT        | Standing order debit                          | REPEATPMT   | ★ |
| D-GUTSCHRIFT         | Standing order credit                         | XFER        | ★ |
| GEHALT/RENTE         | Salary / pension credit                       | DIRECTDEP   | ★ |
| BEZUEGE              | Salary / benefits (older label)               | DIRECTDEP   | ★ |
| GEBUEHREN            | Bank fees                                     | SRVCHG      | ★ |
| ENTGELT              | Charges / fees                                | SRVCHG      | ★ |
| DEPOTGEBUEHREN       | Custody / depot fees                          | SRVCHG      | ○ |
| PROVISION            | Brokerage commission                          | SRVCHG      | ○ |
| GUTSCHRIFT           | General credit                                | CREDIT      | ★ |
| RETOUREN             | Returned goods / refund                       | CREDIT      | ★ |
| STORNO               | Reversal (direction carried by amount sign)   | CREDIT      | ★ |
| BARGELDAUSZ.         | ATM cash withdrawal                           | ATM         | ○ |
| BARAUSZAHLUNG        | ATM cash withdrawal (alternate label)         | ATM         | ○ |
| BAREINZAHLUNG        | Cash deposit at counter                       | DEP         | ○ |
| EINZAHLUNG           | Cash deposit (alternate label)                | DEP         | ○ |
| UMBUCHUNG            | Internal transfer / reclassification          | XFER        | ★ |
| ABSCHLUSS            | Quarterly settlement (interest / Dispo fees)  | INT         | ★ |
| SOLLZINSEN           | Overdraft (Dispo) interest (alternate label)  | INT         | ○ |
| KONTOKORRENTZINS     | Current account interest settlement           | INT         | ○ |
| ZINSEN               | Interest                                      | INT         | ★ |
| KUPON                | Bond coupon payment                           | INT         | ○ |
| ZINS/DIVID.          | Dividend / interest (Verrechnungskonto)       | DIV         | ★ |
| EFFEKTEN             | Securities purchase (Verrechnungskonto)       | DEBIT       | ★ |
| WERTPAPIERKAUF       | Securities purchase (alternate label)         | DEBIT       | ○ |
| WERTPAPIERVERKAUF    | Securities sale proceeds                      | CREDIT      | ○ |
| TILGUNG              | Bond redemption                               | CREDIT      | ○ |

### ATM detection

Cash withdrawals at foreign ATMs appear in Consorsbank statements as
`LASTSCHRIFT` but are reclassified to `ATM` when the continuation lines
contain any of:

- A BLZ-format counterparty line: `< 760 300 80 >` (spaced digit groups, not a BIC)
- A VISA card reference with SB suffix: `VISA06254016SB`
- An ATM terminal number: `SB 30`, `SB 4`
- A bank name in the payee for PNNr 8999 transactions: `VR BANK …`, `SPARKASSE …`

**Why this is hard.**  Consorsbank records cash withdrawals using the same
`LASTSCHRIFT` keyword as regular direct debits — there is no dedicated
transaction type.  The only distinguishing information is buried in the
free-text continuation lines, whose format varies by ATM operator and has
changed across statement generations:

- Older withdrawals (Girocard at another bank's ATM) use a German sort-code
  (*Bankleitzahl*) as the counterparty identifier instead of a BIC, written
  as `< 760 300 80 >` with spaces between digit groups.
- Withdrawals processed via the VISA network (PNNr 8999) go through the same
  code path as VISA card purchases and carry a card reference such as
  `VISA 06254016 VRB-A-OAL`.  The only difference from a merchant purchase
  is that the payee is a bank rather than a shop.
- Some ATM terminals identify themselves with an *SB* suffix
  (*Selbstbedienung*, i.e. self-service) or a terminal number (`SB 30`).

Because no single indicator is present in every withdrawal, the plugin applies
all four heuristics in combination.  Withdrawals that match none of them will
be imported as `DIRECTDEBIT` rather than `ATM`; the memo field will still
contain the full raw text so you can correct the category in GnuCash manually.
If you encounter a misclassified withdrawal, please open an issue with the
(anonymised) continuation-line text so the detection can be extended.


### CSV format

The CSV export from the Consorsbank portal is a semicolon-separated file with
a UTF-8 BOM header. The plugin reads the following columns:

| Column | Description |
|--------|-------------|
| Buchung | Booking date (`DD.MM.YYYY`) |
| Valuta | Value date (`DD.MM.YYYY`) |
| Sender / Empfänger | Counterparty name |
| Buchungstext | Transaction type keyword |
| Verwendungszweck | Payment reference / memo |
| Betrag | Amount (German locale, e.g. `-1.234,56`) |

The account IBAN is computed from the account number in the file header using
the Consorsbank BLZ (`76030080`). The end balance and date are read from the
file header.

CSV transaction type keywords differ from the PDF keywords (German full text
vs. uppercase abbreviations). Entries marked ★ are confirmed against real
CSV exports; entries marked ○ are best-effort additions — see
[Caveats](#caveats) below.

| Buchungstext | Description | OFX type | |
|---|---|---|---|
| Lastschrift | Direct debit | DIRECTDEBIT | ★ |
| Dauerauftrag | Standing order | REPEATPMT | ★ |
| D-Lastschrift | Standing order debit | REPEATPMT | ○ |
| D-Gutschrift | Standing order credit | XFER | ○ |
| ECHTZEIT EURO-UEBERW. | Instant payment (SCT Inst) | XFER | ★ |
| EURO-Überweisung | SEPA credit transfer | XFER | ★ |
| SEPA-Überweisung | SEPA transfer | XFER | ○ |
| Überweisung | Wire transfer | XFER | ○ |
| Gutschrift | General credit | CREDIT | ○ |
| Retouren | Returned goods / refund | CREDIT | ★ |
| Storno | Reversal | CREDIT | ○ |
| Gehalt/Rente | Salary / pension | DIRECTDEP | ★ |
| Bezüge | Salary / benefits | DIRECTDEP | ○ |
| Gebühren | Bank fees | SRVCHG | ★ |
| Entgelt | Charges / fees | SRVCHG | ○ |
| Abschluss | Quarterly settlement / interest | INT | ★ |
| Zinsen | Interest | INT | ○ |
| Zins/Divid. | Dividend / interest | DIV | ○ |
| Effekten | Securities purchase | DEBIT | ○ |
| Umbuchung | Internal transfer | XFER | ○ |
| Barauszahlung | Cash withdrawal | ATM | ○ |
| Bareinzahlung | Cash deposit | DEP | ○ |

ATM detection applies the same heuristics as the PDF parser — checking
`Verwendungszweck` and counterparty fields for BLZ format, VISA…SB, and
SB terminal indicators.


## Caveats

The transaction type mappings marked ○ in the PDF and CSV tables above have
been added on a best-effort basis using knowledge of the Consorsbank product
range and the OFX specification.  They have **not** been verified against real
statements, because the corresponding transaction types were not present in
the statements used to develop and test this plugin (Girokonto,
Tagesgeldkonto, and Verrechnungskonto PDF statements from 2016 to 2026, and
Girokonto CSV exports from 2025 to 2026).

If you encounter a transaction that is mapped to the wrong OFX type, or one
that produces an `Unknown transaction type` warning, please
[open an issue](https://github.com/eduralph/ofxstatement-consorsbank/issues)
and include:

- The transaction keyword (first word of the `Text/Verwendungszweck` column)
- The OFX type you would expect
- The statement type (Girokonto, Tagesgeldkonto, Verrechnungskonto, …)

You do not need to share any amounts, payee names, IBANs, or other personal
information — the keyword and statement type are sufficient.  Enable debug
logging (`ofxstatement -d convert …`) to extract the keyword safely.


## Installation

### Dependencies

- [ofxstatement](https://github.com/kedder/ofxstatement) — the conversion
  framework this plugin hooks into
- [pdfplumber](https://github.com/jsvine/pdfplumber) — PDF text extraction
  (pulls in `pdfminer.six` and `Pillow` automatically)

Both are declared as package dependencies and installed automatically.

```
pip install ofxstatement-consorsbank
```

Or from source:

```
git clone https://github.com/eduralph/ofxstatement-consorsbank
cd ofxstatement-consorsbank
python -m venv .venv
.venv/bin/pip install -e .
```


## Usage

Single file (PDF or CSV — detected by extension):

```
ofxstatement convert -t consorsbank statement.pdf statement.ofx
ofxstatement convert -t consorsbank Umsatzübersicht.csv statement.ofx
```

Multiple files:

```bash
for f in *.pdf *.csv; do
    ofxstatement convert -t consorsbank "$f" "${f%.*}.ofx"
done
```

The output file uses your IBAN as the account ID, so GnuCash will
automatically associate it with the correct account on re-import.
GnuCash deduplicates on the transaction ID, so re-importing a file
you have already imported is safe.

Enable debug logging to see exactly how the parser processes each line:

```
ofxstatement -d convert -t consorsbank statement.pdf statement.ofx
```

Debug output includes per-line state machine decisions, type resolution,
ATM overrides, and header field discovery — without logging any amounts,
payee names, or IBAN digits beyond the last four.


## Development setup

```
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pytest tests/
```


## Status

Tested against Consorsbank Girokonto, Tagesgeldkonto, and Verrechnungskonto
PDF statements from 2016 to 2026 (200+ files), and CSV exports from the
Consorsbank portal.
Feedback and pull requests welcome.


## License

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Copyright (C) 2026  Eduard Ralph
