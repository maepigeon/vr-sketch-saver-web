#!/usr/bin/env python3
'''
End-to-end check: starts sketch_server.py, sends strokes, undo and save the
way web/paint.html does (freehand and ?mode=scaffold), runs export_sketch.py
and reads the results back.

    python3 test_export.py
'''

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import websockets

from obj_polylines import polylines_to_obj, read_obj

HERE = Path( __file__ ).resolve().parent
TMP = Path( tempfile.mkdtemp( prefix = 'vr-sketch-saver-test-' ) )

## A 40cm square at table height.
CORNERS = np.array( [ [ -.2, 1.2, -.2 ], [ .2, 1.2, -.2 ], [ .2, 1.2, .2 ], [ -.2, 1.2, .2 ] ] )


def free_port():
    with socket.socket() as s:
        s.bind( ( 'localhost', 0 ) )
        return s.getsockname()[1]


def wait_for_port( port ):
    for _ in range( 100 ):
        try:
            socket.create_connection( ( 'localhost', port ) ).close()
            return
        except OSError:
            time.sleep( 0.1 )
    raise RuntimeError( 'server did not start on port %d' % port )


def as_vector3_json( pts ):
    return json.dumps( [ { 'x': x, 'y': y, 'z': z } for x, y, z in pts ] )


def stroke( a, b, n = 40, wobble = 0.003 ):
    '''A slightly noisy hand-drawn stroke from a to b.'''
    rng = np.random.default_rng( 0 )
    return np.linspace( a, b, n ) + rng.normal( scale = wobble, size = ( n, 3 ) )


def square_loop():
    '''A closed stroke around the square, passing through its corners.'''
    return np.concatenate( [ np.linspace( CORNERS[i], CORNERS[(i+1) % 4], 30, endpoint = False ) for i in range( 4 ) ] + [ CORNERS[:1] ] )


async def draw_square( ws ):
    for i in range( 4 ):
        await ws.send( 'construction-stroke ' + as_vector3_json( stroke( CORNERS[i], CORNERS[(i+1) % 4] ) ) )
        reply = await ws.recv()
        assert reply.startswith( 'new-straight-line ' ), reply


async def command( ws, message ):
    await ws.send( message )
    kind, _, payload = ( await ws.recv() ).partition( ' ' )
    return kind, json.loads( payload )


async def draw( port ):
    async with websockets.connect( 'ws://localhost:%d' % port ) as ws:
        ## Saving before drawing anything explains why nothing was saved.
        assert ( await command( ws, 'save-sketch' ) )[0] == 'save-failed'
        assert ( await command( ws, 'undo' ) )[0] == 'nothing-to-undo'

        await draw_square( ws )

        ## A trigger tap: ping.py would crash on this and drop the sketch.
        await ws.send( 'construction-stroke ' + as_vector3_json( stroke( CORNERS[0], CORNERS[0], n = 1 ) ) )

        await ws.send( 'shape-stroke ' + as_vector3_json( square_loop() ) )
        reply = await ws.recv()
        assert reply.startswith( 'new-shape-line ' ), reply

        ## The save button saves every stroke as drawn; the tap is dropped.
        kind, payload = await command( ws, 'save-sketch' )
        assert kind == 'saved' and payload['strokes'] == 5, ( kind, payload )


async def undo_everything( port ):
    '''A second session: undoing the scaffold must also undo its snap and key points.'''
    async with websockets.connect( 'ws://localhost:%d' % port ) as ws:
        await draw_square( ws )
        for _ in range( 4 ):
            assert await command( ws, 'undo' ) == ( 'undone', { 'layer': 'scaffold' } )
        assert ( await command( ws, 'undo' ) )[0] == 'nothing-to-undo'

        ## With the key points gone, a shape stroke has nothing to snap to, so
        ## no shape curve is added and there is still nothing to undo.
        await ws.send( 'shape-stroke ' + as_vector3_json( square_loop() ) )
        assert ( await command( ws, 'undo' ) )[0] == 'nothing-to-undo'

        ## Drawing the square again gives the same lines as the first time.
        await draw_square( ws )
        await ws.send( 'shape-stroke ' + as_vector3_json( square_loop() ) )
        assert ( await ws.recv() ).startswith( 'new-shape-line ' )
        assert ( await command( ws, 'undo' ) ) == ( 'undone', { 'layer': 'shapes' } )


