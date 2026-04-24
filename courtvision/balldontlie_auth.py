from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

BALLDONTLIE_API_KEY_ENV_VAR = "BALLDONTLIE_API_KEY"
BALLDONTLIE_GAMES_BASE_URL = "https://api.balldontlie.io/v1"
BALLDONTLIE_GAMES_URL = f"{BALLDONTLIE_GAMES_BASE_URL}/games"
BALLDONTLIE_SMOKE_TEST_DATE = "2026-04-12"
COMMON_BALLDONTLIE_ENV_MISTAKES: tuple[str, ...] = (
    "BALDONTLIE_API_KEY",
    "BALLDONTLIE_KEY",
    "BALLDONTLIE_APIKEY",
    "BALLDONTLIE_API_TOKEN",
    "BALLDONTLIE_TOKEN",
)
BALLDONTLIE_401_HINT = (
    "BallDontLie rejected the Authorization header value. Likely causes: "
    "revoked/invalid API key, wrong env var loaded, hidden whitespace/quotes, "
    "or .env not loaded in this entrypoint."
)

ENV_SOURCE_AUDIT: dict[str, dict[str, Any]] = {}
ENV_BOOTSTRAP_STATE: dict[str, list[str]] = {
    "searched_paths": [],
    "loaded_paths": [],
}


