"""Model Client - Abstract interface and implementations.

Provides:
- ModelClient (abstract base): Defines the interface for all model clients.
- FakeModelClient: Returns preset actions for testing (no network).
- AnthropicModelClient: Uses Anthropic Claude API (requires API key).
- OpenAIModelClient: Uses OpenAI API (optional).

Factory function create_model_client() picks the right implementation.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token usage for a single API call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelClient(ABC):
    """Abstract model client interface."""

    def __init__(self):
        self._call_count: int = 0
        self._total_usage = TokenUsage()

    @abstractmethod
    def get_action(
        self,
        system_prompt: str,
        tools: list[dict],
        messages: list[dict] | None = None,
    ) -> dict:
        """Get a structured action from the model.

        Args:
            system_prompt: The system prompt with context.
            tools: List of tool definitions in OpenAI function format.
            messages: Optional conversation history.

        Returns:
            Parsed action dict with 'action' key.

        Raises:
            ModelError: On API failure, timeout, or invalid response.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    def call_count(self) -> int:
        """Number of API calls made."""
        return self._call_count

    @property
    def total_usage(self) -> TokenUsage:
        """Cumulative token usage across all calls."""
        return self._total_usage


class ModelError(Exception):
    """Raised when the model client fails."""


# ── Fake Model Client ──────────────────────────────────────────────────

class FakeModelClient(ModelClient):
    """Fake model client for testing. Returns preset actions.

    Usage:
        # Pre-program a sequence of actions
        fake = FakeModelClient("fake-test", actions=[
            {"action": "call_tool", "tool_name": "get_issue", ...},
            {"action": "finish", "assessment": {...}},
        ])
    """

    def __init__(self, name: str = "fake-test",
                 actions: list[dict] | None = None):
        super().__init__()
        self._name = name
        self._actions = actions or []
        self._index = 0

    @property
    def model_name(self) -> str:
        return self._name

    def add_action(self, action: dict):
        """Add an action to the sequence."""
        self._actions.append(action)

    def set_actions(self, actions: list[dict]):
        """Replace the action sequence."""
        self._actions = list(actions)
        self._index = 0

    def get_action(
        self,
        system_prompt: str,
        tools: list[dict],
        messages: list[dict] | None = None,
    ) -> dict:
        """Return the next preset action."""
        self._call_count += 1
        if self._index < len(self._actions):
            action = self._actions[self._index]
            self._index += 1
            return dict(action)
        # Default: finish with generic assessment
        return {
            "action": "finish",
            "assessment": {
                "issue_id": "unknown",
                "risk_level": "low",
                "recommended_action": "manual_confirm_required",
                "confidence": 0.5,
                "summary": "Fake model default assessment.",
                "evidence_refs": [],
                "needs_human_review": True,
            },
        }


# ── Real Model Clients ─────────────────────────────────────────────────

def _parse_model_response(raw_text: str) -> dict:
    """Parse model response text into a dict.

    Handles:
    - Pure JSON
    - JSON inside markdown code fences
    - JSON with surrounding text
    """
    text = raw_text.strip()

    # Try direct JSON first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding JSON object in text
    brace_start = text.find('{')
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start:i + 1])
                    except json.JSONDecodeError:
                        break

    raise ModelError(f"Cannot parse model response as JSON: {raw_text[:200]}...")


class OpenAICompatibleClient(ModelClient):
    """Generic OpenAI-compatible API client.

    Works with any OpenAI-compatible endpoint by setting `base_url`.
    Supports: DeepSeek, OpenAI, local models (vLLM, Ollama), etc.

    Uses the Chat Completions API with tool calling.
    """

    def __init__(self, model: str = "deepseek-chat",
                 api_key: str | None = None,
                 base_url: str = "https://api.deepseek.com",
                 timeout: int = 60):
        super().__init__()
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        if not self._api_key:
            raise ModelError(
                f"API key not set for {model}. "
                f"Set DEEPSEEK_API_KEY or pass api_key."
            )

    @property
    def model_name(self) -> str:
        return self._model

    def get_action(
        self,
        system_prompt: str,
        tools: list[dict],
        messages: list[dict] | None = None,
    ) -> dict:
        """Call OpenAI-compatible Chat Completions API."""
        import urllib.error
        import urllib.request

        url = f"{self._base_url}/v1/chat/completions"

        # Build messages array
        msg_list = [{"role": "system", "content": system_prompt}]
        if messages:
            msg_list.extend(messages)
        else:
            msg_list.append({
                "role": "user",
                "content": "Please analyze the current issue and take the next action. "
                           "Respond with a valid JSON object."
            })

        body = json.dumps({
            "model": self._model,
            "messages": msg_list,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1024,
        }).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # Track token usage
            self._call_count += 1
            usage = data.get("usage", {})
            if usage:
                self._total_usage.prompt_tokens += usage.get("prompt_tokens", 0)
                self._total_usage.completion_tokens += usage.get("completion_tokens", 0)
                self._total_usage.total_tokens += usage.get("total_tokens", 0)

            choice = data["choices"][0]
            message = choice.get("message", {})

            # Check for tool calls first
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                tc = tool_calls[0]
                func = tc["function"]
                try:
                    arguments = json.loads(func["arguments"])
                except json.JSONDecodeError:
                    arguments = {"raw": func["arguments"]}
                return {
                    "action": "call_tool",
                    "tool_name": func["name"],
                    "arguments": arguments,
                    "reason": f"Using {func['name']}",
                }

            # Otherwise parse text content as JSON
            content = message.get("content", "")
            if content:
                return _parse_model_response(content)

            raise ModelError("No usable content in API response")  # noqa: B904

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ModelError(f"API HTTP {e.code}: {body[:500]}") from e
        except Exception as e:
            raise ModelError(f"API error: {e}") from e


