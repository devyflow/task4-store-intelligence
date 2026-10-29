from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from data_api import DataAPI, STORES

PROJECT = Path(__file__).resolve().parent
load_dotenv(PROJECT / ".env.local")

TOOLS = [
    {
        "type": "function",
        "name": "get_store_overview",
        "description": "Return the required headline metrics for one store, calculated by the analytics database.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"store": {"type": "string", "enum": list(STORES)}},
            "required": ["store"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_users",
        "description": "Count and list users using deterministic filters. Use this for any question about users, aliases, names, or email prefixes.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "enum": list(STORES)},
                "email_prefixes": {"type": ["array", "null"], "items": {"type": "string"}},
                "name_contains": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["store", "email_prefixes", "name_contains", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_books",
        "description": "Count and list books using title, author, and person-name-in-title filters.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "enum": list(STORES)},
                "title_contains": {"type": ["string", "null"]},
                "author_contains": {"type": ["string", "null"]},
                "person_name_filter": {"type": "string", "enum": ["any", "include", "exclude"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["store", "title_contains", "author_contains", "person_name_filter", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_revenue",
        "description": "Calculate revenue, units, and order counts for a date range and grouping.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "enum": list(STORES)},
                "start_date": {"type": ["string", "null"], "description": "Inclusive YYYY-MM-DD date or null."},
                "end_date": {"type": ["string", "null"], "description": "Inclusive YYYY-MM-DD date or null."},
                "group_by": {"type": "string", "enum": ["day", "month", "total"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 400},
            },
            "required": ["store", "start_date", "end_date", "group_by", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "control_dashboard",
        "description": "Change the visible store/view and apply dashboard filters. Use for commands such as filter, show, select, exclude, switch, or reset.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "enum": list(STORES)},
                "view": {"type": "string", "enum": ["overview", "books", "users", "revenue"]},
                "person_name_filter": {"type": "string", "enum": ["any", "include", "exclude"]},
                "email_prefixes": {"type": "array", "items": {"type": "string"}},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "search": {"type": ["string", "null"]},
            },
            "required": ["store", "view", "person_name_filter", "email_prefixes", "start_date", "end_date", "search"],
            "additionalProperties": False,
        },
    },
]

INSTRUCTIONS = """
You are the Store Intelligence data agent. Answer questions and execute dashboard
commands only through the provided tools. Never estimate counts, calculate from
examples, or claim a filter was applied unless the matching tool succeeded.
Use the currently selected store when the user does not name another one.
For data questions, call a query tool and answer concisely from its returned JSON.
For dashboard commands, call control_dashboard. You may also call a query tool
when a command asks for both an action and the resulting count. Never ask for or
expose the complete raw datasets. Explain a limitation plainly if no tool can
answer the request.
""".strip()


class StoreAgent:
    def __init__(self, data_api: DataAPI, model: str | None = None):
        self.data_api = data_api
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")
        self.client = OpenAI()

    def _execute(self, name: str, arguments: dict) -> dict:
        implementations = {
            "get_store_overview": self.data_api.overview,
            "query_users": self.data_api.users,
            "query_books": self.data_api.books,
            "query_revenue": self.data_api.revenue,
            "control_dashboard": self.data_api.dashboard_action,
        }
        if name not in implementations:
            raise ValueError(f"Unknown tool: {name}")
        return implementations[name](**arguments)

    def respond(self, message: str, current_store: str) -> dict:
        prompt = f"Current store: {current_store}.\nUser request: {message}"
        response = self.client.responses.create(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=prompt,
            tools=TOOLS,
            tool_choice="required",
            parallel_tool_calls=False,
            store=True,
        )
        actions: list[dict] = []
        trace: list[dict] = []

        for _ in range(6):
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                return {"answer": response.output_text, "actions": actions, "tool_trace": trace}

            outputs = []
            for call in calls:
                arguments = json.loads(call.arguments)
                try:
                    result = self._execute(call.name, arguments)
                except Exception as exc:
                    result = {"error": str(exc)}
                trace.append({"tool": call.name, "arguments": arguments, "result": result})
                if result.get("type") == "dashboard_update":
                    actions.append(result)
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            response = self.client.responses.create(
                model=self.model,
                instructions=INSTRUCTIONS,
                previous_response_id=response.id,
                input=outputs,
                tools=TOOLS,
                tool_choice="auto",
                parallel_tool_calls=False,
                store=True,
            )
        raise RuntimeError("Agent exceeded the tool-call limit")
