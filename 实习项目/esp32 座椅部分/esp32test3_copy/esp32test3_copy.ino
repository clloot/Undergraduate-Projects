#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>

// Analog inputs
constexpr uint8_t ACCELERATOR_ADC_PIN = 1;
constexpr uint8_t BRAKE_ADC_PIN = 2;
constexpr uint8_t STEERING_ADC_PIN = 4;

// Digital direction inputs
constexpr uint8_t REVERSE_INPUT_PIN = 41;
constexpr uint8_t FORWARD_INPUT_PIN = 42;

constexpr char DEVICE_NAME[] = "ESP32-S3-ADC";
constexpr char SERVICE_UUID[] = "12345678-1234-1234-1234-123456789abc";
constexpr char CHARACTERISTIC_UUID[] = "87654321-4321-4321-4321-cba987654321";

constexpr uint8_t ADC_SAMPLE_COUNT = 8;
constexpr float FILTER_ALPHA = 0.20f;
constexpr uint32_t SAMPLE_INTERVAL_MS = 20;
constexpr uint32_t BLE_SEND_INTERVAL_MS = 100;
constexpr uint32_t SERIAL_PRINT_INTERVAL_MS = 250;

BLECharacteristic *voltageCharacteristic = nullptr;
volatile bool bleConnected = false;

float acceleratorVoltage = 0.0f;
float brakeVoltage = 0.0f;
float steeringVoltage = 1.56f;
bool filtersInitialized = false;

uint32_t lastSampleTime = 0;
uint32_t lastBleSendTime = 0;
uint32_t lastSerialPrintTime = 0;


class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    bleConnected = true;
    Serial.println("BLE client connected");
  }

  void onDisconnect(BLEServer *server) override {
    bleConnected = false;
    Serial.println("BLE client disconnected; advertising restarted");
    BLEDevice::startAdvertising();
  }
};


uint32_t readAverageMillivolts(uint8_t pin) {
  uint32_t total = 0;

  for (uint8_t i = 0; i < ADC_SAMPLE_COUNT; ++i) {
    total += analogReadMilliVolts(pin);
    delayMicroseconds(250);
  }

  return total / ADC_SAMPLE_COUNT;
}


float lowPassFilter(float previous, float sample) {
  return previous + FILTER_ALPHA * (sample - previous);
}


void updateInputs() {
  const float acceleratorSample = (
    readAverageMillivolts(ACCELERATOR_ADC_PIN) / 1000.0f
  );
  const float brakeSample = (
    readAverageMillivolts(BRAKE_ADC_PIN) / 1000.0f
  );
  const float steeringSample = (
    readAverageMillivolts(STEERING_ADC_PIN) / 1000.0f
  );

  if (!filtersInitialized) {
    acceleratorVoltage = acceleratorSample;
    brakeVoltage = brakeSample;
    steeringVoltage = steeringSample;
    filtersInitialized = true;
  } else {
    acceleratorVoltage = lowPassFilter(
      acceleratorVoltage,
      acceleratorSample
    );
    brakeVoltage = lowPassFilter(brakeVoltage, brakeSample);
    steeringVoltage = lowPassFilter(
      steeringVoltage,
      steeringSample
    );
  }
}


void sendDataOverBle() {
  // Packet: accelerator,brake,steering,reverse,forward
  char packet[64];
  const int reverseState = digitalRead(REVERSE_INPUT_PIN) == HIGH;
  const int forwardState = digitalRead(FORWARD_INPUT_PIN) == HIGH;

  snprintf(
    packet,
    sizeof(packet),
    "%.3f,%.3f,%.3f,%d,%d",
    acceleratorVoltage,
    brakeVoltage,
    steeringVoltage,
    reverseState,
    forwardState
  );

  voltageCharacteristic->setValue(
    reinterpret_cast<uint8_t *>(packet),
    strlen(packet)
  );

  if (bleConnected) {
    voltageCharacteristic->notify();
  }
}


void setupBle() {
  BLEDevice::init(DEVICE_NAME);

  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService *service = server->createService(SERVICE_UUID);

  voltageCharacteristic = service->createCharacteristic(
    CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_READ |
    BLECharacteristic::PROPERTY_NOTIFY
  );

  voltageCharacteristic->addDescriptor(new BLE2902());
  voltageCharacteristic->setValue("0.000,0.000,1.560,0,0");

  service->start();

  BLEAdvertising *advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->start();
}


void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);
  analogSetPinAttenuation(ACCELERATOR_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(BRAKE_ADC_PIN, ADC_11db);
  analogSetPinAttenuation(STEERING_ADC_PIN, ADC_11db);

  // GPIO41/42 are active-high direction inputs.
  pinMode(REVERSE_INPUT_PIN, INPUT_PULLDOWN);
  pinMode(FORWARD_INPUT_PIN, INPUT_PULLDOWN);

  setupBle();

  Serial.println("ESP32-S3 pedals, steering and direction BLE started");
  Serial.println("GPIO1=accelerator GPIO2=brake GPIO4=steering");
  Serial.println("GPIO41=reverse GPIO42=forward");
}


void loop() {
  const uint32_t now = millis();

  if (now - lastSampleTime >= SAMPLE_INTERVAL_MS) {
    lastSampleTime = now;
    updateInputs();
  }

  if (now - lastBleSendTime >= BLE_SEND_INTERVAL_MS) {
    lastBleSendTime = now;
    sendDataOverBle();
  }

  if (now - lastSerialPrintTime >= SERIAL_PRINT_INTERVAL_MS) {
    lastSerialPrintTime = now;

    Serial.printf(
      "ACC:%.3fV BRAKE:%.3fV STEER:%.3fV REV:%d FWD:%d BLE:%s\n",
      acceleratorVoltage,
      brakeVoltage,
      steeringVoltage,
      digitalRead(REVERSE_INPUT_PIN),
      digitalRead(FORWARD_INPUT_PIN),
      bleConnected ? "connected" : "waiting"
    );
  }
}
