#!/usr/bin/env bash
# Installs or updates the VR sketcher on this server (Ubuntu/Debian with Apache or nginx).
# From a checkout of this repo on the server:
#
#     git pull && sudo deploy/deploy.sh
#
# It asks for the website's domain, the URL path of the VR page, the private
# config file, the upload passcode, and the folder (and URL path) uploads go to.
# Enter keeps the answer in [brackets], which is the previous one, so updating is
# just pressing Enter. `sudo deploy/deploy.sh --yes` reuses every answer without asking.
#
# Answers other than the passcode are kept in /etc/vr-sketch-saver/deploy.env;
# the passcode lives only in the config file.

set -euo pipefail

SETTINGS=/etc/vr-sketch-saver/deploy.env
PREFIX=/opt/vr-sketch-saver
SERVICE=vr-sketch-saver
SERVICE_USER=vrsketch
NGINX_SNIPPET=/etc/nginx/snippets/vr-sketch-saver.conf
APACHE_CONF=/etc/apache2/conf-available/vr-sketch-saver.conf
VRSCAFFOLDING_REPO=https://github.com/yig/vrscaffolding
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

ASSUME_YES=0
[ "${1:-}" = --yes ] && ASSUME_YES=1

if [ "$(id -u)" != 0 ]; then
    echo "Run with sudo: sudo $0 $*" >&2
    exit 1
fi

## Defaults, replaced by the previous answers.
DOMAIN=maepigeon.com
APP_PATH=/vr-sketch
CONFIG=/etc/vr-sketch-saver/config.ini
UPLOAD_DIR=/var/www/maepigeon.com/sketches
UPLOAD_URL_PATH=/sketches
HTTP_PORT=8801
WS_PORT=9801
[ -f "$SETTINGS" ] && . "$SETTINGS"

ask() {  # ask VARIABLE "question"
    local answer
    [ "$ASSUME_YES" = 1 ] && return
    read -r -p "$2 [${!1}]: " answer
    [ -n "$answer" ] && printf -v "$1" '%s' "$answer"
    return 0
}

