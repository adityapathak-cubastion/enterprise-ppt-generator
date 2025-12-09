# backend/ai_client.py
from __future__ import annotations
import os
import json
import logging
from typing import Any, Dict, List

from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")


def _check_env() -> None:
    missing = []
    if not AZURE_OPENAI_ENDPOINT:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not AZURE_OPENAI_API_KEY:
        missing.append("AZURE_OPENAI_API_KEY")
    if not AZURE_OPENAI_API_VERSION:
        missing.append("AZURE_OPENAI_API_VERSION")
    if not AZURE_OPENAI_DEPLOYMENT_NAME:
        missing.append("AZURE_OPENAI_DEPLOYMENT_NAME")

    if missing:
        msg = f"Missing Azure OpenAI environment variables: {', '.join(missing)}"
        print(f"[AI_CLIENT] {msg}")
        raise RuntimeError(msg)


def get_azure_client() -> AzureOpenAI:
    """
    Returns an AzureOpenAI client configured from environment variables.
    """
    _check_env()
    print("[AI_CLIENT] Initializing AzureOpenAI client...")
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    return client


def chat_completion_json(
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> Dict[str, Any]:
    """
    Convenience wrapper around Azure OpenAI chat.completions.create
    that enforces JSON output and parses it.
    """
    client = get_azure_client()

    print(
        f"[AI_CLIENT] Calling Azure OpenAI with {len(messages)} messages, "
        f"temperature={temperature}, max_tokens={max_tokens}"
    )
    logger.debug("Sending messages to Azure OpenAI: %s", messages)

    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT_NAME,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    print(f"[AI_CLIENT] Raw response length: {len(content or '')} chars")
    logger.debug("Raw LLM response content: %s", content)

    data = json.loads(content)
    return data
