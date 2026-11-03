"""Regras puras de pontuação (sem Discord, fáceis de testar)."""
from __future__ import annotations

import colorsys
import math

LOW_ACTIVITY_CAP = 3


def compute_level(given: int, received: int, min_activity: int, target_ratio: float = 1.0,
                  boost: float = 1.0) -> int:
    """Converte participação em nível de 1 a 10.

    - given:    publicações de outros membros que a pessoa comentou (janela móvel)
    - received: membros diferentes que comentaram nas publicações dela, somados por publicação
    - Quem deu menos que `min_activity` comentários fica no máximo no nível 3.
    - Acima disso, o nível é proporcional a given/received; a razão `target_ratio`
      (padrão 1,0 = dá tanto quanto recebe) já vale nível 10.
    """
    if given <= 0:
        return 1
    if received <= 0:
        level = 10
    else:
        ratio = given / received * boost   # boost: bônus de quem avalia artistas (1x a 1,5x)
        level = math.floor((ratio / target_ratio) * 10 + 0.5)
    level = max(1, min(10, level))
    if given < min_activity:
        level = min(level, LOW_ACTIVITY_CAP)
    return level


def ratio_of(given: int, received: int) -> float:
    if received <= 0:
        return float(given) if given else 0.0
    return given / received


def level_color(level: int) -> int:
    """Cor do cargo: vermelho (1) até verde (10)."""
    t = (max(1, min(10, level)) - 1) / 9
    hue = (0 + t * 135) / 360          # 0° vermelho -> 135° verde
    r, g, b = colorsys.hsv_to_rgb(hue, 0.72, 0.86)
    return (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)


LEVEL_GLYPHS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def level_role_name(level: int) -> str:
    return f"{LEVEL_GLYPHS[level - 1]} Lv.{level}"


def progress_bar(level: int, width: int = 10) -> str:
    filled = max(0, min(width, level))
    return "█" * filled + "░" * (width - filled)


def recruiter_score(active: int, verified_inactive: int) -> float:
    """Recrutado ativo vale 1 ponto; recrutado verificado mas inativo vale 0,25."""
    return active + 0.25 * verified_inactive


RATING_BOOST_MAX = 1.5


def rating_boost(rated: int, to_rate: int) -> float:
    """1x (não avaliou ninguém) até 1,5x (avaliou todos os artistas disponíveis no momento)."""
    if to_rate <= 0 or rated <= 0:
        return 1.0
    return 1.0 + (RATING_BOOST_MAX - 1.0) * min(1.0, rated / to_rate)


# ---------- Artist Ratings ----------
RATING_WEIGHTS = {"execution": 0.35, "interpretation": 0.30, "arrangement": 0.20, "technical": 0.15}
RATING_MIN_RATERS = 5      # antes disso a classificação é provisória (e as notas ficam escondidas)
RATING_PRIOR = 5           # média ajustada: peso da média geral (evita que um único 5/5 domine)


def weighted(r) -> float:
    return sum(float(r[k]) * w for k, w in RATING_WEIGHTS.items())


def adjusted(scores: list[float], global_mean: float, prior: int = RATING_PRIOR) -> float:
    n = len(scores)
    return (prior * global_mean + sum(scores)) / (prior + n) if n else global_mean
