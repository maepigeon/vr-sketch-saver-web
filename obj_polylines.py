'''
Reading and writing sketches as OBJ polylines, in the same layout as
sketch.obj:

    v x y z          one line per vertex, all vertices first
    ...
    l 1 2 3 ... n    one line per stroke, 1-based vertex indices
    ...

No comments, groups or other OBJ statements are written, so a reader only
needs to understand `v` and `l` lines.
'''

import numpy as np


def resample_polyline( pts, n ):
    '''
    Given:
        pts: a sequence of 3D points
        n: the number of points wanted (at least 2)
    Returns:
        an n-by-3 array of points evenly spaced by arc length along `pts`,
        keeping both endpoints.
    '''
    pts = np.asarray( pts, dtype = float )
    seg_lengths = np.linalg.norm( np.diff( pts, axis = 0 ), axis = 1 )
    arc = np.concatenate( [ [0.], np.cumsum( seg_lengths ) ] )

    if arc[-1] == 0.:
        return np.repeat( pts[:1], n, axis = 0 )

    targets = np.linspace( 0., arc[-1], n )
    return np.stack( [ np.interp( targets, arc, pts[:, k] ) for k in range(3) ], axis = 1 )


def clean_polylines( polylines, points_per_stroke = None ):
    '''
    Drops strokes with fewer than two points and, if `points_per_stroke`
    is given, resamples every stroke to that many points.
    Returns a list of n-by-3 arrays.
    '''
    result = []
    for pts in polylines:
        pts = np.asarray( pts, dtype = float ).reshape( -1, 3 )
        if len( pts ) < 2:
            continue
        if points_per_stroke:
            pts = resample_polyline( pts, points_per_stroke )
        result.append( pts )
    return result


def polylines_to_obj( polylines ):
    '''
    Given a list of n-by-3 point arrays, returns the OBJ text.
    '''
    vertex_lines = []
    stroke_lines = []
    next_index = 1
    for pts in polylines:
        indices = []
        for x, y, z in pts:
            vertex_lines.append( 'v %r %r %r' % ( float(x), float(y), float(z) ) )
            indices.append( str( next_index ) )
            next_index += 1
        stroke_lines.append( 'l ' + ' '.join( indices ) )

    return '\n'.join( vertex_lines + stroke_lines ) + '\n'


def write_obj( path, polylines ):
    with open( path, 'w' ) as f:
        f.write( polylines_to_obj( polylines ) )


def read_obj( path ):
    '''
    Reads `v` and `l` lines from an OBJ file.
    Returns a list of n-by-3 arrays, one per `l` line.
    '''
    vertices = []
    strokes = []
    with open( path ) as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == 'v':
                vertices.append( [ float(c) for c in parts[1:4] ] )
            elif parts[0] == 'l':
                ## OBJ allows "v/vt" pairs on l lines; only the vertex index matters here.
                strokes.append( [ int( p.split('/')[0] ) for p in parts[1:] ] )

    vertices = np.array( vertices )
    return [ vertices[ np.array( s ) - 1 ] for s in strokes ]
