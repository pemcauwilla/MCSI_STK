#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
SERVER_DIR="$PROJECT_ROOT/SetUp_Collab"
SERVER_SCRIPT="$SERVER_DIR/STK_input_server.py"

# chemin supertuxkart
STK_DIR="/home/paulo/SIIA/MCSI/SuperTuxKart-1.2-linux"
if [ ! -d "$STK_DIR" ]; then
    STK_DIR="$(cd "$PROJECT_ROOT/../../SuperTuxKart-1.2-linux" 2>/dev/null && pwd)"
fi

STK_EXEC="$STK_DIR/run_game.sh"

# selection environnement python
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON_CMD="$PROJECT_ROOT/.venv/bin/python"
elif [ -x "$PROJECT_ROOT/../MCSI_SuperTuxKart/.venv/bin/python" ]; then
    PYTHON_CMD="$PROJECT_ROOT/../MCSI_SuperTuxKart/.venv/bin/python"
else
    PYTHON_CMD="python3"
fi

if [ ! -f "$SERVER_SCRIPT" ]; then
    echo "Erreur: serveur introuvable a $SERVER_SCRIPT" >&2
    exit 1
fi

if [ ! -x "$STK_EXEC" ]; then
    echo "Erreur: SuperTuxKart introuvable a $STK_EXEC" >&2
    exit 1
fi

echo "Demarrage de STK et du serveur d'entrees..."
echo "Serveur : $SERVER_SCRIPT"
echo "Jeu     : $STK_EXEC"
echo "Python  : $PYTHON_CMD"

SERVER_ARGS=()
STK_ARGS=()
for arg in "$@"; do
    case "$arg" in
        -d|--debug)
            SERVER_ARGS+=("-d")
            ;;
        *)
            STK_ARGS+=("$arg")
            ;;
    esac
done

if [ "$EUID" -ne 0 ]; then
    sudo -v
fi

# lancement serveur en arriere-plan
sudo "$PYTHON_CMD" "$SERVER_SCRIPT" "${SERVER_ARGS[@]}" &
SERVER_PID=$!

cleanup() {
    echo ""
    echo "Fermeture..."
    python3 -c "import socket; sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.sendto(b'STOPSERVEUR', ('localhost', 6006))" 2>/dev/null || true

    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        sudo kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

sleep 0.5

# lancement jeu au premier plan
"$STK_EXEC" "${STK_ARGS[@]}"
