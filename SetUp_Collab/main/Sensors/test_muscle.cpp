#include <Arduino.h>

const int ledPin = 13;

int max_analog_dta = 300;
int min_analog_dta = 100;
int static_analog_dta = 0;

int getAnalog(int pin) {
  long sum = 0;
  for (int i = 0; i < 32; i++) {
    sum += analogRead(pin);
  }
  int dta = sum >> 5;
  if (dta > max_analog_dta) max_analog_dta = dta;
  if (dta < min_analog_dta) min_analog_dta = dta;
  return sum >> 5;
}

void setup() {
  Serial.begin(115200);
  pinMode(ledPin, OUTPUT);

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

bool contraction_detected = false;
bool previous_contraction = false;
bool state = false;

void loop() {
  int val = analogRead(A0);
  Serial.print(val);
  Serial.print(" - ");
  Serial.println(state);

  contraction_detected = (val > static_analog_dta - 10);
  if (contraction_detected && !previous_contraction) {
    state = !state;
    Serial.println("Contraction détectée");
  }
  previous_contraction = contraction_detected;
  delay(100);
}
