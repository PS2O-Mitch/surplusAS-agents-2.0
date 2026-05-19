"""Bridge from a `google.adk.Agent` to an `a2a.server.agent_execution.AgentExecutor`.

`vertexai.agent_engines.templates.a2a.A2aAgent` needs an `AgentExecutor`
implementation that handles `execute(context, event_queue)` and `cancel(...)`.
This file provides `AdkAgentExecutor` which:

1. Reads the inbound A2A `Message` text out of the request context.
2. Drives the ADK agent via `InMemoryRunner.run_async`.
3. Forwards the ADK event stream onto the A2A `EventQueue` as text messages.

It is intentionally minimal — the Phase-7a spike validates that the
ADK -> A2A path works at all on Vertex. A full migration will replace
this with whatever pattern Google ships once their docs land.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue
    from a2a.types import AgentCard
    from google.adk import Agent

logger = logging.getLogger("surplusas.a2a_bridge")


def build_adk_executor(agent: Agent) -> type:
    """Factory: return a builder Callable that A2aAgent's ctor accepts.

    A2aAgent expects `agent_executor_builder: Callable[..., AgentExecutor]`.
    We return a closure that builds an `AdkAgentExecutor` bound to `agent`.
    """
    # Late imports — the agent runs server-side, where these are available.
    from a2a.server.agent_execution import AgentExecutor

    class AdkAgentExecutor(AgentExecutor):
        """A2A executor that delegates to an ADK Agent via InMemoryRunner."""

        def __init__(self) -> None:
            from google.adk.runners import InMemoryRunner

            self._agent = agent
            self._runner = InMemoryRunner(agent=agent, app_name=agent.name)

        async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
        ) -> None:
            from a2a.helpers.proto_helpers import new_text_message
            from google.genai import types as genai_types

            # Pull plain text from the A2A Message. Future iteration:
            # support function_call parts so peers can call ADK tools directly.
            text = ""
            msg = getattr(context, "message", None)
            if msg is not None:
                for part in (msg.parts or []):
                    root = getattr(part, "root", part)
                    if hasattr(root, "text") and root.text:
                        text += root.text

            user_id = getattr(context, "user_id", None) or "anonymous"
            session_id = getattr(context, "task_id", None) or str(uuid.uuid4())

            content = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=text or "")],
            )

            final_text = ""
            try:
                async for event in self._runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=content,
                ):
                    parts = (event.content.parts if event.content else None) or []
                    for p in parts:
                        text_val = getattr(p, "text", None)
                        if text_val:
                            final_text = text_val
            except Exception:
                logger.exception("ADK runner failed inside A2A executor")
                raise

            await event_queue.enqueue_event(new_text_message(final_text or ""))

        async def cancel(
            self,
            context: RequestContext,
            event_queue: EventQueue,
        ) -> None:
            # ADK Runner doesn't expose mid-flight cancellation today.
            # Best-effort: enqueue a terminal text noting the cancel request.
            from a2a.helpers.proto_helpers import new_text_message
            await event_queue.enqueue_event(new_text_message("[cancelled]"))

    return AdkAgentExecutor


def build_default_card(
    *, agent_name: str, description: str, a2a_url: str
) -> AgentCard:
    """Build an A2A AgentCard for a Vertex-deployed agent.

    The `a2a_url` is the public base URL Vertex exposes the engine at;
    we don't know it at build time, so a placeholder is fine — Vertex
    overrides the URL during `agent_engines.create()` deployment.
    """
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
    from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol

    skills = [
        AgentSkill(
            id="default",
            name=agent_name,
            description=description,
            tags=[agent_name],
            examples=[],
        )
    ]
    return AgentCard(
        name=agent_name,
        description=description,
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True, extended_agent_card=False),
        skills=skills,
        supported_interfaces=[
            AgentInterface(
                url=a2a_url,
                protocol_binding=TransportProtocol.HTTP_JSON,
                protocol_version=PROTOCOL_VERSION_CURRENT,
            )
        ],
    )
