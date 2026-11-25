import json

import pytest

from revoz.core import providers as pv
from revoz.core.providers import Item, ProviderError, Request

REQ = Request(
    [Item("1", "Hello Gordon, Black Mesa is waiting.", "scientist"), Item("2", "Catch me later.", "barney")],
    "es-419",
    {"Gordon": "", "Black Mesa": ""},
    {"barney": "guardia de seguridad, simpático"},
    "Juego de ciencia ficción, tono serio",
    "model-x",
)


def test_prompt_has_language_glossary_characters_and_length_rule():
    system, user = pv.build_prompt(REQ)
    assert "Latin American Spanish" in system and "same length" in system
    assert "Gordon -> Gordon" in system and "barney: guardia" in system and "ciencia ficción" in system
    assert json.loads(user)["lines"][1] == {"id": "2", "character": "barney", "text": "Catch me later."}


def test_parse_translations_tolerates_extra_text_and_ignores_unknown_ids():
    text = 'Claro: {"translations": [{"id": "1", "text": " Hola "}, {"id": "99", "text": "x"}]} listo'
    assert pv.parse_translations(text, {"1", "2"}) == {"1": "Hola"}
    with pytest.raises(ProviderError) as e:
        pv.parse_translations("no es json", {"1"})
    assert e.value.retry


@pytest.mark.parametrize(
    "provider,expected_url,response,answer_path",
    [
        (pv.BUILTIN[0], "https://api.openai.com/v1/chat/completions", "choices", None),
        (pv.Anthropic(), "https://api.anthropic.com/v1/messages", "content", None),
        (pv.Gemini(), "https://generativelanguage.googleapis.com/v1beta/models/model-x:generateContent", "candidates", None),
        (pv.Ollama(), "http://localhost:11434/api/chat", "message", None),
    ],
)
def test_language_models_send_and_parse(monkeypatch, provider, expected_url, response, answer_path):
    answer = json.dumps({"translations": [{"id": "1", "text": "Hola Gordon"}, {"id": "2", "text": "Nos vemos."}]})
    shapes = {
        "choices": {"choices": [{"message": {"content": answer}}]},
        "content": {"content": [{"type": "text", "text": answer}]},
        "candidates": {"candidates": [{"content": {"parts": [{"text": answer}]}}]},
        "message": {"message": {"content": answer}},
    }
    sent = []
    monkeypatch.setattr(pv, "post_json", lambda url, headers, body, timeout=180: sent.append((url, headers, body)) or shapes[response])
    assert provider.translate("CLAVE", REQ) == {"1": "Hola Gordon", "2": "Nos vemos."}
    url, headers, body = sent[0]
    assert url == expected_url and "CLAVE" not in url
    if provider.needs_key:
        assert any("CLAVE" in v for v in headers.values())


def test_deepl_protects_glossary_and_picks_code(monkeypatch):
    monkeypatch.setattr(pv.DeepL, "languages", lambda self, key: {"es", "es-419", "en-us"})
    sent = {}

    def fake_post(url, headers, body, timeout=180):
        sent.update(body)
        # DeepL devuelve el texto con las etiquetas intactas
        return {"translations": [{"text": t.replace("Hello", "Hola").replace("Catch me later.", "Nos vemos.")} for t in body["text"]]}

    monkeypatch.setattr(pv, "post_json", fake_post)
    out = pv.DeepL().translate("abc:fx", REQ)
    assert sent["target_lang"] == "ES-419" and sent["ignore_tags"] == ["x"]
    assert "<x>Gordon</x>" in sent["text"][0] and "<x>Black Mesa</x>" in sent["text"][0]
    assert out == {"1": "Hola Gordon, Black Mesa is waiting.", "2": "Nos vemos."}


def test_google_protects_glossary(monkeypatch):
    monkeypatch.setattr(pv.Google, "languages", lambda self, key: {"es", "en"})
    sent = {}
    monkeypatch.setattr(pv, "post_json", lambda url, h, body, timeout=180: sent.update(body) or {
        "data": {"translations": [{"translatedText": t.replace("Hello", "Hola")} for t in body["q"]]}})  # fmt: skip
    out = pv.Google().translate("k", REQ)
    assert sent["target"] == "es" and '<span translate="no">Gordon</span>' in sent["q"][0]
    assert out["1"] == "Hola Gordon, Black Mesa is waiting."


@pytest.mark.parametrize(
    "langs,code,expected",
    [({"es", "es-419"}, "es-419", "es-419"), ({"es"}, "es-419", "es"), ({"pt-br", "pt-pt"}, "pt", "pt-br"), ({"zh-hans"}, "zh", "zh-hans")],
)
def test_choose_code(langs, code, expected):
    assert pv.choose_code(langs, code) == expected


