# Changelog

## [0.2.2] - 2026-04-12

### Fixed
- Opening balance for PDF statements now read from the authoritative
  `Buchungssaldo alt` / `Buchungssaldo neu` labels on page 1 instead of
  the first `*** Kontostand zum DD.MM. ***` running-day checkpoint.  The
  checkpoint carries the end-of-day balance after that day's transactions,
  so months with activity on the first day produced a wrong start balance
  and tripped ofxstatement's `start + sum(txns) == end` consistency check.
- Statement closing date now read from `Kontostand zum DD.MM.YY` on page 1.

## [0.2.1] - 2026-04-11

### Changed
- Unified `_txn_type` and `_csv_txn_type` into a single
  `_match_txn_type(text, type_map)` helper
- Extracted `_parse_german_amount` for shared German-locale number parsing
  between PDF and CSV parsers
- Replaced generic `ValueError` with ofxstatement's framework `ParseError`
  for format-mismatch errors
- Tightened type annotations (`List[tuple]` → `List[Tuple[str, str]]`)
- Added input validation to `_make_iban` for non-digit account numbers
- Narrowed exception clause in `_apply_balances`

### Documentation
- Added ★/○ confirmation markers to the CSV transaction type table in the
  README; 8 `Buchungstext` entries confirmed against real exports
  (Lastschrift, Dauerauftrag, ECHTZEIT EURO-UEBERW., EURO-Überweisung,
  Retouren, Gehalt/Rente, Gebühren, Abschluss), remaining 14 stay
  best-effort (○)
- Updated Caveats section to reference CSV exports

## [0.2.0] - 2026-04-06

### Added
- CSV import: parse semicolon-separated CSV exports from the Consorsbank portal
  (`Umsätze → Export → CSV`), detected automatically by `.csv` file extension
- `ConsorsCSVParser` with full transaction type mapping (`CSV_TXN_TYPE_MAP`),
  ATM detection, IBAN computation from account number, and end balance parsing
- Format validation: clear error messages when a file does not match the
  expected format for its extension (wrong extension, corrupt file, etc.)
- UTF-8 BOM handling: CSV files exported by the portal include a BOM;
  the parser strips it automatically via `utf-8-sig` encoding

### Fixed
- Stable transaction IDs for duplicate CSV rows (same date/amount/memo):
  row index is now included in the hash input to prevent ID collisions
- Homepage URL in `pyproject.toml` corrected to `eduralph/ofxstatement-consorsbank`

## [0.1.0] - 2026-04-01

### Added
- Initial release: PDF statement parser for Girokonto, Tagesgeldkonto, and
  Verrechnungskonto statements
- Transaction type mapping for all known Consorsbank PDF keywords
- ATM cash withdrawal detection via BLZ format, VISA…SB, SB terminal, and
  bank-name payee heuristics
- Stable transaction IDs (SHA-256 of date/amount/PNNr/memo)
- Balance checkpoint parsing (start/end balance and dates)
- Depot statement detection with clear warning (not supported)
