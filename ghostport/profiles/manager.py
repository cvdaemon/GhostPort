from __future__ import annotations

import json
import logging
from pathlib import Path

from ghostport.config.paths import AppPaths
from ghostport.config.settings import atomic_write_json, quarantine_corrupt
from ghostport.errors import ProfileNotFound
from ghostport.profiles.models import ServerProfile

log = logging.getLogger("ghostport.profiles")


class ProfileManager:
    """JSON-only persistence. Does not touch CredentialStore."""

    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._profiles: dict[str, ServerProfile] = {}
        self.load_warning: str | None = None

    def load(self) -> None:
        self.load_warning = None
        self._profiles = {}
        path = self._paths.profiles_file
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("profiles.json root is not an object")
            items = raw.get("profiles") or []
            if not isinstance(items, list):
                raise ValueError("profiles.json 'profiles' is not a list")
            for item in items:
                if not isinstance(item, dict):
                    continue
                profile = ServerProfile.from_dict(item)
                self._profiles[profile.id] = profile
        except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
            dest = quarantine_corrupt(path)
            self.load_warning = (
                f"profiles.json was corrupt and was moved to {dest.name if dest else 'backup'}."
            )
            log.exception("Failed to load profiles from %s: %s", path, exc)
            self._profiles = {}

    def all(self) -> list[ServerProfile]:
        return sorted(
            self._profiles.values(),
            key=lambda p: (not p.favorite, p.sort_index, p.name.lower()),
        )

    def favorites(self) -> list[ServerProfile]:
        return [p for p in self.all() if p.favorite]

    def get(self, profile_id: str) -> ServerProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ProfileNotFound(
                f"Server profile {profile_id} was not found",
                reason="The saved server no longer exists.",
            ) from exc

    def upsert(self, profile: ServerProfile) -> ServerProfile:
        self._profiles[profile.id] = profile
        self._save()
        return profile

    def delete(self, profile_id: str) -> None:
        if profile_id not in self._profiles:
            raise ProfileNotFound(
                f"Server profile {profile_id} was not found",
                reason="The saved server no longer exists.",
            )
        del self._profiles[profile_id]
        self._save()

    def reorder(self, ordered_ids: list[str]) -> None:
        seen: set[str] = set()
        index = 0
        for profile_id in ordered_ids:
            if profile_id in seen or profile_id not in self._profiles:
                continue
            seen.add(profile_id)
            self._profiles[profile_id].sort_index = index
            index += 1
        for profile in self._profiles.values():
            if profile.id not in seen:
                profile.sort_index = index
                index += 1
        self._save()

    def search(self, query: str) -> list[ServerProfile]:
        needle = query.strip().lower()
        if not needle:
            return self.all()
        hits: list[ServerProfile] = []
        for profile in self.all():
            haystack = " ".join(
                [
                    profile.name,
                    profile.host,
                    profile.username,
                    profile.description,
                    str(profile.port),
                ]
            ).lower()
            if needle in haystack:
                hits.append(profile)
        return hits

    def _save(self) -> None:
        payload = {
            "version": 1,
            "profiles": [p.to_dict() for p in self.all()],
        }
        atomic_write_json(self._paths.profiles_file, payload)
