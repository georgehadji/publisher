"""
Profile loader — versioned vendor specs as data, not code.

Reads YAML profile files from profiles/, validates each against
schemas/profile/profile.schema.json and fills in the schema's `default`
values, so every consumer reads a complete profile and restates no default of
its own. The schema and the YAML are the only sources of a profile fact.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import jsonschema
import yaml  # type: ignore

from publisher_stages import ErrorKind, StageError, with_schema_defaults

_PROFILES_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _PROFILES_DIR.parent / "schemas" / "profile" / "profile.schema.json"

# The profile a build uses when it names none. One name, loaded from its YAML;
# there is no inline copy of it anywhere.
DEFAULT_PROFILE_NAME = "Generic 6x9"


@lru_cache(maxsize=1)
def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _all_profiles() -> tuple[dict, ...]:
    """Every profile in profiles/*/*.yaml, validated and completed."""
    validator = jsonschema.Draft202012Validator(_schema())
    found = []
    for yaml_file in sorted(_PROFILES_DIR.glob("*/*.yaml")):
        for doc in yaml.safe_load_all(yaml_file.read_text(encoding="utf-8")):
            if not doc:
                continue
            errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
            if errors:
                raise ValueError(f"{yaml_file.name}: profile {doc.get('name')!r} is not a "
                                 f"valid profile/1: {errors[0].message}")
            found.append(with_schema_defaults(doc, _schema()))
    return tuple(found)


def load_profile(name: str) -> Optional[dict]:
    """The named profile, or None when no profile has that `name:`."""
    return next((copy.deepcopy(p) for p in _all_profiles() if p["name"] == name), None)


def list_profiles(vendor: Optional[str] = None) -> list[dict]:
    """All profiles, optionally filtered by vendor."""
    return [copy.deepcopy(p) for p in _all_profiles() if vendor is None or p["vendor"] == vendor]


def resolve_profile(name: Optional[str] = None) -> dict:
    """The profile a stage runs with: the named one, or DEFAULT_PROFILE_NAME.

    Every stage resolves "no profile" here, so they cannot disagree about it
    (design-compile and finish used bleed 0 while cover and preflight assumed
    Generic 6x9's 3 mm). An unknown name is bad input, not a silent default.
    """
    profile = load_profile(name or DEFAULT_PROFILE_NAME)
    if profile is None:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Unknown vendor profile: {name!r}. Profiles are loaded from "
                    f"profiles/*/*.yaml by their `name:` field.",
        )
    return profile


def get_default_profile() -> dict:
    """The profile used when a build names none."""
    return resolve_profile(None)
