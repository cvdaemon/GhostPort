from __future__ import annotations

import sys

import pytest

from ghostport.security import dpapi


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_round_trip() -> None:
    blob = dpapi.protect(b"secret-value")
    assert blob != b"secret-value"
    assert dpapi.unprotect(blob) == b"secret-value"
