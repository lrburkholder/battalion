"""Completion envelopes shared by node and application acceptance tests.

These helpers serialize the supplied content without validating role policy.
Malformed payloads must reach the production parser in negative-path tests.
"""
import json
from typing import TypedDict


class Message(TypedDict):
    content: str


class Choice(TypedDict):
    message: Message


class Completion(TypedDict):
    choices: list[Choice]


def litellm_response(content: str) -> Completion:
    return {"choices": [{"message": {"content": content}}]}


def json_response(payload: object, *, fenced: bool = False) -> Completion:
    content = json.dumps(payload)
    if fenced:
        content = f"```json\n{content}\n```"
    return litellm_response(content)


def files_response(files: dict[str, str], *, fenced: bool = False) -> Completion:
    return json_response({"files": files}, fenced=fenced)


def no_change_response(reason: str) -> Completion:
    return json_response({"outcome": "no-change", "files": {}, "reason": reason})