def clean_api_key(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def mask_api_key(value: str | None) -> str:
    cleaned = clean_api_key(value)
    if not cleaned:
        return "<empty>"
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def safe_key_fingerprint(value: str | None) -> str:
    cleaned = clean_api_key(value)
    if not cleaned:
        return "missing"
    tail = cleaned[-4:] if len(cleaned) >= 4 else cleaned
    return f"len={len(cleaned)} last4={tail}"


def load_env_file(search_paths: Sequence[Path] | None = None) -> None:
    searched_paths: list[str] = []
    loaded_paths: list[str] = []
    seen: set[Path] = set()

    for env_path in search_paths or default_env_search_paths():
        try:
            resolved = env_path.resolve()
        except OSError:
            resolved = env_path
        resolved_text = str(resolved)
        searched_paths.append(resolved_text)
        if resolved in seen or not env_path.exists():
            continue
        seen.add(resolved)

        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        loaded_paths.append(resolved_text)

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, raw_value = line.split("=", 1)
            key = key.strip()
            raw_value = raw_value.rstrip("\r\n")
            cleaned_value = clean_api_key(raw_value)
            if not key:
                continue

            dotenv_path = str(env_path)
            audit_payload = {
                "source": "dotenv",
                "dotenv_present": True,
                "dotenv_matches_process": True,
                "dotenv_path": dotenv_path,
                "dotenv_fingerprint": safe_key_fingerprint(cleaned_value),
                "dotenv_had_wrapping_quotes": _has_wrapping_quotes(raw_value),
                "dotenv_had_surrounding_whitespace": str(raw_value or "") != str(raw_value or "").strip(),
            }

            if key in os.environ:
                existing_value = os.environ.get(key, "")
                ENV_SOURCE_AUDIT[key] = {
                    **audit_payload,
                    "source": "process_env",
                    "dotenv_matches_process": clean_api_key(existing_value) == cleaned_value,
                }
                continue

            os.environ[key] = cleaned_value
            ENV_SOURCE_AUDIT[key] = audit_payload

    ENV_BOOTSTRAP_STATE["searched_paths"] = searched_paths
    ENV_BOOTSTRAP_STATE["loaded_paths"] = loaded_paths


def default_env_search_paths() -> tuple[Path, ...]:
    project_root = Path(__file__).resolve().parent.parent
    return (
        Path.cwd() / ".env",
        project_root / ".env",
    )


def env_key_debug_details(
    key: str = BALLDONTLIE_API_KEY_ENV_VAR,
    current_value: str | None = None,
) -> dict[str, Any]:
    raw_value = current_value if current_value is not None else os.getenv(key, "")
    cleaned = clean_api_key(raw_value)
    audit = dict(ENV_SOURCE_AUDIT.get(key, {}))
    source = "missing"
    if cleaned:
        source = "process_env"
    if audit.get("source") == "dotenv":
        source = "dotenv"
    elif audit.get("source") == "process_env" and audit.get("dotenv_present"):
        source = (
            "process_env_dotenv_match"
            if bool(audit.get("dotenv_matches_process"))
            else "process_env_dotenv_mismatch"
        )

    wrong_env_vars = [
        name
        for name in COMMON_BALLDONTLIE_ENV_MISTAKES
        if clean_api_key(os.getenv(name, "")) and name != key
    ]

    details: dict[str, Any] = {
        "env_var_name": key,
        "source": source,
        "fingerprint": safe_key_fingerprint(cleaned),
        "masked_preview": mask_api_key(cleaned),
        "has_value": bool(cleaned),
        "had_wrapping_quotes": _has_wrapping_quotes(raw_value),
        "had_surrounding_whitespace": str(raw_value or "") != str(raw_value or "").strip(),
        "searched_paths": list(ENV_BOOTSTRAP_STATE.get("searched_paths", [])),
        "loaded_paths": list(ENV_BOOTSTRAP_STATE.get("loaded_paths", [])),
        "wrong_env_var_candidates": wrong_env_vars,
    }

    for field in [
        "dotenv_path",
        "dotenv_fingerprint",
        "dotenv_matches_process",
        "dotenv_had_wrapping_quotes",
        "dotenv_had_surrounding_whitespace",
    ]:
        if field in audit:
            details[field] = audit[field]

    if audit.get("source") == "process_env" and audit.get("dotenv_present"):
        details["dotenv_ignored"] = True

    return details


def resolve_api_key(
    *,
    entrypoint: str,
    env_var_name: str = BALLDONTLIE_API_KEY_ENV_VAR,
    current_value: str | None = None,
    logger: logging.Logger | None = None,
) -> tuple[str, dict[str, Any]]:
    load_env_file()
    raw_value = current_value if current_value is not None else os.getenv(env_var_name, "")
    cleaned = clean_api_key(raw_value)
    details = env_key_debug_details(env_var_name, raw_value)
    _log_env_diagnostics(entrypoint=entrypoint, details=details, logger=logger)
    if not cleaned:
        raise RuntimeError(build_missing_key_message(entrypoint=entrypoint, details=details))
    return cleaned, details


def smoke_test_games_api(
    api_key: str,
    *,
    entrypoint: str,
    timeout: int = 30,
    logger: logging.Logger | None = None,
    env_var_name: str = BALLDONTLIE_API_KEY_ENV_VAR,
    test_date: str = BALLDONTLIE_SMOKE_TEST_DATE,
) -> dict[str, Any]:
    cleaned = clean_api_key(api_key)
    details = env_key_debug_details(env_var_name, cleaned)
    if not cleaned:
        raise RuntimeError(build_missing_key_message(entrypoint=entrypoint, details=details))

    params = {"dates[]": test_date, "per_page": 1}
    headers = {
        "Authorization": cleaned,
        "Accept": "application/json",
    }
    resolved_url = prepared_url(BALLDONTLIE_GAMES_URL, params)
    response = requests.get(
        BALLDONTLIE_GAMES_URL,
        headers=headers,
        params=params,
        timeout=timeout,
    )
    result = {
        "status_code": int(response.status_code),
        "resolved_url": str(getattr(response, "url", "") or resolved_url),
        "has_auth": bool(headers.get("Authorization")),
        "masked_key_preview": mask_api_key(cleaned),
        "env_var_name": env_var_name,
        "body_snippet": response_text_preview(response),
    }
    _log_smoke_test(entrypoint=entrypoint, result=result, logger=logger)

    if response.status_code == 401:
        raise RuntimeError(
            build_unauthorized_message(
                entrypoint=entrypoint,
                env_var_name=env_var_name,
                masked_key_preview=result["masked_key_preview"],
                url=result["resolved_url"],
                params=params,
                has_auth=result["has_auth"],
                body_snippet=result["body_snippet"],
                details=details,
            )
        )

    response.raise_for_status()
    return result


def build_unauthorized_message(
    *,
    entrypoint: str,
    env_var_name: str,
    masked_key_preview: str,
    url: str,
    params: Mapping[str, Any] | None,
    has_auth: bool,
    body_snippet: str,
    details: Mapping[str, Any] | None = None,
) -> str:
    suffix = []
    detail_map = dict(details or {})
    if detail_map.get("wrong_env_var_candidates"):
        suffix.append(
            "alternate_envs="
            + ",".join(str(item) for item in detail_map.get("wrong_env_var_candidates", []))
        )
    if detail_map.get("loaded_paths"):
        suffix.append("loaded_dotenv=" + ",".join(str(item) for item in detail_map.get("loaded_paths", [])))
    if detail_map.get("had_wrapping_quotes") or detail_map.get("dotenv_had_wrapping_quotes"):
        suffix.append("quote_warning=true")
    if detail_map.get("had_surrounding_whitespace") or detail_map.get("dotenv_had_surrounding_whitespace"):
        suffix.append("whitespace_warning=true")
    suffix_text = f" {' '.join(suffix)}" if suffix else ""
    return (
        f"{BALLDONTLIE_401_HINT} "
        f"entrypoint={entrypoint} "
        f"env_var={env_var_name} "
        f"key={masked_key_preview} "
        f"status=401 "
        f"url={url} "
        f"params={dict(params or {})} "
        f"has_auth={bool(has_auth)} "
        f"body={body_snippet}{suffix_text}"
    )


def build_missing_key_message(*, entrypoint: str, details: Mapping[str, Any]) -> str:
    wrong_envs = ",".join(str(item) for item in details.get("wrong_env_var_candidates", []))
    loaded_paths = ",".join(str(item) for item in details.get("loaded_paths", []))
    searched_paths = ",".join(str(item) for item in details.get("searched_paths", []))
    hints = [
        f"entrypoint={entrypoint}",
        f"env_var={details.get('env_var_name', BALLDONTLIE_API_KEY_ENV_VAR)}",
        "Cause hints: wrong env var name, .env not loaded in this entrypoint, accidental wrapping quotes, or trailing whitespace.",
    ]
    if wrong_envs:
        hints.append(f"alternate_envs={wrong_envs}")
    if loaded_paths:
        hints.append(f"loaded_dotenv={loaded_paths}")
    if searched_paths:
        hints.append(f"searched_dotenv={searched_paths}")
    return "BALLDONTLIE_API_KEY is missing or empty after cleanup. " + " ".join(hints)


def prepared_url(url: str, params: Mapping[str, Any] | None = None) -> str:
    try:
        request = requests.Request("GET", url, params=params)
        return str(request.prepare().url or url)
    except Exception:
        return url


def response_text_preview(response: requests.Response | str | Any, limit: int = 300) -> str:
    try:
        if isinstance(response, requests.Response) or hasattr(response, "text"):
            text = str(getattr(response, "text", "") or "").strip()
        else:
            text = str(response or "").strip()
    except Exception:
        return "<unavailable>"
    if not text:
        return "<empty>"
    return " ".join(text.split())[:limit]


def _has_wrapping_quotes(value: str | None) -> bool:
    text = str(value or "").strip()
    return len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]


