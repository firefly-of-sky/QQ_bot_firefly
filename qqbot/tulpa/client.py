"""MCP wire client. Schemas are discovered at runtime, including nested objects."""
from contextlib import asynccontextmanager
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect(url, token='', headers=None):
    headers = dict(headers or {})
    if token:
        headers['Authorization'] = 'Bearer '+token
    async with httpx.AsyncClient(headers=headers, trust_env=False, timeout=250) as http:
        async with streamable_http_client(url, http_client=http) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield Peer(session)


class Peer:
    def __init__(self, session):
        self.session = session
        self.tools = {}

    async def discover(self):
        cursor = None
        while True:
            page = await self.session.list_tools(cursor=cursor)
            for tool in page.tools:
                self.tools[tool.name] = tool
            cursor = page.nextCursor
            if not cursor:
                return self.tools

    def schemas(self, names=None, prefix=''):
        return [{'type': 'function', 'function': {
            'name': prefix+name, 'description': tool.description or name,
            'parameters': tool.inputSchema}} for name, tool in self.tools.items()
            if names is None or name in names]

    async def call(self, name, arguments):
        result = await self.session.call_tool(name, arguments)
        data = result.structuredContent
        if data is None:
            text = '\n'.join(block.text for block in result.content if block.type == 'text')
            try:
                data = json.loads(text)
            except ValueError:
                data = {'text': text}
        return data, result.content
