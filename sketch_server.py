#!/usr/bin/env python3
'''
Runs the VR sketcher with saving and undo. One command does everything:

  - serves the VR page at http://localhost:8000/paint.html (web/paint.html, a
    copy of vrscaffolding's page with freehand drawing, Save and Undo;
    everything else, such as three.js, comes straight from vrscaffolding/threejs)
  - records freehand strokes, and for paint.html?mode=scaffold takes the place
    of vrscaffolding/threejs/ping.py, fitting strokes with vrscaffolding's own
    code, unchanged. It listens on ws://localhost:9001 because the Quest 3
    refuses `adb reverse` on ping.py's port 9000.
  - remembers the sketch so it can be saved from VR (left X button) or with
    `python3 export_sketch.py` at any moment

With --config (for a public website, see deploy/README.md), the save button
uploads to the folder named in that private config file instead, and only
after the passcode in it has been entered on the page.

Usage:
    python3 sketch_server.py [--port 9001] [--http-port 8000] [--export-dir exports] [--config config.ini]
'''

import argparse
import asyncio
import collections
import copy
import http.server
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import websockets

from obj_polylines import write_obj
from sketch_export import ExportError, add_export_options, parse_export_options, sketch_polylines
from upload_config import ConfigError, load_upload_config, passcode_matches

HERE = Path( __file__ ).resolve().parent
DEFAULT_VRSCAFFOLDING = HERE.parent / 'vrscaffolding' / 'threejs'

## Largest message accepted from a page; a long stroke is well under 1 MB.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

## Wrong passcodes: each one waits before replying, a connection is closed after
## a few, and after too many from anyone recently, all unlocking pauses.
WRONG_PASSCODE_DELAY = 2
WRONG_PASSCODES_PER_CONNECTION = 5
WRONG_PASSCODES_PER_WINDOW = 20
WRONG_PASSCODE_WINDOW = 15 * 60


class SketchSession:
    '''
    Everything drawn over one connection from paint.html.
    paint.html starts with an empty scene on every page load, and every load
    opens a new connection, so one connection is one sketch.
    '''
    def __init__( self, scaffold_sketch, remote ):
        self.state = scaffold_sketch.make_new_program_state()
        self.remote = remote
        self.connected = True
        self.last_stroke = None
        ## Every stroke as it came from the controller, fitted or not:
        ## { 'points': [[x,y,z], ...], 'undone': bool }
        self.raw_strokes = []
        ## Strokes as drawn on screen, in drawing order:
        ## { 'layer': 'scaffold', 'shapes' or 'raw' (freehand), 'points': ..., 'raw': the raw stroke, 'state_before': ... }
        ## `state_before` is what undo restores.
        self.strokes = []

    def summary( self ):
        return {
            'remote': self.remote,
            'connected': self.connected,
            'last_stroke': self.last_stroke,
            'scaffold': sum( 1 for s in self.strokes if s['layer'] == 'scaffold' ),
            'shapes': sum( 1 for s in self.strokes if s['layer'] == 'shapes' ),
            'raw': sum( 1 for r in self.raw_strokes if not r['undone'] ),
        }

    def sketch( self ):
        '''The sketch as sent to export_sketch.py and passed to `sketch_polylines()`.'''
        return {
            'session': self.summary(),
            'strokes': [ { 'layer': s['layer'], 'points': s['points'] } for s in self.strokes if s['layer'] != 'raw' ],
            'raw': [ r['points'] for r in self.raw_strokes if not r['undone'] ],
        }


def unique_export_path( export_dir ):
    export_dir.mkdir( parents = True, exist_ok = True )
    stem = time.strftime( 'sketch-%Y%m%d-%H%M%S' )
    path = export_dir / ( stem + '.obj' )
    n = 2
    while path.exists():
        path = export_dir / ( '%s-%d.obj' % ( stem, n ) )
        n += 1
    return path


def client_address( websocket ):
    '''The page's address, or the one the web server in front passes on (X-Real-IP from nginx, X-Forwarded-For from Apache).'''
    request = getattr( websocket, 'request', None )
    headers = request.headers if request is not None else getattr( websocket, 'request_headers', {} )
    ## The last X-Forwarded-For entry is the one added by the web server; earlier ones come from the client.
    forwarded = headers.get( 'X-Real-IP' ) or headers.get( 'X-Forwarded-For', '' ).split( ',' )[-1].strip()
    if forwarded:
        return forwarded
    return '%s:%s' % websocket.remote_address[:2] if websocket.remote_address else '?'


