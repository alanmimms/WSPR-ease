# WSPR-ease Project Overview

**WSPR-ease** is a standalone, precision Multiband WSPR beacon
covering the 80m through 10m Amateur Radio bands (3.5 MHz – 29.7 MHz).

It utilizes a hybrid **ESP32-S3 + FPGA** architecture to synthesize
high-purity RF signals using a **6-Step "1-2-1" Harmonic
Cancellation** sequence. This approach eliminates the 3rd harmonic
digitally, allowing for a simplified Low Pass Filter (LPF) design
while maintaining atomic timing accuracy via GNSS discipline.

---

## System Architecture

The system operates as a **Software Defined Transmitter**. The ESP32
calculates the WSPR symbols and frequency, while the FPGA generates
the stepped RF waveform in real-time.

### High-Level Data Flow
```text
[GNSS/PPS] -> [ESP32-S3] --(SPI)--> [iCE40 FPGA] --(DDR IO)--> [74AHCT Buffer] -> [MOSFET PA] -> [Transformer] -> [Switched LPF] -> [ANT]
```

*   **Controller:** ESP32-S3-WROOM-1 (N8R8). Manages Wi-Fi, Web UI,
    WSPR Encoding, Frequency Scheduling, and Band Switching.
*   **RF Synthesis:** Lattice iCE40UP5K. Implements a 32-bit NCO and
    6-step sequencer running at 200 Msps (effective).
*   **Timing:** "Software GPSDO". A standard 40 MHz TCXO provides the
    clock, which is continuously calibrated against the GNSS PPS
    signal to achieve <0.1 Hz accuracy.
*   **Wideband PA:** A transformer-coupled Class D/E amplifier
    designed to operate from 3.5 MHz to 30 MHz.

---

## Hardware Specifications

### Core Components

| Component | Part Number | Package | Notes |
| :--- | :--- | :--- | :--- |
| **MCU** | ESP32-S3-WROOM-1-N8R8 | Module | 8MB Flash, 8MB Octal PSRAM. |
| **FPGA** | iCE40UP5K-SG48 | QFN-48 | Note: Pin 29=VCCPLL. |
| **Per leg RF Buffer** | 74AHCT1G125 | SOT-533 1.6mm x 16.mm | "T" (TTL) variant required for 3.3V logic translation. |
| **MOSFETs** | BS170 (x12) | SOT-23 | 3x Parallel per phase (Total 12). Low $V_{GS(th)}$. |
| **Transformer** | BN-43-202 | Core | Binocular. 4T Primary / 4T Secondary. Safe for 80m. |
| **Oscillator** | TCXO 40MHz | 3225 | 0.5ppm stability. |
| **GNSS** | ATGM336H / NEO-6M | Module | Must output PPS. |
| **Diodes** | 1N5817 / SS14 | SMA | Schottky. Required for Push1/Pull1 drain protection. |

### Power Architecture

*   **Input:** USB-C (5.0V @ 1.2A Max).
*   **3.3V Rail:** LDO (e.g., AP2112K). Powers ESP32, TCXO, GNSS, FPGA
    IO.
*   **1.2V Rail:** LDO (e.g., TLV70012). Powers FPGA Core (VCC).
*   **FPGA Power Controls:** ESP32 manages FPGA Core power good
    (`pg_fpgacore` on IO 41) and enables FPGA IO power (`en_fpgaio` on IO
    42) to ensure correct power sequencing.
*   **PLL Supply:** Filtered 1.2V (10Ω Resistor + 10µF Cap to Pin 29).
*   **PA Supply:** Direct 5V VBUS with 22µF Bulk Capacitor.

---

## Pinout & Interconnects

### ESP32 <-> FPGA Interface

Uses FSPI (SPI2) on native pins for high-speed bitstream loading
(Configuration) and Frequency Updates (Operation).

