"""QTranslator over the JSON catalogs (GUI only; the daemon stays Qt-free)."""
from __future__ import annotations

from PySide6.QtCore import QTranslator

from naga_config.i18n import catalog, lookup


class JsonTranslator(QTranslator):
    def __init__(self, lang: str, parent=None):
        super().__init__(parent)
        self.lang = lang

    def translate(self, context, source, disambiguation=None, n=-1):  # noqa: ARG002
        # None (a null QString) lets Qt fall back to the source text
        return lookup(self.lang, context, source)

    def isEmpty(self) -> bool:
        return not catalog(self.lang)

    def language(self) -> str:
        return self.lang
