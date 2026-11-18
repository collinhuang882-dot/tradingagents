"""Isolate upstream SDK calls so unresponsive sources have a hard timeout."""
import contextlib
import json
import sys

ALLOWED = {
    'stock_zh_index_daily_em', 'stock_zh_index_daily',
    'stock_info_a_code_name', 'stock_info_sh_name_code', 'stock_info_sz_name_code',
    'index_realtime_sw', 'index_hist_sw', 'stock_industry_clf_hist_sw', 'index_component_sw',
    'tool_trade_date_hist_sina', 'stock_zh_a_hist', 'stock_zh_a_daily',
    'stock_notice_report', 'stock_individual_notice_report', 'index_global_hist_sina', 'index_global_name_table',
    'index_global_hist_em', 'stock_individual_info_em',
    'stock_us_daily', 'stock_hk_index_daily_sina',
}


def main():
    request = json.loads(sys.stdin.read())
    name = request['function']
    if name not in ALLOWED:
        raise ValueError('Unsupported SDK function')
    empty_notices = False
    if request.get('budget_owner') or name == 'stock_individual_notice_report':
        from pathlib import Path
        from . import storage
        from .agent_store import reserve
        import requests
        if request.get('budget_data'):
            storage.DATA = Path(request['budget_data'])
        original_send = requests.sessions.Session.send
        request_count = 0
        def counted_send(session, prepared, **kwargs):
            nonlocal request_count, empty_notices
            request_count += 1
            if request_count > 12:
                from .agent_store import BudgetExceeded
                raise BudgetExceeded('单次数据工具请求超过12次，停止该来源分页')
            if request.get('budget_owner'):
                reserve('network', request['budget_owner'])
            kwargs['timeout'] = min(kwargs.get('timeout') or 18, 18) if isinstance(kwargs.get('timeout'), (int, float, type(None))) else 18
            response = original_send(session, prepared, **kwargs)
            if name == 'stock_individual_notice_report' and response.status_code == 200 and prepared.url.startswith('https://np-anotice-stock.eastmoney.com/api/security/ann?'):
                try:
                    data = response.json().get('data') or {}
                    empty_notices = data.get('total_hits') == 0 and data.get('list') == []
                except ValueError:
                    pass
            return response
        requests.sessions.Session.send = counted_send
    with contextlib.redirect_stdout(sys.stderr):
        import akshare as ak
        try:
            frame = getattr(ak, name)(**request.get('params', {}))
        except KeyError:
            if not empty_notices:
                raise
            # AKShare indexes the missing '代码' column for a verified empty response.
            import pandas as pd
            frame = pd.DataFrame()
    # pandas handles timestamps, NumPy scalars and non-finite values consistently.
    sys.stdout.write(frame.to_json(orient='records', force_ascii=True, date_format='iso'))


if __name__ == '__main__':
    main()
