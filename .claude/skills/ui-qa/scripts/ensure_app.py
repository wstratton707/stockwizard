"""
ensure_app.py — check whether the local QuantWizard app is up.

Prints READY if http://localhost:8501 responds healthy, else NOT-READY so the
caller knows to launch the Streamlit server first. No dependencies beyond stdlib.
"""
import sys
import urllib.request

URL = "http://localhost:8501/_stcore/health"

try:
    with urllib.request.urlopen(URL, timeout=3) as r:
        body = r.read().decode("utf-8", "replace").strip()
        if r.status == 200 and "ok" in body.lower():
            print("READY")
            sys.exit(0)
        print(f"NOT-READY (status {r.status}, body {body!r})")
        sys.exit(1)
except Exception as e:  # noqa: BLE001
    print(f"NOT-READY ({type(e).__name__}: {e}) — start the server first")
    sys.exit(1)