def _log_env_diagnostics(
    *,
    entrypoint: str,
    details: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> None:
    target = logger or logging.getLogger("courtvision_auth")
    target.info(
        "BallDontLie key env_var=%s source=%s preview=%s entrypoint=%s",
        details.get("env_var_name", BALLDONTLIE_API_KEY_ENV_VAR),
        details.get("source", "unknown"),
        details.get("masked_preview", "<empty>"),
        entrypoint,
    )
    if details.get("had_wrapping_quotes") or details.get("dotenv_had_wrapping_quotes"):
        target.warning("BallDontLie key quote_warning=true entrypoint=%s", entrypoint)
    if details.get("had_surrounding_whitespace") or details.get("dotenv_had_surrounding_whitespace"):
        target.warning("BallDontLie key whitespace_warning=true entrypoint=%s", entrypoint)
    wrong_env_vars = details.get("wrong_env_var_candidates", [])
    if wrong_env_vars:
        target.warning(
            "BallDontLie alternate_envs=%s entrypoint=%s",
            ",".join(str(item) for item in wrong_env_vars),
            entrypoint,
        )
    if details.get("loaded_paths"):
        target.info(
            "BallDontLie dotenv_loaded=%s entrypoint=%s",
            ",".join(str(item) for item in details.get("loaded_paths", [])),
            entrypoint,
        )
    elif details.get("searched_paths"):
        target.warning(
            "BallDontLie dotenv_not_loaded searched=%s entrypoint=%s",
            ",".join(str(item) for item in details.get("searched_paths", [])),
            entrypoint,
        )


def _log_smoke_test(
    *,
    entrypoint: str,
    result: Mapping[str, Any],
    logger: logging.Logger | None = None,
) -> None:
    target = logger or logging.getLogger("courtvision_auth")
    target.info(
        "BallDontLie smoke_test entrypoint=%s status=%s url=%s has_auth=%s key=%s body=%s",
        entrypoint,
        result.get("status_code"),
        result.get("resolved_url"),
        result.get("has_auth"),
        result.get("masked_key_preview"),
        result.get("body_snippet"),
    )


__all__ = [
    "BALLDONTLIE_401_HINT",
    "BALLDONTLIE_API_KEY_ENV_VAR",
    "BALLDONTLIE_GAMES_URL",
    "BALLDONTLIE_SMOKE_TEST_DATE",
    "ENV_BOOTSTRAP_STATE",
    "ENV_SOURCE_AUDIT",
    "build_missing_key_message",
    "build_unauthorized_message",
    "clean_api_key",
    "default_env_search_paths",
    "env_key_debug_details",
    "load_env_file",
    "mask_api_key",
    "prepared_url",
    "resolve_api_key",
    "response_text_preview",
    "safe_key_fingerprint",
    "smoke_test_games_api",
]
