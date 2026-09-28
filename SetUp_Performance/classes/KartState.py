GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'


class KartState:
    STEERING_COMMANDS = {
        'LEFT': ('P_LEFT', 'R_LEFT'),
        'RIGHT': ('P_RIGHT', 'R_RIGHT'),
        'NONE': (None, None),
    }
    THROTTLE_COMMANDS = {
        'ACCELERATE': ('P_ACCELERATE', 'R_ACCELERATE'),
        'BRAKE': ('P_BRAKE', 'R_BRAKE'),
        'NONE': (None, None),
    }

    def __init__(self, sender, debug=False):
        self.sender = sender
        self.debug = debug
        self.steering = 'NONE'
        self.throttle = 'NONE'
        self.skidding_on = False
        self.nitro_on = False

    def _switch(self, table, current, target):
        if target == current:
            return current
        release_old = table[current][1]
        if release_old:
            self.sender.send(release_old)
        press_new = table[target][0]
        if press_new:
            self.sender.send(press_new)
        if self.debug:
            print(BLUE + f'\tetat : {current} -> {target}' + WHITE)
        return target

    def set_steering(self, target):
        self.steering = self._switch(self.STEERING_COMMANDS, self.steering, target)

    def set_throttle(self, target):
        self.throttle = self._switch(self.THROTTLE_COMMANDS, self.throttle, target)

    def fire(self):
        self.sender.send('FIRE')

    def rescue(self):
        self.sender.send('RESCUE')

    def set_skidding(self, active):
        if active == self.skidding_on:
            return
        self.sender.send('P_SKIDDING' if active else 'R_SKIDDING')
        self.skidding_on = active
        if self.debug:
            print(BLUE + '\tderapage : {}'.format('ON' if active else 'OFF') + WHITE)

    def set_nitro(self, active):
        if active == self.nitro_on:
            return
        self.sender.send('P_NITRO' if active else 'R_NITRO')
        self.nitro_on = active
        if self.debug:
            print(GREEN + '\tdouble-tap -> NITRO' + WHITE)

    def release_all(self):
        self.set_steering('NONE')
        self.set_throttle('NONE')
        self.set_skidding(False)

    def __str__(self):
        if self.steering == 'NONE' and self.throttle == 'NONE':
            return 'immobile'
        return f'{self.steering} + {self.throttle}'
