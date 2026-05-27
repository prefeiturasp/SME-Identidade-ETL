# Arquivo de configuracao do builder de documentacao Sphinx.
#
# Para a lista completa de valores de configuracao embutidos, veja a documentacao:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
import django

# Torna o projeto importavel
sys.path.insert(0, os.path.abspath("../.."))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings_test")
django.setup()

# -- Project information -----------------------------------------------------
project = "SME-Identidade-ETL"
copyright = "2026, SME-SP"
author = "SME-SP"
release = "1.0.0"

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
]

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
    "exclude-members": "queryset,serializer_class,permission_classes,filter_backends",
}

napoleon_google_docstring = False
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "django": ("https://docs.djangoproject.com/en/5.1/", "https://docs.djangoproject.com/en/5.1/_objects/"),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "pt_BR"

# -- Options for HTML output -------------------------------------------------
html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