async def draw_freehand( port ):
    '''A third session, drawn the way paint.html does without ?mode=scaffold.'''
    curves = [ stroke( CORNERS[0], CORNERS[2], n = 50 ), stroke( CORNERS[1], CORNERS[3], n = 60 ), stroke( CORNERS[2], CORNERS[0], n = 70 ) ]
    async with websockets.connect( 'ws://localhost:%d' % port ) as ws:
        await ws.send( 'freehand-stroke ' + as_vector3_json( curves[0] ) )
        ## A tap and a stroke held still: the page doesn't draw them, so they aren't recorded.
        await ws.send( 'freehand-stroke ' + as_vector3_json( curves[0][:1] ) )
        await ws.send( 'freehand-stroke ' + as_vector3_json( [ curves[0][0] ] * 5 ) )
        await ws.send( 'freehand-stroke ' + as_vector3_json( curves[1] ) )
        assert await command( ws, 'undo' ) == ( 'undone', { 'layer': 'raw' } )
        await ws.send( 'freehand-stroke ' + as_vector3_json( curves[2] ) )
        kind, payload = await command( ws, 'save-sketch' )
        assert kind == 'saved' and payload['strokes'] == 2, ( kind, payload )
    return [ curves[0], curves[2] ]


def export( port, *args ):
    out = TMP / ( 'export-%d.obj' % len( list( TMP.glob( 'export-*.obj' ) ) ) )
    subprocess.run( [ sys.executable, str( HERE / 'export_sketch.py' ), str( out ), '--port', str( port ), *args ],
                    check = True, stdout = subprocess.DEVNULL )
    return read_obj( out )


def check_fit( polylines ):
    pts = np.concatenate( polylines )
    lo, hi = pts.min( 0 ), pts.max( 0 )
    assert np.allclose( lo + hi, 0 ), 'not centered'
    assert np.isclose( ( hi - lo ).max(), 1 ), 'largest side is not 1'


def main():
    ## The writer reproduces the sample file byte for byte.
    sample = HERE / 'sketch.obj'
    assert polylines_to_obj( read_obj( sample ) ) == sample.read_text(), 'writer does not match sketch.obj layout'

    port, http_port = free_port(), free_port()
    export_dir = TMP / 'exports'
    server = subprocess.Popen( [ sys.executable, str( HERE / 'sketch_server.py' ), '--port', str( port ),
                                 '--http-port', str( http_port ), '--export-dir', str( export_dir ) ],
                               stdout = subprocess.DEVNULL, stderr = subprocess.DEVNULL )
    try:
        wait_for_port( port )
        wait_for_port( http_port )

        ## The page comes from web/, everything else from vrscaffolding/threejs.
        page = urllib.request.urlopen( 'http://localhost:%d/' % http_port ).read().decode()
        assert 'save-sketch' in page
        assert urllib.request.urlopen( 'http://localhost:%d/js/three.module.js' % http_port ).status == 200

        asyncio.run( draw( port ) )

        ## The save button wrote one file with the default options.
        saved = list( export_dir.glob( '*.obj' ) )
        assert len( saved ) == 1, saved
        saved = read_obj( saved[0] )
        assert len( saved ) == 5 and all( len( p ) == 100 for p in saved )
        check_fit( saved )

        ## Defaults: every stroke as drawn, 100 points, fit to a unit box.
        default = export( port )
        assert len( default ) == 5 and all( np.allclose( a, b ) for a, b in zip( default, saved ) )

        both = export( port, '--layers', 'scaffold,shapes', '--coords', 'world' )
        assert len( both ) == 5 and all( len( p ) == 100 for p in both )
        assert np.allclose( both[0][:, 1], 1.2, atol = 0.01 ), 'scaffold line should be horizontal at table height'

        native = export( port, '--layers', 'shapes', '--points', '0', '--coords', 'world' )
        assert len( native ) == 1 and len( native[0] ) == 121, [ len( p ) for p in native ]

        centered = export( port, '--coords', 'center' )
        pts = np.concatenate( centered )
        assert np.allclose( pts.min( 0 ) + pts.max( 0 ), 0 )

        asyncio.run( undo_everything( port ) )
        ## The latest session now has only the redrawn square.
        assert len( export( port, '--layers', 'scaffold,shapes' ) ) == 4

        drawn = asyncio.run( draw_freehand( port ) )
        exact = export( port, '--points', '0', '--coords', 'world' )
        assert len( exact ) == 2 and all( np.allclose( a, b ) for a, b in zip( exact, drawn ) )
        check_fit( export( port ) )
        assert len( list( export_dir.glob( '*.obj' ) ) ) == 2
    finally:
        server.terminate()
        server.wait()
        shutil.rmtree( TMP )

    print( 'OK' )


if __name__ == '__main__':
    main()