| Signal | ESP32 GPIO | FPGA Pin (SG48) | Function |
| :--- | :--- | :--- | :--- |
| **SPI_CLK** | IO 12 | Pin 15 | Config Clock & Tuning Clock |
| **SPI_MOSI** | IO 11 | Pin 17 | Config Data & Tuning Word |
| **SPI_MISO** | IO 13 | Pin 14 | Readback (PPS Counters) |
| **SPI_CS** | IO 14 | Pin 16 | Chip Select (Active Low) |
| **FPGA_RST** | IO 9 | Pin 8 | Hard Reset (CRESET_B) |
| **FPGA_DONE** | IO 10 | Pin 7 | Config Status (CDONE) |
| **PPS_IN** | IO 48 | Pin 6 | Shared GNSS Pulse Per Second |
| **PG_CORE** | IO 41 | - | FPGA Core Power Good |
| **EN_IO** | IO 42 | - | FPGA IO Power Enable |

### Zephyr Peripheral Map

| Function | GPIO | Notes |
| :--- | :--- | :--- |
| **USB Console** | 19 / 20 | Native USB (D- / D+) |
| **GNSS RXD** | 43 | Connect to Module TX |
| **GNSS TXD** | 44 | Connect to Module RX |
| **VBUS Mon** | 8 | ADC1 Channel 7 (100k/100k Divider) |

### Band Switching (LPF Control)

The relays actuate based on the target frequency to select the
appropriate Low Pass Filter bank.

| Signal | GPIO | Logic |
| :--- | :--- | :--- |
| **loON** | IO 5 | Low Band 6MHz knee (80m) |
| **midON** | IO 6 | Mid Band 16MHz knee (40m - 20m) |
| **hiON** | IO 7 | High  (17m - 10m) |

---

## FPGA Logic (Wideband NCO)

The FPGA logic is frequency-agnostic. It simply steps through the
"1-2-1" sequence at a rate determined by the Tuning Word.

*   **Clock:** 100 MHz PLL (derived from 40 MHz TCXO).
*   **Update Rate:** 200 Msps (using DDR I/O).
*   **Coverage:**
    *   **80m (3.5 MHz):** Oversampling ratio ~57x. Extremely clean
        waveform.
    *   **10m (29.7 MHz):** Oversampling ratio ~6.7x. Clean waveform
        (3rd harmonic cancelled).

---

## RF Power Amplifier

### Topology

A Low-Side Tapped Transformer driver (Class D/E variant) optimized for
5V operation.

*   **Driver:** 74AHCT1G125 (running at 5V).
*   **PA:** 4 Groups of 3x Parallel BS170.
*   **Transformer:** BN-43-202 Binocular Core.
    *   **Primary:** 4 Turns Center Tapped (Tap at 2 Turns).
    *   **Secondary:** 4 Turns.
    *   **Note:** The 4-turn primary provides $\sim 35\mu H$
        inductance ($X_L \approx 770\Omega @ 3.5 MHz$), ensuring
        efficient operation down to the 80m band without saturation.

### Protection Circuitry (Critical)

*   **Flyback Diodes:** 1N5817 Schottky diodes in series with the
    Drains of the Push 1 and Pull 1 MOSFET groups. This blocks reverse
    current during the negative half of the "1-2-1" cycle.
*   **Gate Resistors:** 10Ω series resistors on all Gate lines to
    dampen parasitic oscillation (ringing) which can occur due to the
    fast edges of the 74AHCT1G125.

---

### Power Output Variability

There is a very elegant digital method to reduce power within the FPGA
without changing the transformer, VBUS voltage, or "1-2-1" harmonic
cancellation logic.

Since we use an NCO-based sequencer to drive the 74AHCT1G125 buffers, the
simplest method is **Pulse Width Control (Duty Cycle Modulation)** at
the sequence level.

#### The "Gap" Method (Zero-State Insertion)
Currently, the 6-step sequence likely transitions immediately from one
state to the next (e.g., Push Peak $\rightarrow$ Push Base
$\rightarrow$ Push Peak). To reduce power, insert a "Dead Time" or
"High-Z" state between every transition.

Instead of a continuous drive, modify the FPGA logic to:
1. Drive the FET for a portion of the 60° window.
2. Turn **OFF** all FETs for the remainder of that window.

Because the 74AHCT1G125 has an **Output Enable (nOE)** pin, or simply
drive all four gate signals (`paPushPeak`, `paPushBase`, etc.) to
**0** in Verilog, to effectively "chop" the waveform.


### Implementation: The 2nd Accumulator
Add a small "Power Register" (e.g., 8-bit) that acts as a threshold.
* The main NCO determines the **frequency** (the 6-step cycle).
* A second, faster counter (or a sub-division of the system clock)
  determines the **duration** of the "On" time within each of those 6
  steps.

