"""Tests for kub_utilities."""

import base64
import hashlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from kub.kub_utilities import (
    HTTPError,
    Http,
    KUBAuthenticationError,
    KUBUtilityTypes,
    KubUtility,
    _pkce_pair,
)


# ---------------------------------------------------------------------------
# _pkce_pair
# ---------------------------------------------------------------------------


def test_pkce_pair_returns_two_strings():
    verifier, challenge = _pkce_pair()
    assert isinstance(verifier, str)
    assert isinstance(challenge, str)


def test_pkce_pair_challenge_is_valid_base64url():
    _, challenge = _pkce_pair()
    padded = challenge + "=" * (-len(challenge) % 4)
    base64.urlsafe_b64decode(padded)  # must not raise


def test_pkce_pair_challenge_derived_from_verifier():
    verifier, challenge = _pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected


def test_pkce_pair_uniqueness():
    v1, c1 = _pkce_pair()
    v2, c2 = _pkce_pair()
    assert v1 != v2
    assert c1 != c2


def test_pkce_pair_no_padding_in_challenge():
    _, challenge = _pkce_pair()
    assert "=" not in challenge


# ---------------------------------------------------------------------------
# HTTPError
# ---------------------------------------------------------------------------


def test_http_error_attributes():
    err = HTTPError(404, "Not Found")
    assert err.status_code == 404
    assert err.message == "Not Found"


def test_http_error_is_base_exception():
    assert issubclass(HTTPError, BaseException)


def test_http_error_args_contain_message_and_code():
    err = HTTPError(500, "Server Error")
    assert "Server Error" in err.args
    assert 500 in err.args


def test_http_error_can_be_raised_and_caught():
    with pytest.raises(HTTPError) as exc_info:
        raise HTTPError(403, "Forbidden")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# KUBAuthenticationError
# ---------------------------------------------------------------------------


def test_kub_authentication_error_is_base_exception():
    assert issubclass(KUBAuthenticationError, BaseException)


def test_kub_authentication_error_can_be_raised():
    with pytest.raises(KUBAuthenticationError):
        raise KUBAuthenticationError("invalid credentials")


# ---------------------------------------------------------------------------
# KUBUtilityTypes
# ---------------------------------------------------------------------------


def test_utility_type_electricity():
    assert KUBUtilityTypes.ELECTRICITY.value == "E"


def test_utility_type_gas():
    assert KUBUtilityTypes.GAS.value == "G"


def test_utility_type_water():
    assert KUBUtilityTypes.WATER.value == "W"


def test_utility_type_wastewater():
    assert KUBUtilityTypes.WASTEWATER.value == "WW"


def test_utility_type_all_members():
    names = {m.name for m in KUBUtilityTypes}
    assert names == {"ELECTRICITY", "GAS", "WATER", "WASTEWATER"}


# ---------------------------------------------------------------------------
# Http
# ---------------------------------------------------------------------------


def test_http_init_defaults():
    http = Http()
    assert http.access_token == ""
    assert http.session_cookies == {}
    assert http._session is None


def test_http_build_cookie_header_empty():
    http = Http()
    assert http._build_cookie_header() == ""


def test_http_build_cookie_header_single():
    http = Http(session_cookies={"id_token": "abc123"})
    assert http._build_cookie_header() == "id_token=abc123"


def test_http_build_cookie_header_multiple():
    http = Http(session_cookies={"a": "1", "b": "2"})
    header = http._build_cookie_header()
    assert "a=1" in header
    assert "b=2" in header
    assert "; " in header


async def test_http_fetch_raises_when_no_session():
    http = Http()
    with pytest.raises(RuntimeError, match="async context manager"):
        await http.fetch("https://example.com")


async def test_http_post_raises_when_no_session():
    http = Http()
    with pytest.raises(RuntimeError, match="async context manager"):
        await http.post("https://example.com", {})


async def test_http_post_form_raises_when_no_session():
    http = Http()
    with pytest.raises(RuntimeError, match="async context manager"):
        await http.post_form("https://example.com", {})


# ---------------------------------------------------------------------------
# KubUtility – initialisation
# ---------------------------------------------------------------------------


