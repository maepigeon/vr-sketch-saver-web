#!/usr/bin/env python3
'''
Saves the sketch currently drawn in VR as OBJ polylines (same layout as
sketch.obj). sketch_server.py must be running.

By default: the strokes exactly as drawn, each resampled to 100 points, with
the sketch centered on the origin and scaled so its largest side is 1.

Examples:
    python3 export_sketch.py                       # writes sketch-YYYYmmdd-HHMMSS.obj
    python3 export_sketch.py chair.obj
    python3 export_sketch.py chair.obj --layers shapes      # fitted curves from paint.html?mode=scaffold
    python3 export_sketch.py chair.obj --points 0 --coords world
'''

import argparse
import asyncio
import json
import sys
import time

import websockets

from obj_polylines import write_obj
from sketch_export import ExportError, add_export_options, parse_export_options, sketch_polylines


async def fetch_sketch( host, port ):
    uri = 'ws://%s:%d' % ( host, port )
    try:
        async with websockets.connect( uri, max_size = None ) as websocket:
            await websocket.send( 'get-sketch' )
            reply = await websocket.recv()
    except OSError as e:
        sys.exit( 'Could not reach the sketch server at %s (%s). Is sketch_server.py running?' % ( uri, e ) )

    kind, _, payload = reply.partition( ' ' )
    if kind == 'error':
        sys.exit( payload )
    if kind != 'sketch':
        sys.exit( 'Unexpected reply from %s: %s. Is that sketch_server.py and not ping.py?' % ( uri, reply[:80] ) )
    return json.loads( payload )


def main():
    parser = argparse.ArgumentParser( description = __doc__, formatter_class = argparse.RawDescriptionHelpFormatter )
    parser.add_argument( 'output', nargs = '?', help = 'OBJ file to write (default: sketch-<date>-<time>.obj)' )
    add_export_options( parser )
    parser.add_argument( '--port', type = int, default = 9001 )
    parser.add_argument( '--host', default = 'localhost' )
    args = parser.parse_args()
    export_options = parse_export_options( parser, args )

    sketch = asyncio.run( fetch_sketch( args.host, args.port ) )
    try:
        polylines = sketch_polylines( sketch, **export_options )
    except ExportError as e:
        sys.exit( str( e ) )

    output = args.output or time.strftime( 'sketch-%Y%m%d-%H%M%S.obj' )
    write_obj( output, polylines )

    s = sketch['session']
    print( 'Wrote %d strokes (%d vertices) to %s' % ( len( polylines ), sum( len( p ) for p in polylines ), output ) )
    print( 'Sketch from %s (%s): %d strokes, %d shape curves, %d scaffold lines'
           % ( s['remote'], 'connected' if s['connected'] else 'disconnected', s['raw'], s['shapes'], s['scaffold'] ) )


if __name__ == '__main__':
    main()
