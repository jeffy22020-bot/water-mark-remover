@echo off
rem Run Watermark Remover from source on Windows (needs Python 3.12 from python.org).
cd /d "%~dp0"
if not exist .venv (
  echo Setting up for the first time - this downloads about 400 MB and takes a few minutes...
  py -3.12 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python scripts\fetch_models.py
  python scripts\make_samples.py
) else (
  call .venv\Scripts\activate.bat
)
start "" pythonw run_app.py %*
