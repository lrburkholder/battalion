"""Shared helpers for extracting content from litellm-style completion
responses, used across node implementations."""
from __future__ import annotations

from typing import Any


def extract_content(response: Any) -> str:
    """Extract text content from a litellm/OpenAI-shaped completion
    response, whether it's a dict (as in tests) or a real ModelResponse
    object (attribute access)."""
    if isinstance(response, dict):
        return response["choices"][0]["message"]["content"]
    return response.choices[0].message.content
