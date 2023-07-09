import datetime
import json
import uuid
from typing import List, Tuple

import dash
import pandas as pd
from dash import html, Output, Input, State
from dash.exceptions import PreventUpdate
from dash_extensions.enrich import FileSystemStore, Trigger

from backend import data
from backend.utils import to_asset_pairs, to_controls_state_dict
from backend.web.layouts.controls import create_controls, create_step_control, create_progress_bar
from backend.web.layouts.graphs_layout import create_graph_layout, create_trades_predictions, create_network, \
    create_prices
from backend.web.layouts.tables import create_arbitrage_table, create_report_table
from trading.api.exchange_api import ExchangesAPI
from trading.blockchain import Blockchain
from trading.common.constants import AVG_FEES
from trading.exchanges.arbitrage_pairs import arbitrage_pairs
from trading.strategies import name_to_strategy
from .layout import html_layout
from ..layouts.bars import create_trades


def init_dashboard(server):
    """Create a Plotly Dash dashboard."""
    dash_app = dash.Dash(
        title='Crypto Arbitrage Analytics Bot',
        server=server,
        routes_pathname_prefix='/dashapp/',
        external_stylesheets=[
            '/static/dist/css/styles.css',
        ],
    )

    # Initialize callbacks after our app is loaded
    # Pass dash_app as a parameter
    init_callbacks(dash_app, server.config)

    data.init_client(server.config.get('TRADING_CONFIG'))

    dash_app.index_string = html_layout

    def serve_layout():
        session_id = str(uuid.uuid4())
        return html.Div(
            id='dash-container',
            children=create_graph_layout(server.config, session_id),
        )

    # Create Dash Layout
    dash_app.layout = serve_layout

    return dash_app.server


