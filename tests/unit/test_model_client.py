import sys
from types import SimpleNamespace

from unity_audit.agents.model_client import AnthropicModelClient


def test_anthropic_client_initializes_usage_counters():
    client = AnthropicModelClient(api_key="test-key")

    assert client.call_count == 0
    assert client.total_usage.total_tokens == 0


def test_anthropic_client_converts_tools_and_tracks_usage(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(
                    type="tool_use",
                    name="inspect_asset",
                    input={"asset_path": "Textures/a.png"},
                )],
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=FakeAnthropic),
    )
    client = AnthropicModelClient(api_key="test-key")
    openai_tools = [{
        "type": "function",
        "function": {
            "name": "inspect_asset",
            "description": "Inspect an asset",
            "parameters": {
                "type": "object",
                "properties": {"asset_path": {"type": "string"}},
                "required": ["asset_path"],
            },
        },
    }]

    action = client.get_action("system", openai_tools)

    assert captured["tools"] == [{
        "name": "inspect_asset",
        "description": "Inspect an asset",
        "input_schema": openai_tools[0]["function"]["parameters"],
    }]
    assert action["tool_name"] == "inspect_asset"
    assert client.call_count == 1
    assert client.total_usage.prompt_tokens == 11
    assert client.total_usage.completion_tokens == 7
    assert client.total_usage.total_tokens == 18
