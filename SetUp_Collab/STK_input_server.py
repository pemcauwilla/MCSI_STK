#!/usr/bin/env python3
import sys
import time
import socket
import threading

try:
    import keyboard
except ImportError as err:
    print(f"\n\033[91m[ERREUR] Module keyboard introuvable : {err}\033[0m")
    print("\033[93mSur Linux, lancer avec sudo pour acceder aux inputs clavier :\033[0m")
    print("  sudo python STK_input_server.py\n")
    sys.exit(1)

GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'

stop = False
DEBUG = False

# socket udp ecoute
address = ('localhost', 6006)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(address)


def _hold_and_release(key_name, hold_seconds):
    try:
        keyboard.press(key_name)
        time.sleep(hold_seconds)
        keyboard.release(key_name)
    except Exception as err:
        print(f"Erreur robust_tap({key_name}): {err}")


def robust_tap(key_name, hold_seconds=0.06):
    # impulsion courte non bloquante dans un thread
    t = threading.Thread(target=_hold_and_release, args=(key_name, hold_seconds), daemon=True)
    t.start()


# mapping commandes udp -> touches clavier
bindings = [
    ['UP', 'up', keyboard.press_and_release],
    ['DOWN', 'down', keyboard.press_and_release],
    ['LEFT', 'left', keyboard.press_and_release],
    ['RIGHT', 'right', keyboard.press_and_release],
    ['SELECT', 'enter', robust_tap],
    ['CANCEL', 'backspace', robust_tap],
    ['BACK', 'backspace', robust_tap],
    ['FIRE', 'space', robust_tap],
    ['P_FIRE', 'space', keyboard.press],
    ['R_FIRE', 'space', keyboard.release],
    ['NITRO', 'n', robust_tap],
    ['P_NITRO', 'n', keyboard.press],
    ['R_NITRO', 'n', keyboard.release],
    ['P_SKIDDING', 'v', keyboard.press],
    ['R_SKIDDING', 'v', keyboard.release],
    ['P_LOOKBACK', 'b', keyboard.press],
    ['R_LOOKBACK', 'b', keyboard.release],
    ['RESCUE', 'backspace', robust_tap],
    ['P_RESCUE', 'backspace', keyboard.press],
    ['R_RESCUE', 'backspace', keyboard.release],
    ['PAUSE', 'escape', robust_tap],
    ['P_UP', 'up', keyboard.press],
    ['R_UP', 'up', keyboard.release],
    ['P_DOWN', 'down', keyboard.press],
    ['R_DOWN', 'down', keyboard.release],
    ['P_LEFT', 'left', keyboard.press],
    ['R_LEFT', 'left', keyboard.release],
    ['P_RIGHT', 'right', keyboard.press],
    ['R_RIGHT', 'right', keyboard.release],
    ['P_ACCELERATE', 'up', keyboard.press],
    ['R_ACCELERATE', 'up', keyboard.release],
    ['P_BRAKE', 'down', keyboard.press],
    ['R_BRAKE', 'down', keyboard.release],
]

commands = [b[0] for b in bindings]

if len(sys.argv) > 1:
    for i in range(1, len(sys.argv)):
        if sys.argv[i] == '-d':
            DEBUG = True

print()
print('STK input server started ', end='')
if DEBUG:
    print(GREEN + '(Debug mode)' + WHITE)
else:
    print()

while not stop:
    data, addr = sock.recvfrom(1024)
    if isinstance(data, bytes):
        data = data.decode('utf-8').replace(',', '')

    if data == 'STOPSERVEUR':
        stop = True
    else:
        if data in commands:
            if DEBUG:
                print(YELLOW + '\t' + data + WHITE)
            b = bindings[commands.index(data)]
            b[2](b[1])
        else:
            if DEBUG:
                print(RED + '\t' + data + WHITE + ' (Unknown)')

print('STK input server stopped')