class AnthropicModelClient(ModelClient):
    """Model client using Anthropic Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-6",
                 api_key: str | None = None,
                 timeout: int = 60):
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._timeout = timeout

        if not self._api_key:
            raise ModelError(
                "ANTHROPIC_API_KEY not set. Set the environment variable "
                "or pass api_key to AnthropicModelClient."
            )

    @property
    def model_name(self) -> str:
        return self._model

    def get_action(
        self,
        system_prompt: str,
        tools: list[dict],
        messages: list[dict] | None = None,
    ) -> dict:
        """Call Anthropic API and parse the response."""
        try:
            # Use the anthropic SDK if available, otherwise HTTP
            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=self._api_key, timeout=self._timeout)

                msg_list = messages or []
                response = client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=system_prompt,
                    messages=msg_list if msg_list else [
                        {"role": "user", "content": "Please analyze the current issue and take the next action."}
                    ],
                    tools=tools,
                )

                # Extract tool use or text
                for block in response.content:
                    if block.type == "tool_use":
                        return {
                            "action": "call_tool",
                            "tool_name": block.name,
                            "arguments": dict(block.input),
                            "reason": f"Using {block.name}",
                        }
                    elif block.type == "text":
                        return _parse_model_response(block.text)

                raise ModelError("No usable content in Anthropic response")

            except ImportError:
                # Fall back to requests
                import urllib.error
                import urllib.request

                url = "https://api.anthropic.com/v1/messages"
                headers = {
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                body = json.dumps({
                    "model": self._model,
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": messages or [
                        {"role": "user", "content": "Please analyze the current issue and take the next action."}
                    ],
                    "tools": tools,
                }).encode("utf-8")

                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                for block in data.get("content", []):
                    if block.get("type") == "tool_use":
                        return {
                            "action": "call_tool",
                            "tool_name": block["name"],
                            "arguments": dict(block.get("input", {})),
                            "reason": f"Using {block['name']}",
                        }
                    elif block.get("type") == "text":
                        return _parse_model_response(block["text"])

                raise ModelError("No usable content in Anthropic response")  # noqa: B904

        except urllib.error.HTTPError as e:
            raise ModelError(f"Anthropic API HTTP {e.code}: {e.reason}") from e
        except Exception as e:
            raise ModelError(f"Anthropic API error: {e}") from e


# ── Factory ────────────────────────────────────────────────────────────

def _load_api_key_from_env_file() -> dict[str, str]:
    """Load API keys from .env file in current directory or project root.

    Returns dict of env var name -> value. Does NOT override existing env vars.
    """
    import os
    keys = {}
    # Check common locations for .env
    candidates = [
        ".env",
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
        os.path.expanduser("~/.unity-audit.env"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k and v and k not in os.environ:
                                keys[k] = v
            except OSError:
                pass
    return keys


# Load .env keys at import time
_ENV_FILE_KEYS = _load_api_key_from_env_file()


def create_model_client(
    model_name: str,
    api_key: str | None = None,
    timeout: int = 60,
) -> ModelClient:
    """Create a model client based on the model name.

    Args:
        model_name: Model identifier.
            - "fake:..." -> FakeModelClient
            - "claude-..." -> AnthropicModelClient
            - "deepseek:..." -> OpenAICompatibleClient (DeepSeek)
            - "deepseek-chat" or "deepseek-reasoner" -> DeepSeek auto-detect
            - "gpt-..." or "openai:..." -> OpenAICompatibleClient (OpenAI)
        api_key: API key. If None, looks in env vars and .env file.
        timeout: Request timeout in seconds.

    Returns:
        ModelClient instance.
    """
    if model_name.startswith("fake:"):
        return FakeModelClient(name=model_name)

    if model_name.startswith("claude-"):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or _ENV_FILE_KEYS.get("ANTHROPIC_API_KEY")
        if not key:
            raise ModelError(
                f"Model '{model_name}' requires ANTHROPIC_API_KEY. "
                f"Set it in environment, .env file, or --api-key."
            )
        return AnthropicModelClient(model=model_name, api_key=key, timeout=timeout)

    if model_name.startswith("deepseek:") or model_name.startswith("deepseek-"):
        # Support "deepseek:chat" or "deepseek-chat"
        if model_name.startswith("deepseek:"):
            model = model_name.split(":", 1)[1]
        else:
            model = model_name
        key = api_key or os.environ.get("DEEPSEEK_API_KEY") or _ENV_FILE_KEYS.get("DEEPSEEK_API_KEY")
        if not key:
            raise ModelError(
                f"Model '{model_name}' requires DEEPSEEK_API_KEY. "
                f"Set it in environment, .env file, or --api-key."
            )
        return OpenAICompatibleClient(
            model=model,
            api_key=key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
        )

    if model_name.startswith("gpt-") or model_name.startswith("openai:"):
        if model_name.startswith("openai:"):
            model = model_name.split(":", 1)[1]
        else:
            model = model_name
        key = api_key or os.environ.get("OPENAI_API_KEY") or _ENV_FILE_KEYS.get("OPENAI_API_KEY")
        if not key:
            raise ModelError(
                f"Model '{model_name}' requires OPENAI_API_KEY."
            )
        return OpenAICompatibleClient(
            model=model,
            api_key=key,
            base_url="https://api.openai.com",
            timeout=timeout,
        )

    raise ValueError(f"Unknown model: {model_name}")
