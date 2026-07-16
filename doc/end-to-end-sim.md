# Mixed-Signal Transmitter Architecture: Phase-Interpolated Ring Counter

## 1. Executive Summary
This document defines the "Path B" digital RF generation architecture
for the WSPR-ease transmitter. It addresses the fundamental physical
limitations of executing 32-bit Numerically Controlled Oscillator
(NCO) math at >100 MHz within the iCE40UP5K FPGA.

By dividing the synthesis labor between the Si5351 (Frequency) and the
iCE40UP5K (Phase), this architecture achieves:
* **Zero "Pulse-Swallowing" Jitter:** The carrier edge is
  mathematically and physically locked to the Si5351's analog PLL.
* **True SSB Phase Resolution:** Provides dynamic 16-bit phase
  modulation capability for Polar Modulation (EER) via a fast,
  pipelined datapath.
* **Guaranteed Timing Closure:** Eliminates long 32-bit carry chains,
  allowing the FPGA to easily close timing at the absolute worst-case
  required frequency of 148.5 MHz (10m band).

---

## 2. Division of Synthesis Labor
In traditional digital transceivers, a static high-frequency clock
drives an NCO phase accumulator to synthesize both frequency and
phase. This architecture abandons that approach.

* **Frequency Generation (Si5351):** The microcontroller
  (ESP32/RP2040) drives the Si5351 via I2C to generate a clock that is
  an exact multiple of the target transmit frequency ($F_{tx}$). All
  sub-Hz FSK frequency shifting for modes like WSPR is handled by the
  Si5351's internal glitchless shadow registers.
* **Phase Modulation (iCE40UP5K):** The FPGA receives this synchronous
  clock. It acts as a rigid, pipelined state machine to divide the
  clock down to the target $F_{tx}$, generating the RF waveform states
  (1-2-1 or Square) while injecting delta-sigma dither and SPI-driven
  Phase Modulation (PM).

---

## 3. Clock Multipliers and Delta-Sigma Headroom
To cleanly perform Delta-Sigma phase dithering (chattering a pin edge
back and forth to create high-resolution fractional phase), the FPGA
requires a mathematical minimum of 2.5 clock ticks per output state.
If the ticks per state drop below 2.5, there is insufficient temporal
headroom to dither without collapsing the macro-waveform states.

To satisfy this constraint across all HF bands while keeping the FPGA
clock under its absolute routing limit (~150 MHz), the system uses a
10 MHz crossover rule:

* **Below 10 MHz (1-2-1 Emission):** The RF cycle requires 6 states.
  To maintain 2.5 ticks/state, the Si5351 provides a **$15 \times
  F_{tx}$** clock.
* **Above 10 MHz (Square Wave):** The RF cycle requires 2 states. To
  maintain 2.5 ticks/state, the Si5351 provides a **$5 \times
  F_{tx}$** clock.

### Worldwide HF Band Strategy (80m - 10m)

| Band | Range (MHz) | Type | States/Cycle | Si5351 Mult | Ticks/State | Max Clock |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **80m** | 3.500 - 4.000 | 1-2-1 | 6 | 15x | 2.5 | 60.00 MHz |
| **60m** | 5.330 - 5.405 | 1-2-1 | 6 | 15x | 2.5 | 81.08 MHz |
| **40m** | 7.000 - 7.300 | 1-2-1 | 6 | 15x | 2.5 | 109.50 MHz |
| **30m** | 10.100 - 10.150 | Square Wave | 2 | 5x | 2.5 | 50.75 MHz |
| **20m** | 14.000 - 14.350 | Square Wave | 2 | 5x | 2.5 | 71.75 MHz |
| **17m** | 18.068 - 18.168 | Square Wave | 2 | 5x | 2.5 | 90.84 MHz |
| **15m** | 21.000 - 21.450 | Square Wave | 2 | 5x | 2.5 | 107.25 MHz |
| **12m** | 24.890 - 24.990 | Square Wave | 2 | 5x | 2.5 | 124.95 MHz |
| **10m** | 28.000 - 29.700 | Square Wave | 2 | 5x | 2.5 | 148.50 MHz |

