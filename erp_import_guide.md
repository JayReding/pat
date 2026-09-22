# ERP Import Guide — getting invoice/payment data into PAT

PAT analyzes **invoice↔payment pairs**: every row needs an invoice (number,
amount, date) and — unless it is an unpaid new-value shipment — its payment
(transfer/payment number, amount, date). Dates are the critical fields; terms,
due dates, and timing metrics are derived automatically when missing.

No connector or proprietary tooling is required. Every major ERP/accounting
system below can export CSV (or Excel, which PAT also reads from the same
flow) natively. Export, upload on the Data Import page, check the
auto-detected column mapping, Validate & Preview, then confirm.

## General rules for any system

- Prefer **one row per invoice-payment pairing**. If an invoice was partially
  paid, one row per payment application is fine.
- Required columns: invoice number, invoice amount, invoice date, payment
  date, payment/transfer amount. A payment/transfer reference is strongly
  recommended (PAT auto-generates one if blank, but grouping suffers).
- Amounts may carry `$`, commas, or `(parentheses)` negatives. Dates may be
  `YYYY-MM-DD`, `MM/DD/YYYY`, `DD/MM/YYYY` (pick the matching Date format on
  the import page), or Excel serials.
- Two-file mode: upload an **invoices file** and a **payments file**; PAT
  joins them on the invoice reference you pick. Invoices with no matching
  payment are imported as **unpaid (new value)** rows.

## NetSuite

Single joined file via a Transaction saved search
(`Reports > Saved Searches > All Saved Searches > New > Transaction`):

- Criteria: `Type is Invoice`, plus `Main Line is false` (one row per line)
  or a second search on `Customer Payment` with `Applied To Transaction`
  fields for the payment side. For a one-shot joined export, include both
  sets of columns below.
- Results columns: `Document Number` (= invoice no), `Date` (= invoice date),
  `Amount`, `Terms`, `Due Date`, `Payment Date` / `Applied To Transaction`
  date, `Payment Amount` / `Applied Amount`, `Payment Number`.
- `Export - CSV` from the search results. Amounts export clean; dates export
  as `M/D/YYYY`, which PAT parses.

## Salesforce

From a tabular/tabular-joined report on your Invoice and Payment objects
(field names vary by org and billing package — FinancialForce, etc.):

- Report columns: invoice number, invoice date, invoice amount, due date,
  payment terms, payment number, payment date, payment amount.
- Run the report, `Export > Details Only`, format CSV or Excel.
- Or use `Setup > Data Export` for a full-object CSV dump and trim it in a
  spreadsheet first.

## QuickBooks Online

- Invoices: `Sales > Invoices`, customize columns (Num, Date, Customer,
  Amount, Terms, Due Date), `Export to Excel`/print to CSV.
- Payments: `Sales > All Sales > Payments`, export Payment Date, Payment no,
  Amount Received, and the applied-to Invoice no.
- Import via two-file mode, joining on the invoice number. Alternatively
  use `Reports > Transaction Detail by Account` (Accounts Receivable) for a
  near-joined view and import it as a single file.

## Xero

- Invoices: `Business > Invoices`, `Export` (Invoice Number, Invoice Date,
  Due Date, Total). Amounts and dates are clean.
- Payments show per invoice (`Amount Paid`, `Date Paid`) — include them for
  single-file import, or export the payment list separately and use two-file
  mode joined on Invoice Number.

## Dynamics 365 / Business Central

- Invoices: posted sales invoices list or an AR aging/detail report with
  Invoice no., Posting Date, Amount, Payment Terms, Due Date.
- Payments: customer ledger entries / applied payments with Payment Date,
  Amount Settled, and the Applies-to Invoice no.
- `Open in Excel` / export to CSV, then single-file or two-file import
  joined on the invoice number.

## SAP (ECC / S/4HANA)

- Customer line items (`FBL5N` or Fiori `Manage Customer Line Items`):
  document number (= invoice), posting/document date, amount, payment terms,
  and crucially the **clearing document + clearing date** — the clearing date
  IS the payment date, so this report is nearly joined already.
- `Export > To Spreadsheet` (XLSX). Map clearing document → Transfer Number
  and clearing date → Payment Date.

## After import

The preview step shows historical/preference/new-value counts against the
main case's petition date. If counts look wrong, the usual causes are date
columns mapped month-first vs day-first, or amounts in mixed currencies
(PAT assumes a single currency per import).
