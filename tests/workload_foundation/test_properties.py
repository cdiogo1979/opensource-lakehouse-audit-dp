"""Hypothesis properties for environment configuration loading."""

import os
import string
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, seed, settings, strategies as st
import yaml

from config_loader import EnvironmentConfig, load_environment_config

HYPOTHESIS_SEED = int(os.environ.get("HYPOTHESIS_SEED", "0"))
ENVIRONMENTS = ("dev", "uat", "prod")
TOKEN_CHARS = string.ascii_lowercase + string.digits


@st.composite
def environment_config_strategy(draw):
    """Generate realistic non-secret EnvironmentConfig values."""
    environment = draw(st.sampled_from(ENVIRONMENTS))
    bucket_token = draw(st.text(alphabet=TOKEN_CHARS, min_size=1, max_size=16))
    prefix_tokens = draw(st.lists(st.text(alphabet=TOKEN_CHARS, min_size=1, max_size=12), max_size=4))
    database_token = draw(st.text(alphabet=TOKEN_CHARS + "_", min_size=1, max_size=24))
    suffix = "/".join(prefix_tokens)
    storage_path = f"s3a://bucket-{bucket_token}/{environment}/"
    if suffix:
        storage_path = f"{storage_path}{suffix}/"
    return EnvironmentConfig(
        environment=environment,
        settings={
            "storage_path": storage_path,
            "database_name": f"{environment}_{database_token}",
        },
    )


@seed(HYPOTHESIS_SEED)
@settings(max_examples=60, print_blob=True)
@given(
    environment=st.sampled_from(ENVIRONMENTS),
    token=st.text(alphabet=TOKEN_CHARS, min_size=1, max_size=24),
)
def test_loader_isolates_environment_files(environment, token):
    """Loading any supported environment returns only that file's values."""
    with TemporaryDirectory() as directory:
        config_dir = Path(directory)
        for selected in ENVIRONMENTS:
            values = {
                "storage_path": f"s3a://bucket-{selected}/{token}/{selected}",
                "database_name": f"{selected}_{token}",
            }
            (config_dir / f"{selected}.yml").write_text(yaml.safe_dump(values), encoding="utf-8")

        config = load_environment_config(environment, config_dir)

        assert config.environment == environment
        assert config["storage_path"] == f"s3a://bucket-{environment}/{token}/{environment}"
        assert config["database_name"] == f"{environment}_{token}"


@seed(HYPOTHESIS_SEED)
@settings(max_examples=60, print_blob=True)
@given(config=environment_config_strategy())
def test_valid_config_values_are_preserved(config):
    """For generated valid values, the loader preserves required config data."""
    values = {
        "storage_path": config["storage_path"],
        "database_name": config["database_name"],
    }
    with TemporaryDirectory() as directory:
        config_dir = Path(directory)
        (config_dir / f"{config.environment}.yml").write_text(yaml.safe_dump(values), encoding="utf-8")

        loaded = load_environment_config(config.environment, config_dir)

        assert loaded == config


YAML_SCALAR = st.one_of(st.none(), st.booleans(), st.integers(min_value=-1000, max_value=1000), st.text(max_size=12))
YAML_VALUE = st.recursive(
    YAML_SCALAR,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4),
    max_leaves=10,
)


@seed(HYPOTHESIS_SEED)
@settings(max_examples=60, print_blob=True)
@given(extra_value=YAML_VALUE)
def test_arbitrary_nested_yaml_settings_are_preserved(extra_value):
    """Generic nested keys and YAML values remain available to applications."""
    values = {
        "storage_path": "s3a://bucket-dev/root",
        "database_name": "dev_db",
        "application_options": {"custom_value": extra_value},
    }
    with TemporaryDirectory() as directory:
        config_dir = Path(directory)
        (config_dir / "dev.yml").write_text(yaml.safe_dump(values), encoding="utf-8")

        config = load_environment_config("dev", config_dir)

        assert config.to_dict()["application_options"]["custom_value"] == extra_value
        assert config.to_dict() == values


@seed(HYPOTHESIS_SEED)
@settings(max_examples=60, print_blob=True)
@given(
    environment=st.sampled_from(ENVIRONMENTS),
    invalid_value=st.one_of(
        st.none(),
        st.integers(),
        st.booleans(),
        st.sampled_from(("", " ", "\t", "\n")),
    ),
    key=st.sampled_from(("storage_path", "database_name")),
)
def test_invalid_required_values_always_fail(environment, invalid_value, key):
    """Generated empty and non-string required values are rejected."""
    values = {
        "storage_path": f"s3a://bucket-{environment}/root",
        "database_name": f"{environment}_db",
    }
    values[key] = invalid_value
    with TemporaryDirectory() as directory:
        config_dir = Path(directory)
        (config_dir / f"{environment}.yml").write_text(yaml.safe_dump(values), encoding="utf-8")

        try:
            load_environment_config(environment, config_dir)
        except ValueError:
            return
        raise AssertionError(f"Invalid value for {key} was accepted: {invalid_value!r}")


@seed(HYPOTHESIS_SEED)
@settings(max_examples=40, print_blob=True)
@given(
    selected=st.sampled_from(ENVIRONMENTS),
    token=st.text(alphabet=TOKEN_CHARS, min_size=1, max_size=12),
)
def test_missing_selected_file_does_not_fall_back(selected, token):
    """A missing selected file fails even when other environment files exist."""
    with TemporaryDirectory() as directory:
        config_dir = Path(directory)
        for environment in ENVIRONMENTS:
            if environment == selected:
                continue
            (config_dir / f"{environment}.yml").write_text(
                yaml.safe_dump(
                    {
                        "storage_path": f"s3a://bucket-{environment}/{token}",
                        "database_name": f"{environment}_{token}",
                    }
                ),
                encoding="utf-8",
            )

        try:
            load_environment_config(selected, config_dir)
        except FileNotFoundError:
            return
        raise AssertionError(f"Missing {selected}.yml unexpectedly fell back.")
