#!/usr/bin/env python3
"""
Build a sparse OW map-check graphics BMP for ROM generation.

Drag an 8bpp 128x128 source BMP onto this script (keep the full source
outside the repo), or pass its path as argv[1].

Compares the image to the vanilla JP ROM at SNES $18C000 using the ROM
path from resources/user settings, then writes:

  data/overworld/<same-filename-as-input>

Unchanged 8x8 tiles become 0xFF (skipped when patching). Changed tiles
are written in full. Seed generation loads that sparse BMP and converts
linear pixels to ROM 8x8 tile order before writing.

Optional argv[2] overrides the output path.
"""

import os
import sys


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    return os.path.abspath(os.path.join(_script_dir(), '..', '..'))


def _pause_if_needed():
    # Keep the console open when launched via drag-and-drop on Windows.
    if os.name == 'nt' and sys.stdin.isatty():
        try:
            input('Press Enter to exit...')
        except EOFError:
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    repo_root = _repo_root()
    script_dir = _script_dir()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from source.overworld.OWMap import (
        SPARSE_SKIP,
        get_rom_path_from_settings,
        make_sparse_diff_pixels,
        mark_skip_palette_entry,
        read_8bpp_bmp,
        vanilla_rom_to_linear_pixels,
        write_8bpp_bmp,
    )

    if not argv:
        print('Usage: drop an 8bpp 128x128 source BMP on this script, or pass its path.')
        print('Writes data/overworld/<same-filename> for ROM generation.')
        _pause_if_needed()
        return 1

    input_path = os.path.abspath(argv[0])
    if not os.path.isfile(input_path):
        print(f'Input file not found: {input_path}')
        _pause_if_needed()
        return 1

    if len(argv) >= 2:
        output_path = os.path.abspath(argv[1])
    else:
        output_path = os.path.join(script_dir, os.path.basename(input_path))

    if os.path.normcase(input_path) == os.path.normcase(output_path):
        print('Error: input and output paths are the same.')
        print('Keep the full source BMP outside the repo,')
        print(f'and let this script write {output_path}')
        _pause_if_needed()
        return 1

    try:
        rom_path = get_rom_path_from_settings(repo_root)
        print(f'Using ROM: {rom_path}')
        vanilla_linear = vanilla_rom_to_linear_pixels(rom_path)
        width, height, palette, pixels = read_8bpp_bmp(input_path)
        sparse, changed, changed_tiles = make_sparse_diff_pixels(
            pixels, palette, vanilla_linear, palette, skip=SPARSE_SKIP)
        write_8bpp_bmp(
            output_path, width, height,
            mark_skip_palette_entry(palette, skip=SPARSE_SKIP),
            sparse)
    except Exception as exc:
        print(f'Error: {exc}')
        _pause_if_needed()
        return 1

    total = len(sparse)
    print(f'Read {width}x{height} from {input_path}')
    print(f'Changed pixels: {changed} / {total} ({changed / total * 100:.2f}%)')
    print(f'Changed 8x8 tiles: {len(changed_tiles)}')
    if changed_tiles:
        print('  ' + ', '.join(f'({tx},{ty})' for tx, ty in changed_tiles))
    print(f'Wrote {output_path}')
    _pause_if_needed()
    return 0


if __name__ == '__main__':
    sys.exit(main())
