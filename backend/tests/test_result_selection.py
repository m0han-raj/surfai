"""Choosing which extracted results answer the question.

The whole design rests on one rule: the model picks indices, and the fields
come from the page. A model asked to repeat a price will occasionally get it
wrong, and a wrong price on a shopping card is not a cosmetic defect. A model
that can only return [0, 3, 7] cannot invent one.

So most of these are about what happens when the model returns something
unhelpful, because that is the only way a fabricated field could ever reach a
card.
"""

from __future__ import annotations

from app.agents.results import ResultItem, select_results

ITEMS = [
    {"title": "Nike Revolution 7 Running Shoes", "price": "₹2,499", "url": "https://x/1"},
    {"title": "Nike Court Vision Low", "price": "₹899", "url": "https://x/2"},
    {"title": "Puma Flex Renew", "price": "₹1,799", "url": "https://x/3"},
]


def test_the_chosen_items_come_back_whole() -> None:
    chosen = select_results(ITEMS, [1, 0])

    assert [item.title for item in chosen] == [
        "Nike Court Vision Low",
        "Nike Revolution 7 Running Shoes",
    ]
    assert [item.price for item in chosen] == ["₹899", "₹2,499"]


def test_the_model_s_order_is_kept() -> None:
    """It ranked them; showing them in page order would discard that."""
    assert [item.title for item in select_results(ITEMS, [2, 1])][0] == "Puma Flex Renew"


def test_an_index_that_does_not_exist_is_dropped() -> None:
    """Rather than raising, or worse, wrapping round to the wrong product."""
    chosen = select_results(ITEMS, [1, 99, -1, 0])
    assert [item.title for item in chosen] == [
        "Nike Court Vision Low",
        "Nike Revolution 7 Running Shoes",
    ]


def test_a_repeated_index_appears_once() -> None:
    assert len(select_results(ITEMS, [0, 0, 0])) == 1


def test_choosing_nothing_yields_nothing() -> None:
    """A search with no matches shows no cards, not every card."""
    assert select_results(ITEMS, []) == []


def test_no_selection_at_all_yields_nothing() -> None:
    """A model that omitted the field has not said "show everything"."""
    assert select_results(ITEMS, None) == []


def test_items_that_are_not_objects_are_ignored() -> None:
    """The list arrives from a page, through a model's tool result."""
    assert select_results(["not an item", 42, None], [0, 1, 2]) == []


def test_an_item_with_no_title_is_ignored() -> None:
    """A card with nothing to read is worse than one card fewer."""
    assert select_results([{"price": "₹10"}], [0]) == []


def test_fields_the_page_did_not_have_stay_absent() -> None:
    item = select_results([{"title": "A job", "url": "https://x/j"}], [0])[0]

    assert item.price is None
    assert item.image is None
    assert item.meta == []


def test_a_link_the_panel_must_not_follow_is_dropped() -> None:
    """Card urls come from page content, so they get the same allowlist as
    every other page-derived link."""
    for href in ("javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd"):
        item = select_results([{"title": "Click me", "url": href}], [0])[0]
        assert item.url is None, href
        # The item survives; only its link is dropped.
        assert item.title == "Click me"


def test_ordinary_links_survive() -> None:
    for href in ("https://x/1", "http://localhost:3000/p/1"):
        assert select_results([{"title": "t", "url": href}], [0])[0].url == href


def test_an_image_from_a_scheme_we_do_not_trust_is_dropped() -> None:
    item = select_results([{"title": "t", "image": "javascript:alert(1)"}], [0])[0]
    assert item.image is None


def test_long_fields_are_cut_to_size() -> None:
    item = select_results([{"title": "x" * 5000, "price": "y" * 500}], [0])[0]

    assert len(item.title) <= 300
    assert len(item.price or "") <= 100


def test_the_number_of_cards_is_bounded() -> None:
    """Forty cards is a wall, not an answer."""
    many = [{"title": f"Item {i}"} for i in range(100)]
    assert len(select_results(many, list(range(100)))) <= 12


def test_meta_lines_are_bounded_too() -> None:
    item = select_results([{"title": "t", "meta": ["a" * 500] * 20}], [0])[0]

    assert len(item.meta) <= 3
    assert all(len(line) <= 200 for line in item.meta)


def test_a_result_item_serialises_for_the_wire() -> None:
    item = ResultItem(title="t", price="₹1", url="https://x/1")
    assert item.to_dict()["title"] == "t"
    assert "meta" in item.to_dict()


# --- the page you are already on ------------------------------------------


def test_a_page_with_a_result_list_offers_its_items(client, fake_llm) -> None:
    """The second way in: no search, just a list page and a question about it.

    Nothing is selected here, because nothing was searched for. The page lists
    these; SurfAI shows what it lists.
    """
    fake_llm.push({"intent": "question"})
    fake_llm.text = "Three running shoes, from 899 to 2,499 rupees."

    directive = client.post(
        "/api/chat",
        json={
            "message": "what is on this page?",
            "page_context": {
                "url": "https://www.amazon.in/s?k=shoes",
                "title": "shoes",
                "summary": "Results for shoes",
                "elements": [{"id": "e1", "type": "button", "text": "Add to cart"}],
                "items": [
                    {"title": "Nike Revolution 7", "price": "₹2,499", "url": "https://a/1"},
                    {"title": "Nike Court Vision", "price": "₹899", "url": "https://a/2"},
                ],
            },
            "tab_context": {
                "url": "https://www.amazon.in/s?k=shoes",
                "title": "shoes",
                "tab_id": 1,
            },
        },
    ).json()

    assert [r["title"] for r in directive["results"]] == [
        "Nike Revolution 7",
        "Nike Court Vision",
    ]
    assert directive["results"][0]["price"] == "₹2,499"


