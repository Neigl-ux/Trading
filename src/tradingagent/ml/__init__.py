"""Phase 6 – eigene Lernmodelle (SVM und Entscheidungsbaum), kostenlos und lokal auf dem Server.

Module:
  features   Eingangsgrößen und Zielgröße aus Tageskerzen (nur Vergangenheitsdaten)
  models     SVM, Entscheidungsbaum, Grundrate; Platt-Kalibrierung
  evaluate   Walk-Forward mit Purging, Prognose- und Handelskennzahlen, Zulassung
  registry   Modellversionen (joblib + SQLite), Champion-Wechsel nur nach bestandener Prüfung
  earnings   Quartalszahlen-Kalender und Branchenhinweise (austauschbare Datenquellen)
  service    Befehle train / predict / earnings / worker (Dauerdienst auf dem Server)
"""
from pathlib import Path

import yaml

from ..config import ROOT

CONFIG_FILE = ROOT / "config" / "ml.yaml"


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONFIG_FILE).read_text(encoding="utf-8"))
