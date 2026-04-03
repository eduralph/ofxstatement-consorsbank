~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Consorsbank plugin for ofxstatement
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Converts **Consorsbank** (BNP Paribas Germany) PDF bank statements to OFX
format for import into GnuCash or other personal finance software.

`ofxstatement`_ is a tool to convert proprietary bank statements to OFX format.

.. _ofxstatement: https://github.com/kedder/ofxstatement


Supported statement types
=========================

* **Girokonto** (current account)
* **Tagesgeldkonto** (savings account)

The plugin parses the standard PDF exported from the Consorsbank online portal.
The PDF must have a text layer (i.e. not a scanned image); all PDFs downloaded
directly from the portal qualify.

Transaction types handled:

* LASTSCHRIFT (direct debit) — including VISA card charges (PNNr 8999)
* EURO-UEBERW. (SEPA credit transfer, debit and credit)
* GIROCARD (debit card)
* DAUERAUFTRAG (standing order)
* GEHALT/RENTE (salary / pension)
* GEBUEHREN / ENTGELT (fees and charges)
* GUTSCHRIFT (credit)


Installation
============

::

  $ pip install ofxstatement-consorsbank

Or from source::

  $ git clone https://github.com/eduralph/ofxstatement-consorsbank
  $ cd ofxstatement-consorsbank
  $ python -m venv .venv
  $ .venv/bin/pip install -e .


Usage
=====

::

  $ ofxstatement convert -t consorsbank statement.pdf statement.ofx

The output file uses your IBAN as the account ID, so GnuCash will
automatically associate it with the correct account on re-import.


Development setup
=================

::

  $ python -m venv .venv
  $ .venv/bin/pip install -e ".[dev]"

Run the unit tests::

  $ .venv/bin/pytest tests/

To run the full integration test, place a real statement PDF at
``tests/statement.pdf`` and run::

  $ .venv/bin/pytest tests/ -v

To inspect the raw pdfplumber text extraction alongside the parsed
transactions::

  $ .venv/bin/python debug_pdf.py tests/statement.pdf


Status
======

Early development — tested against January 2026 Girokonto statements.
Feedback and pull requests welcome.