def make_handler( scaffold_sketch, sessions, export_dir, export_options, upload_config_path = None ):
    '''
    Without `upload_config_path`, the save button writes to `export_dir`.
    With it, saving needs the passcode from that file and writes to its upload_dir.
    '''
    wrong_passcode_times = collections.deque()

    def latest_session():
        drawn = [ s for s in sessions if s.last_stroke is not None ]
        return max( drawn, key = lambda s: s.last_stroke ) if drawn else None

    def log_counts( session ):
        s = session.summary()
        print( '[%s] %d strokes (%d shape curves, %d scaffold lines)' % ( session.remote, s['raw'], s['shapes'], s['scaffold'] ) )

    async def handle_stroke( websocket, session, command, parameters ):
        input_curve = json.loads( parameters )
        raw = { 'points': [ [ pt['x'], pt['y'], pt['z'] ] for pt in input_curve ], 'undone': False }
        session.raw_strokes.append( raw )
        session.last_stroke = time.time()
        state_before = copy.deepcopy( session.state )

        ## Same calls and replies as ping.py. A stroke the fitting code cannot
        ## handle (e.g. a quick tap) is logged and skipped instead of closing
        ## the connection and losing the sketch.
        try:
            if command == 'construction-stroke':
                new_line = scaffold_sketch.incorporate_new_raw_construction_line( session.state, input_curve ).tolist()
                session.strokes.append( { 'layer': 'scaffold', 'points': new_line, 'raw': raw, 'state_before': state_before } )
                await websocket.send( 'new-straight-line ' + json.dumps( new_line ) )
            else:
                new_shape = scaffold_sketch.incorporate_new_raw_shape_line( session.state, input_curve )
                if new_shape:
                    session.strokes.append( { 'layer': 'shapes', 'points': new_shape, 'raw': raw, 'state_before': state_before } )
                    await websocket.send( 'new-shape-line ' + json.dumps( new_shape ) )
                else:
                    print( 'Shape stroke did not pass near two scaffold points; nothing added.' )
        except Exception as e:
            ## The fitting code may have changed the state before failing.
            session.state = state_before
            where = traceback.extract_tb( e.__traceback__ )[-1]
            reason = type( e ).__name__ + ( ': %s' % e if str( e ) else '' )
            print( 'Skipped %s with %d points (%s at %s:%d)' % (
                command, len( input_curve ), reason, Path( where.filename ).name, where.lineno ), file = sys.stderr )

        log_counts( session )

    def handle_freehand_stroke( session, parameters ):
        ## Freehand strokes are kept exactly as drawn. paint.html has already drawn
        ## the stroke and applies the same test, so both keep the same undo list.
        points = [ [ pt['x'], pt['y'], pt['z'] ] for pt in json.loads( parameters ) ]
        if not any( p != points[0] for p in points ):
            return
        raw = { 'points': points, 'undone': False }
        session.raw_strokes.append( raw )
        session.last_stroke = time.time()
        session.strokes.append( { 'layer': 'raw', 'points': points, 'raw': raw, 'state_before': session.state } )
        print( '[%s] %d strokes' % ( session.remote, session.summary()['raw'] ) )

    async def handle_undo( websocket, session ):
        if session is None or not session.strokes:
            await websocket.send( 'nothing-to-undo {}' )
            return
        stroke = session.strokes.pop()
        session.state = stroke['state_before']
        stroke['raw']['undone'] = True
        await websocket.send( 'undone ' + json.dumps( { 'layer': stroke['layer'] } ) )
        print( 'Undid a %s stroke.' % { 'shapes': 'shape', 'scaffold': 'scaffold', 'raw': 'freehand' }[ stroke['layer'] ] )
        log_counts( session )

    async def handle_unlock( websocket, remote, attempt ):
        '''Returns True if `attempt` is the passcode.'''
        def reply( message ):
            return websocket.send( 'unlock-failed ' + json.dumps( { 'message': message } ) )

        if upload_config_path is None:
            await reply( 'This server saves without a passcode.' )
            return False
        try:
            config = load_upload_config( upload_config_path )
        except ConfigError as e:
            print( 'Unlock from %s failed: %s' % ( remote, e ), file = sys.stderr )
            await reply( 'Uploads are not set up on the server.' )
            return False

        now = time.time()
        while wrong_passcode_times and wrong_passcode_times[0] < now - WRONG_PASSCODE_WINDOW:
            wrong_passcode_times.popleft()
        if len( wrong_passcode_times ) >= WRONG_PASSCODES_PER_WINDOW:
            print( 'Unlock from %s refused: too many wrong passcodes recently.' % remote, file = sys.stderr )
            await reply( 'Too many wrong passcodes. Try again in a few minutes.' )
            return False

        if passcode_matches( config, attempt ):
            print( 'Uploads unlocked for', remote )
            await websocket.send( 'unlocked ' + json.dumps( { 'public_url': config['public_url'] } ) )
            return True

        wrong_passcode_times.append( now )
        print( 'Wrong passcode from', remote, file = sys.stderr )
        await asyncio.sleep( WRONG_PASSCODE_DELAY )
        await reply( 'Wrong passcode.' )
        return False

    async def handle_save( websocket, session, unlocked ):
        try:
            if upload_config_path is not None and not unlocked:
                raise ExportError( 'Uploads are locked. Enter the passcode on the page first.' )
            if session is None:
                raise ExportError( 'Nothing has been drawn yet.' )
            polylines = sketch_polylines( session.sketch(), **export_options )

            public_url = None
            directory = export_dir
            if upload_config_path is not None:
                config = load_upload_config( upload_config_path )
                directory, public_url = config['upload_dir'], config['public_url']
            path = unique_export_path( directory )
            write_obj( path, polylines )
        except ( ExportError, ConfigError, OSError ) as e:
            print( 'Save from VR failed:', e )
            await websocket.send( 'save-failed ' + json.dumps( { 'message': str( e ) } ) )
            return
        print( 'Saved %d strokes to %s' % ( len( polylines ), path ) )
        reply = { 'file': path.name, 'strokes': len( polylines ) }
        if public_url:
            reply['url'] = public_url + path.name
        await websocket.send( 'saved ' + json.dumps( reply ) )

    async def handle_get_sketch( websocket ):
        session = latest_session()
        if session is None:
            await websocket.send( 'error Nothing has been drawn since the server started.' )
            return
        await websocket.send( 'sketch ' + json.dumps( session.sketch() ) )

    async def handler( websocket, path = None ):
        remote = client_address( websocket )
        ## Created on the first stroke, so export requests don't count as sessions.
        session = None
        unlocked = False
        wrong_passcodes = 0
        try:
            async for message in websocket:
                parsed = message.split( ' ', 1 )
                command = parsed[0]
                parameters = None if len( parsed ) == 1 else parsed[1]

                if command in ( 'construction-stroke', 'shape-stroke', 'freehand-stroke' ):
                    if session is None:
                        session = SketchSession( scaffold_sketch, remote )
                        sessions.append( session )
                        print( 'New drawing session from', remote )
                    if command == 'freehand-stroke':
                        handle_freehand_stroke( session, parameters )
                    else:
                        await handle_stroke( websocket, session, command, parameters )
                elif command == 'undo':
                    await handle_undo( websocket, session )
                elif command == 'save-sketch':
                    await handle_save( websocket, session, unlocked )
                elif command == 'hello':
                    await websocket.send( 'hello ' + json.dumps( { 'passcode_required': upload_config_path is not None } ) )
                elif command == 'unlock':
                    unlocked = await handle_unlock( websocket, remote, parameters or '' )
                    if not unlocked:
                        wrong_passcodes += 1
                        if wrong_passcodes >= WRONG_PASSCODES_PER_CONNECTION:
                            break
                elif command == 'get-sketch':
                    ## On a public server, other people's sketches stay private.
                    if upload_config_path is not None and not unlocked:
                        await websocket.send( 'error Exporting needs the passcode on this server.' )
                    else:
                        await handle_get_sketch( websocket )
                else:
                    print( 'Unknown command:', command, file = sys.stderr )
        finally:
            if session is not None:
                session.connected = False
                print( 'Drawing session from %s disconnected; it can still be exported until another session draws.' % remote )
                ## Only the latest sketch can be exported once its page is gone, so forget the others.
                latest = latest_session()
                sessions[:] = [ s for s in sessions if s.connected or s is latest ]

    return handler


