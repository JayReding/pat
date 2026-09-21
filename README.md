# The Preference Analysis Tool (PAT)

This is a tool for analyzing preference claims in bankruptcy under Section 547 of the Bankruptcy Code. It analyzes common defenses such as the ordinary course of business defense and the new value defense.

PAT is in early development and should not be used for real-world use at this point.

**Current Features**
- Quick access to key defenses- analyze ordinary course, new value, and other defense easily and consistently.
- Case Insights - examine key statistics comparing the historical baseline to the preference period in one view.
- Handles creating historical baseline based on historical invoicing data.
- Gives statatistical insights on whether a standard deviation approach may be appropriate.
- Creates historical and preference-period weighted averages. This prevents small aberrant invoices from creating skews in the data analysis.
- Allows for custom ordinary course of business analysis, including presets for total range and 15 day range centered on the historical weighted average.
- Indicates whether a "total range" approach for ordinary course of business may be inappropriate.
- Calculates subsequent new value, including the interplay between ordinary course and new value defenses.
- Saves state of each case so that OCB ranges and other settings are preserved.
- Allows for analyzing the ordinary course by DSO (days an invoice is outstanding from the invoice date) or DPD (days an invoice is past due).
- Basic user management and administration.
- Basic firm management and administration.
- Can export charts in PDF or Excel for use as exhibits.
- Authorized users can back up and restore by main case or individual subcases.


**Future Features**
- Data entry tools.
- Guest access to particular subfiles.
- More robust multi-user support.
- Integrations with other software.

**Installation and Running**

Requires Python 3.11 or newer (developed and tested on Python 3.14).

```bash
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The admin password is randomly generated on startup and will appear in the console on first start. Please note that the password will only be shown once.

Then open http://127.0.0.1:8050 in a browser.

*Notes:*
- The SQLite databases (`pat_cases.db`, `pat_state.db`, `pat_users.db`) are created automatically on first run.
- The data files `courts.json` and `states.json` must stay in the same directory as `app.py`.
- The web UI loads the Bootswatch theme, Font Awesome, and Dash ag-grid JavaScript from a CDN at runtime, so internet access is needed unless those assets are vendored locally.


PAT is written in Python using the Dash library and should run on any platform that supports Python and Dash.

&copy;2026 Jay Reding
