const int ledPin = 13;
const int PIN_VIBRATION = 2;

int max_analog_dta = 300;
int min_analog_dta = 100;
int static_analog_dta = 0;

bool previous_vibrationSensor = false;
bool contraction_detected = false;
bool previous_contraction = false;
bool state = false;

// moyenne glissante sur 32 mesures
int getAnalog(int pin) {
  long sum = 0;
  for (int i = 0; i < 32; i++) {
    sum += analogRead(pin);
  }
  int dta = sum >> 5;
  if (dta > max_analog_dta) max_analog_dta = dta;
  if (dta < min_analog_dta) min_analog_dta = dta;
  return dta;
}

void calibration() {
  long sum = 0;
  for (int i = 0; i <= 10; i++) {
    for (int j = 0; j < 100; j++) {
      sum += getAnalog(A0);
      delay(1);
    }
  }
  sum /= 1100;
  static_analog_dta = sum;
  Serial.print("static_analog_dta = ");
  Serial.println(static_analog_dta);
}

void setup() {
  Serial.begin(115200);
  pinMode(ledPin, OUTPUT);
  while (!Serial) {
    ; // attente liaison usb
  }
  calibration();
}

void loop() {
  int vibrationSensor = digitalRead(PIN_VIBRATION);
  int muscleSensor = analogRead(A0);

  // front montant capteur vibration
  if (vibrationSensor == HIGH && previous_vibrationSensor == LOW) {
    digitalWrite(ledPin, HIGH);
    Serial.println("vibrationSensor");
  } else {
    digitalWrite(ledPin, LOW);
  }
  previous_vibrationSensor = vibrationSensor;

  // detection seuil emg
  contraction_detected = (muscleSensor > static_analog_dta - 10);
  if (contraction_detected && !previous_contraction) {
    state = !state;
    Serial.println("Contraction");
  }
  previous_contraction = contraction_detected;
}
