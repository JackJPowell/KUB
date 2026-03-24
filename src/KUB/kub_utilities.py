"""Knoxville Utilities Board API"""

import base64
import copy
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from enum import Enum
from urllib.parse import parse_qs, urlparse

import aiohttp

# ---------------------------------------------------------------------------
# Azure AD B2C / OAuth constants
# ---------------------------------------------------------------------------
_CLIENT_ID = "806e58e2-5935-4d1e-abce-2d85ea0dd776"
_TENANT_HOST = "https://login.kub.org"
_TENANT_PATH = "/login.kub.org/B2C_1_sign_in"
_POLICY = "B2C_1_sign_in"
_REDIRECT_URI = "https://www.kub.org/auth-callback"
_SCOPE = f"openid profile {_CLIENT_ID}"
_AUTHORIZE_URL = f"{_TENANT_HOST}{_TENANT_PATH}/oauth2/v2.0/authorize"
_TOKEN_URL = f"{_TENANT_HOST}{_TENANT_PATH}/oauth2/v2.0/token"
_SELF_ASSERTED_URL = f"{_TENANT_HOST}{_TENANT_PATH}/SelfAsserted"
_CONFIRMED_URL = f"{_TENANT_HOST}{_TENANT_PATH}/api/CombinedSigninAndSignup/confirmed"


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class HTTPError(BaseException):
    """Raised when an HTTP operation fails."""

    def __init__(self, status_code, message) -> None:
        """Raise HTTP Error."""
        self.status_code = status_code
        self.message = message
        super().__init__(self.message, self.status_code)


class KUBAuthenticationError(BaseException):
    """Raised when HTTP login fails."""


class KUBUtilityTypes(Enum):
    """KUB Utility Types"""

    ELECTRICITY = "E"
    GAS = "G"
    WATER = "W"
    WASTEWATER = "WW"


class Http:
    """Simple http class to wrap api calls"""

    def __init__(self, access_token: str = "") -> None:
        self._session: aiohttp.ClientSession | None = None
        self.access_token = access_token

    async def __aenter__(self):
        headers: dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        )
        return self

    async def __aexit__(self, *err):
        if self._session:
            await self._session.close()
        self._session = None

    async def fetch(self, url):
        """http get"""
        assert self._session is not None
        resp = await self._session.get(url)
        resp.raise_for_status()
        return resp

    async def post(self, url, payload):
        """HTTP post (JSON body)"""
        assert self._session is not None
        resp = await self._session.post(url, json=payload)
        resp.raise_for_status()
        return resp

    async def post_form(self, url, data: dict, headers: dict | None = None):
        """HTTP post (form-encoded body)"""
        assert self._session is not None
        resp = await self._session.post(url, data=data, headers=headers or {})
        resp.raise_for_status()
        return resp


