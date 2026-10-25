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

enum ActiveServo { NO_SERVO, UP_SERVO, DOWN_SERVO, RIGHT_SERVO, LEFT_SERVO };

Servo upServo;
Servo downServo;
Servo rightServo;
Servo leftServo;

int upAngle = RELEASE_ANGLE;
int downAngle = RELEASE_ANGLE;
int rightAngle = RELEASE_ANGLE;
int leftAngle = RELEASE_ANGLE;
ActiveServo activeServo = NO_SERVO;
unsigned long contactStartedAt = 0;
bool wasConnected = false;

BLEPeripheral blePeripheral;
BLEService servoService("19B10000-E8F2-537E-4F6C-D104768A1214");
BLECharacteristic commandCharacteristic(
  "19B10001-E8F2-537E-4F6C-D104768A1214", BLEWrite, 20
);

char serialBuffer[21];
int serialIndex = 0;

void releaseAll() {
  upServo.write(RELEASE_ANGLE);
  downServo.write(RELEASE_ANGLE);
  rightServo.write(RELEASE_ANGLE);
  leftServo.write(RELEASE_ANGLE);
  upAngle = RELEASE_ANGLE;
  downAngle = RELEASE_ANGLE;
  rightAngle = RELEASE_ANGLE;
  leftAngle = RELEASE_ANGLE;
  activeServo = NO_SERVO;
}

void releaseActiveServo() {
  switch (activeServo) {
    case UP_SERVO: upServo.write(RELEASE_ANGLE); upAngle = RELEASE_ANGLE; break;
    case DOWN_SERVO: downServo.write(RELEASE_ANGLE); downAngle = RELEASE_ANGLE; break;
    case RIGHT_SERVO: rightServo.write(RELEASE_ANGLE); rightAngle = RELEASE_ANGLE; break;
    case LEFT_SERVO: leftServo.write(RELEASE_ANGLE); leftAngle = RELEASE_ANGLE; break;
    case NO_SERVO: return;
  }
  activeServo = NO_SERVO;
}

void pressServo(ActiveServo selected) {
  // Only one actuator may be at the touch angle at a time.
  // Repeated press packets cannot refresh the current actuator's deadline.
  if (activeServo == selected) return;
  releaseAll();
  switch (selected) {
    case UP_SERVO: upServo.write(TOUCH_ANGLE); upAngle = TOUCH_ANGLE; break;
    case DOWN_SERVO: downServo.write(TOUCH_ANGLE); downAngle = TOUCH_ANGLE; break;
    case RIGHT_SERVO: rightServo.write(TOUCH_ANGLE); rightAngle = TOUCH_ANGLE; break;
    case LEFT_SERVO: leftServo.write(TOUCH_ANGLE); leftAngle = TOUCH_ANGLE; break;
    case NO_SERVO: return;
  }
  activeServo = selected;
  contactStartedAt = millis();
}

void printAngles() {
  Serial.print("UP="); Serial.print(upAngle);
  Serial.print(" DOWN="); Serial.print(downAngle);
  Serial.print(" RIGHT="); Serial.print(rightAngle);
  Serial.print(" LEFT="); Serial.println(leftAngle);
}

void printHelp() {
  Serial.println("\n===== SERVO CONTROLLER =====");
  Serial.println("Directional press commands: up0, down0, left0, right0");
  Serial.println("Release commands (legacy *80 names; actual angle is 70): up80, down80, left80, right80");
  Serial.println("reset releases all servos; ? prints current commanded positions");
  Serial.println("Firmware auto-releases a pressed servo after 2000 ms or on BLE disconnect.");
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
  else if (strcmp(cmd, "up80") == 0 && activeServo == UP_SERVO) releaseActiveServo();
  else if (strcmp(cmd, "down80") == 0 && activeServo == DOWN_SERVO) releaseActiveServo();
  else if (strcmp(cmd, "left80") == 0 && activeServo == LEFT_SERVO) releaseActiveServo();
  else if (strcmp(cmd, "right80") == 0 && activeServo == RIGHT_SERVO) releaseActiveServo();
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

  if (activeServo != NO_SERVO && millis() - contactStartedAt >= MAX_CONTACT_MS) {
    releaseActiveServo();
    Serial.println("Auto-release: servo contact limit reached");
  }

  if (commandCharacteristic.written()) {
    int length = commandCharacteristic.valueLength();
    if (length > 0 && length < 20) {
      char cmd[21];
      memcpy(cmd, commandCharacteristic.value(), length);
      cmd[length] = '\0';
      handleCommand(cmd);
    }
  }

  readSerialCommands();
}
