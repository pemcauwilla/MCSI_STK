import time
from oscpy.server import OSCThreadServer
from classes.SensorState import PadState, Vector3State

GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'

STK_SERVER_ADDRESS = ('localhost', 6006)
OSC_LISTEN_IP = '0.0.0.0'
OSC_LISTEN_PORT = 8000
TOUCH_TIMEOUT = 0.05


def run_calibration(bind_fn=None):
    # affiche les valeurs brutes des capteurs
    pad = PadState()
    acc = Vector3State()
    gyr = Vector3State()
    bounds = {
        'xmin': float('inf'), 'xmax': float('-inf'),
        'ymin': float('inf'), 'ymax': float('-inf'),
        'gyr': 0.0,
    }

    osc = OSCThreadServer()
    osc.listen(address=OSC_LISTEN_IP, port=OSC_LISTEN_PORT, default=True)
    if bind_fn is not None:
        bind_fn(osc, pad, acc, gyr)
    else:
        osc.bind(b'/multisense/pad/touch', lambda *v: pad.set_x(v[0]) if len(v) > 0 else None)
        osc.bind(b'/multisense/accelerometer/x', lambda *v: acc.set_x(v[0]))
        osc.bind(b'/multisense/accelerometer/y', lambda *v: acc.set_y(v[0]))
        osc.bind(b'/multisense/accelerometer/z', lambda *v: acc.set_z(v[0]))
        osc.bind(b'/multisense/gyroscope/x', lambda *v: gyr.set_x(v[0]))

    print('\nMode calibration - Ctrl+C pour quitter')
    print('  1. touchers coins pad')
    print('  2. inclinaison tel')
    print('  3. secousse pour seuil gyro')
    print(f'Ecoute OSC {OSC_LISTEN_IP}:{OSC_LISTEN_PORT}\n')

    try:
        while True:
            x, y, page = pad.snapshot()
            ax, ay, az, aage = acc.snapshot()
            gx, gy, gz, gage = gyr.snapshot()

            ligne = ''
            if x is not None:
                bounds['xmin'] = min(bounds['xmin'], x)
                bounds['xmax'] = max(bounds['xmax'], x)
                bounds['ymin'] = min(bounds['ymin'], y)
                bounds['ymax'] = max(bounds['ymax'], y)
                etat_touch = 'touche ' if page < TOUCH_TIMEOUT else 'relache'
                ligne += f"pad {x:+.2f} {y:+.2f} [{bounds['xmin']:+.2f},{bounds['xmax']:+.2f}] {etat_touch} | "
            if ax is not None:
                ligne += f"acc {ax:+.2f} {ay:+.2f} {az:+.2f} | "
            if gx is not None:
                bounds['gyr'] = max(bounds['gyr'], abs(gx))
                ligne += f"gyr x {gx:+6.2f} rad/s pic {bounds['gyr']:5.2f}"
            if ligne:
                print('\r' + ligne + '   ', end='')

            time.sleep(1.0 / 30)
    except KeyboardInterrupt:
        print('\n\nValeurs observees :')
        print(f"  PAD_X: {bounds['xmin']:.3f}, {bounds['xmax']:.3f}")
        print(f"  PAD_Y: {bounds['ymin']:.3f}, {bounds['ymax']:.3f}")
        print(f"  GYR_X_SHAKE_THRESHOLD: {bounds['gyr'] * 0.7:.1f}")
    finally:
        osc.stop()