class KubUtility:
    """KUB utilities api"""

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.person_id = ""
        self.account_id = ""

        self.account = {}
        self.session_start: datetime | None = None
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expires_at: datetime | None = None

        self.usage = {"electricity": {}, "gas": {}, "water": {}, "wastewater": {}}
        self.monthly_total = {
            "electricity": {"usage": None, "cost": None},
            "gas": {"usage": None, "cost": None},
            "water": {"usage": None, "cost": None},
            "wastewater": {"usage": None, "cost": None},
        }
        self.services = {}
        self.service_list = []
        self.http: Http | None = None

    @property
    def is_session_active(self) -> bool:
        """Returns True when the access token is still valid (with 60 s margin)."""
        if not self._token_expires_at or not self._access_token:
            return False
        return datetime.now() < self._token_expires_at - timedelta(seconds=60)

    # ------------------------------------------------------------------
    # OAuth / Authentication helpers
    # ------------------------------------------------------------------

    async def _retrieve_access_token(self):
        """Authenticate via Azure AD B2C and store the access + refresh tokens.

        The flow is the standard Azure AD B2C "SelfAsserted" headless flow:
          1. GET /oauth2/v2.0/authorize  – obtain session cookies + CSRF token
          2. POST /SelfAsserted          – submit credentials
          3. GET /api/.../confirmed      – exchange for an auth *code*
          4. POST /oauth2/v2.0/token     – exchange code for tokens (PKCE)
        """
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)

        timeout = aiohttp.ClientTimeout(total=30)
        # Use a raw session with cookie jar so we can drive the B2C flow
        async with aiohttp.ClientSession(
            timeout=timeout,
            cookie_jar=aiohttp.CookieJar(),
        ) as session:
            # ----------------------------------------------------------
            # Step 1 – GET authorize page to seed cookies & CSRF token
            # ----------------------------------------------------------
            params = {
                "client_id": _CLIENT_ID,
                "response_type": "code",
                "redirect_uri": _REDIRECT_URI,
                "scope": _SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            async with session.get(_AUTHORIZE_URL, params=params) as resp:
                if resp.status != 200:
                    raise KUBAuthenticationError(
                        f"Authorize page returned HTTP {resp.status}"
                    )
                html = await resp.text()

            # Extract CSRF token and transaction ID from the page
            csrf_match = re.search(r'"csrf"\s*:\s*"([^"]+)"', html)
            trans_match = re.search(r'"transId"\s*:\s*"([^"]+)"', html)
            if not csrf_match or not trans_match:
                raise KUBAuthenticationError(
                    "Could not locate CSRF token or transId in authorize response."
                )
            csrf_token = csrf_match.group(1)
            trans_id = trans_match.group(1)

            # ----------------------------------------------------------
            # Step 2 – POST credentials to SelfAsserted endpoint
            # ----------------------------------------------------------
            self_asserted_params = {"tx": trans_id, "p": _POLICY}
            self_asserted_headers = {
                "X-CSRF-TOKEN": csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": str(resp.url),
            }
            credential_data = {
                "request_type": "RESPONSE",
                "logonIdentifier": self.username,
                "password": self.password,
            }
            async with session.post(
                _SELF_ASSERTED_URL,
                params=self_asserted_params,
                data=credential_data,
                headers=self_asserted_headers,
            ) as sa_resp:
                sa_json = await sa_resp.json(content_type=None)
                if str(sa_json.get("status")) != "200":
                    error_msg = sa_json.get("message", "Authentication failed")
                    raise KUBAuthenticationError(error_msg)

            # ----------------------------------------------------------
            # Step 3 – GET confirmed endpoint to receive the auth code
            # ----------------------------------------------------------
            confirmed_params = {
                "csrf_token": csrf_token,
                "tx": trans_id,
                "p": _POLICY,
            }
            # The confirmed endpoint redirects to redirect_uri with ?code=...
            # We must NOT follow the redirect so we can intercept the code.
            async with session.get(
                _CONFIRMED_URL,
                params=confirmed_params,
                allow_redirects=False,
            ) as confirmed_resp:
                location = confirmed_resp.headers.get("Location", "")

            if not location:
                raise KUBAuthenticationError(
                    "No redirect location returned from confirmed endpoint."
                )

            parsed = urlparse(location)
            qs = parse_qs(parsed.query)
            auth_code = qs.get("code", [None])[0]
            if not auth_code:
                error = qs.get("error_description", qs.get("error", ["Unknown"]))[0]
                raise KUBAuthenticationError(
                    f"Auth code not found in redirect. Error: {error}"
                )

            # ----------------------------------------------------------
            # Step 4 – Exchange auth code for tokens
            # ----------------------------------------------------------
            token_data = {
                "client_id": _CLIENT_ID,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": _REDIRECT_URI,
                "code_verifier": verifier,
                "scope": _SCOPE,
            }
            async with session.post(_TOKEN_URL, data=token_data) as token_resp:
                if token_resp.status != 200:
                    body = await token_resp.text()
                    raise KUBAuthenticationError(
                        f"Token exchange failed (HTTP {token_resp.status}): {body}"
                    )
                token_json = await token_resp.json()

        self._access_token = token_json["access_token"]
        self._refresh_token = token_json.get("refresh_token", "")
        expires_in = int(token_json.get("expires_in", 3600))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        self.session_start = datetime.now()

        # Propagate new token to any active Http instance
        if self.http is not None:
            self.http.access_token = self._access_token

    async def _refresh_access_token(self):
        """Use the refresh token to obtain a new access token without re-authenticating."""
        if not self._refresh_token:
            await self._retrieve_access_token()
            return

        token_data = {
            "client_id": _CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "scope": _SCOPE,
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.post(_TOKEN_URL, data=token_data) as token_resp:
                if token_resp.status != 200:
                    # Refresh token expired – fall back to full login
                    await self._retrieve_access_token()
                    return
                token_json = await token_resp.json()

        self._access_token = token_json["access_token"]
        self._refresh_token = token_json.get("refresh_token", self._refresh_token)
        expires_in = int(token_json.get("expires_in", 3600))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        if self.http is not None:
            self.http.access_token = self._access_token

    async def _ensure_token(self):
        """Ensure we have a valid access token, refreshing or re-authenticating as needed."""
        if not self.is_session_active:
            if self._refresh_token:
                await self._refresh_access_token()
            else:
                await self._retrieve_access_token()

    async def _retrieve_account_info(self):
        """Retrieve Account Info"""
        assert self.http is not None
        if not self.account_id:
            response = await self.http.fetch(
                f"https://www.kub.org/api/auth/v1/users/{self.username}"
            )
            json = await response.json()
            self.person_id = json["person"][0]["id"]
            self.account_id = json["person"][0]["accounts"][0]
        await self._retrieve_services()

    async def _retrieve_services(self):
        assert self.http is not None
        url = f"https://www.kub.org/api/cis/v1/accounts/{self.account_id}?include=all"
        response = await self.http.fetch(url)
        json = await response.json()
        self.services = json["service-point"]

        for service in self.services:
            match service["type"]:
                case "E-RES":
                    self.account["electricity"] = service["id"]
                    self.service_list.append(KUBUtilityTypes.ELECTRICITY)
                case "G-RES":
                    self.account["gas"] = service["id"]
                    self.service_list.append(KUBUtilityTypes.GAS)
                case "W/S-RES":
                    self.account["water"] = service["id"]
                    self.account["wastewater"] = service["id"]
                    self.service_list.append(KUBUtilityTypes.WATER)
                    self.service_list.append(KUBUtilityTypes.WASTEWATER)
                case _:
                    raise ValueError(
                        f"An unexpected service type: {service['type']} (id: {service['id']})"
                    )
        return self.services

    async def retrieve_account_info(self):
        """Retrieves account info from KUB api"""
        await self._retrieve_access_token()
        async with Http(self._access_token) as self.http:
            await self._retrieve_account_info()
        self.http = None

    async def retrieve_access_token(self):
        """Fetches access token"""
        await self._retrieve_access_token()

    async def _retrieve_usage(
        self,
        utility_type,
        start_date: str = datetime.today().strftime("%Y-%m-%d"),
        end_date: str = datetime.today().strftime("%Y-%m-%d"),
    ):
        utility = utility_type.name.lower()
        account = self.account[utility]

        # If we are processing wastewater so just copy water
        # This does not account for separate meters for water and wastewater
        # However, I do not know what the response looks like to process
        # this case properly
        if utility_type == KUBUtilityTypes.WASTEWATER:
            water = KUBUtilityTypes.WATER.name.lower()
            self.usage[utility] = copy.deepcopy(self.usage[water])
            self.monthly_total[utility]["usage"] = self.monthly_total[water]["usage"]
            self.monthly_total[utility]["cost"] = self.monthly_total[water]["cost"]
            return self.usage

        url = (
            f"https://www.kub.org/api/ami/v1/usage-values"
            f"?endDate={end_date}"
            f"&personId={self.person_id}"
            f"&servicePointId={account}"
            f"&startDate={start_date}"
            f"&utilityType={utility_type.value}"
        )

        assert self.http is not None
        response = await self.http.fetch(url)
        json = await response.json()
        total = 0.0
        total_cost = 0.0
        date = ""
        usage_data = {}
        for idx, usage in enumerate(json["usage-value"]):
            if len(usage["usageValuesChildren"]) == 0:
                # Pull data from the base object
                usage_data["id"] = usage["id"]
                usage_data["readDateTime"] = usage["readDateTime"]

                # Grab the usage object via index
                data = json["usage-aggregate"][idx]

                # Read data from the usage object
                usage_data["utilityUsed"] = data["readValue"]
                usage_data["uom"] = data["uom"]
                usage_data["cost"] = data["cost"]

                # Create another object with key of time
                time = datetime.fromisoformat(usage["readDateTime"]).strftime(
                    "%H:%M:%S"
                )
                self.usage[utility][date][time] = {}

                # Apend all the data
                self.usage[utility][date][time] = copy.deepcopy(usage_data)

                if (
                    datetime.fromisoformat(usage["readDateTime"]).month
                    == datetime.now().month
                ):
                    total = data["readValue"] + total
                    total_cost = data["cost"] + total_cost
            else:
                # This is the aggregate case so create a new blank object in the list
                date = datetime.fromisoformat(usage["readDateTime"]).strftime(
                    "%Y-%m-%d"
                )
                self.usage[utility][date] = {}

        self.monthly_total[utility]["usage"] = total
        self.monthly_total[utility]["cost"] = total_cost
        return self.usage

    async def retrieve_last_31_days(self):
        """Retrieve all usage for the last 31 days"""
        date = datetime.today() - timedelta(days=31)
        start_date = date.strftime("%Y-%m-%d")

        await self._ensure_token()
        async with Http(self._access_token) as self.http:
            if not self.person_id:
                await self._retrieve_account_info()

            for service in self.service_list:
                await self._retrieve_usage(service, start_date=start_date)
        self.http = None
        return self.usage

    async def retrieve_monthly_usage(self):
        """Retrieve all usage for the current month"""
        start_date = datetime.today().replace(day=1).strftime("%Y-%m-%d")

        await self._ensure_token()
        async with Http(self._access_token) as self.http:
            if not self.person_id:
                await self._retrieve_account_info()
            for service in self.service_list:
                await self._retrieve_usage(service, start_date=start_date)
        self.http = None
        return self.usage

    async def retrieve_usage_by_range(
        self,
        start_date: str = datetime.today().strftime("%Y-%m-%d"),
        end_date: str = datetime.today().strftime("%Y-%m-%d"),
    ):
        """Retrieve usage for a custom date range"""
        await self._ensure_token()
        async with Http(self._access_token) as self.http:
            if not self.person_id:
                await self._retrieve_account_info()
            for service in self.service_list:
                await self._retrieve_usage(
                    service, start_date=start_date, end_date=end_date
                )
        self.http = None
        return self.usage

    async def retrieve_monthly_summary(self):
        """Retrieve summary of usage for the current month"""
        start_date = datetime.today().replace(day=1).strftime("%Y-%m-%d")

        await self._ensure_token()
        async with Http(self._access_token) as self.http:
            if not self.person_id:
                await self._retrieve_account_info()
            for service in self.service_list:
                await self._retrieve_usage(service, start_date=start_date)
        self.http = None
        return self.monthly_total

    async def get_usage_by_datetime(self, usage_record: datetime = datetime.now()):
        """Retrieve usage by datetime"""
        await self.retrieve_monthly_usage()
        date_key = usage_record.replace(day=1).strftime("%Y-%m-%d")
        hour_key = usage_record.strftime("%H:00:00")
        elec = (self.usage.get("electricity") or {}).get(date_key, {}).get(hour_key)
        gas = (self.usage.get("gas") or {}).get(date_key, {}).get(hour_key)
        water = (self.usage.get("water") or {}).get(date_key, {}).get(hour_key)
        return elec, gas, water

    async def get_available_services(self):
        """Returns available services for account"""
        await self._ensure_token()
        async with Http(self._access_token) as self.http:
            if not self.person_id:
                await self._retrieve_account_info()
        self.http = None
        return self.services

    async def verify_access(self):
        """Verify username and password is able to retrieve api token"""
        await self._retrieve_access_token()
        return self.is_session_active
