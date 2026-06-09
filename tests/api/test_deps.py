import pytest
from fastapi import HTTPException

from amap_service.api.deps import check_api_key
from amap_service.config.schema import ApiAuthConfig


def test_auth_disabled_allows_any():
    auth = ApiAuthConfig(enabled=False)
    check_api_key(auth, provided=None)  # 不抛


def test_auth_enabled_rejects_missing():
    auth = ApiAuthConfig(enabled=True, api_key="secret")
    with pytest.raises(HTTPException) as e:
        check_api_key(auth, provided=None)
    assert e.value.status_code == 401


def test_auth_enabled_rejects_wrong():
    auth = ApiAuthConfig(enabled=True, api_key="secret")
    with pytest.raises(HTTPException) as e:
        check_api_key(auth, provided="nope")
    assert e.value.status_code == 401


def test_auth_enabled_accepts_correct():
    auth = ApiAuthConfig(enabled=True, api_key="secret")
    check_api_key(auth, provided="secret")  # 不抛
