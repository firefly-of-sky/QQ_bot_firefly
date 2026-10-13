"""Conversation execution; QQ event handling does not own model/tool logic."""
from dataclasses import replace

from .agent import Agent
from .llm import messages
from .progress import task_kind
from .search import SALARY_RULES, salary_question, search_context, strip_search_citations
from .tools import build_tools


class ConversationRuntime:
    def __init__(self, cfg, llm, image_loader, search):
        self.cfg, self.llm = cfg, llm
        self.image_loader, self.search = image_loader, search

    async def respond(self, model, history, text, urls, query, search_required, state):
        if model.supports_tools:
            if urls:
                state.stage = "image"
            images = await self.image_loader.load(urls) if urls else None
            state.stage = task_kind(query, bool(urls))
            registry = build_tools(self.cfg, self.search, state,
                                   web_enabled=(self.cfg.auto_search or search_required) and self.search.available())
            result = await Agent(self.cfg, self.llm, registry).run(
                model, history, text, images, force_search=search_required)
            return result.text
        return await self.legacy(model, history, text, urls, query, search_required, state)

    async def legacy(self, model, history, text, urls, query, search_required, state):
        """Explicit compatibility mode for providers without native tool calls."""
        results = []
        if search_required:
            state.stage = "search"
            results = await self.search.query(query)
            text += search_context(results, query, min(18000, self.cfg.context_budget_bytes // 2))
        if urls:
            state.stage = "image"
        images = await self.image_loader.load(urls) if urls else None
        state.stage = task_kind(query, bool(urls))
        if results and state.stage == "thinking":
            state.stage = "summary"
        cfg = self.cfg
        if results and salary_question(query):
            cfg = replace(cfg, system_prompt=cfg.system_prompt + SALARY_RULES)
        answer = await self.llm.complete(model, messages(cfg, history, text, images))
        return strip_search_citations(answer, results) if results else answer
