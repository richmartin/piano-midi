# The panel lives in app.py. deploy.sh compiles it (and ui, layout, texts) to .mpy, because
# CircuitPython compiles a .py in RAM when it's imported, and the panel needs that RAM.
import app  # noqa: F401
