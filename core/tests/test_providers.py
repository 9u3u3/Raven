"""Cloud providers (PLAN4 4) without network: the registry, the three adapters with fake clients, the sealed
payload rule, the routes, and key masking."""

import ast
import json
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from raven_core import config, db, keys  # noqa: E402
from raven_core.privacy import gate  # noqa: E402
from raven_core.router import cloud  # noqa: E402
from raven_core.router import providers as reg  # noqa: E402
from raven_core.router.providers import anthropic_adapter, gemini, openai_compat  # noqa: E402

db.init()
SRC = Path(reg.__file__).parents[3]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for v in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    for r in reg.rows():
        keys.put(reg.key_name(r["id"]), None)
    for k in ("cloud_providers", "cloud_provider", "groq_api_key", "anthropic_api_key", "groq_model"):
        db.set_setting(k, None)
    yield


def sealed(text="hello"):
    red = gate.Redactor(use_layer2=False)
    return gate.seal(red.redact(text), red)


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d = code, data
        self.text = json.dumps(data)

    def json(self):
        return self._d


# ---------------------------------------------------------------- registry


def test_old_settings_become_rows():
    db.set_setting("groq_api_key", "gsk_old")
    db.set_setting("anthropic_api_key", "sk-ant-old")
    db.set_setting("cloud_provider", "anthropic")
    db.set_setting("groq_model", "llama-3.3-70b-versatile")
    rs = reg.rows()
    assert [r["id"] for r in rs] == ["anthropic", "groq"]
    assert rs[1]["model"] == "llama-3.3-70b-versatile"
    assert reg.order() == ["anthropic", "groq"]


def test_add_update_order_remove():
    reg.add("groq")
    reg.add("gemini")
    with pytest.raises(reg.ProviderError):
        reg.add("groq")
    with pytest.raises(reg.ProviderError):
        reg.add("custom", base_url="ftp://x")
    c = reg.add("custom", base_url="http://192.168.1.20:1234/v1/", name="LM Studio")
    assert c["id"] == "custom1" and c["base_url"] == "http://192.168.1.20:1234/v1"
    assert reg.order() == ["custom1"]  # no keys yet: only the custom server can run
    reg.set_key("groq", "gsk_x")
    reg.set_key("gemini", "AIza_x")
    assert reg.order() == ["groq", "gemini", "custom1"]
    reg.reorder(["custom1", "gemini", "groq"])
    reg.update("gemini", on=False)
    assert reg.order() == ["custom1", "groq"]
    reg.update("groq", model="openai/gpt-oss-20b")
    assert reg.model_for(reg._row("groq")) == "openai/gpt-oss-20b"
    with pytest.raises(reg.ProviderError):
        reg.reorder(["groq"])
    reg.remove("groq")
    assert keys.get("groq_api_key") is None
    assert [r["id"] for r in reg.rows()] == ["custom1", "gemini"]


