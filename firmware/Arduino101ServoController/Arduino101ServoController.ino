#include <CurieBLE.h>
#include <Servo.h>
#include <string.h>

// Keep this at or below the Reflex hardware limit. The servo is returned to
// RELEASE_ANGLE locally even if the phone disconnects or stops running.
const unsigned long MAX_CONTACT_MS = 2000UL;

const int UP_PIN = 2;
const int DOWN_PIN = 3;
const int RIGHT_PIN = 4;
const int LEFT_PIN = 5;

const int TOUCH_ANGLE = 0;
// Existing command names say "80", but the supplied sketch configures 70.
// Preserve that working physical position and make it explicit.
const int RELEASE_ANGLE = 70;

enum ServoIndex { UP_SERVO, DOWN_SERVO, RIGHT_SERVO, LEFT_SERVO, SERVO_COUNT };

Servo upServo;
Servo downServo;
Servo rightServo;
Servo leftServo;

int commandedAngles[SERVO_COUNT] = {
  RELEASE_ANGLE, RELEASE_ANGLE, RELEASE_ANGLE, RELEASE_ANGLE
};
bool servoPressed[SERVO_COUNT] = { false, false, false, false };
unsigned long contactStartedAt[SERVO_COUNT] = { 0, 0, 0, 0 };
bool wasConnected = false;

BLEPeripheral blePeripheral;
BLEService servoService("19B10000-E8F2-537E-4F6C-D104768A1214");
BLECharacteristic commandCharacteristic(
  "19B10001-E8F2-537E-4F6C-D104768A1214", BLEWrite, 20
);

char serialBuffer[21];
int serialIndex = 0;

Servo& servoFor(ServoIndex index) {
  switch (index) {
    case UP_SERVO: return upServo;
    case DOWN_SERVO: return downServo;
    case RIGHT_SERVO: return rightServo;
    default: return leftServo;
  }
}

void setAngle(ServoIndex index, int angle) {
  servoFor(index).write(angle);
  commandedAngles[index] = angle;
}

void releaseServo(ServoIndex selected) {
  setAngle(selected, RELEASE_ANGLE);
  servoPressed[selected] = false;
}

void releaseAll() {
  for (int i = 0; i < SERVO_COUNT; i++) releaseServo((ServoIndex)i);
}

void pressServo(ServoIndex selected) {
  // Repeated press packets do not refresh this servo's independent deadline.
  if (servoPressed[selected]) return;
  setAngle(selected, TOUCH_ANGLE);
  servoPressed[selected] = true;
  contactStartedAt[selected] = millis();
}

void printAngles() {
  Serial.print("UP="); Serial.print(commandedAngles[UP_SERVO]);
  Serial.print(" DOWN="); Serial.print(commandedAngles[DOWN_SERVO]);
  Serial.print(" RIGHT="); Serial.print(commandedAngles[RIGHT_SERVO]);
  Serial.print(" LEFT="); Serial.println(commandedAngles[LEFT_SERVO]);
}

void printHelp() {
  Serial.println("\n===== SERVO CONTROLLER =====");
  Serial.println("Hold commands: up0, down0, left0, right0; multiple servos may be held together");
  Serial.println("Release each servo independently: up80, down80, left80, right80");
  Serial.println("reset releases all servos; ? prints current commanded positions");
  Serial.println("Each servo auto-releases after 2000 ms or all release on BLE disconnect.");
  Serial.println("============================\n");
}

void handleCommand(char *cmd) {
  if (strcmp(cmd, "reset") == 0) {
    releaseAll();
    Serial.println("Reset: all servos released to 70 degrees");
    return;
  }
  if (strcmp(cmd, "?") == 0) {
    printAngles();
    return;
  }

  if (strcmp(cmd, "up0") == 0) pressServo(UP_SERVO);
  else if (strcmp(cmd, "down0") == 0) pressServo(DOWN_SERVO);
  else if (strcmp(cmd, "left0") == 0) pressServo(LEFT_SERVO);
  else if (strcmp(cmd, "right0") == 0) pressServo(RIGHT_SERVO);
  else if (strcmp(cmd, "up80") == 0) releaseServo(UP_SERVO);
  else if (strcmp(cmd, "down80") == 0) releaseServo(DOWN_SERVO);
  else if (strcmp(cmd, "left80") == 0) releaseServo(LEFT_SERVO);
  else if (strcmp(cmd, "right80") == 0) releaseServo(RIGHT_SERVO);
  else {
    Serial.print("Unknown or inactive release command: ");
    Serial.println(cmd);
    return;
  }

  Serial.print("Executed: ");
  Serial.println(cmd);
}

void readSerialCommands() {
  // Bound work per loop so serial input cannot starve the local release timer.
  int bytesRead = 0;
  while (Serial.available() > 0 && bytesRead < 21) {
    bytesRead++;
    char incomingChar = Serial.read();
    if (incomingChar == '\n' || incomingChar == '\r') {
      if (serialIndex > 0) {
        serialBuffer[serialIndex] = '\0';
        handleCommand(serialBuffer);
        serialIndex = 0;
      }
    } else if (serialIndex < 20) {
      serialBuffer[serialIndex++] = incomingChar;
    } else {
      serialIndex = 0;
      Serial.println("Command too long. Try again.");
    }
  }
}

void setup() {
  Serial.begin(9600);
  upServo.attach(UP_PIN);
  downServo.attach(DOWN_PIN);
  rightServo.attach(RIGHT_PIN);
  leftServo.attach(LEFT_PIN);
  releaseAll();

  blePeripheral.setLocalName("ServoController");
  blePeripheral.setAdvertisedServiceUuid(servoService.uuid());
  blePeripheral.addAttribute(servoService);
  blePeripheral.addAttribute(commandCharacteristic);
  blePeripheral.begin();

  Serial.println("ServoController BLE ready");
  Serial.println("Serial Monitor control ready");
  printHelp();
}

void loop() {
  blePeripheral.poll();

  bool connected = blePeripheral.connected();
  if (wasConnected && !connected) {
    releaseAll();
    Serial.println("BLE disconnected: all servos released");
  }
  wasConnected = connected;

  for (int i = 0; i < SERVO_COUNT; i++) {
    if (servoPressed[i] && millis() - contactStartedAt[i] >= MAX_CONTACT_MS) {
      releaseServo((ServoIndex)i);
      Serial.print("Auto-release: ");
      Serial.print(i);
      Serial.println(" servo contact limit reached");
    }
  }

  if (commandCharacteristic.written()) {
    int length = commandCharacteristic.valueLength();
    if (length > 0 && length < 20) {
      char cmd[21];
      memcpy(cmd, commandCharacteristic.value(), length);
      cmd[length] = '\0';
      if (connected) handleCommand(cmd);
      else Serial.println("Ignored BLE command: no central is connected");
    }
  }

  readSerialCommands();
}
