from sproutcode.config import load_api_key


def test_load_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert load_api_key() == "from-env"


def test_load_api_key_falls_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DEEPSEEK_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setattr("sproutcode.config.ENV_FILE", str(env_file))
    assert load_api_key() == "from-file"
