from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any


class AuthMethod(str, Enum):
    PASSWORD = "password"
    PRIVATE_KEY = "private_key"
    AGENT = "agent"


def coerce_auth_method(value: object) -> AuthMethod:
    """Qt combo currentData() often returns a plain str, not the Enum member."""
    if isinstance(value, AuthMethod):
        return value
    if isinstance(value, str):
        try:
            return AuthMethod(value)
        except ValueError:
            pass
    return AuthMethod.PASSWORD


@dataclass
class ServerProfile:
    id: str
    name: str
    host: str
    port: int = 22
    username: str = ""
    description: str = ""
    auth_method: AuthMethod = AuthMethod.PASSWORD
    private_key_path: str | None = None
    secret_ref: str | None = None
    favorite: bool = True
    sort_index: int = 0
    keep_alive: bool = True
    keep_alive_interval_sec: int = 30
    timeout_sec: int = 10
    term_type: str = "xterm-256color"
    initial_directory: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["auth_method"] = self.auth_method.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerProfile:
        names = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in names}
        method = filtered.get("auth_method", AuthMethod.PASSWORD)
        if isinstance(method, str):
            filtered["auth_method"] = AuthMethod(method)
        if filtered.get("private_key_path") == "":
            filtered["private_key_path"] = None
        if filtered.get("secret_ref") == "":
            filtered["secret_ref"] = None
        return cls(**filtered)

    def display_target(self) -> str:
        user = self.username or "?"
        return f"{user}@{self.host}:{self.port}"
