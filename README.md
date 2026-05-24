# KUB

An async Python library for retrieving utility usage data from the [Knoxville Utilities Board](https://www.kub.org) (KUB) API.

## Features

- Authenticates via Azure AD B2C using PKCE OAuth2, mirroring the KUB web app flow
- Supports electricity, gas, water, and wastewater services
- Retrieves hourly usage and cost data for any date range
- Session management with automatic token refresh
- Compatible with Home Assistant custom components

## Requirements

- Python 3.12 or higher
- [aiohttp](https://docs.aiohttp.org/) 3.9 or higher

## Installation

```bash
pip install kub
```

## Usage

### Basic setup

```python
import asyncio
from kub import KubUtility

async def main():
    utility = KubUtility("your@email.com", "yourpassword")

    # Retrieve usage for the last 31 days
    usage = await utility.retrieve_last_31_days()
    print(usage)

asyncio.run(main())
```

### Available methods

| Method | Description |
|---|---|
| `retrieve_last_31_days()` | Fetches hourly usage for the past 31 days across all services |
| `retrieve_monthly_usage()` | Fetches hourly usage from the first of the current month to today |
| `retrieve_usage_by_range(start_date, end_date)` | Fetches usage for a custom range; dates are `YYYY-MM-DD` strings |
| `retrieve_account_info()` | Fetches account metadata (account ID, person ID, service list) without pulling usage data |

### Usage data structure

The `usage` attribute is a dictionary keyed by utility type, then by date, then by time:

```python
{
    "electricity": {
        "2026-05-01": {
            "08:00:00": {
                "id": "...",
                "readDateTime": "2026-05-01T08:00:00",
                "utilityUsed": 0.42,
                "uom": "kWh",
                "cost": 0.06
            },
            ...
        },
        ...
    },
    "gas": { ... },
    "water": { ... },
    "wastewater": { ... }
}
```

Monthly totals (for the current calendar month) are available via `monthly_total`:

```python
utility.monthly_total["electricity"]
# {"usage": 312.5, "cost": 42.18}
```

### Supported utility types

The `KUBUtilityTypes` enum represents the services KUB provides:

```python
from kub import KUBUtilityTypes

KUBUtilityTypes.ELECTRICITY
KUBUtilityTypes.GAS
KUBUtilityTypes.WATER
KUBUtilityTypes.WASTEWATER
```

Only the services active on your account are populated. Check `utility.service_list` after calling any retrieval method to see which types are present.

## Home Assistant

This library is designed to work as a dependency for a Home Assistant custom component. Place the `kub` directory inside `custom_components/` and import from it using standard relative imports.

## Development

Install development dependencies:

```bash
pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

## License

MIT
