"""Smoke-test Avito API access using .env (secrets are not printed)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()


def main() -> int:
    if not ENV_PATH.exists():
        print("FAIL: .env not found", file=sys.stderr)
        return 1

    load_env(ENV_PATH)
    client_id = os.environ.get("AVITO_CLIENT_ID", "")
    client_secret = os.environ.get("AVITO_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("FAIL: AVITO_CLIENT_ID or AVITO_CLIENT_SECRET is empty", file=sys.stderr)
        return 1

    # trust_env=False — обход системного SOCKS-прокси, который ломает httpx
    with httpx.Client(trust_env=False, timeout=30.0) as http:
        token_resp = http.post(
            "https://api.avito.ru/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        print(f"Token HTTP: {token_resp.status_code}")
        if token_resp.status_code != 200:
            print(f"FAIL: {token_resp.text[:400]}", file=sys.stderr)
            return 1

        token = token_resp.json()["access_token"]
        print("OK: authorization successful")

        headers = {"Authorization": f"Bearer {token}"}

        profile_resp = http.get("https://api.avito.ru/core/v1/accounts/self", headers=headers)
        print(f"Profile HTTP: {profile_resp.status_code}")
        if profile_resp.status_code != 200:
            print(f"FAIL: {profile_resp.text[:400]}", file=sys.stderr)
            return 1

        profile = profile_resp.json()
        user_id = profile.get("id") or profile.get("user_id")
        print(f"Account ID: {user_id}")
        print(f"Name: {profile.get('name') or profile.get('username')}")
        if profile.get("email"):
            print(f"Email: {profile['email']}")

        if user_id:
            balance_resp = http.get(
                f"https://api.avito.ru/core/v1/accounts/{user_id}/balance/",
                headers=headers,
            )
            print(f"Balance HTTP: {balance_resp.status_code}")
            if balance_resp.status_code == 200:
                balance = balance_resp.json()
                for key, value in balance.items():
                    if value is not None:
                        print(f"Balance {key}: {value}")

        listings_resp = http.get(
            "https://api.avito.ru/core/v1/items",
            params={"status": "active", "per_page": 5, "page": 1},
            headers=headers,
        )
        print(f"Listings HTTP: {listings_resp.status_code}")
        if listings_resp.status_code == 200:
            payload = listings_resp.json()
            resources = payload.get("resources") or payload.get("items") or []
            print(f"Active listings (sample): {len(resources)}")
            for item in resources[:3]:
                item_id = item.get("id")
                title = item.get("title") or item.get("name")
                price = item.get("price")
                print(f"  - [{item_id}] {title} | {price}")
        else:
            print(f"Listings note: {listings_resp.text[:300]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
