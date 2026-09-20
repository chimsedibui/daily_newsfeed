"""Chuoi provider du phong: thu tu, chon model, danh dau provider hong."""
import pytest

from news_bot import llm


@pytest.fixture(autouse=True)
def clean_state():
    llm.reset_down()
    llm.get_settings.cache_clear()
    yield
    llm.reset_down()
    llm.get_settings.cache_clear()


def _settings(monkeypatch, **env):
    defaults = {
        "LLM_PROVIDERS": "gemini,vertex,openai",
        "GEMINI_API_KEY": "k",
        "VERTEX_PROJECT": "p",
        "OPENAI_API_KEY": "o",
        "SUMMARIZER_MODEL": "",
        "EDITOR_MODEL": "",
    }
    for key, value in {**defaults, **env}.items():
        monkeypatch.setenv(key, value)
    llm.get_settings.cache_clear()


def test_chain_follows_configured_order(monkeypatch):
    _settings(monkeypatch)
    assert llm.provider_chain() == ["gemini", "vertex", "openai"]

    _settings(monkeypatch, LLM_PROVIDERS="openai,gemini")
    assert llm.provider_chain() == ["openai", "gemini"]


def test_provider_without_its_credential_is_skipped(monkeypatch):
    """Khai bao mot provider ma thieu key cua no thi phai bo qua, khong no o runtime."""
    _settings(monkeypatch, GEMINI_API_KEY="")
    assert llm.provider_chain() == ["vertex", "openai"]

    _settings(monkeypatch, GEMINI_API_KEY="", VERTEX_PROJECT="", OPENAI_API_KEY="")
    assert llm.provider_chain() == []


def test_unknown_provider_is_ignored(monkeypatch):
    _settings(monkeypatch, LLM_PROVIDERS="khong-ton-tai,gemini")
    assert llm.provider_chain() == ["gemini"]


def test_duplicate_entries_collapse(monkeypatch):
    _settings(monkeypatch, LLM_PROVIDERS="gemini,gemini,openai")
    assert llm.provider_chain() == ["gemini", "openai"]


def test_systemic_error_takes_provider_out_of_the_chain(monkeypatch):
    _settings(monkeypatch)
    assert llm.mark_down("gemini", "429 RESOURCE_EXHAUSTED: quota exceeded")
    assert llm.provider_chain() == ["vertex", "openai"]


def test_ordinary_error_does_not_take_a_provider_down(monkeypatch):
    """Mot bai khong parse duoc thi bai sau van co the on - khong duoc loai ca provider."""
    _settings(monkeypatch)
    assert not llm.mark_down("gemini", "parsing_error: ValidationError on field topics")
    assert llm.provider_chain() == ["gemini", "vertex", "openai"]


def test_marking_down_twice_reports_only_once(monkeypatch):
    _settings(monkeypatch)
    assert llm.mark_down("gemini", "401 unauthenticated")
    assert not llm.mark_down("gemini", "401 unauthenticated")


def test_model_override_applies_only_to_the_first_provider(monkeypatch):
    """Ten model cua Gemini khong co nghia gi voi OpenAI - khong duoc ap sang."""
    _settings(monkeypatch, SUMMARIZER_MODEL="gemini-3.8-flash")
    assert llm.model_for("gemini", "summarize") == "gemini-3.8-flash"
    assert llm.model_for("openai", "summarize") == "gpt-5.6-luna"
    assert llm.model_for("vertex", "summarize") == "gemini-2.5-flash-lite"


def test_each_provider_has_a_model_for_both_purposes(monkeypatch):
    _settings(monkeypatch)
    for provider in llm.provider_chain():
        for purpose in ("summarize", "compose"):
            model = llm.model_for(provider, purpose)
            assert model, f"{provider}/{purpose} khong co model"
            assert model in llm.PRICING, f"{model} thieu trong bang gia"
