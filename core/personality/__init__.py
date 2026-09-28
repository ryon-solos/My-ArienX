"""Adaptive Personality Engine — public-safe, general-purpose, MARK-specific in
nothing but the base-identity reference.

High-level characteristics only (temperament, style, energy, ...). Never
stores or injects copyrighted dialogue, scripts, lyrics, catchphrases, or
imitations of exact source text — enforced at build time (caps + quote
scrub) and at render time (fixed safety footer).
"""

from .personality_profile import PersonalityProfile, SAFETY_FOOTER, PROFILE_FIELDS
from .personality_manager import PersonalityManager

__all__ = [
    "PersonalityProfile",
    "PersonalityManager",
    "SAFETY_FOOTER",
    "PROFILE_FIELDS",
]
