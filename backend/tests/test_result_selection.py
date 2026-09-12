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
