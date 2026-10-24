from __future__ import annotations


class GhostportError(Exception):
    def __init__(
        self,
        message: str,
        *,
        reason: str = "",
        profile_name: str = "",
        host: str = "",
        port: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason or message
        self.profile_name = profile_name
        self.host = host
        self.port = port

    def user_title(self) -> str:
        if self.profile_name:
            return f"Unable to connect to {self.profile_name}"
        return self.message

    def user_body(self) -> str:
        lines = [self.user_title(), ""]
        if self.host:
            lines.append(f"Host: {self.host}")
        if self.port is not None:
            lines.append(f"Port: {self.port}")
        if self.host or self.port is not None:
            lines.append("")
        lines.append("Reason:")
        lines.append(self.reason)
        return "\n".join(lines)


class ConnectionFailed(GhostportError):
    pass


class AuthenticationFailed(GhostportError):
    pass


class HostKeyUnknown(GhostportError):
    pass


class HostKeyMismatch(GhostportError):
    pass


class CredentialMissing(GhostportError):
    pass


class TransferFailed(GhostportError):
    pass


class ProfileNotFound(GhostportError):
    pass


class ProfileValidationError(GhostportError):
    pass


class HostKeyAborted(GhostportError):
    """User rejected the TOFU dialog. Not shown as a connection-error dialog."""