*(Note: `Max Clock` is calculated using the top edge of the band
allocation to verify absolute worst-case timing closure).*

---

## 4. The 4-Stage Phase Modulator Pipeline
Because frequency is handled externally, the traditional 32-bit phase
accumulator is replaced by a 4-stage, fully registered 16-bit
datapath. Every stage MUST be separated by a hardware register to
ensure timing closure at 148.5 MHz.

### Stage 1: The Rigid Ring Counter
A simple integer counter that wraps based on the current clock
multiplier ($N$).
* **15F Mode:** Counts 0 to 14, wraps to 0.
* **5F Mode:** Counts 0 to 4, wraps to 0.
* **Function:** Provides a mathematically perfect, zero-jitter
  subdivision of the Si5351 clock.

### Stage 2: The Fractional Angle LUT
Converts the rigid integer count into a 16-bit fractional phase space
($0$ to $65535$, representing $0^\circ$ to $360^\circ$).
* **15F Mode Step Size:** $65536 / 15 \approx 4369$. (LUT Output: 0,
  4369, 8738...)
* **5F Mode Step Size:** $65536 / 5 \approx 13107$. (LUT Output: 0,
  13107, 26214...)
* **Function:** Translates clock ticks into a standard phase unit that
  can be manipulated by standard binary math.

### Stage 3: Phase Modulation & Dither Adder
Injects sub-degree phase control (from the microcontroller via
I2S/SPI) and LFSR dithering noise.

`Total_Phase_16b = Base_Phase_LUT + SPI_Phase_Offset_16b +
LFSR_Dither_16b`

* **SPI_Phase_Offset:** Driven by the ESP32 for SSB/Polar Modulation.
* **LFSR_Dither:** A 16-bit Pseudo-Random Number Generator. It must be
  volume-controlled (right-shifted by ~4 bits) and structured to
  maintain a zero-mean, ensuring it chatters the phase edge to smear
  quantization noise without fundamentally shifting the output
  frequency.

### Stage 4: DSP State Mapper (`SB_MAC16`)
Maps the 16-bit phase value to the physical transformer pin states
using the iCE40's hard DSP multiplier blocks to avoid fabric logic
delay.
* **15F Mode (6 States):** `(Total_Phase_16b * 6) >> 16`. (Output
  mapped to states 0-5).
* **5F Mode (2 States):** `(Total_Phase_16b * 2) >> 16`. (Output
  mapped to states 0-1).
* **Output:** The resulting state indexes directly into combinatorial
  logic that asserts the correct 4 physical DDR output pins
  (`PushBase`, `PushPeak`, `PullBase`, `PullPeak`).

---

## 5. Timing Closure Summary
At the 10m band ceiling (29.7 MHz), the 5F clock runs at **148.5
MHz**. The available clock period is exactly **6.73 nanoseconds**.

By isolating operations with synchronous registers, the 6.73 ns budget
resets at every stage.
* **Cycle 1 (Counter):** 3-bit increment. Fabric delay: ~0.5 ns.
  **Pass.**
* **Cycle 2 (LUT):** 4-input LUT logic array. Fabric delay: ~1.0 ns.
  **Pass.**
* **Cycle 3 (16-bit Adder):** 16-bit carry-chain. Fabric delay: ~2.5
  ns + ~3.0 ns routing. **Pass.**
* **Cycle 4 (`SB_MAC16`):** Phase enters DSP block. By utilizing the
  `SB_MAC16`'s built-in input and output registers (e.g.,
  `p_TOP_C_REG=1`), the math executes directly in hard silicon with
  near-zero routing penalty. **Pass.**

**Critical Requirement:** The `SB_MAC16` internal registers must be
utilized, and the 16-bit Phase Modulation addition (Stage 3) must
happen in fabric prior to DSP entry to prevent the place-and-route
tool from attempting to stretch combinational logic across the chip.