def test_release_build_has_no_claude_cli(monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    assert "claude" not in reg.presets()
    with pytest.raises(reg.ProviderError):
        reg.add("claude")
    db.set_setting("cloud_providers", [{"id": "claude", "preset": "claude", "on": True}])
    assert reg.rows() == []


def test_public_rows_hold_no_key():
    reg.add("openai")
    reg.set_key("openai", "sk-secret-abcd1234")
    row = reg.public()[0]
    assert row["key"] == "…1234" and "sk-secret" not in json.dumps(row)


# ---------------------------------------------------------------- the sealed payload rule


def test_a_plain_string_never_reaches_an_adapter(monkeypatch):
    reg.add("groq")
    reg.set_key("groq", "gsk_x")
    called = []
    monkeypatch.setattr(openai_compat, "chat", lambda *a: called.append(a) or {})
    with pytest.raises(cloud.CloudError, match="redaction gate"):
        reg.chat("groq", "sys", "Aadhaar 2341 2341 2346", {"type": "object"})
    with pytest.raises(cloud.CloudError, match="redaction gate"):
        next(reg.stream("groq", "sys", "plain text"))
    with pytest.raises(cloud.CloudError, match="redaction gate"):
        reg.chat("groq", "sys", sealed() + " added after the check", {"type": "object"})
    assert not called


def test_seal_blocks_what_the_gate_finds():
    red = gate.Redactor(use_layer2=False)
    with pytest.raises(gate.Blocked, match="CARD"):
        gate.seal("card 4111 1111 1111 1111", red)
    with pytest.raises(gate.Blocked):
        gate.seal("hello", None)


def test_seal_fails_closed_without_the_privacy_filter(monkeypatch):
    monkeypatch.setattr(gate.layer2, "available", lambda: False)
    red = gate.Redactor()
    with pytest.raises(gate.Blocked, match="not installed"):
        gate.seal(red.redact("hello"), red)
    monkeypatch.setattr(gate.layer2, "available", lambda: True)
    monkeypatch.setattr(gate.layer2, "error", "failed to load")
    with pytest.raises(gate.Blocked, match="not ready"):
        gate.seal("hello", gate.Redactor())


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out |= {f"{n.module or ''}.{a.name}" for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
    return out


def test_only_the_registry_imports_an_adapter_and_only_the_gate_seals():
    adapters = ("anthropic_adapter", "openai_compat", "gemini", "claude_cli")
    for f in SRC.rglob("*.py"):
        rel = f.relative_to(SRC).as_posix()
        if rel.startswith("raven_core/router/providers/"):
            continue
        assert not any(a in imp for imp in _imports(f) for a in adapters), f"{rel} imports an adapter"
        if rel != "raven_core/privacy/gate.py":
            assert "Sealed(" not in f.read_text(), f"{rel} makes a Sealed payload"
    # The SDKs for the cloud come in only inside the adapters.
    for f in SRC.rglob("*.py"):
        rel = f.relative_to(SRC).as_posix()
        if rel.startswith("raven_core/router/providers/"):
            continue
        assert not any(i.split(".")[0] == "anthropic" or i.startswith("google.genai") for i in _imports(f)), rel


# ---------------------------------------------------------------- adapters


def test_openai_compat_chat(monkeypatch):
    reg.add("openai")
    reg.set_key("openai", "sk-test")
    reg.update("openai", model="gpt-5-mini")
    sent = []

    def post(url, headers, json, timeout):
        sent.append((url, headers, dict(json)))
        if len(sent) == 1:
            return Resp(400, {"error": "response_format is not supported"})
        return Resp(200, {"choices": [{"message": {"content": "Here:\n```json\n{\"summary\": \"ok\"}\n```"}}]})

    monkeypatch.setattr(openai_compat.httpx, "post", post)
    data, label = reg.chat("openai", "sys", sealed(), {"type": "object"})
    assert data == {"summary": "ok"} and label == "OpenAI · gpt-5-mini"
    url, headers, body = sent[0]
    assert url == "https://api.openai.com/v1/chat/completions" and headers["Authorization"] == "Bearer sk-test"
    assert body["response_format"] == {"type": "json_object"} and "temperature" not in body
    assert body["reasoning_effort"] == "low" and "max_completion_tokens" in body
    assert "response_format" not in sent[1][2]


def test_openai_compat_bad_key(monkeypatch):
    reg.add("groq")
    reg.set_key("groq", "gsk_bad")
    monkeypatch.setattr(openai_compat.httpx, "get", lambda *a, **k: Resp(401, {"error": "invalid"}))
    assert reg.test("groq") == {"ok": False, "detail": "Groq: the key is not valid"}


def test_openai_compat_stream(monkeypatch):
    reg.add("groq")
    reg.set_key("groq", "gsk_x")
    reg.update("groq", model="llama-3.1-8b-instant")
    lines = ['data: {"choices":[{"delta":{"content":"Hel"}}]}', "", 'data: {"choices":[{"delta":{"content":"lo"}}]}', "data: [DONE]"]

    class S:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self):
            return iter(lines)

    monkeypatch.setattr(openai_compat.httpx, "stream", lambda *a, **k: S())
    out = list(reg.stream("groq", "sys", sealed()))
    assert out == ["Groq · llama-3.1-8b-instant", "Hel", "lo"]


def test_openrouter_lists_free_models_first(monkeypatch):
    reg.add("openrouter")
    reg.set_key("openrouter", "sk-or-x")
    calls = []

    def get(url, headers, timeout):
        calls.append(url)
        if url.endswith("/key"):
            return Resp(200, {"data": {}})
        return Resp(200, {"data": [
            {"id": "openai/gpt-5", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
            {"id": "meta-llama/llama-3.3-70b-instruct:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "image/model", "architecture": {"output_modalities": ["image"]}},
        ]})

    monkeypatch.setattr(openai_compat.httpx, "get", get)
    ms = reg.models("openrouter", fresh=True)
    assert [m["id"] for m in ms] == ["meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-5"] and ms[0]["free"]
    assert reg.model_for(reg._row("openrouter")) == "meta-llama/llama-3.3-70b-instruct:free"
    assert calls[0].endswith("/key")
    assert reg.test("openrouter")["detail"] == "2 models, 1 free"


def test_anthropic_uses_structured_outputs(monkeypatch):
    reg.add("anthropic")
    reg.set_key("anthropic", "sk-ant-test")
    reg.update("anthropic", model="claude-sonnet-5-5")
    sent = {}

    class Block:
        type, text = "text", '{"summary": "ok", "points": []}'

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return type("R", (), {"stop_reason": "end_turn", "content": [Block()]})()

    monkeypatch.setattr(anthropic_adapter, "_client", lambda ctx: type("C", (), {"messages": Messages()})())
    schema = {"type": "object", "properties": {"summary": {"type": "string", "maxLength": 300},
                                               "points": {"type": "array", "items": {"type": "string"}}}}
    data, label = reg.chat("anthropic", "sys", sealed(), schema)
    assert data == {"summary": "ok", "points": []} and label == "Anthropic · claude-sonnet-5-5"
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "maxLength" not in fmt["schema"]["properties"]["summary"]
    assert "tool_choice" not in sent and "temperature" not in sent and sent["model"] == "claude-sonnet-5-5"


def test_anthropic_refusal_is_a_cloud_error(monkeypatch):
    reg.add("anthropic")
    reg.set_key("anthropic", "sk-ant-test")
    reg.update("anthropic", model="claude-haiku-5-5")
    msgs = type("M", (), {"create": lambda self, **kw: type("R", (), {"stop_reason": "refusal", "content": []})()})()
    monkeypatch.setattr(anthropic_adapter, "_client", lambda ctx: type("C", (), {"messages": msgs})())
    with pytest.raises(cloud.CloudError, match="refused"):
        reg.chat("anthropic", "sys", sealed(), {"type": "object"})


def test_gemini_chat_and_models(monkeypatch):
    reg.add("gemini")
    reg.set_key("gemini", "AIza_test")
    sent = {}

    class Models:
        def generate_content(self, model, contents, config):
            sent.update(model=model, contents=contents, config=config)
            return type("R", (), {"text": '{"summary": "ok"}'})()

        def list(self):
            M = lambda n, a: type("M", (), {"name": n, "supported_actions": a})()  # noqa: E731
            return [M("models/gemini-2.5-flash", ["generateContent"]), M("models/text-embedding-004", ["embedContent"]),
                    M("models/gemini-2.5-pro", ["generateContent"])]

    monkeypatch.setattr(gemini, "_client", lambda ctx: type("C", (), {"models": Models()})())
    assert [m["id"] for m in reg.models("gemini", fresh=True)] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    data, label = reg.chat("gemini", "sys", sealed(), {"type": "object"})
    assert data == {"summary": "ok"} and label == "Google Gemini · gemini-2.5-flash"
    assert sent["config"].response_mime_type == "application/json" and sent["config"].system_instruction == "sys"


# ---------------------------------------------------------------- routes and keys


def _client():
    from raven_core import app as app_mod

    # No `with`: the lifespan (indexer threads) does not start, so other tests keep a quiet database.
    return TestClient(app_mod.app, headers={"Authorization": f"Bearer {app_mod.TOKEN}"})


def test_provider_routes():
    c = _client()
    r = c.post("/cloud/providers/add", json={"preset": "groq", "key": "gsk_route_9999"})
    assert r.status_code == 200, r.text
    c.post("/cloud/providers/add", json={"preset": "gemini"})
    rows = {p["id"]: p for p in r.json()["providers"]}
    assert rows["groq"]["key"] == "…9999" and rows["groq"]["ready"]
    r = c.post("/cloud/providers/order", json={"ids": ["gemini", "groq"]})
    assert [p["id"] for p in r.json()["providers"]] == ["gemini", "groq"]
    r = c.post("/cloud/providers/update", json={"id": "groq", "model": "llama-3.1-8b-instant", "on": False})
    assert {p["id"]: p for p in r.json()["providers"]}["groq"]["on"] is False
    assert c.post("/cloud/providers/add", json={"preset": "nope"}).status_code == 400
    presets = {p["id"] for p in c.get("/cloud/providers").json()["presets"]}
    assert {"anthropic", "openai", "groq", "openrouter", "gemini", "custom"} <= presets


def test_settings_never_return_a_key_and_refuse_key_settings():
    c = _client()
    reg.add("anthropic")
    reg.set_key("anthropic", "sk-ant-secret-1234")
    r = c.get("/settings")
    assert r.status_code == 200, r.text
    assert r.json()["anthropic_api_key"] == "…1234"
    assert "sk-ant-secret" not in r.text
    assert c.post("/settings", json={"key": "groq_api_key", "value": "gsk_x"}).status_code == 400
    assert c.post("/settings", json={"key": "cloud_provider", "value": "groq"}).status_code == 400


def test_claude_env_picks_the_account(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/else")
    db.set_setting("claude_config_dir", "~/.claude-other")
    env = cloud.claude_env()
    assert env["CLAUDE_CONFIG_DIR"] == os.path.expanduser("~/.claude-other")
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    # The default account: no CLAUDE_CONFIG_DIR at all (its config is ~/.claude.json).
    db.set_setting("claude_config_dir", "")
    assert "CLAUDE_CONFIG_DIR" not in cloud.claude_env()


def test_claude_env_uses_the_real_sign_in_when_home_is_a_demo_folder(tmp_path, monkeypatch):
    """With HOME set to a demo folder that has no Claude sign-in, claude -p uses the one in the real home."""
    import pwd

    from raven_core.router import cloud

    real = tmp_path / "real"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude" / ".credentials.json").write_text("{}")
    demo = tmp_path / "demo"
    demo.mkdir()
    monkeypatch.setenv("HOME", str(demo))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: type("P", (), {"pw_dir": str(real)})())
    monkeypatch.setattr(cloud.db, "get_setting", lambda k, default=None: None)
    assert cloud.claude_env()["CLAUDE_CONFIG_DIR"] == str(real / ".claude")
    # A demo home with its own sign-in (or links to the real one) keeps it.
    (demo / ".claude").mkdir()
    (demo / ".claude" / ".credentials.json").write_text("{}")
    assert "CLAUDE_CONFIG_DIR" not in cloud.claude_env()
    # A chosen account always wins.
    assert cloud.claude_env("/x/acct")["CLAUDE_CONFIG_DIR"] == "/x/acct"