**Mathematical Result:** Reduce the "On" time of each step by 50% to
drop the $V_{RMS}$ of the secondary waveform. Since $P = V^2 / R$, the
power drops quadratically.
* **100% Duty:** 1.5W
* **70% Duty:** ~0.73W
* **50% Duty:** ~0.37W

### Why this is better than "Skipping Cycles"
Some digital PAs use "Pulse Density Modulation" (skipping whole RF
cycles). WSPR-ease does not do this. Skipping cycles creates massive
sub-harmonic spurs that will fail FCC/Off-air purity requirements.

By narrowing the individual steps within the "1-2-1" sequence:
1. Maintain the **fundamental frequency**.
2. The **"1-2-1" ratio** is preserved (so the 3rd harmonic
   cancellation still functions).
3. The **Low Pass Filter** sees a signal that is simply "smaller" in
   amplitude, though slightly richer in very high-order harmonics
   (which the LPF is already designed to kill).

### Code Snippet Logic (Verilog)
In the sequencer, add a `paGlobalEn` signal:

```verilog
// Simple Power Control Logic
reg [7:0] pwrCounter;
wire paGlobalEn = (pwrCounter < pwrThreshold); // pwrThreshold set by ESP32 via SPI

always @(posedge clkFast) begin
    pwrCounter <= pwrCounter + 1;
    
    // Assign gate outputs
    paPushPeak = (currentState == PUSH_PEAK) && paGlobalEn;
    paPushBase = (currentState == PUSH_BASE) && paGlobalEn;
    // ... etc
end
```

### Hardware Benefit: FET Thermals
This method is extremely efficient. Because the MOSFETs are either
"Fully ON" or "Fully OFF," they don't dissipate extra heat like a
linear regulator would. In fact, reducing power this way makes the PA
run **cooler**, as the switching losses remain low but the average
current decreases.

**Architect's Note:** Using the **nOE** pin of the 74AHCT1G125 for
this "chopping" is the cleanest way to implement it if you want to
ensure all lines go high-impedance simultaneously, but doing it in the
gate-drive logic (as shown above) gives you more granular control
without stressing the 74AHCT enable pin's timing.


## PCB Design Rules

*   **Symbol Verification:** Ensure the iCE40UP5K-SG48 symbol is used.
    (Pin 29 is VCCPLL, Pin 24 is VPP\_2V5). **Do not use LP1K
    symbols.**
*   **Decoupling:**
    *   **FPGA VCCPLL (Pin 29):** Must have 10Ω Series Resistor and
        10µF Capacitor to GND.
    *   **RF PA:** Place 22µF bulk capacitance right at the
        Transformer Center Tap.
*   **Layout:**
    *   Keep the 74AHCT -> MOSFET -> Transformer loop as short as
        possible.
    *   Use Copper Pours for the MOSFET Drains (Heatsinking).
    *   Keep GNSS Antenna input traces away from the 30 MHz RF
        switching nodes.

---

## Firmware Operation (Zephyr)

*   **Boot:** ESP32 loads bitstream to FPGA via SPI.
*   **Band Select:** User selects Band (e.g., 20m). ESP32 sets
    `LPF_loON` + `LPF_lo2ON` HIGH.
*   **Calibration:** ESP32 measures PPS interval vs 40 MHz clock to
    determine true frequency.
*   **Transmit:** ESP32 computes NCO tuning word for target
    frequency + WSPR tone shift and updates FPGA over SPI at 1.46 Hz.

---

## Scheduling

WSPR transmission can be scheduled very flexibly. A set of ham bands
and times for those bands to be used is configurable. Once this is
done, the choice of which band to transmit on is made based on a
selection of random, round-robin, or a list of bands. This last
selection provides a mechanism to allow a band to be listed e.g., five
times in a random or round-robin list to make it five times as likely
to be chosen as the others.

For time, the UTC, user's local time zone, and/or offset from sunrise
and sunset at grid square center or GNSS lat/lon. Each sunrise/sunset
relative time needs to be evaluated at time of scheduling since it
changes daily.

---

## Notes

*   Web UI needs to support both desktop and phone browser sizes and
    use cases.