url_path() {  # "vr-sketch/" -> "/vr-sketch"
    local p="/${1#/}"
    p="${p%/}"
    if [ -z "$p" ] || [[ "$p" =~ [[:space:]\;\{\}\$\'\"] ]]; then
        echo "Not a usable URL path: '$1' (it can't be / or contain spaces or ;{}\$'\")." >&2
        exit 1
    fi
    printf '%s' "$p"
}

abs_path() {  # an absolute path without a trailing slash
    local p="${1%/}"
    if [[ "$p" != /* ]] || [[ "$p" =~ [[:space:]\;\{\}\$\'\"] ]]; then
        echo "Not a usable absolute path: '$1' (no spaces or ;{}\$'\")." >&2
        exit 1
    fi
    printf '%s' "$p"
}

echo "== VR sketcher: where things go (Enter keeps the answer in brackets)"
ask DOMAIN          "Website domain"
ask APP_PATH        "URL path of the VR page (https://$DOMAIN<path>)"
ask CONFIG          "Private config file (holds the passcode; keep it outside the website)"
ask UPLOAD_DIR      "Folder on this server that sketches upload to"
ask UPLOAD_URL_PATH "URL path where that folder is public (https://$DOMAIN<path>)"
APP_PATH=$(url_path "$APP_PATH")
UPLOAD_URL_PATH=$(url_path "$UPLOAD_URL_PATH")
CONFIG=$(abs_path "$CONFIG")
UPLOAD_DIR=$(abs_path "$UPLOAD_DIR")
PUBLIC_URL="https://$DOMAIN$UPLOAD_URL_PATH/"
if [ "$APP_PATH" = "$UPLOAD_URL_PATH" ]; then
    echo "The VR page and the uploads need different URL paths." >&2
    exit 1
fi

## The passcode: kept from the current config unless a new one is typed.
PASSCODE=
if [ -f "$CONFIG" ]; then
    PASSCODE=$(cd "$REPO" && python3 -c '
import sys
from upload_config import load_upload_config, ConfigError
try:
    print( load_upload_config( sys.argv[1] )["passcode"] )
except ConfigError:
    pass
' "$CONFIG")
fi
if [ "$ASSUME_YES" = 1 ] && [ -z "$PASSCODE" ]; then
    echo "No passcode in $CONFIG yet: run without --yes to set one." >&2
    exit 1
fi
if [ "$ASSUME_YES" = 0 ]; then
    while true; do
        if [ -n "$PASSCODE" ]; then
            read -r -s -p "Upload passcode (Enter keeps the current one): " typed; echo
            [ -z "$typed" ] && break
        else
            read -r -s -p "Upload passcode (at least 8 characters): " typed; echo
        fi
        typed="${typed#"${typed%%[![:space:]]*}"}"; typed="${typed%"${typed##*[![:space:]]}"}"
        if [ ${#typed} -lt 8 ] || [ "$typed" = change-me ]; then
            echo "  Use at least 8 characters." >&2
            continue
        fi
        read -r -s -p "Type it again: " again; echo
        again="${again#"${again%%[![:space:]]*}"}"; again="${again%"${again##*[![:space:]]}"}"
        if [ "$typed" != "$again" ]; then
            echo "  They don't match." >&2
            continue
        fi
        PASSCODE=$typed
        break
    done
fi

echo "== Installing packages"
missing=()
python3 -c 'import ensurepip' 2>/dev/null || missing+=( python3-venv )
command -v git >/dev/null || missing+=( git )
command -v rsync >/dev/null || missing+=( rsync )
if [ ${#missing[@]} -gt 0 ]; then
    apt-get update -q
    apt-get install -y -q "${missing[@]}"
fi

id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"

echo "== Copying the app to $PREFIX"
mkdir -p "$PREFIX"
rsync -a --delete --exclude .git --exclude __pycache__ --exclude exports --exclude .DS_Store --exclude sketch.obj \
    "$REPO/" "$PREFIX/app/"
if [ -d "$PREFIX/vrscaffolding/.git" ]; then
    git -C "$PREFIX/vrscaffolding" pull -q --ff-only
else
    git clone -q "$VRSCAFFOLDING_REPO" "$PREFIX/vrscaffolding"
fi
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q -r "$PREFIX/app/requirements.txt"

echo "== Writing $CONFIG"
mkdir -p "$(dirname "$CONFIG")"
## Passed through the environment so the passcode never appears in a command line.
PASSCODE="$PASSCODE" UPLOAD_DIR="$UPLOAD_DIR" PUBLIC_URL="$PUBLIC_URL" python3 - "$CONFIG" <<'EOF'
import configparser, os, sys
config = configparser.ConfigParser( interpolation = None )
config['upload'] = { 'passcode': os.environ['PASSCODE'], 'upload_dir': os.environ['UPLOAD_DIR'], 'public_url': os.environ['PUBLIC_URL'] }
fd = os.open( sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640 )
with os.fdopen( fd, 'w' ) as f:
    f.write( '; Written by deploy/deploy.sh. sketch_server.py re-reads it on every unlock and save.\n' )
    config.write( f )
EOF
chown "root:$SERVICE_USER" "$CONFIG"
chmod 640 "$CONFIG"
PASSCODE=

mkdir -p "$UPLOAD_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$UPLOAD_DIR"
chmod 755 "$UPLOAD_DIR"

mkdir -p "$(dirname "$SETTINGS")"
{
    echo "# Answers from deploy/deploy.sh, used as its defaults next time."
    for v in DOMAIN APP_PATH CONFIG UPLOAD_DIR UPLOAD_URL_PATH HTTP_PORT WS_PORT; do
        printf '%s=%q\n' "$v" "${!v}"
    done
} > "$SETTINGS"

echo "== Starting the $SERVICE service"
## The service can't write under /home with ProtectHome on.
PROTECT_HOME=true
[[ "$UPLOAD_DIR" == /home/* || "$UPLOAD_DIR" == /root/* ]] && PROTECT_HOME=false
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
# Written by deploy/deploy.sh; re-running it overwrites this file.
[Unit]
Description=VR sketcher (https://$DOMAIN$APP_PATH/)
After=network.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PREFIX/app
ExecStart=$PREFIX/venv/bin/python sketch_server.py --host 127.0.0.1 --port $WS_PORT --http-port $HTTP_PORT --vrscaffolding $PREFIX/vrscaffolding/threejs --config $CONFIG --export-dir $PREFIX/exports
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=$PROTECT_HOME

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 2
if ! systemctl is-active -q "$SERVICE"; then
    journalctl -u "$SERVICE" -n 20 --no-pager
    echo "The service didn't start; see the log above." >&2
    exit 1
fi

## Whichever web server serves the site sends the VR page, its websocket and the
## uploads folder on to the right place.
if systemctl is-active -q apache2 2>/dev/null; then
    echo "== Apache"
    a2enmod -q proxy proxy_http proxy_wstunnel headers alias >/dev/null
    cat > "$APACHE_CONF" <<EOF
# VR sketcher. Written by deploy/deploy.sh; re-running it overwrites this file.
# Enabled with a2enconf, so it applies to every site on this server.

RedirectMatch 301 ^$APP_PATH\$ $APP_PATH/
RedirectMatch 301 ^$UPLOAD_URL_PATH\$ $UPLOAD_URL_PATH/

# The sketch websocket, then the VR page and its scripts.
ProxyPass $APP_PATH/ws ws://127.0.0.1:$WS_PORT/
ProxyPass $APP_PATH/ http://127.0.0.1:$HTTP_PORT/
ProxyPassReverse $APP_PATH/ http://127.0.0.1:$HTTP_PORT/

# Uploaded sketches: public, listed, and readable as text from other sites.
Alias $UPLOAD_URL_PATH/ $UPLOAD_DIR/
<Directory $UPLOAD_DIR>
    Options +Indexes -ExecCGI
    AllowOverride None
    Require all granted
    <FilesMatch "\.obj\$">
        ForceType text/plain
    </FilesMatch>
    Header set Access-Control-Allow-Origin "*"
</Directory>
EOF
    a2enconf -q "$(basename "$APACHE_CONF" .conf)" >/dev/null
    if ! apache2ctl configtest; then
        a2disconf -q "$(basename "$APACHE_CONF" .conf)" >/dev/null
        echo "Apache rejected $APACHE_CONF (see above), so it was turned off again and the site is unchanged." >&2
        exit 1
    fi
    systemctl reload apache2
    echo
    echo "Done. In the headset's browser, open: https://$DOMAIN$APP_PATH/"
    echo "Uploads appear at: $PUBLIC_URL"
    exit 0
fi

echo "== nginx"
if ! command -v nginx >/dev/null; then
    echo "Neither Apache nor nginx is running. Put the server behind HTTPS (the headset needs it for VR) and send"
    echo "  https://$DOMAIN$APP_PATH/    to http://127.0.0.1:$HTTP_PORT/"
    echo "  https://$DOMAIN$APP_PATH/ws  to ws://127.0.0.1:$WS_PORT (websocket)"
    echo "  https://$DOMAIN$UPLOAD_URL_PATH/  to the folder $UPLOAD_DIR"
    exit 0
fi
mkdir -p "$(dirname "$NGINX_SNIPPET")"
cat > "$NGINX_SNIPPET" <<EOF
# VR sketcher. Written by deploy/deploy.sh; re-running it overwrites this file.
# Included from the HTTPS server block for $DOMAIN.

location = $APP_PATH { return 301 $APP_PATH/; }
location = $UPLOAD_URL_PATH { return 301 $UPLOAD_URL_PATH/; }

# The sketch websocket.
location = $APP_PATH/ws {
    proxy_pass http://127.0.0.1:$WS_PORT;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_read_timeout 1d;
    proxy_send_timeout 1d;
}

# The VR page and its scripts.
location $APP_PATH/ {
    proxy_pass http://127.0.0.1:$HTTP_PORT/;
    proxy_set_header Host \$host;
}

# Uploaded sketches: public, listed, and readable as text from other sites.
location $UPLOAD_URL_PATH/ {
    alias $UPLOAD_DIR/;
    autoindex on;
    types { text/plain obj; }
    add_header Access-Control-Allow-Origin *;
}
EOF

if grep -rqs "snippets/$(basename "$NGINX_SNIPPET")" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/; then
    nginx -t -q
    systemctl reload nginx
    echo
    echo "Done. In the headset's browser, open: https://$DOMAIN$APP_PATH/"
    echo "Uploads appear at: $PUBLIC_URL"
else
    site=$(grep -rlE "server_name[^;]*[[:space:]]$DOMAIN[[:space:];]" /etc/nginx/sites-enabled/ 2>/dev/null | head -1 || true)
    echo
    echo "One more step (only the first time): add this line"
    echo
    echo "    include snippets/$(basename "$NGINX_SNIPPET");"
    echo
    echo "inside the server { ... } block for $DOMAIN that has 'listen 443 ssl'${site:+, in $site}."
    echo "Then run:  sudo nginx -t && sudo systemctl reload nginx"
    echo "(No HTTPS yet? sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d $DOMAIN)"
    echo
    echo "After that, open https://$DOMAIN$APP_PATH/ in the headset's browser."
fi
