from __future__ import annotations

from paramiko.transport import Transport

from ghostport.profiles.models import ServerProfile


def apply_keepalive(transport: Transport, profile: ServerProfile) -> None:
    if profile.keep_alive:
        interval = profile.keep_alive_interval_sec or 30
        transport.set_keepalive(int(interval))
