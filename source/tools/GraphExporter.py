import sys, importlib.util
import logging
from dataclasses import dataclass
from BaseClasses import Entrance, RegionType, Terrain, WorldType
from OWEdges import OWExitTypes

@dataclass
class Color:
    r: float
    g: float
    b: float

class GephiStreamer(object):
    PORT = 8216
    #COLOR_NODE_

    def __init__(self, port=PORT):
        self.streamer = self.connect(port)

    def connect(self, port):
        if 'gephistreamer' in sys.modules or (spec := importlib.util.find_spec('gephistreamer')) is not None:
            from gephistreamer import streamer
            from ws4py.exc import HandshakeError
            try:
                return streamer.Streamer(streamer.GephiWS(port=port, workspace='workspace1'))
            except ConnectionRefusedError as ex:
                logging.getLogger('').debug('GephiStreamer is unable to connect')
            except HandshakeError as ex:
                logging.getLogger('').debug('GephiStreamer is unable to connect, possible jammed port')
        return None

    def add_edge(self, object):
        if self.streamer:
            from gephistreamer import graph
            if type(object) is Entrance and object.parent_region and object.connected_region:
                def get_region_kind(region):
                    type = ''
                    if region.type == RegionType.LightWorld:
                        type = 'LightWorld'
                    elif region.type == RegionType.DarkWorld:
                        type = 'DarkWorld'
                    elif region.type == RegionType.Dungeon:
                        type = 'Dungeon'
                    elif region.type == RegionType.Cave:
                        type = 'Cave'
                    elif region.type == RegionType.Menu:
                        type = 'Menu'
                    return type
                def get_entrance_kind(entrance):
                    type = ''
                    if entrance.spot_type == 'Entrance':
                        if entrance.parent_region.type in [RegionType.LightWorld, RegionType.DarkWorld]:
                            if object.name in OWExitTypes['OWTerrain'] + OWExitTypes['Ledge']:
                                type = 'Terrain'
                            elif object.name in OWExitTypes['Portal']:
                                type = 'Portal'
                            else:
                                type = 'Entrance'
                        elif entrance.parent_region.type in [RegionType.Dungeon, RegionType.Cave]:
                            type = 'Underworld'
                        else:
                            type = 'Menu'
                    else:
                        type = entrance.spot_type
                    return type
                node_source = graph.Node(object.parent_region.name, object.parent_region.name, kind=get_region_kind(object.parent_region))
                node_target = graph.Node(object.connected_region.name, object.connected_region.name, kind=get_region_kind(object.connected_region))

                self.streamer.add_node(node_source,node_target)
                self.streamer.add_edge(graph.Edge(node_source,node_target,label=object.name,kind=get_entrance_kind(object)))