def test_kub_utility_init():
    ku = KubUtility("user@example.com", "s3cr3t")
    assert ku.username == "user@example.com"
    assert ku.password == "s3cr3t"
    assert ku.person_id == ""
    assert ku.account_id == ""
    assert ku.account == {}
    assert ku.session_start is None
    assert ku._access_token == ""
    assert ku._refresh_token == ""
    assert ku._token_expires_at is None
    assert ku._session_cookies == {}
    assert ku.service_list == []
    assert ku.http is None


def test_kub_utility_usage_initial_keys():
    ku = KubUtility("user", "pass")
    assert set(ku.usage.keys()) == {"electricity", "gas", "water", "wastewater"}


def test_kub_utility_monthly_total_initial_values():
    ku = KubUtility("user", "pass")
    for key in ("electricity", "gas", "water", "wastewater"):
        assert ku.monthly_total[key]["usage"] is None
        assert ku.monthly_total[key]["cost"] is None


# ---------------------------------------------------------------------------
# KubUtility.is_session_active
# ---------------------------------------------------------------------------


def test_is_session_active_no_expiry():
    ku = KubUtility("user", "pass")
    assert ku.is_session_active is False


def test_is_session_active_expired_token():
    ku = KubUtility("user", "pass")
    ku._token_expires_at = datetime.now() - timedelta(hours=1)
    ku._access_token = "token"
    assert ku.is_session_active is False


def test_is_session_active_no_credentials():
    ku = KubUtility("user", "pass")
    ku._token_expires_at = datetime.now() + timedelta(hours=1)
    # Neither cookies nor access token set
    assert ku.is_session_active is False


def test_is_session_active_with_bearer_token():
    ku = KubUtility("user", "pass")
    ku._token_expires_at = datetime.now() + timedelta(hours=1)
    ku._access_token = "valid_token"
    assert ku.is_session_active is True


def test_is_session_active_with_session_cookies():
    ku = KubUtility("user", "pass")
    ku._token_expires_at = datetime.now() + timedelta(hours=1)
    ku._session_cookies = {"id_token": "cookieval"}
    assert ku.is_session_active is True


def test_is_session_active_near_expiry_is_false():
    """Token expiring within 60 s should be treated as inactive."""
    ku = KubUtility("user", "pass")
    ku._token_expires_at = datetime.now() + timedelta(seconds=30)
    ku._access_token = "token"
    assert ku.is_session_active is False


# ---------------------------------------------------------------------------
# KubUtility._retrieve_services (mocked Http)
# ---------------------------------------------------------------------------


async def test_retrieve_services_electricity():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={"service-point": [{"type": "E-RES", "id": "elec-001"}]}
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    await ku._retrieve_services()

    assert KUBUtilityTypes.ELECTRICITY in ku.service_list
    assert ku.account["electricity"] == "elec-001"


async def test_retrieve_services_gas():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={"service-point": [{"type": "G-RES", "id": "gas-001"}]}
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    await ku._retrieve_services()

    assert KUBUtilityTypes.GAS in ku.service_list
    assert ku.account["gas"] == "gas-001"


async def test_retrieve_services_combined_water_wastewater():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={"service-point": [{"type": "W/S-RES", "id": "water-001"}]}
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    await ku._retrieve_services()

    assert KUBUtilityTypes.WATER in ku.service_list
    assert KUBUtilityTypes.WASTEWATER in ku.service_list
    assert ku.account["water"] == "water-001"
    assert ku.account["wastewater"] == "water-001"


async def test_retrieve_services_separate_wastewater():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={"service-point": [{"type": "SO-RES", "id": "ww-001"}]}
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    await ku._retrieve_services()

    assert KUBUtilityTypes.WASTEWATER in ku.service_list
    assert ku.account["wastewater"] == "ww-001"


async def test_retrieve_services_unknown_type_raises():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={"service-point": [{"type": "UNKNOWN", "id": "xxx"}]}
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    with pytest.raises(ValueError, match="unexpected service type"):
        await ku._retrieve_services()


async def test_retrieve_services_multiple():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(
        return_value={
            "service-point": [
                {"type": "E-RES", "id": "elec-001"},
                {"type": "G-RES", "id": "gas-001"},
                {"type": "W/S-RES", "id": "water-001"},
            ]
        }
    )
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    await ku._retrieve_services()

    assert KUBUtilityTypes.ELECTRICITY in ku.service_list
    assert KUBUtilityTypes.GAS in ku.service_list
    assert KUBUtilityTypes.WATER in ku.service_list
    assert KUBUtilityTypes.WASTEWATER in ku.service_list


