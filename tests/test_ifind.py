import copy
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app import storage, ifind, ak_adapter, agent_store
from app.analytics import clean_bars
from app.providers import ProviderError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA', tmp_path)
    monkeypatch.setenv('TRADINGAGENT_NO_WORKER', '1')
    monkeypatch.delenv('IFIND_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('IFIND_REFRESH_TOKEN', raising=False)
    storage.init()
    storage.save_settings({'ifind_enabled': True, 'ifind_access_token': 'secret-access'})


def response(cps=1):
    price = 10 if cps == 1 else 20
    return {'errorcode': 0, 'tables': [{'thscode': '601138.SH', 'time': ['2026-11-13', '2026-11-16'],
            'table': {'open': [price, price], 'high': [price+1, price+1], 'low': [price-1, price-1],
                      'close': [price, price], 'volume': [10000, 20000], 'amount': [100000, 200000],
                      'preClose': [10, 10], 'changeRatio': [0, 0]}}]}


def test_http_contract_refresh_and_units(monkeypatch):
    storage.save_settings({'ifind_refresh_token': 'secret-refresh'})
    calls = []
    def fake(url, **kw):
        calls.append((url, kw))
        data = {'errorcode': 0, 'data': {'access_token': 'fresh-access'}} if url.endswith('get_access_token') else response(int(kw['json']['functionpara']['CPS']))
        return httpx.Response(200, json=data, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', fake)
    agent_store.ensure_budget('ifind-test')
    with agent_store.context('ifind-test'):
        rows, factors, provider, notes = ifind.IFinD().history('601138.SH', '20261113', '20261116')
    assert agent_store.budget('ifind-test')['network'] == 3
    assert calls[0][1]['headers']['refresh_token'] == 'secret-refresh'
    assert calls[1][1]['headers']['access_token'] == 'fresh-access'
    assert calls[1][1]['json']['functionpara'] == {'CPS':'1','Interval':'D','Fill':'Omit','Currency':'MHB'}
    bars, errors, adjusted = clean_bars(rows, '2026-11-16', factors)
    assert bars[-1]['volume'] == 20000 and bars[-1]['amount'] == 200000
    assert bars[-1]['adjusted_close'] == 10 and adjusted and not errors and not notes
    assert 'iFinD' in provider


@pytest.mark.parametrize('failure', ['wrong_code','stale','nan','ohlc','duplicate','length','empty'])
def test_bad_quotes_never_accepted(monkeypatch, failure):
    data = response(); table = data['tables'][0]
    if failure == 'wrong_code': table['thscode'] = '600519.SH'
    elif failure == 'stale': table['time'][-1] = '2026-11-14'
    elif failure == 'nan': table['table']['close'][-1] = float('nan')
    elif failure == 'ohlc': table['table']['close'][-1] = 50
    elif failure == 'duplicate': table['time'][-1] = table['time'][0]
    elif failure == 'length': table['table']['volume'] = []
    else: data['tables'] = []
    monkeypatch.setattr(ifind, 'post', lambda *a: data)
    with pytest.raises(ProviderError): ifind.IFinD().history('601138.SH','20261113','20261116')


def test_adjustment_failure_keeps_price_without_fake_trend(monkeypatch):
    def fake(endpoint, payload, headers):
        if payload['functionpara']['CPS'] == '3': raise ProviderError('no permission')
        return response()
    monkeypatch.setattr(ifind, 'post', fake)
    rows, factors, _, notes = ifind.IFinD().history('601138.SH','20261113','20261116')
    assert rows and not factors and notes
    assert not clean_bars(rows, '2026-11-16', factors)[2]


def test_fallback_only_after_ak_failure(monkeypatch):
    calls = []
    fallback = ([], {}, '同花顺 iFinD/HTTP 股票日线', [])
    monkeypatch.setattr(ifind.IFinD, 'history', lambda *a: calls.append('ifind') or fallback)
    monkeypatch.setattr(ak_adapter.AKShare, '_history', lambda *a: ('ak', {}, 'AKShare', []))
    client = ak_adapter.AKShare()
    assert client.history('601138.SH', '20261113', '20261116')[0] == 'ak'
    assert not calls
    def fail(*a): raise ProviderError('AK unavailable')
    monkeypatch.setattr(ak_adapter.AKShare, '_history', fail)
    with pytest.raises(ProviderError): client.history('601138.SH', '20261113', '20261116', 'stock_zh_a_hist')
    assert not calls
    assert client.history('601138.SH', '20261113', '20261116', 'stock_zh_a_daily')[2] == fallback[2]
    assert calls == ['ifind']
    storage.save_settings({'ifind_enabled': False})
    with pytest.raises(ProviderError): client.history('601138.SH', '20261113', '20261116')
    assert calls == ['ifind']


def test_invalid_ak_current_day_triggers_full_chain(monkeypatch):
    attempts = []
    def empty(function, **kwargs):
        attempts.append(function)
        return []
    monkeypatch.setattr(ak_adapter, 'fetch', empty)
    monkeypatch.setattr(ifind.IFinD, 'history', lambda *a: ([], {}, 'ifind', []))
    assert ak_adapter.AKShare().history('601138.SH','20261113','20261116')[2] == 'ifind'
    assert attempts == ['stock_zh_a_hist', 'stock_zh_a_daily']


def test_errors_and_public_settings_hide_credentials(monkeypatch):
    def fake(url, **kw):
        return httpx.Response(200, json={'errorcode': -1, 'errmsg': 'secret-access'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx, 'post', fake)
    with pytest.raises(ProviderError) as exc: ifind.IFinD().history('601138.SH','20261113','20261116')
    assert 'secret-access' not in str(exc.value)
    from app.main import app
    with TestClient(app) as client:
        r = client.get('/api/settings')
        assert r.json()['ifind_configured']
        assert 'secret-access' not in r.text and 'ifind_access_token' not in r.text
        result = client.post('/api/settings/ifind/test')
        assert result.status_code == 422 and 'secret-access' not in result.text
