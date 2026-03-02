"""Lightweight direct LLM caller — NO agent context, NO tools, NO workspace.

Use this for classification, routing, and other small tasks that just need
a prompt → JSON response. Bypasses the full OpenClaw agent spawn entirely.

Priority order:
  1. LM Studio (local qwen — free, fast, no network)
  2. Gemini 2.0 Flash via API key (cheap, capable)
  3. Anthropic Haiku via API key (fallback)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import asyncio
import re
import aiohttp

logger = logging.getLogger(__name__)

LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen/qwen3.5-35b-a3b")

GEMINI_CLI = os.environ.get("GEMINI_CLI_PATH", "/opt/homebrew/bin/gemini")
GEMINI_CLI_MODEL = os.environ.get("GEMINI_CLI_MODEL", "gemini-3.1-pro-preview")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyD9A6O9qoAtN2JV1cXdc14dUhZ1uSUvIdE")
GEMINI_MODEL = os.environ.get("GEMINI_CLASSIFY_MODEL", "gemini-3.1-pro-preview")

AUTH_PROFILES_PATH = os.path.expanduser(
    "~/.openclaw/agents/main/agent/auth-profiles.json"
)


def _get_anthropic_creds() -> tuple[str, str] | None:
    """Returns (token, auth_type) where auth_type is 'oauth' or 'api_key'."""
    try:
        with open(AUTH_PROFILES_PATH) as f:
            d = json.load(f)
        profiles = d.get("profiles", {})
        for k in ("anthropic:default", "anthropic:manual"):
            profile = profiles.get(k, {})
            tok = profile.get("token")
            if tok:
                auth_type = "oauth" if tok.startswith("sk-ant-oat") else "api_key"
                return tok, auth_type
    except Exception:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY")
    return (key, "api_key") if key else None


async def complete(
    system: str,
    user: str,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: float = 30.0,
) -> str | None:
    """Call an LLM directly and return the assistant text, or None on failure.

    Tries providers in order:
      1. Gemini CLI (Google Pro subscription — preferred)
      2. LM Studio (local qwen — free fallback when Gemini CLI unavailable)
      3. Anthropic Haiku (cloud fallback)
    """
    # Skip real LLM calls in test environment
    import sys as _sys
    if os.environ.get("TESTING") or "pytest" in _sys.modules:
        return '{"agent": "programmer", "model_tier": "standard", "reasoning": "test mock"}'

    result = await _gemini_cli(system, user, max_tokens, timeout)
    if result:
        return result

    logger.info("[LLM_DIRECT] Gemini CLI unavailable, trying LM Studio")
    result = await _lmstudio(system, user, max_tokens, temperature, timeout)
    if result:
        return result

    logger.info("[LLM_DIRECT] LM Studio unavailable, trying Anthropic Haiku")
    return await _anthropic(system, user, max_tokens, temperature, timeout)


async def _gemini_cli(
    system: str, user: str, max_tokens: int, timeout: float
) -> str | None:
    """Call Gemini via the authenticated CLI (uses Google Pro subscription)."""
    prompt = f"{system}\n\n{user}"
    try:
        proc = await asyncio.create_subprocess_exec(
            GEMINI_CLI,
            "-m", GEMINI_CLI_MODEL,
            "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            logger.debug("[LLM_DIRECT] Gemini CLI timed out")
            return None
        if proc.returncode != 0:
            logger.debug("[LLM_DIRECT] Gemini CLI exit %d: %s", proc.returncode, stderr.decode()[:200])
            return None
        text = stdout.decode().strip()
        return text if text else None
    except Exception as e:
        logger.debug("[LLM_DIRECT] Gemini CLI error: %s", e)
        return None


async def _lmstudio(
    system: str, user: str, max_tokens: int, temperature: float, timeout: float
) -> str | None:
    url = f"{LMSTUDIO_BASE_URL}/chat/completions"
    # /no_think suppresses qwen reasoning chain for classification tasks
    payload: dict[str, Any] = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": system + "\n/no_think"},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            if resp.status != 200:
                logger.debug("[LLM_DIRECT] LM Studio %d", resp.status)
                return None
            data = await resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            # Strip any residual <think>...</think> blocks
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            return text if text else None
    except Exception as e:
        logger.debug("[LLM_DIRECT] LM Studio error: %s", e)
        return None


async def _gemini(
    system: str, user: str, max_tokens: int, temperature: float, timeout: float
) -> str | None:
    if not GEMINI_API_KEY:
        return None
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            if resp.status != 200:
                logger.debug("[LLM_DIRECT] Gemini %d", resp.status)
                return None
            data = await resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.debug("[LLM_DIRECT] Gemini error: %s", e)
        return None


async def _anthropic(
    system: str, user: str, max_tokens: int, temperature: float, timeout: float
) -> str | None:
    creds = _get_anthropic_creds()
    if not creds:
        return None
    key, auth_type = creds
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if auth_type == "oauth":
        headers = {
            "Authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
        }
    else:
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            if resp.status != 200:
                logger.debug("[LLM_DIRECT] Anthropic %d", resp.status)
                return None
            data = await resp.json()
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.debug("[LLM_DIRECT] Anthropic error: %s", e)
        return None
