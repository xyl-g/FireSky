"""Compatibility module for the legacy Qt frontend."""

import sys

from firesky import desktop_data

sys.modules[__name__] = desktop_data
