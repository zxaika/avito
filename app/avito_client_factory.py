"""Создание AvitoClient с обходом системного SOCKS-прокси на Windows."""

from __future__ import annotations

import httpx
from avito import AvitoClient
from avito.auth.provider import AlternateTokenClient, AuthProvider, TokenClient
from avito.auth.settings import AuthSettings
from avito.config import AvitoSettings
from avito.core import Transport
from avito.core.transport import build_httpx_timeout

from app.config import AppConfig, PROJECT_ENV_PATH, apply_config_to_env
from app.logging_setup import get_logger

logger = get_logger("client")


def _build_settings(config: AppConfig) -> AvitoSettings:
    apply_config_to_env(config)
    if config.client_id and config.client_secret:
        logger.debug("Настройки из AppConfig (client_id=%s...)", config.client_id[:6])
        return AvitoSettings(
            auth=AuthSettings(
                client_id=config.client_id,
                client_secret=config.client_secret,
            ),
            user_id=config.user_id,
        ).validate_required()
    if PROJECT_ENV_PATH.exists():
        logger.debug("Настройки из .env: %s", PROJECT_ENV_PATH)
        return AvitoSettings.from_env(env_file=PROJECT_ENV_PATH).validate_required()
    logger.debug("Настройки из переменных окружения")
    return AvitoSettings.from_env().validate_required()


def create_avito_client(config: AppConfig) -> AvitoClient:
    logger.debug("Создание AvitoClient (trust_env=False, обход SOCKS-прокси)")
    settings = _build_settings(config)
    http = httpx.Client(
        base_url=settings.base_url.rstrip("/"),
        timeout=build_httpx_timeout(settings.timeouts),
        trust_env=False,
    )
    auth_provider = AuthProvider(
        settings.auth,
        token_client=TokenClient(settings.auth, sdk_settings=settings, client=http),
        alternate_token_client=AlternateTokenClient(
            settings.auth,
            sdk_settings=settings,
            client=http,
        ),
        autoteka_token_client=TokenClient(
            settings.auth,
            token_url=settings.auth.autoteka_token_url,
            sdk_settings=settings,
            client=http,
        ),
    )
    transport = Transport(settings, auth_provider=auth_provider, client=http)
    return AvitoClient._from_transport(  # noqa: SLF001
        settings,
        transport=transport,
        auth_provider=auth_provider,
    )