async def test_retrieve_services_raises_without_http():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"
    # http is None (default)
    with pytest.raises(RuntimeError, match="No active HTTP session"):
        await ku._retrieve_services()


# ---------------------------------------------------------------------------
# KubUtility._retrieve_usage (mocked Http)
# ---------------------------------------------------------------------------


def _make_usage_response(read_date: str, read_time: str, value: float, cost: float):
    """Build a minimal API response for a single day + single hourly reading."""
    return {
        "usage-value": [
            # Aggregate/date row – non-empty children
            {
                "id": "agg-day",
                "readDateTime": f"{read_date}T00:00:00",
                "usageValuesChildren": ["child-placeholder"],
            },
            # Detail/hourly row – empty children
            {
                "id": "detail-1",
                "readDateTime": f"{read_date}T{read_time}",
                "usageValuesChildren": [],
            },
        ],
        "usage-aggregate": [
            # Index 0: aggregate placeholder (not read for detail)
            {"readValue": 0.0, "uom": "kWh", "cost": 0.0},
            # Index 1: matches the detail row
            {"readValue": value, "uom": "kWh", "cost": cost},
        ],
    }


async def test_retrieve_usage_electricity_current_month():
    ku = KubUtility("user", "pass")
    ku.account_id = "acct-001"
    ku.person_id = "person-001"
    ku.account["electricity"] = "elec-001"

    today = datetime.now()
    read_date = today.strftime("%Y-%m-%d")
    read_time = "10:00:00"
    payload = _make_usage_response(read_date, read_time, 5.5, 0.75)

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value=payload)
    mock_http = MagicMock()
    mock_http.fetch = AsyncMock(return_value=mock_resp)
    ku.http = mock_http

    result = await ku._retrieve_usage(KUBUtilityTypes.ELECTRICITY)

    assert result is ku.usage
    assert read_date in ku.usage["electricity"]
    assert read_time in ku.usage["electricity"][read_date]
    assert ku.usage["electricity"][read_date][read_time]["utilityUsed"] == 5.5
    assert ku.usage["electricity"][read_date][read_time]["cost"] == 0.75
    assert ku.monthly_total["electricity"]["usage"] == pytest.approx(5.5)
    assert ku.monthly_total["electricity"]["cost"] == pytest.approx(0.75)


async def test_retrieve_usage_wastewater_copies_water():
    ku = KubUtility("user", "pass")
    ku.account["water"] = "water-001"
    ku.account["wastewater"] = "water-001"

    # Pre-populate water data
    ku.usage["water"] = {"2024-01-15": {"10:00:00": {"utilityUsed": 3.0}}}
    ku.monthly_total["water"]["usage"] = 3.0
    ku.monthly_total["water"]["cost"] = 0.50

    # Calling for WASTEWATER should copy water without making HTTP calls
    mock_http = MagicMock()
    ku.http = mock_http

    await ku._retrieve_usage(KUBUtilityTypes.WASTEWATER)

    mock_http.fetch.assert_not_called()
    assert ku.usage["wastewater"] == ku.usage["water"]
    assert ku.monthly_total["wastewater"]["usage"] == 3.0
    assert ku.monthly_total["wastewater"]["cost"] == 0.50


async def test_retrieve_usage_raises_without_http():
    ku = KubUtility("user", "pass")
    ku.account["electricity"] = "elec-001"
    ku.person_id = "person-001"
    # http is None (not wastewater, so it won't short-circuit)
    with pytest.raises(RuntimeError, match="No active HTTP session"):
        await ku._retrieve_usage(KUBUtilityTypes.ELECTRICITY)


# ---------------------------------------------------------------------------
# Public API surface (kub package re-exports)
# ---------------------------------------------------------------------------


def test_public_api_imports():
    import kub

    assert hasattr(kub, "KubUtility")
    assert hasattr(kub, "KUBUtilityTypes")
    assert hasattr(kub, "KUBAuthenticationError")
    assert hasattr(kub, "HTTPError")
