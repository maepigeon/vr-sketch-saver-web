'''
The private config file that lets the save button in VR upload sketches to a
public folder on a website (see deploy/config.example.ini):

    [upload]
    passcode = a long passcode only you know
    upload_dir = /var/www/maepigeon.com/html/sketches
    public_url = https://maepigeon.com/sketches/

sketch_server.py --config <file> reads it again on every unlock and save, so
edits take effect without restarting the server.
'''

import configparser
import hmac
from pathlib import Path

PLACEHOLDER_PASSCODE = 'change-me'
MIN_PASSCODE_LENGTH = 8


class ConfigError( Exception ):
    pass


def load_upload_config( path ):
    '''
    Returns { 'passcode': str, 'upload_dir': Path, 'public_url': str or None }.
    Raises ConfigError if the file is missing or incomplete.
    '''
    parser = configparser.ConfigParser( interpolation = None )
    try:
        with open( path ) as f:
            parser.read_file( f )
    except OSError as e:
        raise ConfigError( 'Could not read %s (%s).' % ( path, e ) )
    except configparser.Error as e:
        raise ConfigError( 'Could not parse %s (%s).' % ( path, e ) )

    if not parser.has_section( 'upload' ):
        raise ConfigError( '%s has no [upload] section.' % path )
    section = parser['upload']

    passcode = section.get( 'passcode', '' ).strip()
    if passcode in ( '', PLACEHOLDER_PASSCODE ):
        raise ConfigError( 'Set a passcode in %s.' % path )
    if len( passcode ) < MIN_PASSCODE_LENGTH:
        raise ConfigError( 'The passcode in %s must be at least %d characters.' % ( path, MIN_PASSCODE_LENGTH ) )

    upload_dir = section.get( 'upload_dir', '' ).strip()
    if not upload_dir:
        raise ConfigError( 'Set upload_dir in %s.' % path )
    upload_dir = Path( upload_dir )
    if not upload_dir.is_absolute():
        raise ConfigError( 'upload_dir in %s must be an absolute path.' % path )

    public_url = section.get( 'public_url', '' ).strip() or None
    if public_url and not public_url.endswith( '/' ):
        public_url += '/'

    return { 'passcode': passcode, 'upload_dir': upload_dir, 'public_url': public_url }


def passcode_matches( config, attempt ):
    ## Constant-time, so response timing doesn't hint at the passcode.
    return hmac.compare_digest( config['passcode'].encode(), attempt.strip().encode() )