def test_a_general_question_beside_a_list_shows_no_cards(client, fake_llm) -> None:
    """Cards answer a question about the page. "write me a haiku" is not one."""
    fake_llm.push({"intent": "question"})
    fake_llm.text = "Rain on the window."

    directive = client.post(
        "/api/chat",
        json={
            "message": "write me a haiku about rain",
            "page_context": {
                "url": "https://www.amazon.in/s?k=shoes",
                "title": "shoes",
                "summary": "Results",
                "elements": [],
                "items": [{"title": "Nike Revolution 7", "price": "₹2,499"}],
            },
            "tab_context": {"url": "https://www.amazon.in/s?k=shoes", "title": "s", "tab_id": 1},
        },
    ).json()

    assert directive["results"] == []


def test_a_page_with_no_list_yields_no_cards(client, fake_llm) -> None:
    fake_llm.push({"intent": "question"})
    fake_llm.text = "It is an article about carbonara."

    directive = client.post(
        "/api/chat",
        json={
            "message": "what is on this page?",
            "page_context": {
                "url": "https://cooking.example.com/carbonara",
                "title": "Carbonara",
                "summary": "A Roman pasta dish.",
                "elements": [{"id": "e1", "type": "button", "text": "Print"}],
            },
            "tab_context": {
                "url": "https://cooking.example.com/carbonara",
                "title": "c",
                "tab_id": 1,
            },
        },
    ).json()

    assert directive["results"] == []


# --- cards while the task is still running --------------------------------


async def test_results_appear_before_the_task_finishes(fake_llm, product_page) -> None:
    """Watching results arrive beats watching a spinner.

    The items were already being extracted and stored on every successful
    EXTRACT; they just sat unused until the task ended. Now each step carries
    what has been found so far.
    """
    from app.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator(fake_llm)
    fake_llm.push(
        {"type": "action", "activity": "Reading", "action": {"action": "EXTRACT"}},
        {"type": "action", "activity": "Opening", "action": {"action": "CLICK", "target": "e2"}},
    )

    await orchestrator.start(task_id="p1", message="find shoes", page=product_page)
    directive = await orchestrator.continue_task(
        task_id="p1",
        page=product_page,
        result={
            "success": True,
            "action": "EXTRACT",
            "url_changed": False,
            "page_changed": True,
            "data": {"items": [
                {"title": "Nike Revolution 7", "price": "₹2,499"},
                {"title": "Nike Court Vision", "price": "₹899"},
            ]},
        },
    )

    assert directive.type == "action", "the task should still be running"
    assert [item.title for item in directive.results] == [
        "Nike Revolution 7",
        "Nike Court Vision",
    ]


async def test_a_step_with_nothing_found_carries_no_cards(fake_llm, product_page) -> None:
    from app.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator(fake_llm)
    fake_llm.push({"type": "action", "activity": "Typing",
                   "action": {"action": "TYPE", "target": "e1", "value": "shoes"}})

    directive = await orchestrator.start(task_id="p2", message="find shoes", page=product_page)
    assert directive.results == []


def test_no_more_than_ten_cards_are_ever_shown() -> None:
    """A panel is four hundred pixels wide; ten is already a scroll."""
    many = [{"title": f"Item {i}"} for i in range(50)]
    assert len(select_results(many, list(range(50)))) == 10


async def test_cards_appear_as_soon_as_the_page_shows_results(fake_llm, product_page) -> None:
    """Not only after an explicit EXTRACT.

    The first version keyed off extracted data, which meant cards appeared only
    if the planner chose to EXTRACT and then did something else afterwards. In
    the ordinary flow -- search, land on results, answer -- that never happens,
    so nothing showed until the end. The snapshot already carries the page's
    result list, so the moment a search lands, there is something to show.
    """
    from app.agents.orchestrator import Orchestrator

    results_page = dict(
        product_page,
        items=[
            {"title": "Nike Revolution 7", "price": "₹2,499"},
            {"title": "Nike Court Vision", "price": "₹899"},
        ],
    )
    orchestrator = Orchestrator(fake_llm)
    fake_llm.push(
        {"type": "action", "activity": "Searching", "action": {"action": "CLICK", "target": "e2"}},
        {"type": "action", "activity": "Reading", "action": {"action": "EXTRACT"}},
    )

    await orchestrator.start(task_id="p3", message="find shoes", page=product_page)
    directive = await orchestrator.continue_task(
        task_id="p3",
        page=results_page,
        result={"success": True, "action": "CLICK", "url_changed": True, "page_changed": True},
    )

    assert [item.title for item in directive.results] == [
        "Nike Revolution 7",
        "Nike Court Vision",
    ]


async def test_extracted_results_win_over_the_page_snapshot(fake_llm, product_page) -> None:
    """An EXTRACT read the page deliberately; the snapshot is only a glance."""
    from app.agents.orchestrator import Orchestrator

    page_with_items = dict(product_page, items=[{"title": "From the snapshot"}])
    orchestrator = Orchestrator(fake_llm)
    fake_llm.push(
        {"type": "action", "activity": "Reading", "action": {"action": "EXTRACT"}},
        {"type": "action", "activity": "Next", "action": {"action": "SCROLL", "direction": "down"}},
    )

    await orchestrator.start(task_id="p4", message="find shoes", page=page_with_items)
    directive = await orchestrator.continue_task(
        task_id="p4",
        page=page_with_items,
        result={
            "success": True, "action": "EXTRACT", "url_changed": False, "page_changed": True,
            "data": {"items": [{"title": "From the extraction"}]},
        },
    )

    assert [item.title for item in directive.results] == ["From the extraction"]
