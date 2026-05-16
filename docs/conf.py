import sys
import os

sys.path.insert(0, os.path.abspath("../tracker/src"))

project = "Flight Tracker SG Bern"
copyright = "2026, Benjamin Bürgi"
author = "Benjamin Bürgi"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

autodoc_mock_imports = ["ogn"]

html_theme = "furo"

source_suffix = [".rst", ".md"]
