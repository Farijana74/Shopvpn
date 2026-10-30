# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""تولید خروجی JSON از Provider های تنظیم‌شده‌ی دستیار هوشمند، بدون ابزار و بدون مکالمه."""

import asyncio
import json
import logging
import re

import aiohttp

import ai_support
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

_log = logging.getLogger("ai_text")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict:
    """اولین آبجکت JSON داخل متن را برمی‌گرداند؛ در صورت نامعتبر بودن ValueError."""
    match = _JSON_RE.search(text or "")
    if not match:
        raise ValueError("JSON object not found")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("JSON root is not an object")
    return data


async def _gemini(db, system_prompt: str, user_text: str) -> str:
    from google.genai import types

    keys = ai_support.resolve_gemini_keys(db)
    if not keys:
        raise RuntimeError("Gemini API key تنظیم نشده")
    config = types.GenerateContentConfig(
        system_instruction=system_prompt, response_mime_type="application/json", temperature=0.4,
    )
    model = ai_support.resolve_gemini_model(db)
    last_exc = None
    for api_key in keys:
        client = ai_support._build_client(api_key)
        try:
            response = await asyncio.to_thread(
                client.models.generate_content, model=model, contents=user_text, config=config,
            )
            return (response.text or "").strip()
        except Exception as exc:
            last_exc = exc
            if not ai_support._is_retryable(exc):
                raise
    raise last_exc or RuntimeError("Gemini failed")


async def _openai_compatible(db, provider: str, system_prompt: str, user_text: str) -> str:
    keys = ai_support.resolve_provider_keys(db, provider)
    if not keys:
        raise RuntimeError(f"{provider} API key تنظیم نشده")
    url = ai_support.resolve_provider_url(db, provider)
    model = ai_support.resolve_provider_model(db, provider)
    if not url or not model:
        raise RuntimeError(f"{provider} مدل یا آدرس API تنظیم نشده")
    last_exc = None
    for api_key in keys:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://telegram.org/"
            headers["X-Title"] = "ShopVPN Churn"
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            "temperature": 0.4,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=45, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"{provider} HTTP {resp.status}: {body[:300]}")
            data = json.loads(body)
            return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            last_exc = exc
            if not ai_support._is_retryable(exc):
                raise
    raise last_exc or RuntimeError(f"{provider} failed")


async def generate_json(db, system_prompt: str, user_text: str) -> dict:
    """از Provider های فعال به‌ترتیب تلاش می‌کند و اولین JSON معتبر را برمی‌گرداند."""
    if not ai_support.is_configured(db):
        raise RuntimeError("AI not configured")
    providers = ai_support.active_providers(db)
    last_exc = None
    for provider in providers:
        try:
            if provider == "gemini":
                raw = await _gemini(db, system_prompt, user_text)
            else:
                raw = await _openai_compatible(db, provider, system_prompt, user_text)
            return parse_json_object(raw)
        except Exception as exc:
            last_exc = exc
            _log.warning("ai_text provider %s failed: %s", provider, exc)
    raise last_exc or RuntimeError("all AI providers failed")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
