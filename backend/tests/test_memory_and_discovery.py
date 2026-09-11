"""Memory agent and tool discovery (AC-04, AC-10, AC-11, AC-12)."""

from __future__ import annotations

import pytest

from app.agents.memory_agent import MemoryAgent, score_favourite
from app.agents.page_agent import PageAgent
from app.agents.tool_discovery import DiscoveredTool, ToolDiscovery, expand_tool

JOBS = {
    "id": "f1",
    "name": "AI Jobs",
    "url": "https://jobs.example.com/search",
    "domain": "jobs.example.com",
    "intent": "Find entry-level AI/ML jobs",
    "description": "Early-career machine learning roles",
    "preferences": {"location": "India", "experience": "0-2 years"},
}
LAPTOPS = {
    "id": "f2",
    "name": "Gaming Laptops",
    "url": "https://shop.example.com/laptops",
    "domain": "shop.example.com",
    "intent": "Find RTX 4060 laptops under 80000",
    "description": "Laptop shortlist",
    "preferences": {"budget": "80000", "gpu": "RTX 4060"},
}
PAPERS = {
    "id": "f3",
    "name": "Research Papers",
    "url": "https://arxiv.example.com/list",
    "domain": "arxiv.example.com",
    "intent": "Track new transformer papers",
    "description": "Recent NLP research",
    "preferences": {},
}
ALL = [JOBS, LAPTOPS, PAPERS]


# --- scoring --------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_id",
    [
        ("open my AI jobs favourite", "f1"),
        ("check my AI jobs", "f1"),
        ("my job search", "f1"),
        ("open my laptop search", "f2"),
        ("show me gaming laptops", "f2"),
        ("find RTX 4060 under 80000", "f2"),
        ("search my research favourite", "f3"),
        ("open my papers", "f3"),
        ("transformer papers", "f3"),
    ],
)
async def test_natural_language_references_resolve(query: str, expected_id: str) -> None:
    match = await MemoryAgent(None).resolve(query, ALL, use_llm=False)
    assert match.found, f"no match for {query!r}"
    assert match.favourite["id"] == expected_id


async def test_unrelated_reference_does_not_match() -> None:
    match = await MemoryAgent(None).resolve("open my sourdough recipes", ALL, use_llm=False)
    assert not match.found
    assert match.method == "no-match"
    assert len(match.alternatives) == 3


async def test_resolution_works_without_a_model() -> None:
    """Favourites must stay usable when the LLM is unreachable."""
    match = await MemoryAgent(None).resolve("check my AI jobs", ALL)
    assert match.found
    assert match.method.startswith("lexical")


def test_exact_name_dominates() -> None:
    assert score_favourite("open my AI Jobs", JOBS) > score_favourite("open my AI Jobs", LAPTOPS)


def test_empty_query_scores_zero() -> None:
    assert score_favourite("", JOBS) == 0.0
    assert score_favourite("the my a", JOBS) == 0.0


async def test_no_favourites_is_handled() -> None:
    match = await MemoryAgent(None).resolve("anything", [])
    assert not match.found
    assert match.method == "none"


async def test_llm_tiebreak_only_accepts_offered_ids(fake_llm) -> None:
    """A hallucinated id must not be honoured."""
    fake_llm.push({"favourite_id": "f-does-not-exist"})
    match = await MemoryAgent(fake_llm).resolve("open my thing", ALL)
    assert match.favourite is None or match.favourite["id"] in {"f1", "f2", "f3"}


# --- favourite creation (AC-10) ------------------------------------------


async def test_draft_favourite_uses_the_model(fake_llm, product_page) -> None:
    fake_llm.push(
        {
            "name": "Gaming Laptops",
            "intent": "Find RTX 4060 laptops under 80000",
            "description": "Laptop shortlist",
            "preferences": {"budget": "80000", "gpu": "RTX 4060"},
        }
    )
    draft = await MemoryAgent(fake_llm).draft_favourite(
        "save this as my gaming laptops, budget 80000", product_page
    )
    assert draft["name"] == "Gaming Laptops"
    assert draft["preferences"]["gpu"] == "RTX 4060"
    assert draft["url"] == product_page["url"]
    assert draft["domain"] == "shop.example.com"


async def test_draft_favourite_falls_back_without_a_model(product_page) -> None:
    draft = await MemoryAgent(None).draft_favourite(
        "save this as my laptop search", product_page
    )
    assert draft["name"] == "Laptop Search"
    assert draft["url"] == product_page["url"]
    assert draft["intent"]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("save this as my laptop search", "Laptop Search"),
        ('save this as "AI Jobs"', "Ai Jobs"),
        ("save this as the research list", "Research List"),
    ],
)
async def test_fallback_name_extraction(message: str, expected: str, product_page) -> None:
    draft = await MemoryAgent(None).draft_favourite(message, product_page)
    assert draft["name"] == expected


