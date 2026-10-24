from __future__ import annotations

import logging
import socket
import uuid
from dataclasses import dataclass

from paramiko.pkey import PKey
from paramiko.ssh_exception import AuthenticationException, SSHException
from paramiko.transport import Transport

from ghostport.errors import (
    AuthenticationFailed,
    ConnectionFailed,
    CredentialMissing,
    HostKeyAborted,
)
from ghostport.profiles.models import AuthMethod, ServerProfile
from ghostport.security.credentials import CredentialStore
from ghostport.ssh.agent import auth_with_agent, load_private_key
from ghostport.ssh.known_hosts import (
    HostKeyDecision,
    HostKeyGate,
    HostKeyKind,
    HostKeyPrompt,
    KnownHostsStore,
    fingerprint_sha256,
)

log = logging.getLogger("ghostport.ssh")


@dataclass
class AuthMaterial:
    method: AuthMethod
    password: str | None = None
    pkey: PKey | None = None
    use_agent: bool = False


class HostKeyEmitter:
    """Protocol-like callable used by the worker to emit a HostKeyPrompt."""

    def emit(self, prompt: HostKeyPrompt) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def load_auth_material(profile: ServerProfile, store: CredentialStore) -> AuthMaterial:
    if profile.auth_method is AuthMethod.AGENT:
        return AuthMaterial(method=AuthMethod.AGENT, use_agent=True)
    secret: str | None = None
    if profile.secret_ref:
        secret = store.get_secret(profile.secret_ref)
    if profile.auth_method is AuthMethod.PASSWORD:
        if not secret:
            raise CredentialMissing(
                "Saved credentials were not found.",
                reason=(
                    "Saved credentials were not found on this Windows account. "
                    "Edit the server and re-enter the password/passphrase."
                ),
                profile_name=profile.name,
                host=profile.host,
                port=profile.port,
            )
        return AuthMaterial(method=AuthMethod.PASSWORD, password=secret)
    if not profile.private_key_path:
        raise AuthenticationFailed(
            "Authentication failed.",
            reason="No private key path is saved on this profile.",
            profile_name=profile.name,
            host=profile.host,
            port=profile.port,
        )
    pkey = load_private_key(profile.private_key_path, secret)
    return AuthMaterial(method=AuthMethod.PRIVATE_KEY, pkey=pkey)


def translate_connect_error(
    exc: BaseException,
    profile: ServerProfile,
) -> ConnectionFailed | AuthenticationFailed | CredentialMissing | HostKeyAborted:
    host, port, name = profile.host, profile.port, profile.name
    if isinstance(exc, (ConnectionFailed, AuthenticationFailed, CredentialMissing, HostKeyAborted)):
        if not exc.profile_name:
            exc.profile_name = name
        if not exc.host:
            exc.host = host
        if exc.port is None:
            exc.port = port
        return exc
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return ConnectionFailed(
            "Unable to connect",
            reason="Connection timed out.",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, ConnectionRefusedError):
        return ConnectionFailed(
            "Unable to connect",
            reason="Connection refused.",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, socket.gaierror):
        return ConnectionFailed(
            "Unable to connect",
            reason=f"DNS lookup failed: {exc}",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, AuthenticationException):
        return AuthenticationFailed(
            "Authentication failed.",
            reason="Check username, password, or private key.",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, EOFError):
        return ConnectionFailed(
            "Unable to connect",
            reason="EOF during SSH banner. The host closed the connection.",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, SSHException):
        return ConnectionFailed(
            "Unable to connect",
            reason=str(exc) or "SSH protocol error.",
            profile_name=name,
            host=host,
            port=port,
        )
    if isinstance(exc, OSError):
        return ConnectionFailed(
            "Unable to connect",
            reason=str(exc) or "Network error.",
            profile_name=name,
            host=host,
            port=port,
        )
    return ConnectionFailed(
        "Unable to connect",
        reason=str(exc) or exc.__class__.__name__,
        profile_name=name,
        host=host,
        port=port,
    )


def open_authenticated_transport(
    profile: ServerProfile,
    credentials: CredentialStore,
    known_hosts: KnownHostsStore,
    *,
    timeout_sec: float,
    host_key_gate: HostKeyGate,
    emit_prompt,
) -> Transport:
    """TCP + banner + TOFU + auth. Worker thread only."""
    timeout = float(timeout_sec)
    sock: socket.socket | None = None
    transport: Transport | None = None
    try:
        log.info(
            "Connecting to %s host=%s port=%s auth=%s",
            profile.name,
            profile.host,
            profile.port,
            profile.auth_method.value,
        )
        sock = socket.create_connection((profile.host, profile.port), timeout=timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            log.debug("TCP_NODELAY not set", exc_info=True)
        transport = Transport(sock)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.start_client(timeout=timeout)
        remote = transport.get_remote_server_key()
        verdict = known_hosts.lookup(profile.host, profile.port, remote)
        if verdict != "match":
            stored_type, stored_fp = known_hosts.stored_identity(profile.host, profile.port)
            kind = HostKeyKind.UNKNOWN if verdict == "unknown" else HostKeyKind.MISMATCH
            prompt = HostKeyPrompt(
                prompt_id=str(uuid.uuid4()),
                kind=kind,
                host=profile.host,
                port=profile.port,
                hostname_key=known_hosts.hostname_key(profile.host, profile.port),
                key_type=remote.get_name(),
                fingerprint_sha256=fingerprint_sha256(remote),
                stored_key_type=stored_type,
                stored_fingerprint_sha256=stored_fp,
            )
            host_key_gate.begin(prompt.prompt_id)
            emit_prompt(prompt)
            decision = host_key_gate.wait(prompt.prompt_id)
            if decision is HostKeyDecision.TRUST:
                known_hosts.remember(profile.host, profile.port, remote)
            elif decision is HostKeyDecision.REPLACE:
                known_hosts.replace(profile.host, profile.port, remote)
            else:
                transport.close()
                raise HostKeyAborted(
                    "Host key rejected.",
                    reason="The host key was not trusted.",
                    profile_name=profile.name,
                    host=profile.host,
                    port=profile.port,
                )
            if kind is HostKeyKind.UNKNOWN:
                log.info(
                    "Trusted new host key for %s type=%s fp=%s",
                    prompt.hostname_key,
                    prompt.key_type,
                    prompt.fingerprint_sha256,
                )
            else:
                log.info(
                    "Replaced host key for %s type=%s fp=%s",
                    prompt.hostname_key,
                    prompt.key_type,
                    prompt.fingerprint_sha256,
                )

        material = load_auth_material(profile, credentials)
        try:
            if material.use_agent:
                auth_with_agent(transport, profile.username)
            elif material.method is AuthMethod.PASSWORD:
                assert material.password is not None
                transport.auth_password(profile.username, material.password)
            elif material.pkey is not None:
                transport.auth_publickey(profile.username, material.pkey)
            else:
                raise AuthenticationFailed(
                    "Authentication failed.",
                    reason="Check username, password, or private key.",
                    profile_name=profile.name,
                    host=profile.host,
                    port=profile.port,
                )
        finally:
            material.password = None
            material.pkey = None

        if not transport.is_authenticated():
            raise AuthenticationFailed(
                "Authentication failed.",
                reason="Check username, password, or private key.",
                profile_name=profile.name,
                host=profile.host,
                port=profile.port,
            )
        return transport
    except Exception:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                log.debug("transport.close after failure raised", exc_info=True)
        elif sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        raise
