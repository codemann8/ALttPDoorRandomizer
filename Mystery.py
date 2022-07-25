import argparse
import logging
import RaceRandom as random
import urllib.request
import urllib.parse
import yaml

from DungeonRandomizer import parse_cli
from Main import main as DRMain
from source.classes.BabelFish import BabelFish
from yaml.constructor import SafeConstructor

def add_bool(self, node):
    return self.construct_scalar(node)

SafeConstructor.add_constructor(u'tag:yaml.org,2002:bool', add_bool)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--multi', default=1, type=lambda value: min(max(int(value), 1), 255))
    multiargs, _ = parser.parse_known_args()

    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', help='Path to the weights file to use for rolling game settings, urls are also valid')
    parser.add_argument('--samesettings', help='Rolls settings per weights file rather than per player', action='store_true')
    parser.add_argument('--seed', help='Define seed number to generate.', type=int)
    parser.add_argument('--multi', default=1, type=lambda value: min(max(int(value), 1), 255))
    parser.add_argument('--names', default='')
    parser.add_argument('--teams', default=1, type=lambda value: max(int(value), 1))
    parser.add_argument('--create_spoiler', action='store_true')
    parser.add_argument('--no_race', action='store_true')
    parser.add_argument('--suppress_rom', action='store_true')
    parser.add_argument('--suppress_meta', action='store_true')
    parser.add_argument('--bps', action='store_true')
    parser.add_argument('--rom')
    parser.add_argument('--enemizercli')
    parser.add_argument('--outputpath')
    parser.add_argument('--loglevel', default='info', choices=['debug', 'info', 'warning', 'error', 'critical'])
    for player in range(1, multiargs.multi + 1):
        parser.add_argument(f'--p{player}', help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.seed is None:
        random.seed(None)
        seed = random.randint(0, 999999999)
    else:
        seed = args.seed
    random.seed(seed)

    seedname = f'M{random.randint(0, 999999999)}'
    print(f"Generating mystery for {args.multi} player{'s' if args.multi > 1 else ''}, {seedname} Seed {seed}")

    weights_cache = {}
    if args.weights:
        weights_cache[args.weights] = get_weights(args.weights)
        print(f"Weights: {args.weights} >> {weights_cache[args.weights]['description']}")
    for player in range(1, args.multi + 1):
        path = getattr(args, f'p{player}')
        if path:
            if path not in weights_cache:
                weights_cache[path] = get_weights(path)
            print(f"P{player} Weights: {path} >> {weights_cache[path]['description']}")

    erargs = parse_cli(['--multi', str(args.multi)])
    erargs.seed = seed
    erargs.names = args.names
    erargs.create_spoiler = args.create_spoiler
    erargs.suppress_rom = args.suppress_rom
    erargs.suppress_meta = args.suppress_meta
    erargs.bps = args.bps
    erargs.race = not args.no_race
    erargs.outputname = seedname
    if args.outputpath:
        erargs.outputpath = args.outputpath
    erargs.loglevel = args.loglevel
    erargs.mystery = True

    if args.rom:
        erargs.rom = args.rom
    if args.enemizercli:
        erargs.enemizercli = args.enemizercli

    mw_settings = {'algorithm': False}

    settings_cache = {k: (roll_settings(v) if args.samesettings else None) for k, v in weights_cache.items()}

    for player in range(1, args.multi + 1):
        path = getattr(args, f'p{player}') if getattr(args, f'p{player}') else args.weights
        if path:
            settings = settings_cache[path] if settings_cache[path] else roll_settings(weights_cache[path])
            for k, v in vars(settings).items():
                if v is not None:
                    if k == 'algorithm':  # multiworld wide parameters
                        if not mw_settings[k]:  # only use the first roll
                            setattr(erargs, k, v)
                            mw_settings[k] = True
                    else:
                        getattr(erargs, k)[player] = v
        else:
            raise RuntimeError(f'No weights specified for player {player}')

    # set up logger
    loglevel = {'error': logging.ERROR, 'info': logging.INFO, 'warning': logging.WARNING, 'debug': logging.DEBUG}[erargs.loglevel]
    logging.basicConfig(format='%(message)s', level=loglevel)

    DRMain(erargs, seed, BabelFish())

def get_weights(path):
    try:
        if urllib.parse.urlparse(path).scheme:
            return yaml.load(urllib.request.urlopen(path), Loader=yaml.FullLoader)
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.load(f, Loader=yaml.SafeLoader)
    except Exception as e:
        raise Exception(f'Failed to read weights file: {e}')

def roll_settings(weights):
    def get_choice(option, root=None):
        root = weights if root is None else root
        if option not in root:
            return None
        if type(root[option]) is not dict:
            return root[option]
        if not root[option]:
            return None
        return random.choices(list(root[option].keys()), weights=list(map(int,root[option].values())))[0]

    def get_choice_default(option, root=weights, default=None):
        choice = get_choice(option, root)
        if choice is None and default is not None:
            return default
        return choice

    while True:
        subweights = weights.get('subweights', {})
        if len(subweights) == 0:
            break
        chances = ({k: int(v['chance']) for (k, v) in subweights.items()})
        subweight_name = random.choices(list(chances.keys()), weights=list(chances.values()))[0]
        subweights = weights.get('subweights', {}).get(subweight_name, {}).get('weights', {})
        subweights['subweights'] = subweights.get('subweights', {})
        weights = {**weights, **subweights}

    ret = argparse.Namespace()

    ret.algorithm = get_choice('algorithm')

    glitch_map = {'none': 'noglitches', 'no_logic': 'nologic', 'owglitches': 'owglitches',
                  'owg': 'owglitches', 'minorglitches': 'minorglitches'}
    glitches_required = get_choice('glitches_required')
    if glitches_required is not None:
        if glitches_required not in glitch_map.keys():
            print(f'Logic did not match one of: {", ".join(glitch_map.keys())}')
            glitches_required = 'none'
        ret.logic = glitch_map[glitches_required]

    item_placement = get_choice('item_placement')
    # not supported in ER

    dungeon_items = get_choice('dungeon_items')
    ret.mapshuffle = get_choice('map_shuffle') == 'on' if 'map_shuffle' in weights else dungeon_items in ['mc', 'mcs', 'full']
    ret.compassshuffle = get_choice('compass_shuffle') == 'on' if 'compass_shuffle' in weights else dungeon_items in ['mc', 'mcs', 'full']
    ret.keyshuffle = get_choice('smallkey_shuffle') == 'on' if 'smallkey_shuffle' in weights else dungeon_items in ['mcs', 'full']
    ret.bigkeyshuffle = get_choice('bigkey_shuffle') == 'on' if 'bigkey_shuffle' in weights else dungeon_items in ['full']

    ret.accessibility = get_choice('accessibility')
    ret.restrict_boss_items = get_choice('restrict_boss_items')

    overworld_shuffle = get_choice('overworld_shuffle')
    ret.ow_shuffle = overworld_shuffle if overworld_shuffle != 'none' else 'vanilla'
    ret.ow_crossed = get_choice('overworld_crossed')
    ret.ow_keepsimilar = get_choice('overworld_keepsimilar') == 'on'
    ret.ow_mixed = get_choice('overworld_swap') == 'on'
    ret.ow_whirlpool = get_choice('whirlpool_shuffle') == 'on'
    overworld_flute = get_choice('flute_shuffle')
    ret.ow_fluteshuffle = overworld_flute if overworld_flute != 'none' else 'vanilla'
    ret.bonk_drops = get_choice('bonk_drops') == 'on'
    entrance_shuffle = get_choice('entrance_shuffle')
    ret.shuffle = entrance_shuffle if entrance_shuffle != 'none' else 'vanilla'
    overworld_map = get_choice('overworld_map')
    ret.overworld_map = overworld_map if overworld_map != 'default' else 'default'
    door_shuffle = get_choice('door_shuffle')
    ret.door_shuffle = door_shuffle if door_shuffle != 'none' else 'vanilla'
    ret.intensity = get_choice('intensity')
    ret.experimental = get_choice('experimental') == 'on'
    ret.collection_rate = get_choice('collection_rate') == 'on'

    ret.dungeon_counters = get_choice('dungeon_counters') if 'dungeon_counters' in weights else 'default'
    if ret.dungeon_counters == 'default':
        ret.dungeon_counters = 'pickup' if ret.door_shuffle != 'vanilla' or ret.compassshuffle == 'on' else 'off'

    ret.pseudoboots = get_choice('pseudoboots') == 'on'
    ret.shopsanity = get_choice('shopsanity') == 'on'
    ret.dropshuffle = get_choice('dropshuffle') == 'on'
    ret.pottery = get_choice('pottery') if 'pottery' in weights else 'none'
    ret.colorizepots = get_choice('colorizepots') == 'on'
    ret.shufflepots = get_choice('pot_shuffle') == 'on'
    ret.mixed_travel = get_choice('mixed_travel') if 'mixed_travel' in weights else 'prevent'
    ret.standardize_palettes = get_choice('standardize_palettes') if 'standardize_palettes' in weights else 'standardize'

    goal = get_choice('goals')
    if goal is not None:
        ret.goal = {'ganon': 'ganon',
                    'fast_ganon': 'crystals',
                    'dungeons': 'dungeons',
                    'pedestal': 'pedestal',
                    'triforce-hunt': 'triforcehunt',
                    'trinity': 'trinity'
                    }[goal]

    ret.openpyramid = get_choice('open_pyramid') if 'open_pyramid' in weights else 'auto'

    ret.shuffleganon = get_choice('shuffleganon') == 'on'
    ret.shufflelinks = get_choice('shufflelinks') == 'on'

    ret.crystals_gt = get_choice('tower_open')
    ret.crystals_ganon = get_choice('ganon_open')

    from ItemList import set_default_triforce
    default_tf_goal, default_tf_pool = set_default_triforce(ret.goal, 0, 0)
    goal_min = get_choice_default('triforce_goal_min', default=default_tf_goal)
    goal_max = get_choice_default('triforce_goal_max', default=default_tf_goal)
    pool_min = get_choice_default('triforce_pool_min', default=default_tf_pool)
    pool_max = get_choice_default('triforce_pool_max', default=default_tf_pool)
    ret.triforce_goal = random.randint(int(goal_min), int(goal_max))
    min_diff = get_choice_default('triforce_min_difference', default=(default_tf_pool-default_tf_goal))
    ret.triforce_pool = random.randint(max(int(pool_min), ret.triforce_goal + int(min_diff)), int(pool_max))

    ret.mode = get_choice('world_state')
    if ret.mode == 'retro':
        ret.mode = 'open'
        ret.retro = True
    ret.retro = get_choice('retro') == 'on'  # this overrides world_state if used

    ret.bombbag = get_choice('bombbag') == 'on'

    ret.hints = get_choice('hints') == 'on'

    swords = get_choice('weapons')
    if swords is not None:
        ret.swords = {'randomized': 'random',
                    'assured': 'assured',
                    'vanilla': 'vanilla',
                    'swordless': 'swordless'
                    }[swords]

    ret.difficulty = get_choice('item_pool')

    ret.item_functionality = get_choice('item_functionality')

    old_style_bosses = {'basic': 'simple',
                        'normal': 'full',
                        'chaos': 'random'}
    boss_choice = get_choice('boss_shuffle')
    if boss_choice in old_style_bosses.keys():
        boss_choice = old_style_bosses[boss_choice]
    ret.shufflebosses = boss_choice

    enemy_choice = get_choice('enemy_shuffle')
    if enemy_choice == 'chaos':
        enemy_choice = 'random'
    ret.shuffleenemies = enemy_choice

    old_style_damage = {'none': 'default',
                        'chaos': 'random'}
    damage_choice = get_choice('enemy_damage')
    if damage_choice in old_style_damage:
        damage_choice = old_style_damage[damage_choice]
    ret.enemy_damage = damage_choice

    ret.enemy_health = get_choice('enemy_health')

    ret.beemizer = get_choice('beemizer') if 'beemizer' in weights else '0'

    inventoryweights = weights.get('startinventory', {})
    startitems = []
    for item in inventoryweights.keys():
        if get_choice(item, inventoryweights) == 'on':
            startitems.append(item)
    ret.startinventory = ','.join(startitems)
    if len(startitems) > 0:
        ret.usestartinventory = True

    if 'rom' in weights:
        romweights = weights['rom']
        ret.sprite = get_choice('sprite', romweights)
        ret.disablemusic = get_choice('disablemusic', romweights) == 'on'
        ret.quickswap = get_choice('quickswap', romweights) == 'on'
        ret.reduce_flashing = get_choice('reduce_flashing', romweights) == 'on'
        ret.msu_resume = get_choice('msu_resume', romweights) == 'on'
        ret.fastmenu = get_choice('menuspeed', romweights)
        ret.heartcolor = get_choice('heartcolor', romweights)
        ret.heartbeep = get_choice('heartbeep', romweights)
        ret.ow_palettes = get_choice('ow_palettes', romweights)
        ret.uw_palettes = get_choice('uw_palettes', romweights)
        ret.shuffle_sfx = get_choice('shuffle_sfx', romweights) == 'on'

    return ret

if __name__ == '__main__':
    main()
