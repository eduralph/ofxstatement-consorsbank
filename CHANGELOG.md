# Changelog

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
