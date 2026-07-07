// ============================================================
// Music visualizer — Arduino side
// Receives a single byte (0-6) over serial: how many LEDs to
// light, filled left-to-right as a bar (both rows per column).
// Uses the exact same multiplexing/wiring you already verified —
// nothing about refreshDisplay() or the column bitmask changes.
// ============================================================

const int LATCH_PIN = 11;   // 74HC595 RCLK
const int CLOCK_PIN  = 12;  // 74HC595 SRCLK
const int DATA_PIN   = 10;  // 74HC595 SER

const int ROW_PINS[2] = {2, 3};

const int NUM_ROWS = 2;
const int NUM_COLS = 3;

// Confirmed by your bit-scan test
const byte COL_BITMASK[NUM_COLS] = {
  0b00000010, // column 1 -> Q1
  0b00000100, // column 2 -> Q2
  0b00001000  // column 3 -> Q3
};

bool grid[NUM_ROWS][NUM_COLS] = {{0,0,0},{0,0,0}};

int activeRow = 0;
unsigned long lastRowSwitch = 0;
const unsigned long ROW_INTERVAL_US = 2000; // ~250Hz refresh, unchanged from before

// Fill order for the bar graph: both LEDs in col1, then col2, then col3
const int FILL_ORDER_ROW[6] = {0, 1, 0, 1, 0, 1};
const int FILL_ORDER_COL[6] = {0, 0, 1, 1, 2, 2};

int currentLevel = 0; // 0-6, how many LEDs should be lit right now

void setColumns(byte pattern) {
  digitalWrite(LATCH_PIN, LOW);
  shiftOut(DATA_PIN, CLOCK_PIN, MSBFIRST, pattern);
  digitalWrite(LATCH_PIN, HIGH);
}

void refreshDisplay() {
  if (micros() - lastRowSwitch < ROW_INTERVAL_US) return;
  lastRowSwitch = micros();

  digitalWrite(ROW_PINS[0], LOW);
  digitalWrite(ROW_PINS[1], LOW);

  byte pattern = 0;
  for (int c = 0; c < NUM_COLS; c++) {
    if (grid[activeRow][c]) pattern |= COL_BITMASK[c];
  }
  setColumns(pattern);
  digitalWrite(ROW_PINS[activeRow], HIGH);

  activeRow = (activeRow + 1) % NUM_ROWS;
}

void applyLevel(int level) {
  for (int r = 0; r < NUM_ROWS; r++)
    for (int c = 0; c < NUM_COLS; c++)
      grid[r][c] = false;

  for (int i = 0; i < level; i++) {
    grid[FILL_ORDER_ROW[i]][FILL_ORDER_COL[i]] = true;
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(DATA_PIN, OUTPUT);
  pinMode(ROW_PINS[0], OUTPUT);
  pinMode(ROW_PINS[1], OUTPUT);
}

void loop() {
  refreshDisplay(); // must run every pass, unconditionally, same as before

  if (Serial.available() > 0) {
    int incoming = Serial.read();
    if (incoming >= 0 && incoming <= 6) {
      currentLevel = incoming;
      applyLevel(currentLevel);
    }
  }
}