def init_callbacks(app, config):
    controls_state = [
        State('json-input-portfolio-control', 'value'),
        State('select-strategy-select-control', 'value'),
        State('multiselect-platforms-select-control', 'value'),
        State('use-fees-checkbox-control', 'checked'),
        State('date-range-picker-control', 'value'),
        State('start-timestamp-timeinput-control', 'value'),
        State('end-timestamp-timeinput-control', 'value'),
        State('multiselect-assets-select-control', 'value'),
        State('slider-max-trade-ratio-control', 'value'),
        State('slider-min-spread-control', 'value'),
        State('select-primary-granularity-select-control', 'value'),
        State('select-secondary-granularity-select-control', 'value')
    ]

    def reset_cache(fsc):
        graphs_history = fsc.get('graphs_history')
        fsc.clear()
        fsc.set('report_table', None)
        fsc.set('run_progress', None)
        fsc.set('graphs_history', graphs_history)

    @app.callback(Output('empty-output', 'children'), Input('session-id', 'data'))
    def on_page_load(session_id):
        fsc = FileSystemStore(f'./cache/{session_id}')
        reset_cache(fsc)
        raise PreventUpdate

    @app.callback(
        [
            Output('loading-controls', 'children', allow_duplicate=True),
            Output('step-control', 'children', allow_duplicate=True),
            Output('trades-predicted-plot', 'children', allow_duplicate=True),
            Output('trades-plot', 'children', allow_duplicate=True),
            Output('trading-graph', 'children', allow_duplicate=True),
            Output('prices-plot', 'children', allow_duplicate=True),
        ],
        [
            Input('run-button-control', 'n_clicks'),
            *controls_state,
            State('session-id', 'data')
        ],
        prevent_initial_call=True
    )
    def update_report_state(
            run_n_clicks,
            json_portfolio: str,
            strategy_name: str,
            platforms: List[str],
            use_fees: bool,
            date_range: Tuple[str],
            start_time: str,
            end_time: str,
            assets: List[str],
            max_trade_ratio: float,
            min_spread: float,
            primary_granularity: int,
            secondary_granularity: int,
            session_id: str
    ):
        if run_n_clicks is None:
            raise PreventUpdate

        controls_state = dict(
            json_portfolio=json_portfolio,
            strategy_name=strategy_name,
            platforms=platforms,
            fees_checkbox=use_fees,
            date_range=date_range,
            start_time=start_time,
            end_time=end_time,
            assets=assets,
            max_trade_ratio=max_trade_ratio,
            min_spread=min_spread,
            primary_granularity=primary_granularity,
            secondary_granularity=secondary_granularity
        )

        fsc = FileSystemStore(f'./cache/{session_id}')
        reset_cache(fsc)

        prices_api = ExchangesAPI(db=data.get_db('crypto_exchanges'), exchanges_names=platforms)

        # TODO: real fees and prices data
        blockchain = Blockchain(prices_api=prices_api,
                                fees_data=AVG_FEES,
                                prices_data=None,
                                disable_fees=not use_fees)

        start_date = datetime.datetime.strptime(f'{date_range[0]} {start_time.split("T")[1]}', '%Y-%m-%d %H:%M:%S')
        start_timestamp = int(datetime.datetime.timestamp(start_date))

        end_date = datetime.datetime.strptime(f'{date_range[1]} {end_time.split("T")[1]}', '%Y-%m-%d %H:%M:%S')
        end_timestamp = int(datetime.datetime.timestamp(end_date))

        report_data = None
        prices_data = None
        graphs_history = []
        for run_res in name_to_strategy(strategy_name)(blockchain=blockchain,
                                                       start_timestamp=start_timestamp,
                                                       end_timestamp=end_timestamp,
                                                       timespan=primary_granularity,
                                                       primary_granularity=primary_granularity,
                                                       secondary_granularity=secondary_granularity,
                                                       portfolio=json.loads(json_portfolio),
                                                       platforms=platforms,
                                                       symbols=assets,
                                                       max_trade_ratio=max_trade_ratio / 100,
                                                       min_spread=min_spread / 100):
            report_data = pd.DataFrame(run_res['report'])
            prices_data = run_res['prices']
            graphs_history = run_res['graphs_history']
            fsc.set('graphs_history', graphs_history)
            fsc.set('report_table', report_data)
            fsc.set('prices', prices_data)
            fsc.set('run_progress',
                    (run_res['end_timestamp'] - start_timestamp) / (end_timestamp - start_timestamp) * 100)

        return create_controls(config, controls_state), \
            create_step_control(report_data, disabled=False), \
            create_trades_predictions(report_data), \
            create_trades(report_data, secondary_granularity), \
            create_network(graphs_history[-1]), \
            create_prices(prices_data)

    @app.callback([
        Output('report-table-output', 'children'),
        Output('report-table', 'style'),
    ], [
        Trigger('interval-progress', 'n_intervals'),
        State('session-id', 'data')
    ])
    def update_report_table(n_intervals, session_id):
        fsc = FileSystemStore(f'./cache/{session_id}')

        data = fsc.get('report_table')
        if data is None:
            raise PreventUpdate
        return create_report_table(data), {'display': 'block'}

    @app.callback(
        Output('simulation-progress-bar', 'children'),
        [
            Trigger('interval-progress', 'n_intervals'),
            State('session-id', 'data')
        ]
    )
    def update_run_progress(n_intervals, session_id):
        fsc = FileSystemStore(f'./cache/{session_id}')

        value = fsc.get('run_progress')  # get progress
        if value is None:
            # cache is only reset after we've hit 100% completion in the progress bar
            reset_cache(fsc)
            return create_progress_bar(0)
        value = min(value, 100)

        if value == 100:
            reset_cache(fsc)
        return create_progress_bar(value)

    @app.callback(
        [
            Output('loading-arbitrage-table-output', 'children'),
            Output('loading-controls', 'children', allow_duplicate=True),
            Output('arbitrage-table', 'style'),
        ],
        [
            Input('view-arbitrage-button-control', 'n_clicks'),
            *controls_state
        ],
        prevent_initial_call=True,
    )
    def update_arbitrages(n_clicks,
                          *args):
        if n_clicks is None:
            raise PreventUpdate

        controls_state = to_controls_state_dict(*args)
        return create_arbitrage_table(
            arbitrage_pairs(controls_state['platforms'], to_asset_pairs(controls_state['assets']))), \
            create_controls(config, controls_state), \
            {'display': 'block'}

    @app.callback(
        Output('trading-graph', 'children', allow_duplicate=True),
        [
            Input('slider-step-control', 'value'),
            State('session-id', 'data')
        ],
        prevent_initial_call=True)
    def update_running(step: int, session_id: str):
        fsc = FileSystemStore(f'./cache/{session_id}')

        return update_network(fsc, step)

    @app.callback([
        Output('step-control', 'children', allow_duplicate=True),
        Output('trades-predicted-plot', 'children', allow_duplicate=True),
        Output('trades-plot', 'children', allow_duplicate=True),
        Output('trading-graph', 'children', allow_duplicate=True),
        Output('prices-plot', 'children', allow_duplicate=True),
    ], [
        Trigger('interval-progress', 'n_intervals'),
        State('select-secondary-granularity-select-control', 'value'),
        State('slider-step-control', 'value'),
        State('session-id', 'data')
    ],
        prevent_initial_call=True)
    def update_running(n_intervals, secondary_granularity: int, step: int, session_id: str):
        fsc = FileSystemStore(f'./cache/{session_id}')

        return update_step_control(fsc), \
            update_trades_predictions(fsc), \
            update_trades(fsc, secondary_granularity), \
            update_network(fsc, step), \
            update_prices(fsc)

    def update_step_control(fsc: FileSystemStore):
        data = fsc.get('report_table')
        if data is None:
            raise PreventUpdate
        return create_step_control(data, disabled=True)

    def update_trades_predictions(fsc: FileSystemStore):
        data = fsc.get('report_table')
        if data is None:
            raise PreventUpdate
        return create_trades_predictions(data)

    def update_trades(fsc: FileSystemStore, gran: int):
        data = fsc.get('report_table')
        if data is None:
            raise PreventUpdate
        return create_trades(data, gran)

    def update_network(fsc: FileSystemStore, graph_idx: int = -1):
        data = fsc.get('graphs_history')
        if data is None:
            raise PreventUpdate
        if len(data) == 0:
            raise PreventUpdate
        return create_network(data[graph_idx])

    def update_prices(fsc: FileSystemStore):
        data = fsc.get('prices')
        if data is None:
            raise PreventUpdate
        return create_prices(data)
