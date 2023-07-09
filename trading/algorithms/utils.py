from typing import List, Tuple, Dict

import numpy as np

from trading.algorithms.network import Edge
from trading.asset import Asset
from trading.blockchain.exchange import Exchanger


def assets_to_edges_list(assets: List[Asset], timestamp: int, exchanger: Exchanger, timespan: int) -> Tuple[
    List[Edge],
    Dict[str, int]
]:
    edges_list = []
    nodes_mapping = dict()
    for from_node, from_asset in enumerate(assets):
        nodes_mapping[f'{from_asset.platform}_{from_asset.symbol}'] = from_node
        for to_node, to_asset in enumerate(assets):
            if from_node == to_node:
                continue
            if from_asset.platform != to_asset.platform and from_asset.symbol != to_asset.symbol:
                continue
            edges_list.append(Edge(from_asset, from_node, to_asset, to_node,
                                   np.log(exchanger.exchange(timestamp, from_asset, to_asset, timespan))))

    return edges_list, nodes_mapping


def nodes_to_assets(nodes: List[int], assets: List[Asset]) -> List[Asset]:
    result = []
    for node in nodes:
        result.append(assets[node])
    return result
