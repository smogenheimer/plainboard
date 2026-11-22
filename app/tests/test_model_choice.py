from conftest import agents_by_slug
from plainboard.services import arena, model_choice
from plainboard.services.model_choice import choose, clean, equivalent_model, fill
from plainboard_protocol import ModelInfo, ProviderInfo
from test_accounts import accounts, report_limits, turn
from test_orchestration import make_dev, runner


def provider(name: str, *models: tuple[str, str]) -> ProviderInfo:
    return ProviderInfo(name=name, label=name, models=[ModelInfo(id=i, label=i, tier=t, arena=i.title()) for i, t in models])  # type: ignore[arg-type]


A = provider("a", ("a-big", "xlarge"), ("a-mid", "medium"), ("a-eye", "medium"), ("a-tiny", "small"))
B = provider("b", ("b-large", "large"), ("b-medium", "medium"))


def test_the_model_of_a_strength_is_the_best_of_its_class():
    assert choose(A, "medium") == "a-mid"  # a class nobody filled holds every model: the first listed
    assert choose(A, "medium", {"a-mid": 60, "a-eye": 90, "a-big": 95}) == "a-eye"  # the highest score of that strength
    assert choose(A, "medium", {"a-big": 95, "a-tiny": 40}) == "a-tiny"  # none of that strength in the class: the next smaller one
    assert choose(A, "large", {"a-big": 95}) == "a-big"  # all are larger: the smallest
    assert choose(ProviderInfo(name="none", label="", models=[]), "medium") is None


def test_another_provider_never_gets_a_larger_model_than_the_agents_own():
    catalog = [A, B]
    assert equivalent_model(catalog, "a-mid", "b") == "b-medium"
    assert equivalent_model(catalog, "a-big", "b") == "b-large"  # no model that large: the next smaller one
    assert equivalent_model(catalog, "a-tiny", "b") == "b-medium"  # all are larger: the smallest
    assert equivalent_model(catalog, "unknown", "b") == "b-medium"  # a model the runner does not list counts as medium
    assert equivalent_model(catalog, "a-mid", "a") == "a-mid" and equivalent_model(catalog, "a-mid", "gone") is None
    # Within a class only its models stand in.
    assert equivalent_model(catalog, "a-mid", "b", {"vision": {"b": {"b-large": 80}}}, "vision") == "b-large"


def test_the_classes_setting_keeps_the_fixed_classes_and_refuses_nonsense(client):
    assert clean(None) == {"allround": {}, "coding": {}, "vision": {}}
    assert clean({"Vision": {"a": {"a-eye": 90, "a-mid": None}}, "research": {}})["vision"] == {"a": {"a-eye": 90.0}}
    assert client.put("/api/settings", json={"values": {"model_classes": {"vision": {"a": {"a-eye": 140}}}}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"default_worker_strength": "huge"}}).status_code == 400
    saved = client.put("/api/settings", json={"values": {"model_classes": {"research": {"codex": {"gpt-6.1-sol": 70}}}}}).json()["values"]["model_classes"]
    assert set(saved) == {"allround", "coding", "vision", "research"}


def test_arena_fills_only_what_is_empty(client, monkeypatch):
    rated = {"allround": {"A-Mid (High)": 1500.0, "A-Mid": 1400.0, "A-Tiny (Max)": 1300.0, "Other": 1200.0}, "coding": {}, "vision": {"B-Large": 1000.0}}
    filled = fill({"allround": {"a": {"a-tiny": 12}}}, [A, B], rated)
    assert filled["allround"] == {"a": {"a-tiny": 12.0, "a-mid": 100.0}}  # the user's score stays; the best entry of a name counts
    assert filled["vision"] == {"b": {"b-large": 100.0}} and filled["coding"] == {}

    async def ratings(boards):
        return {"allround": {"Sonnet": 1500.0, "Luna": 1200.0, "Other": 1000.0}}

    monkeypatch.setattr(arena, "ratings", ratings)
    monkeypatch.setattr(type(runner(client)), "providers", _named(type(runner(client)).providers))
    got = client.post("/api/settings/model-classes/arena", json={"classes": {"allround": {"codex": {"gpt-6-luna": 5}}}}).json()["classes"]
    assert got["allround"] == {"codex": {"gpt-6-luna": 5.0}, "claude_code": {"claude-sonnet-5-5": 100.0}}
    assert client.get("/api/settings").json()["values"]["model_classes"] is None  # nothing is saved: the form decides


def _named(providers):
    async def with_names(self):
        return [p.model_copy(update={"models": [m.model_copy(update={"arena": m.label}) for m in p.models]}) for p in await providers(self)]

    return with_names


def test_an_agent_runs_on_the_model_of_its_class_and_strength(client, project):
    pid = project["id"]
    first = accounts(client)["Claude"]
    client.patch(f"/api/accounts/{first['id']}", json={"auth_mode": "oauth_token", "secret": "tok-a"})
    dev = make_dev(client, pid)
    turn(client, pid, dev["id"])
    assert runner(client).specs[-1].model == "claude-sonnet-5-5" and dev["strength"] is None  # as before: the model it names

    # No model: class and strength decide, on its own provider ...
    assert client.patch(f"/api/agents/{dev['id']}", json={"model": "", "strength": "small"}).json()["model"] == ""
    turn(client, pid, dev["id"])
    assert runner(client).specs[-1].model == "claude-haiku-5-5"
    assert client.patch(f"/api/agents/{dev['id']}", json={"model_class": "nothing"}).status_code == 400
    client.put("/api/settings", json={"values": {"model_classes": {"vision": {"claude_code": {"claude-sonnet-5-5": 80}, "codex": {"gpt-6.1-sol": 90}}}}})
    client.patch(f"/api/agents/{dev['id']}", json={"model_class": "vision", "strength": "large"})
    turn(client, pid, dev["id"])
    assert runner(client).specs[-1].model == "claude-sonnet-5-5"  # the class has no large model here: the next smaller one

    # ... and on another one while its own is used up.
    client.put("/api/settings", json={"values": {"switch_providers": True}})
    report_limits(client, Claude=(1.0, "rejected"))
    turn(client, pid, dev["id"])
    assert (runner(client).specs[-1].provider, runner(client).specs[-1].model) == ("codex", "gpt-6.1-sol")
    report_limits(client, Claude=(0.1, "allowed"))

    # A model taken away without a strength gets the usual one, and a new agent the installation's.
    other = client.post(f"/api/projects/{pid}/agents", json={"name": "Eye", "model_class": "vision"}).json()
    assert (other["model"], other["model_class"], other["strength"]) == ("", "vision", "medium")
    client.put("/api/settings", json={"values": {"default_worker_strength": "small", "default_summary_strength": "small"}})
    third = client.post(f"/api/projects/{pid}/agents", json={"name": "Small"}).json()
    assert (third["model"], third["strength"], third["summary_model"]) == ("", "small", "")
    turn(client, pid, third["id"])
    assert runner(client).specs[-1].model == "claude-haiku-5-5"
    assert agents_by_slug(client, pid)["small"]["model"] == ""
    assert model_choice.label(type("A", (), third)) == "allround/small"
