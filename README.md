# The Preference Analysis Tool

This is a tool for analyzing preference claims in bankruptcy under Section 547 of the Bankruptcy Code. It analyzes common defenses such as the ordinary course of business defense and the new value defense.

PAT is in early development and should not be used for real-world use at this point. As PAT develops, new features will be added.

**Current Features**
- Handles creating historical baseline based on historical invoicing data.
- Gives statatistical insights on whether a standard deviation approach may be appropriate.
- Creates historical and preference-period weighted averages. This prevents small aberrant invoices from creating 
- Allows for custom ordinary course of business analysis, including presets for total range and 15 day range centered on the historical weighted average.
- Indicates whether a "total range" approach for ordinary course of business may be inappropriate.
- Calculates subsequent new value, including the interplay between ordinary course and new value defenses.
- Saves state of each case so that OCB ranges and other settings are preserved.

**Future Features**
- User management and administration
- Data entry tools
- Integrations with other software.


PAT is written in Python using the Dash library and should run on any platform that supports Python and Dash.

(c)2026 Reding Law PLLC 