class LayeredRequestHandler( http.server.SimpleHTTPRequestHandler ):
    '''
    Serves each path from the first of `roots` that has it, so web/paint.html
    is used in place of vrscaffolding's while js/, main.css etc. come from
    vrscaffolding. Never cached, so the headset always gets the current page.
    '''
    roots = []

    def translate_path( self, path ):
        candidate = None
        for root in self.roots:
            self.directory = str( root )
            candidate = super().translate_path( path )
            if os.path.exists( candidate ):
                break
        return candidate

    def do_GET( self ):
        path, _, query = self.path.partition( '?' )
        if path in ( '/', '/index.html' ):
            self.send_response( 302 )
            ## Relative, so it also works under a path such as maepigeon.com/vr-sketch/.
            self.send_header( 'Location', 'paint.html' + ( '?' + query if query else '' ) )
            self.end_headers()
            return
        super().do_GET()

    def end_headers( self ):
        self.send_header( 'Cache-Control', 'no-store' )
        super().end_headers()

    def log_request( self, code = '-', size = '-' ):
        if str( code ).isdigit() and int( code ) >= 400:
            super().log_request( code, size )


def start_http_server( port, roots ):
    handler = type( 'Handler', ( LayeredRequestHandler, ), { 'roots': roots } )
    try:
        httpd = http.server.ThreadingHTTPServer( ( '127.0.0.1', port ), handler )
    except OSError as e:
        sys.exit( 'Could not serve the VR page on port %d (%s). If `python3 -m http.server` is still running, stop it: '
                  'this server serves the page now. Or pass --http-port.' % ( port, e ) )
    threading.Thread( target = httpd.serve_forever, daemon = True ).start()
    return httpd


