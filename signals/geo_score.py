"""
signals/geo_score.py
====================
Geopolitical Risk Score via NLP para Vantage Quant System.

Converte headlines de news em score 0.0-1.0 de risco geopolítico.
Score > 0.85 → Risk Engine reduz leverage 75% automaticamente.

Uso:
    from signals.geo_score import GeoScorer
    scorer = GeoScorer()
    score = scorer.score(["Fed raises rates", "Iran closes Hormuz"])
"""

import re
from dataclasses import dataclass
from typing import List, Dict
from datetime import datetime


@dataclass
class GeoResult:
    score: float            # 0.0 (paz) a 1.0 (crise máxima)
    level: str              # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    triggers: List[str]     # keywords que dispararam o score
    dominant_theme: str     # tema principal detectado
    lever_action: str       # ação recomendada ao Risk Engine
    timestamp: datetime


class GeoScorer:
    """
    Score geopolítico baseado em keyword weighting por categoria.
    Produção: substituir por modelo NLP (BERT/FinBERT) com feed Reuters/Bloomberg.
    """

    KEYWORDS: Dict[str, Dict[str, float]] = {
        "war_conflict": {
            "war": 0.90, "airstrike": 0.85, "invasion": 0.90,
            "missile": 0.80, "nuclear": 0.95, "ceasefire": -0.30,
            "attack": 0.75, "troops": 0.60, "military": 0.55,
            "conflict": 0.65, "bomb": 0.80, "explosion": 0.70,
        },
        "energy_supply": {
            "hormuz": 0.85, "strait": 0.70, "pipeline": 0.60,
            "oil embargo": 0.80, "opec cut": 0.65, "supply shock": 0.75,
            "energy crisis": 0.70, "refinery": 0.50, "lng": 0.45,
        },
        "monetary_shock": {
            "emergency rate": 0.70, "currency crisis": 0.75,
            "bank run": 0.80, "default": 0.75, "hyperinflation": 0.80,
            "fed emergency": 0.85, "boj intervention": 0.70,
            "devaluation": 0.65, "capital controls": 0.75,
        },
        "trade_sanctions": {
            "sanctions": 0.65, "tariffs": 0.45, "trade war": 0.60,
            "export ban": 0.65, "chip ban": 0.55, "decoupling": 0.50,
            "embargo": 0.70, "blacklist": 0.55,
        },
        "political_instability": {
            "coup": 0.80, "election fraud": 0.55, "impeachment": 0.50,
            "protest": 0.40, "riot": 0.60, "assassination": 0.85,
            "government collapse": 0.75, "martial law": 0.85,
        },
        "taiwan_china": {
            "taiwan strait": 0.90, "pla exercises": 0.85,
            "taiwan invasion": 0.95, "china blockade": 0.90,
            "tsmc": 0.60, "taiwan independence": 0.70,
        },
    }

    LEVEL_MAP = [
        (0.85, "CRITICAL", "REDUCE_LEVERAGE_75PCT"),
        (0.65, "HIGH",     "REDUCE_LEVERAGE_50PCT"),
        (0.40, "MEDIUM",   "REDUCE_LEVERAGE_25PCT"),
        (0.00, "LOW",      "NORMAL"),
    ]

    def score(self, headlines: List[str]) -> GeoResult:
        text = " ".join(headlines).lower()
        # Remove pontuação
        text = re.sub(r"[^\w\s]", " ", text)

        raw_scores: Dict[str, float] = {}
        triggers: List[str] = []

        for category, kw_map in self.KEYWORDS.items():
            cat_score = 0.0
            for kw, weight in kw_map.items():
                if kw in text:
                    cat_score += weight
                    triggers.append(kw)
            raw_scores[category] = min(cat_score, 1.0)

        # Score final: média ponderada (conflito e energia pesam mais)
        weights = {
            "war_conflict": 0.30, "energy_supply": 0.20,
            "monetary_shock": 0.15, "trade_sanctions": 0.12,
            "political_instability": 0.13, "taiwan_china": 0.10,
        }
        final = sum(raw_scores[c] * w for c, w in weights.items())
        final = min(round(final, 4), 1.0)

        # Tema dominante
        dominant = max(raw_scores, key=raw_scores.get) if raw_scores else "none"

        # Level e ação
        level, action = "LOW", "NORMAL"
        for threshold, lvl, act in self.LEVEL_MAP:
            if final >= threshold:
                level, action = lvl, act
                break

        return GeoResult(
            score=final, level=level, triggers=list(set(triggers)),
            dominant_theme=dominant, lever_action=action,
            timestamp=datetime.utcnow()
        )


# ======================================================================
# TESTE LOCAL
# ======================================================================
if __name__ == "__main__":
    scorer = GeoScorer()

    scenarios = [
        ("Cenário Calmo", [
            "Fed holds rates steady", "EU GDP grows 0.2%",
            "Dollar edges lower on soft data"
        ]),
        ("Tensão Moderada", [
            "Iran threatens to close Hormuz strait",
            "Oil surges on supply fears", "BoJ considers rate hike"
        ]),
        ("Crise Alta", [
            "PLA military exercises near Taiwan strait",
            "US imposes chip ban on China", "Oil embargo announced",
            "Emergency Fed meeting called", "Bank run fears in Asia"
        ]),
    ]

    print("\n" + "="*55)
    print("  GEO SCORE — Teste de cenários")
    print("="*55)
    for name, headlines in scenarios:
        r = scorer.score(headlines)
        print(f"\n  [{name}]")
        print(f"  Score    : {r.score:.4f}")
        print(f"  Level    : {r.level}")
        print(f"  Ação     : {r.lever_action}")
        print(f"  Tema     : {r.dominant_theme}")
        print(f"  Triggers : {r.triggers[:5]}")
    print("\n  ✅ GeoScorer OK\n")
