from __future__ import annotations

import logging
import sys

from paramiko.agent import AgentSSH
from paramiko.pkey import PKey
from paramiko.transport import Transport

from ghostport.errors import AuthenticationFailed

log = logging.getLogger("ghostport.ssh")


class _ConnAgent(AgentSSH):
    def __init__(self, conn: object) -> None:
        super().__init__()
        self._connect(conn)


def _try_openssh() -> _ConnAgent | None:
    if sys.platform != "win32":
        return None
    from paramiko import win_openssh

    if not win_openssh.can_talk_to_agent():
        return None
    try:
        conn = win_openssh.OpenSSHAgentConnection()
        agent = _ConnAgent(conn)
    except OSError as exc:
        log.info("OpenSSH agent pipe failed: %s", exc)
        return None
    except Exception as exc:
        log.info("OpenSSH agent connect failed: %s", exc)
        return None
    return agent


def _try_pageant() -> _ConnAgent | None:
    if sys.platform != "win32":
        return None
    from paramiko import win_pageant

    if not win_pageant.can_talk_to_agent():
        return None
    try:
        conn = win_pageant.PageantConnection()
        return _ConnAgent(conn)
    except Exception as exc:
        log.info("Pageant connect failed: %s", exc)
        return None


def auth_with_agent(transport: Transport, username: str) -> None:
    """OpenSSH named pipe first, then Pageant. Do not call paramiko.Agent() first."""
    agents: list[_ConnAgent] = []
    openssh = _try_openssh()
    if openssh is not None:
        if openssh.get_keys():
            agents.append(openssh)
        else:
            log.info("OpenSSH agent reachable but has no keys; trying Pageant")
            openssh._close()
    pageant = _try_pageant()
    if pageant is not None:
        agents.append(pageant)

    last_exc: Exception | None = None
    for agent in agents:
        try:
            for key in agent.get_keys():
                try:
                    transport.auth_publickey(username, key)
                    if transport.is_authenticated():
                        return
                except Exception as exc:  # noqa: BLE001 — try next identity
                    last_exc = exc
                    log.info("Agent key rejected: %s", exc)
        finally:
            agent._close()

    reason = "SSH agent is not reachable or has no keys. Start Windows OpenSSH Authentication Agent or Pageant."
    if last_exc is not None:
        reason = f"{reason} Last error: {last_exc}"
    raise AuthenticationFailed(
        "Authentication failed.",
        reason=reason,
        host=getattr(transport, "hostname", "") or "",
    )


def load_private_key(path: str, passphrase: str | None) -> PKey:
    from paramiko.dsskey import DSSKey
    from paramiko.ecdsakey import ECDSAKey
    from paramiko.ed25519key import Ed25519Key
    from paramiko.rsakey import RSAKey
    from paramiko.ssh_exception import PasswordRequiredException, SSHException

    password = passphrase if passphrase else None
    last: Exception | None = None
    for cls in (Ed25519Key, RSAKey, ECDSAKey, DSSKey):
        try:
            return cls.from_private_key_file(path, password=password)
        except PasswordRequiredException as exc:
            raise AuthenticationFailed(
                "Authentication failed.",
                reason="Private key is passphrase-protected. Edit the server and save the passphrase.",
            ) from exc
        except SSHException as exc:
            last = exc
            continue
        except OSError as exc:
            raise AuthenticationFailed(
                "Authentication failed.",
                reason=f"Could not read private key file: {exc}",
            ) from exc
    raise AuthenticationFailed(
        "Authentication failed.",
        reason=f"Unsupported or corrupt private key. {last}",
    )
