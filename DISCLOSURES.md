# Disclosures

## AI assistance
Claude used for research and writing code purposes.

## Data

| Dataset | Source | Licence | Used for |
|---|---|---|---|
| Thehro synthetic world | our own simulator, `thehro-complete-code/thehro/thehro/simulate.py`, seed 7 | ours; published here | training, the alert lines, every Thehro result |
| PaySim | E. A. Lopez-Rojas, A. Elmir and S. Axelsson, *PaySim: A financial mobile money simulator for fraud detection*, 28th European Modeling and Simulation Symposium (EMSS), Larnaca, Cyprus, 2016. Kaggle: `ealaxi/paysim1` | **TO FILL** from the Kaggle page (our plan lists CC BY-SA 4.0; check it) | public benchmark of the method (`scripts/paysim_benchmark.py`); not redistributed, downloaded into the git-ignored `data/paysim/`. File used: `PS_20174392719_1491204439457_log.csv`, 6,362,620 transactions, SHA-256 `16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b` |

No real customer, payment or telecom data is used anywhere. Customer IDs, accounts and phone numbers are synthetic.

## Libraries

Python 3.13 with the exact versions in `thehro-complete-code/thehro/requirements-lock.txt`. Direct dependencies:
NumPy, pandas, scikit-learn 1.9.1 (pinned: the model bundle was saved with it), SciPy, Matplotlib, FastAPI,
Pydantic, Uvicorn, HTTPX and pytest, and segno 1.6.6 (BSD licence) to draw the QR code for the "open on a phone"
link. Playwright (`requirements-dev.txt`) only for the slide checker and our browser checks.

The judge page and the officer console are plain HTML, CSS and JavaScript with no libraries and nothing loaded from
the internet.

## Assets and other tools

- Fonts: the system fonts of the viewing device. No logos of real banks, NPCI, I4C or DoT.
- Phone signals in the demo: **set by hand**. A judge ticks "on a call", "internet call", "screen shared" and so on
  in judge mode, and we say the source is simulated. No app on the demo phone reads calls.
- Hindi strings in `thehro/explain.py` and the judge page. **TO FILL**: who checked them.
