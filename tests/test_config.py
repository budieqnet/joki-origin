import pytest
import json
import os
from unittest.mock import patch, MagicMock

from joki.config import _load_models, _DEFAULT_MODELS

@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {}, clear=True)
def test_load_models_default(mock_config_path):
    # If config file doesn't exist, it should return _DEFAULT_MODELS
    # and auto create config.
    mock_config_path.exists.return_value = False
    
    models = _load_models()
    
    assert "gemma4" in models
    assert "deepseek" in models
    assert models["gemma4"]["api_keys"] == [""]

@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {"JOKI_GEMMA4_KEY": "test-key-123"}, clear=True)
def test_load_models_with_env(mock_config_path):
    mock_config_path.exists.return_value = False
    
    models = _load_models()
    
    assert models["gemma4"]["api_keys"] == ["test-key-123"]

@patch("joki.config._CONFIG_PATH")
@patch.dict(os.environ, {}, clear=True)
def test_load_models_from_file(mock_config_path):
    mock_config_path.exists.return_value = True
    custom_models = {
        "models": {
            "custom_model": {
                "name": "Custom Model",
                "base_url": "http://localhost",
                "model": "custom:latest",
                "api_keys": ["custom-key"]
            }
        }
    }
    mock_config_path.read_text.return_value = json.dumps(custom_models)
    
    models = _load_models()
    
    assert "custom_model" in models
    assert models["custom_model"]["api_keys"] == ["custom-key"]
