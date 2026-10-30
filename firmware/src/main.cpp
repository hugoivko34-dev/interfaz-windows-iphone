#include <Arduino.h>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>

// Serial protocol constants (see docs/protocol.md).
static constexpr uint8_t SYNC_BYTE = 0xA5;
static constexpr uint8_t TYPE_MOUSE = 0x01;
static constexpr uint8_t TYPE_KEYS = 0x02;
static constexpr uint8_t TYPE_CONSUMER = 0x03;
static constexpr uint8_t TYPE_RELEASE_ALL = 0x04;
static constexpr uint8_t TYPE_PING = 0x05;
static constexpr uint8_t TYPE_STATUS = 0x81;
static constexpr uint8_t LED_PIN = 2;

static NimBLEHIDDevice *hid = nullptr;
static NimBLECharacteristic *keyboardInput = nullptr;
static NimBLECharacteristic *mouseInput = nullptr;
static NimBLECharacteristic *consumerInput = nullptr;
static bool bleConnected = false;
static bool statusPending = false;
static uint32_t lastLedToggle = 0;
static bool ledState = false;

// One composite report map: keyboard (including LED output), mouse and consumer control.
static const uint8_t reportMap[] = {
    0x05,0x01, 0x09,0x06, 0xA1,0x01, 0x85,0x01,
    0x05,0x07, 0x19,0xE0, 0x29,0xE7, 0x15,0x00, 0x25,0x01,
    0x75,0x01, 0x95,0x08, 0x81,0x02,
    0x75,0x08, 0x95,0x01, 0x81,0x01,
    0x05,0x08, 0x19,0x01, 0x29,0x05, 0x75,0x01, 0x95,0x05, 0x91,0x02,
    0x75,0x03, 0x95,0x01, 0x91,0x01,
    0x05,0x07, 0x19,0x00, 0x29,0x65, 0x15,0x00, 0x25,0x65,
    0x75,0x08, 0x95,0x06, 0x81,0x00, 0xC0,
    0x05,0x01, 0x09,0x02, 0xA1,0x01, 0x85,0x02,
    0x09,0x01, 0xA1,0x00, 0x05,0x09, 0x19,0x01, 0x29,0x03,
    0x15,0x00, 0x25,0x01, 0x95,0x03, 0x75,0x01, 0x81,0x02,
    0x95,0x01, 0x75,0x05, 0x81,0x01,
    0x05,0x01, 0x09,0x30, 0x09,0x31, 0x09,0x38, 0x15,0x81,
    0x25,0x7F, 0x75,0x08, 0x95,0x03, 0x81,0x06, 0xC0, 0xC0,
    0x05,0x0C, 0x09,0x01, 0xA1,0x01, 0x85,0x03,
    0x15,0x00, 0x26,0xFF,0x03, 0x19,0x00, 0x2A,0xFF,0x03,
    0x75,0x10, 0x95,0x01, 0x81,0x00, 0xC0
};

static void sendStatus() {
  const uint8_t connected = bleConnected ? 1 : 0;
  uint8_t checksum = TYPE_STATUS ^ 1 ^ connected;
  Serial.write(SYNC_BYTE); Serial.write(TYPE_STATUS); Serial.write(1);
  Serial.write(connected); Serial.write(checksum);
}

static void sendKeyboard(const uint8_t *report) {
  if (!bleConnected || keyboardInput == nullptr) return;
  keyboardInput->setValue(report, 8);
  keyboardInput->notify();
}

static void sendMouse(uint8_t buttons, int8_t x, int8_t y, int8_t wheel) {
  if (!bleConnected || mouseInput == nullptr) return;
  const uint8_t report[] = {buttons, static_cast<uint8_t>(x), static_cast<uint8_t>(y), static_cast<uint8_t>(wheel)};
  mouseInput->setValue(report, sizeof(report));
  mouseInput->notify();
}

static void sendConsumer(uint16_t usage) {
  if (!bleConnected || consumerInput == nullptr) return;
  const uint8_t report[] = {static_cast<uint8_t>(usage & 0xFF), static_cast<uint8_t>(usage >> 8)};
  consumerInput->setValue(report, sizeof(report));
  consumerInput->notify();
}

class ServerCallbacks final : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *) override { bleConnected = true; statusPending = true; }
  void onDisconnect(NimBLEServer *) override {
    bleConnected = false; statusPending = true;
    NimBLEDevice::startAdvertising();
  }
};

