'''
Turning a sketch from sketch_server.py into the polylines that get written
to OBJ. Shared by export_sketch.py (the command) and the save button in VR,
so both produce the same files for the same options.
'''

import numpy as np

from obj_polylines import clean_polylines

LAYERS = ( 'scaffold', 'shapes', 'raw' )
COORDS = ( 'fit', 'center', 'world' )

DEFAULT_LAYERS = 'raw'
DEFAULT_POINTS = 100
DEFAULT_COORDS = 'fit'


class ExportError( Exception ):
    pass


def add_export_options( parser ):
    parser.add_argument( '--layers', default = DEFAULT_LAYERS,
        help = 'comma-separated layers to export: raw (strokes exactly as drawn, including everything drawn freehand), '
               'shapes (white fitted curves) and scaffold (yellow construction lines) from paint.html?mode=scaffold. '
               'Default: %(default)s' )
    parser.add_argument( '--points', type = int, default = DEFAULT_POINTS,
        help = 'resample every stroke to this many points evenly spaced along it, like sketch.obj; '
               '0 keeps the original points. Default: %(default)s' )
    parser.add_argument( '--coords', choices = COORDS, default = DEFAULT_COORDS,
        help = 'fit: center the sketch on the origin and scale it so its largest side is 1; '
               'center: center it without scaling; world: VR coordinates as drawn (meters, floor at y = 0). Default: %(default)s' )


def parse_export_options( parser, args ):
    '''
    Validates the options added by `add_export_options()`.
    Returns them as keyword arguments for `sketch_polylines()`.
    '''
    layers = [ l.strip() for l in args.layers.split( ',' ) if l.strip() ]
    if not layers or any( l not in LAYERS for l in layers ):
        parser.error( '--layers must be a comma-separated list of: %s' % ', '.join( LAYERS ) )
    if args.points == 1 or args.points < 0:
        parser.error( '--points must be 0 or at least 2' )
    return { 'layers': layers, 'points': args.points, 'coords': args.coords }


def sketch_polylines( sketch, layers = ( DEFAULT_LAYERS, ), points = DEFAULT_POINTS, coords = DEFAULT_COORDS ):
    '''
    Given:
        sketch: the dictionary sketch_server.py sends for 'get-sketch'
        layers, points, coords: see `add_export_options()`
    Returns:
        a list of n-by-3 arrays, one per stroke, in drawing order.
    Raises ExportError if there is nothing to export.
    '''
    polylines = [ s['points'] for s in sketch['strokes'] if s['layer'] in layers ]
    if 'raw' in layers:
        polylines += sketch['raw']
    polylines = clean_polylines( polylines, points or None )

    if not polylines:
        s = sketch['session']
        raise ExportError( 'Nothing to export in %s (the sketch has %d strokes, %d shape curves, %d scaffold lines).'
                           % ( ' + '.join( layers ), s['raw'], s['shapes'], s['scaffold'] ) )

    if coords in ( 'fit', 'center' ):
        all_pts = np.concatenate( polylines )
        lo, hi = all_pts.min( axis = 0 ), all_pts.max( axis = 0 )
        center = ( lo + hi ) / 2
        scale = 1.
        if coords == 'fit' and ( hi - lo ).max() > 0:
            scale = 1. / ( hi - lo ).max()
        polylines = [ ( pts - center ) * scale for pts in polylines ]

    return polylines
