"""
Puppeteer-Code
==============
A terminal-native coding engine orchestrated by the Puppeteer++ framework.

The orchestrator picks one specialised agent per step (scout, coder, test
runner, debugger, ...).  Tests are the ground truth: every edit runs in an
isolated git sandbox, improvements are checkpointed, regressions are rolled
back, and the user only ever sees a verified final diff.

Entry point: `python -m puppeteer_code --help` (or `pcode` once installed).
"""

import os as _os
import sys as _sys

# Make the sibling packages (core/, training/, extensions/) importable when
# running from a source checkout.
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

__version__ = "0.1.0"
