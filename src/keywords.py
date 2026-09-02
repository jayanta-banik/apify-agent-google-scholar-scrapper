"""Dependency-free keyword extraction over a small corpus of publication text.

The original prototype used KeyBERT, which drags a full sentence-transformers stack
(~2 GB of torch) into the actor image for a handful of short strings. This scores
unigrams and bigrams by frequency and length instead: far cheaper, and good enough
for labelling a researcher's current topics.
"""

from __future__ import annotations

import re
from collections import Counter

_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9'\-]+")
# Scholar titles use typographic dashes; fold them so "near-ground" stays one token.
_DASH_TRANSLATION = str.maketrans({dash: '-' for dash in '\u2010\u2011\u2012\u2013\u2014\u2212'})

STOP_WORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before being below between
    both but by can cannot could did do does doing down during each few for from further had has have having he her
    here hers herself him himself his how i if in into is it its itself journal let me more most my myself no nor not
    of off on once only or other ought our ours ourselves out over own proceedings same she should so some such than
    that the their theirs them themselves then there these they this those through to too under until up very via was
    we were what when where which while who whom why with would you your yours yourself yourselves using used use
    based new novel toward towards approach approaches method methods study studies analysis results international
    conference ieee acm springer elsevier vol pp preprint arxiv review letters transactions
    towards first second third case field work paper report application applications system systems
    """.split()
)


def extract_keywords(documents: list[str], top_n: int = 6) -> list[str]:
    """Return up to `top_n` keyword phrases describing the given documents."""
    if top_n <= 0:
        return []
    tokenized = [_tokenize(document) for document in documents]
    tokenized = [tokens for tokens in tokenized if tokens]
    if not tokenized:
        return []

    document_frequency: Counter[str] = Counter()
    position_bonus: dict[str, float] = {}
    for tokens in tokenized:
        bigrams = [f'{first} {second}' for first, second in zip(tokens, tokens[1:])]
        for phrases in (tokens, bigrams):
            for position, phrase in enumerate(dict.fromkeys(phrases)):
                document_frequency[phrase] += 1
                # Titles lead with their topic, so an earlier phrase breaks ties upward.
                position_bonus[phrase] = max(position_bonus.get(phrase, 0.0), _position_weight(position, len(phrases)))

    scored: dict[str, float] = {}
    for phrase, frequency in document_frequency.items():
        # A repeated multi-word phrase is the strongest signal; an unrepeated one is
        # weaker than a plain word, since every title yields dozens of throwaway pairs.
        weight = (2.2 if frequency > 1 else 0.55) if ' ' in phrase else 1.0
        scored[phrase] = weight * frequency + 0.25 * position_bonus[phrase]

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    return _deduplicate([phrase for phrase, _score in ranked], top_n)


def _position_weight(position: int, total: int) -> float:
    """Phrases near the front of a title carry the topic; later ones trail off."""
    return 1.0 - (position / max(total, 1))


def _tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_PATTERN.findall(text.lower().translate(_DASH_TRANSLATION)) if token not in STOP_WORDS and len(token) > 2]


def _deduplicate(phrases: list[str], top_n: int) -> list[str]:
    """Drop phrases already covered by a higher-ranked multi-word phrase."""
    selected: list[str] = []
    claimed_words: set[str] = set()
    for phrase in phrases:
        words = set(phrase.split())
        if words & claimed_words:
            continue
        selected.append(phrase)
        claimed_words |= words
        if len(selected) == top_n:
            break
    return selected
