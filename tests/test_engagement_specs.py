"""Every engagement tool must be able to describe itself: a tool whose ``Args`` is missing or
whose schema cannot be built kills the session's first provider request, which is exactly how a
whole engagement fails to run."""

from __future__ import annotations

import json

from intern.engagement.extension import EngagementExtension


def test_every_engagement_tool_builds_a_spec() -> None:
    extension = EngagementExtension()
    tools = extension.tools()
    assert len(tools) >= 20, "the harness ships a tool family per area"
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)), "two engagement tools cannot share a name"
    for tool in tools:
        spec = tool.spec()
        assert spec.name == tool.name
        parameters = json.dumps(spec.parameters)
        assert parameters.strip().startswith("{"), tool.name
        assert "properties" in parameters, tool.name