def load_scaffold_sketch( vrscaffolding ):
    if not ( vrscaffolding / 'scaffold_sketch.py' ).exists():
        sys.exit( 'Could not find scaffold_sketch.py in %s. Pass --vrscaffolding /path/to/vrscaffolding/threejs' % vrscaffolding )

    ## Don't leave .pyc files behind in the vrscaffolding checkout.
    sys.dont_write_bytecode = True
    sys.path.insert( 0, str( vrscaffolding ) )
    import scaffold_sketch
    return scaffold_sketch


async def serve( host, port, handler ):
    try:
        server = await websockets.serve( handler, host, port, max_size = MAX_MESSAGE_BYTES )
    except OSError as e:
        sys.exit( 'Could not listen on port %d (%s). Is ping.py or another sketch_server.py still running?' % ( port, e ) )
    async with server:
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser( description = __doc__, formatter_class = argparse.RawDescriptionHelpFormatter )
    parser.add_argument( '--port', type = int, default = 9001, help = 'websocket port paint.html connects to (default: %(default)s)' )
    parser.add_argument( '--host', default = 'localhost' )
    parser.add_argument( '--http-port', type = int, default = 8000, help = 'port for the VR page; 0 to not serve it (default: %(default)s)' )
    parser.add_argument( '--vrscaffolding', default = os.environ.get( 'VRSCAFFOLDING', DEFAULT_VRSCAFFOLDING ),
        help = 'path to vrscaffolding/threejs (default: %(default)s)' )
    parser.add_argument( '--export-dir', default = HERE / 'exports',
        help = 'where the save button in VR writes OBJ files (default: %(default)s)' )
    parser.add_argument( '--config', default = os.environ.get( 'VR_SKETCH_CONFIG' ),
        help = 'private config file with the upload passcode and folder (see deploy/config.example.ini); '
               'when given, saving from VR needs the passcode and writes to that folder instead of --export-dir' )
    group = parser.add_argument_group( 'what the save button in VR writes (same options as export_sketch.py)' )
    add_export_options( group )
    args = parser.parse_args()
    ## Show progress right away even when output goes to a file or pipe.
    sys.stdout.reconfigure( line_buffering = True )

    export_options = parse_export_options( parser, args )
    vrscaffolding = Path( args.vrscaffolding ).resolve()
    scaffold_sketch = load_scaffold_sketch( vrscaffolding )
    config_path = Path( args.config ).resolve() if args.config else None
    if config_path is not None:
        ## Catch mistakes now rather than on the first save from VR.
        try:
            upload_config = load_upload_config( config_path )
        except ConfigError as e:
            sys.exit( e )
    handler = make_handler( scaffold_sketch, [], Path( args.export_dir ).resolve(), export_options, config_path )

    if args.http_port:
        start_http_server( args.http_port, [ HERE / 'web', vrscaffolding ] )
        print( 'VR page:          http://localhost:%d/paint.html' % args.http_port )
    print( 'Sketch websocket: ws://%s:%d' % ( args.host, args.port ) )
    if config_path is None:
        print( 'Save from VR with the left X button (files go to %s),' % Path( args.export_dir ).resolve() )
        print( 'or from here with: python3 export_sketch.py my_sketch.obj' )
    else:
        print( 'Upload from VR with the left X button after entering the passcode from %s' % config_path )
        print( '(files go to %s%s; the config is re-read on every save).' % (
            upload_config['upload_dir'], ', at ' + upload_config['public_url'] if upload_config['public_url'] else '' ) )
    try:
        asyncio.run( serve( args.host, args.port, handler ) )
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