// Incremental parser; a bad frame returns to SYNC search without blocking serial input.
enum ParseState : uint8_t { WAIT_SYNC, READ_TYPE, READ_LENGTH, READ_PAYLOAD, READ_CHECKSUM };
static ParseState parserState = WAIT_SYNC;
static uint8_t frameType = 0, frameLength = 0, payload[32], payloadIndex = 0, frameChecksum = 0;

static void handleFrame() {
  if (frameType == TYPE_PING && frameLength == 0) sendStatus();
  else if (frameType == TYPE_KEYS && frameLength == 7) {
    // Serial payload is modifiers + 6 keys; the HID report has a reserved byte in between.
    const uint8_t report[8] = {payload[0], 0, payload[1], payload[2], payload[3], payload[4], payload[5], payload[6]};
    sendKeyboard(report);
  }
  else if (frameType == TYPE_CONSUMER && frameLength == 2)
    sendConsumer(static_cast<uint16_t>(payload[0] | (payload[1] << 8)));
  else if (frameType == TYPE_RELEASE_ALL && frameLength == 0) {
    const uint8_t emptyKeys[8] = {}; sendKeyboard(emptyKeys);
    sendMouse(0, 0, 0, 0); sendConsumer(0);
  } else if (frameType == TYPE_MOUSE && frameLength == 6) {
    const int16_t dx = static_cast<int16_t>(payload[0] | (payload[1] << 8));
    const int16_t dy = static_cast<int16_t>(payload[2] | (payload[3] << 8));
    const int8_t wheel = static_cast<int8_t>(payload[4]);
    const uint8_t buttons = payload[5];
    int16_t leftX = dx, leftY = dy;
    do {
      const int8_t stepX = static_cast<int8_t>(constrain(leftX, -127, 127));
      const int8_t stepY = static_cast<int8_t>(constrain(leftY, -127, 127));
      sendMouse(buttons, stepX, stepY, leftX == dx && leftY == dy ? wheel : 0);
      leftX -= stepX; leftY -= stepY;
    } while (leftX != 0 || leftY != 0);
  }
}

static void parseSerial() {
  while (Serial.available() > 0) {
    const uint8_t byteIn = static_cast<uint8_t>(Serial.read());
    switch (parserState) {
      case WAIT_SYNC: if (byteIn == SYNC_BYTE) parserState = READ_TYPE; break;
      case READ_TYPE: frameType = byteIn; frameChecksum = byteIn; parserState = READ_LENGTH; break;
      case READ_LENGTH:
        frameLength = byteIn; frameChecksum ^= byteIn; payloadIndex = 0;
        if (frameLength > sizeof(payload)) parserState = (byteIn == SYNC_BYTE ? READ_TYPE : WAIT_SYNC);
        else parserState = frameLength == 0 ? READ_CHECKSUM : READ_PAYLOAD;
        break;
      case READ_PAYLOAD:
        payload[payloadIndex++] = byteIn; frameChecksum ^= byteIn;
        if (payloadIndex == frameLength) parserState = READ_CHECKSUM;
        break;
      case READ_CHECKSUM:
        if (byteIn == frameChecksum) handleFrame();
        parserState = (byteIn == SYNC_BYTE ? READ_TYPE : WAIT_SYNC);
        break;
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.begin(921600);
  NimBLEDevice::init("EdgeHop");
  NimBLEDevice::setSecurityAuth(true, false, true);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);

  NimBLEServer *server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  hid = new NimBLEHIDDevice(server);
  hid->manufacturer()->setValue("EdgeHop");
  hid->pnp(0x02, 0x303A, 0x1001, 0x0100);
  hid->hidInfo(0x00, 0x01);
  hid->setBatteryLevel(100);
  hid->reportMap(const_cast<uint8_t *>(reportMap), sizeof(reportMap));
  keyboardInput = hid->inputReport(1);
  hid->outputReport(1);  // Keyboard LEDs (caps lock etc.); the host expects it to exist.
  mouseInput = hid->inputReport(2);
  consumerInput = hid->inputReport(3);
  hid->startServices();
  NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(hid->hidService()->getUUID());
  advertising->setAppearance(HID_KEYBOARD);
  advertising->start();
}

void loop() {
  parseSerial();
  if (statusPending) { statusPending = false; sendStatus(); }
  if (bleConnected) {
    digitalWrite(LED_PIN, HIGH);
  } else if (millis() - lastLedToggle >= 750) {
    lastLedToggle = millis(); ledState = !ledState; digitalWrite(LED_PIN, ledState ? HIGH : LOW);
  }
}
