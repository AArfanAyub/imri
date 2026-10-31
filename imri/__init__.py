"""IMRI — offline library & resource-centre management for schools.

A single-process desktop application: Python owns the database, a loopback
HTTP server serves the JSON API and the static client, and a chromeless
browser window is the only view. No network, no installer, no dependencies
outside the Python 3.11 standard library.
"""

__version__ = "1.0.0"
__product__ = "IMRI"
__all__ = ["__version__", "__product__"]
