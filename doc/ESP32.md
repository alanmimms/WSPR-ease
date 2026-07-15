# ESP32 Microcontroller Pinouts and Functionality

## Pinout

| GPIO | Function | Description |
| 0 | paEn | Transmitter power amplifier enable |
| 1 | NC | |
| 2 | NC | |
| 3 | NC | |
| 4 | GPIO4 | Pin brought out to a test point on the PCB for testing, debugging, whatever |
| 5 | loON | Enable for the lowest band (4MHz) low pass filter |
| 6 | midON | Enable for the middle band (11.5MHz) low pass filter |
| 7 | hiON | Enable for the high band (32MHz) low pass filter |
| 8 | VBUSadc | Analog input to measure the VBUS voltage, scaled to 50% |
| 9 | NC | |
| 10 | txSDA | I2C bus SDA signal for SI5351 control |
| 11 | txSCL | I2C bus SCL signal for SI5351 control |
| 12 | tcxo | input used to measure (count) the TCXO frequency against vs the gnssPPS signal |
| 13 | cpldMOSI | SPI bus ESP32 master-out to CPLD |
| 14 | cpldSCK | SPI bus ESP32 clock to CPLD |
| 15 | gnssNRESET | Active-low reset for GNSS chip |
| 16 | gnssPPS | Very precise 1Hz signal from GNSS to use as a time base |
| 17 | NC | |
| 18 | NC | |
| USB_D-/USB-D+ | USB-/USB+ | USB bus signals from USB connector |
| 21 | cpldMISO | SPI bus ESP32 master-in from CPLD |
| 35 | NC | (used by PSRAM) |
| 36 | NC | (used by PSRAM) |
| 37 | NC | (used by PSRAM) |
| 38 | hbRed | Heartbeat/status LED red (active low) |
| 39 | hbGrn | Heartbeat/status LED green (active low) |
| 40 | hbBlu | Heartbeat/status LED blue (active low) |
| 41 | NC | |
| 42 | NC | |
| 45 | cpldDONE | Signal asserted by CPLD when it finishes its programming cycle |
| 46 | NC | |
| 47 | cpldNCS | SPI bus ESP32 active low chip select for CPLD |
| 48 | cpldPROG | Signal asserted by ESP32 to put CPLD into its PROGRAMMING mode when deasserting reset |
| TXD | gnssRx | UART transmitter from ESP32 to GNSS chip |
| RXD | gnssTx | UART receiver from GNSS chip to ESP32 |
