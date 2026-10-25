# Runs once at power-up, before code.py. Turns on CircuitPython's second USB serial port: the
# first stays the REPL console (for debugging), the second is the link to pianod on the Pi.
import usb_cdc

usb_cdc.enable(console=True, data=True)
