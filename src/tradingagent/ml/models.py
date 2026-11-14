"""Die beiden Lernmodelle und die Kalibrierung.

* SVM: Standardisierung → RBF-Kern (Nyström-Näherung, damit es mit 100.000+ Zeilen auf 2 Kernen läuft)
  → lineare Support Vector Machine. Liefert einen Abstand zur Trennfläche, keine Wahrscheinlichkeit.
* Entscheidungsbaum: begrenzte Tiefe und Mindestgröße der Blätter gegen Überanpassung.
* Grundrate: sagt immer den Anteil positiver Fälle aus dem Training voraus – Messlatte für „lernt etwas“.

Alle Modelle werden mit Platt-Skalierung kalibriert: eine logistische Kurve über den Modell-Score, angepasst
auf einem EIGENEN, späteren Zeitraum (nie auf den Trainingsdaten). Erst danach ist die Ausgabe eine
Wahrscheinlichkeit, deren Qualität in den Prüfjahren gemessen wird.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.kernel_approximation import Nystroem
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier

MODEL_NAMES = {"svm": "SVM", "tree": "Entscheidungsbaum", "base": "Grundrate"}


def svm_grid(level: int = 1) -> list[dict[str, Any]]:
    """Ausbaustufe 1 ist schnell; höhere Stufen nutzen mehr Kern-Komponenten (genauer, rechenintensiver)."""
    comps = {1: 300, 2: 800, 3: 1500, 4: 2500}[level]
    return [{"C": c, "gamma": g, "components": comps} for c in (0.01, 0.1) for g in (0.5, 1.0)]


def tree_grid(level: int = 1) -> list[dict[str, Any]]:
    depths = (3, 4, 5, 6) if level == 1 else (3, 4, 5, 6, 7, 8)
    leaves = (300, 1000) if level == 1 else (150, 300, 1000, 3000)
    return [{"max_depth": d, "min_samples_leaf": m} for d in depths for m in leaves]


def make(kind: str, params: dict[str, Any], n_features: int, seed: int) -> Any:
    if kind == "svm":
        return Pipeline([
            ("scale", StandardScaler()),
            ("kernel", Nystroem(kernel="rbf", gamma=params["gamma"] / n_features, n_components=params["components"], random_state=seed)),
            ("svm", LinearSVC(C=params["C"], dual="auto", max_iter=4000, random_state=seed)),
        ])
    if kind == "tree":
        return DecisionTreeClassifier(max_depth=params["max_depth"], min_samples_leaf=params["min_samples_leaf"], random_state=seed)
    raise ValueError(kind)


def raw_score(model: Any, kind: str, X: np.ndarray) -> np.ndarray:
    if kind == "svm":
        return model.decision_function(X)
    p = model.predict_proba(X)[:, 1]
    eps = 1e-4
    return np.log((p + eps) / (1 - p + eps))  # Logit der Blatt-Häufigkeit


@dataclass
class Calibrated:
    """Fertiges Modell: Rohmodell + Platt-Kalibrierung. predict() liefert P(positiv)."""
    kind: str
    params: dict[str, Any]
    model: Any = None
    platt: Any = None
    base_rate: float = 0.5
    meta: dict[str, Any] = field(default_factory=dict)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.kind == "base":
            return np.full(len(X), self.base_rate)
        s = raw_score(self.model, self.kind, X).reshape(-1, 1)
        return self.platt.predict_proba(s)[:, 1]


def fit_calibrated(kind: str, params: dict[str, Any], X_fit: np.ndarray, y_fit: np.ndarray, X_cal: np.ndarray,
                   y_cal: np.ndarray, seed: int) -> Calibrated:
    base = float(np.mean(y_fit))
    if kind == "base":
        return Calibrated("base", {}, base_rate=base)
    m = make(kind, params, X_fit.shape[1], seed)
    m.fit(X_fit, y_fit)
    platt = LogisticRegression(C=1e4, max_iter=1000)
    platt.fit(raw_score(m, kind, X_cal).reshape(-1, 1), y_cal)
    return Calibrated(kind, params, m, platt, base)
