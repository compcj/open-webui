import ssl
import pytest
import redis
import redis.asyncio
from open_webui.utils.redis import _build_sentinel, build_sentinel_tls_kwargs, build_sentinel_kwargs


@pytest.mark.parametrize('async_mode', [False, True])
def test_sentinel_encrypts_discovery_and_master_without_losing_managed_connection(async_mode):
    module = redis.asyncio if async_mode else redis
    proxy = _build_sentinel(module, 'rediss://SyntheticMaster/2', [('sentinel.invalid', 26379)], True, async_mode)
    discovery = proxy._sentinel.sentinels[0].connection_pool
    master = proxy._resolve_master().connection_pool
    assert discovery.connection_class.__name__ == 'SSLConnection'
    assert master.connection_class.__name__ == 'SentinelManagedSSLConnection'
    for pool in (discovery, master):
        assert pool.connection_kwargs['ssl_cert_reqs'] == 'required'
        assert pool.connection_kwargs['ssl_check_hostname'] is True
    assert proxy._service_name == 'SyntheticMaster'


def test_plain_sentinel_and_typed_tls_options():
    assert build_sentinel_tls_kwargs('redis://master/0') == {}
    value = build_sentinel_tls_kwargs('rediss://master/0?ssl_ca_certs=ca.pem&ssl_check_hostname=false')
    assert value['ssl_check_hostname'] is False and value['ssl_ca_certs'] == 'ca.pem'
    assert build_sentinel_kwargs('rediss://master/0', {'socket_timeout': 3})['ssl'] is True
    with pytest.raises(ValueError):
        build_sentinel_tls_kwargs('rediss://master/0?ssl_server_hostname=ignored.invalid')
