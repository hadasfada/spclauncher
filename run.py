import sys

if sys.version_info < (3, 10):
    print(f"ERROR: Python 3.10+ is required. You are running Python {sys.version}.")
    print("Install Python 3.10+: https://www.python.org/downloads/")
    sys.exit(1)

import runpy
runpy.run_module("launcher.main", run_name="__main__")
