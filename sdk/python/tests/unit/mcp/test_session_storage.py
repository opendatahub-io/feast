"""Session-storage config factory.

OAuth state (client registrations, transactions, codes, token mappings) has
to be shared across replicas or a ``/callback`` can land on a node that never
saw the matching ``/authorize``. The factory's job is to turn loose YAML/env
values into exactly the kwargs each ``key_value.aio`` store constructor
wants, and to refuse anything it does not recognise.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Optional

import pytest

# py-key-value-aio arrives with the optional feast[mcp-server] extra.
pytest.importorskip("key_value", reason="feast[mcp-server] extra not installed")

from feast.mcp.session_storage import (  # noqa: E402
    MemorySessionStorageConfig,
    RedisSessionStorageConfig,
    SessionStorageConfig,
    SessionStorageConfigFactory,
)


class TestSupportedBackends:
    def test_all_documented_backends_are_registered(self):
        assert set(SessionStorageConfigFactory.supported()) == {
            "memory",
            "redis",
            "valkey",
            "disk",
            "mongodb",
            "postgresql",
        }

    @pytest.mark.parametrize(
        "backend,shared",
        [
            ("redis", True),
            ("valkey", True),
            ("mongodb", True),
            ("postgresql", True),
            ("memory", False),
            ("disk", False),
        ],
    )
    def test_shared_flag_matches_the_deployment_reality(self, backend, shared):
        """``shared`` is what warns operators off node-local stores."""
        assert SessionStorageConfigFactory.create(backend).shared is shared


class TestCreate:
    def test_unknown_backend_lists_the_valid_ones(self):
        with pytest.raises(ValueError) as excinfo:
            SessionStorageConfigFactory.create("memcached")
        message = str(excinfo.value)
        assert "memcached" in message
        assert "redis" in message

    def test_unknown_option_is_rejected_with_the_valid_options(self):
        with pytest.raises(ValueError) as excinfo:
            SessionStorageConfigFactory.create("redis", {"hostname": "cache"})
        message = str(excinfo.value)
        assert "hostname" in message
        assert "host" in message

    def test_defaults_apply_when_no_options_are_given(self):
        cfg = SessionStorageConfigFactory.create("redis")
        assert isinstance(cfg, RedisSessionStorageConfig)
        assert cfg.host == "localhost"
        assert cfg.port == 6379


class TestCoercion:
    """Options arrive as strings from YAML and env vars."""

    def test_int_option_is_coerced(self):
        cfg = SessionStorageConfigFactory.create("redis", {"port": "6380"})
        assert cfg.port == 6380
        assert isinstance(cfg.port, int)

    @pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on"])
    def test_truthy_bool_strings(self, raw):
        assert SessionStorageConfigFactory.create("redis", {"ssl": raw}).ssl is True

    @pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off", ""])
    def test_falsy_bool_strings(self, raw):
        assert SessionStorageConfigFactory.create("redis", {"ssl": raw}).ssl is False

    def test_real_bools_pass_through(self):
        assert SessionStorageConfigFactory.create("redis", {"ssl": True}).ssl is True

    def test_strings_are_left_alone(self):
        cfg = SessionStorageConfigFactory.create("redis", {"host": "cache.internal"})
        assert cfg.host == "cache.internal"


class TestStoreKwargs:
    """The rendered kwargs are passed verbatim as ``StoreClass(**kwargs)``."""

    def test_redis_url_form_excludes_host_and_port(self):
        cfg = SessionStorageConfigFactory.create("redis", {"url": "redis://cache:6379"})
        assert cfg.to_store_kwargs() == {"url": "redis://cache:6379"}

    def test_redis_host_form_when_no_url_is_given(self):
        cfg = SessionStorageConfigFactory.create(
            "redis", {"host": "cache", "port": "6380", "db": "2"}
        )
        kwargs = cfg.to_store_kwargs()
        assert kwargs["host"] == "cache"
        assert kwargs["port"] == 6380
        assert kwargs["db"] == 2
        assert "url" not in kwargs

    def test_none_valued_options_are_pruned_so_store_defaults_apply(self):
        kwargs = SessionStorageConfigFactory.create("redis").to_store_kwargs()
        assert "password" not in kwargs
        assert "default_collection" not in kwargs

    def test_memory_backend_renders_empty_kwargs_by_default(self):
        cfg = SessionStorageConfigFactory.create("memory")
        assert isinstance(cfg, MemorySessionStorageConfig)
        assert cfg.to_store_kwargs() == {}


class TestDescribe:
    def test_describe_omits_secrets(self):
        cfg = SessionStorageConfigFactory.create(
            "redis",
            {
                "password": "hunter2",  # pragma: allowlist secret
                "url": "redis://cache:6379",
            },
        )
        described = cfg.describe()
        assert "hunter2" not in str(described)
        assert "redis://cache:6379" not in str(described)

    def test_describe_reports_the_backend_and_store(self):
        described = SessionStorageConfigFactory.create("redis").describe()
        assert described["backend"] == "redis"
        assert described["store"].endswith(":RedisStore")
        assert described["requires_extra"] == "redis"
        assert described["shared"] is True


@dataclass(frozen=True)
class DummySessionStorageConfig(SessionStorageConfig):
    """A third-party backend, registered at runtime rather than shipped."""

    backend: ClassVar[str] = "dummy-test-backend"
    store_module: ClassVar[str] = "dummy.store"
    store_class: ClassVar[str] = "DummyStore"
    requires_extra: ClassVar[Optional[str]] = None
    shared: ClassVar[bool] = True

    slot: Optional[str] = None
    retries: int = 3

    def to_store_kwargs(self) -> dict[str, Any]:
        return {"slot": self.slot, "retries": self.retries}


class TestRegister:
    @pytest.fixture
    def registered(self):
        SessionStorageConfigFactory.register(DummySessionStorageConfig)
        yield
        SessionStorageConfigFactory._registry.pop("dummy-test-backend", None)

    def test_a_custom_backend_can_be_created(self, registered):
        cfg = SessionStorageConfigFactory.create("dummy-test-backend", {"slot": "a"})
        assert cfg.to_store_kwargs() == {"slot": "a", "retries": 3}

    def test_a_custom_backend_gets_the_same_option_coercion(self, registered):
        cfg = SessionStorageConfigFactory.create("dummy-test-backend", {"retries": "7"})
        assert cfg.retries == 7

    def test_a_custom_backend_appears_in_supported(self, registered):
        assert "dummy-test-backend" in SessionStorageConfigFactory.supported()

    def test_unregistered_backend_is_gone_again(self):
        assert "dummy-test-backend" not in SessionStorageConfigFactory.supported()
