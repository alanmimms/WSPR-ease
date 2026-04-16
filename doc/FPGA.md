# WSPR-ease FPGA Technical Reference

This document describes the internal architecture and SPI register
interface of the iCE40UP5K FPGA used in the WSPR-ease project.

## 1. Architecture Overview

The FPGA performs real-time RF synthesis and timing measurement. To
support high-purity transmission up to the 10m amateur band (~28 MHz),
the FPGA operates at a **90 MHz** internal clock rate. Utilizing DDR
(Double Data Rate) outputs, the system achieves an effective sample
rate of **180 Msps**.

### Key Specifications
*   **System Clock:** 90 MHz (11.1 ns period), synthesized from a 40
    MHz TCXO via PLL.
*   **Effective Sample Rate:** 180 Msps (via `SB_IO` DDR).
*   **Modulation:** 1-2-1 stepped amplitude synthesis (6 samples per
    RF cycle).
*   **Timing Margin:** ~35% (Achieved 121 MHz Fmax).
*   **3rd Harmonic Suppression:** ~55 dBc (Theoretical -18.5 dBc,
    significantly improved by 1-2-1 modulation and high-speed
    alignment).

---

## 2. RF Synthesis Chain (The Exciter)

To achieve timing closure at 90 MHz on the iCE40 architecture, the
entire critical path of the RF exciter is "hardened" using `SB_MAC16`
DSP blocks. This eliminates the delays associated with fabric carry
chains and routing, which typically limit iCE40 designs to <100 MHz.

To make this design continue to work as we develop and add features,
the FPGA timing closure for the 90MHz clock domain needs significant
margin. The goal should always be to achieve timing closure at 100MHz
if at all possible.

### 2.1 1-2-1 Stepped Modulation
A simple square wave contains massive odd-order harmonics (3rd
harmonic at -9.5 dBc). To simplify analog filtering, we drive a
push-pull transformer with a 6-state stepped sequence: `+1, +2, +1,
-1, -2, -1`. This weighting theoretically eliminates the 3rd harmonic
and significantly reduces higher-order spurs.

### 2.2 DSP-Hardened Pipeline
The synthesis path is implemented as an 11-cycle pipeline across six
`SB_MAC16` blocks:

1.  **DSP 0 (NCO):** A 32-bit phase accumulator. It calculates `Phase
    = Phase + TuningWord` in a single cycle. Moving this to a DSP
    block eliminates the 32-bit fabric carry chain.
2.  **DSP 1 (Falling Edge Offset):** Calculates the mid-cycle phase
    ($P_F = P_R + TuningWord/2$) to enable 180 Msps DDR output
    generation.
3.  **DSP 2 & 3 (State Multipliers):** Maps the 32-bit phase to the 6
    discrete RF states. It performs $State = \lfloor
    \frac{Phase[31:16] \times 6}{2^{16}} \rfloor$. Using hard
    multipliers eliminates complex fabric-based state machines or
    shift-add logic.
4.  **DSP 4 & 5 (Hardened Comparators):** Handles duty cycle (Power)
    control by performing a subtraction `(FractionalPhase -
    PowerThreshold)`. The sign bit of the result determines if the
    pulse is active within its $60^\circ$ wedge.

### 2.3 DDR Output Stage
The decoded gate drive signals are registered and packed into `SB_IO`
primitives in DDR mode (`PIN_TYPE=6'b011000`). This ensures that the
two samples calculated per 90 MHz clock cycle are precisely aligned to
the rising and falling edges of the physical clock.

---

## 3. SPI Interface Protocol

The SPI control plane is decoupled from the 90 MHz RF synthesis domain
using shadow registers and multi-stage synchronizers to ensure stable
operation.

### 3.1 Frame Format (40-bit)
| Bits | Field | Description |
| :--- | :--- | :--- |
| 39 | **W/nR** | 1 = Write Operation, 0 = Read Operation |
| 38:32 | **Address** | 7-bit Register Address |
| 31:0 | **Data** | 32-bit Data (Payload) |

### 3.2 Register Map
| Address | Name | Type | Description |
| :--- | :--- | :--- | :--- |
| 0x00 | **CONTROL** | R/W | `[31:24]` Power Threshold (0-255)<br>`[23:2]` Reserved<br>`[1]` PLL Locked (RO)<br>`[0]` TX Enable |
| 0x01 | **TUNING** | R/W | 32-bit NCO Tuning Word. $M = \frac{6 \cdot f_{out} \cdot 2^{32}}{f_{clk} \cdot 2}$ |
| 0x03 | **PPS** | RO | `[31:5]` 27-bit Counter latched at GNSS PPS rising edge.<br>`[4:0]` PPS generation counter. |
| 0x0B | **SIGNATURE**| RO | Fixed value `0x52505357` ("WSPR"). |

---

## 4. Frequency Calibration (FreqCounter)

To ensure sub-Hz frequency accuracy, the FPGA includes a high-speed
frequency counter synchronized to the GNSS PPS signal.

*   **Pipelined Counter:** A 28-bit counter implemented in 7-bit
    pipelined stages to meet 90 MHz timing without stall cycles.
*   **PPS Latching:** The rising edge of the GNSS PPS signal latches
    the current counter value.
*   **MCU Integration:** The ESP32 reads the `PPS` register to
    calculate the actual TCXO drift and applies a correction factor to
    the `TUNING` word.

---

## 5. Timing Closure & Routing Discipline

Achieving 90 MHz on the iCE40UP5K requires strict adherence to
hardware-native primitives:

1.  **DSP Hardening:** Moving all 16-bit and 32-bit arithmetic into
    `SB_MAC16` macros is the primary reason the design achieves >120
    MHz Fmax.
2.  **Segmented Logic:** Any fabric-based counters (like the
    FreqCounter) are broken into small bit-fields with registered
    carry-forward bits.
3.  **Synchronizers:** All signals crossing from the SPI domain (12
    MHz) to the system domain (90 MHz) use 2-stage synchronizers to
    prevent metastability.
4.  **IO Packing:** The use of internal `SB_IO` registers removes
    routing-dependent jitter from the RF drive signals.