async def test_draft_falls_back_when_the_model_is_unreachable(fake_llm, product_page) -> None:
    """Saving a favourite must not depend on the model being up."""
    fake_llm.unavailable = True
    draft = await MemoryAgent(fake_llm).draft_favourite(
        "save this as my laptop search", product_page
    )
    assert draft["name"] == "Laptop Search"
    assert draft["url"] == product_page["url"]
    assert draft["metadata"]["source"] == "heuristic"


# --- AC-12: goal composition ---------------------------------------------


def test_goal_carries_intent_and_preferences() -> None:
    goal = MemoryAgent.build_goal(JOBS, "check my AI jobs")
    assert "check my AI jobs" in goal
    assert "Find entry-level AI/ML jobs" in goal
    assert "location: India" in goal
    assert "experience: 0-2 years" in goal


def test_goal_without_preferences_is_still_valid() -> None:
    goal = MemoryAgent.build_goal(PAPERS, "check my papers")
    assert "Track new transformer papers" in goal


# --- AC-04: semantic understanding and tool discovery --------------------


def test_page_agent_identifies_the_expected_controls(product_page) -> None:
    understanding, page, scan = PageAgent().analyze(product_page)

    assert understanding.domain == "shop.example.com"
    assert understanding.has_search
    assert "e1" in understanding.search_input_ids
    assert "e2" in understanding.search_button_ids
    assert understanding.has_filters
    assert "e3" in understanding.filter_ids
    assert understanding.has_pagination
    assert "e5" in understanding.pagination_ids
    assert not scan.is_suspicious


def test_page_agent_classifies_an_auth_page() -> None:
    page = {
        "url": "https://x.example.com/login",
        "title": "Sign in",
        "summary": "Sign in to continue",
        "elements": [
            {"id": "e1", "type": "input", "inputType": "email", "ariaLabel": "Email"},
            {"id": "e2", "type": "input", "inputType": "password", "ariaLabel": "Password"},
            {"id": "e3", "type": "button", "text": "Log in"},
        ],
    }
    understanding, _, _ = PageAgent().analyze(page)
    assert understanding.requires_auth
    assert understanding.page_type == "auth"


def test_page_agent_handles_an_empty_page() -> None:
    understanding, page, scan = PageAgent().analyze({})
    assert understanding.element_count == 0
    assert not understanding.has_search
    assert not scan.is_suspicious


async def test_tool_discovery_finds_site_capabilities(product_page) -> None:
    understanding, page, _ = PageAgent().analyze(product_page)
    result = await ToolDiscovery().discover(understanding, page)

    names = {t["name"] for t in result["tools"]}
    assert "search" in names
    assert "next_page" in names
    assert any(n.startswith("filter_") for n in names)
    assert result["source"] == "heuristic"

    search = next(t for t in result["tools"] if t["name"] == "search")
    assert search["element_ids"] == ["e1", "e2"]
    assert search["action_template"][0]["action"] == "TYPE"
    assert search["action_template"][1]["action"] == "CLICK"


def test_tool_expansion_substitutes_parameters() -> None:
    tool = DiscoveredTool(
        name="search",
        description="search",
        element_ids=["e1", "e2"],
        action_template=[
            {"action": "TYPE", "target": "e1", "value": "{query}"},
            {"action": "CLICK", "target": "e2"},
        ],
    )
    actions = expand_tool(tool, {"query": "RTX 4060"})
    assert actions[0]["value"] == "RTX 4060"
    assert actions[1] == {"action": "CLICK", "target": "e2"}


def test_tool_expansion_requires_its_arguments() -> None:
    tool = DiscoveredTool(
        name="search",
        description="search",
        action_template=[{"action": "TYPE", "target": "e1", "value": "{query}"}],
    )
    with pytest.raises(ValueError, match="query"):
        expand_tool(tool, {})


async def test_llm_discovered_tools_referencing_absent_elements_are_dropped(
    fake_llm, product_page
) -> None:
    """A model cannot introduce an element that is not on the page."""
    understanding, page, _ = PageAgent().analyze(product_page)
    fake_llm.push(
        {
            "tools": [
                {"name": "real", "description": "ok", "element_ids": ["e1"]},
                {"name": "fake", "description": "hallucinated", "element_ids": ["e404"]},
            ]
        }
    )
    tools = await ToolDiscovery(fake_llm).discover_llm(understanding, page)
    names = {t.name for t in tools}
    assert "real" in names
    assert "fake" not in names