def test_default_model_prefers_cheap_ones():
    assert pv.BUILTIN[0].default_model(["gpt-big", "gpt-x-mini", "gpt-y"]) == "gpt-x-mini"
    assert pv.Anthropic().default_model(["claude-big", "claude-haiku-9"]) == "claude-haiku-9"


def test_default_model_skips_non_chat_and_previews():
    openai = next(p for p in pv.BUILTIN if p.name == "OpenAI")
    listed = ["codex-mini-latest", "gpt-4.1-mini", "gpt-4o-mini", "gpt-4o-mini-tts", "o4-mini", "whisper-1"]
    assert openai.default_model(listed) == "gpt-4.1-mini"  # codex y tts no chatean
    assert pv.Anthropic().default_model(["claude-3-5-haiku-20241022", "claude-haiku-4-5", "claude-opus-4"]) == "claude-haiku-4-5"
    gemini = ["gemini-2.0-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash-lite-preview-06-17", "gemini-2.5-flash-preview-tts"]
    assert pv.Gemini().default_model(gemini) == "gemini-2.5-flash-lite"
    openrouter = next(p for p in pv.BUILTIN if p.name == "OpenRouter")
    assert openrouter.default_model(["openai/gpt-4.1-mini", "openai/gpt-4.1-mini:batch"]) == "openai/gpt-4.1-mini"


def test_reasoning_models_think_before_the_json():
    text = '<think>Let me write {"translations": ...} carefully</think>{"translations": [{"id": "1", "text": "Hola"}]}'
    assert pv.parse_translations(text, {"1"}) == {"1": "Hola"}


def test_models_that_reject_temperature_are_retried_without_it(monkeypatch):
    sent = []

    def fake_post(url, headers, body, timeout=180):
        sent.append(body)
        if "temperature" in body:
            raise ProviderError("El servicio respondió con error 400: Unsupported value: 'temperature' does not support 0.2")
        return {"choices": [{"message": {"content": '{"translations": [{"id": "1", "text": "Hola"}]}'}}]}

    monkeypatch.setattr(pv, "post_json", fake_post)
    assert pv.BUILTIN[0].translate("k", REQ) == {"1": "Hola"}
    assert len(sent) == 2 and "temperature" not in sent[1]


class _HTTPError(pv.urllib.error.HTTPError):
    def __init__(self, code, body):
        super().__init__("https://x", code, "err", {}, None)
        self._body = json.dumps(body).encode()

    def read(self, n=-1):
        return self._body


@pytest.mark.parametrize(
    "code, body, method_body, message",
    [
        (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}, {"x": 1}, "Clave inválida o sin permisos"),
        (400, {"code": "invalid-argument", "error": "Incorrect API key provided."}, {"x": 1}, "Clave inválida o sin permisos"),
        (404, {"error": {"message": "The model `gpt-9` does not exist"}}, {"x": 1}, "El servicio respondió con error 404: The model `gpt-9` does not exist"),
        (422, {"detail": [{"msg": "field required"}]}, {"x": 1}, "El servicio respondió con error 422: field required"),
    ],
)
def test_errors_say_what_the_service_said(monkeypatch, code, body, method_body, message):
    def fail(req, timeout=None):
        raise _HTTPError(code, body)

    monkeypatch.setattr(pv.urllib.request, "urlopen", fail)
    with pytest.raises(ProviderError) as e:
        pv.post_json("https://api.example.com/x", {}, method_body)
    assert str(e.value) == message


def test_cohere_translates_and_lists_chat_models(monkeypatch):
    cohere = next(p for p in pv.BUILTIN if p.name == "Cohere")
    assert cohere.can_translate
    monkeypatch.setattr(pv, "get_json", lambda url, h, timeout=15: {"models": [{"name": "command-a-03-2025"}, {"name": "embed-v4.0"}]})
    assert cohere.models("k") == ["command-a-03-2025"]


@pytest.mark.parametrize("url", ["http://example.com/x", "http://192.168.0.5:11434/api", "ftp://x"])
def test_only_https_or_this_pc(url):
    with pytest.raises(ValueError):
        pv._check_url(url)
    pv._check_url("http://localhost:11434/api/tags")  # Ollama en la propia PC sí


def test_non_translators_say_so():
    with pytest.raises(ProviderError):
        pv.ElevenLabs().translate("k", REQ)
    assert {p.name for p in pv.BUILTIN if p.can_translate} >= {"OpenAI", "Anthropic", "DeepL", "Ollama (local, gratis)"}
