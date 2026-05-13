# WSPR-ease FPGA Technical Reference

This document describes the internal architecture, evolution, and SPI
register interface of the iCE40UP5K FPGA used in the WSPR-ease
project.

## Architecture Overview

The FPGA performs real-time RF synthesis and timing measurement. To
support high-purity transmission up to the 10m amateur band (~28 MHz),
the FPGA operates at a **90 MHz** internal clock rate. Utilizing DDR
(Double Data Rate) outputs, the system achieves an effective sample
rate of **180 Msps**.

The gateware was recently rewritten in **Amaranth HDL**, a
Python-based hardware description language. This move enables better
abstraction for complex pipelining and automated register map
generation, which are crucial for meeting the strict 11.1 ns timing
requirement.

### Key Specifications
*   **System Clock:** 90 MHz (11.1 ns period), synthesized from a 40
    MHz TCXO via PLL.
*   **Effective Sample Rate:** 180 Msps (via `SB_IO` DDR).
*   **Modulation:** 1-2-1 stepped amplitude synthesis (6 samples per
    RF cycle).
*   **NCO Resolution:** 48-bit frequency control for sub-millihertz
    accuracy.
*   **Timing Margin:** ~35% (Achieved 121 MHz Fmax).
*   **3rd Harmonic Suppression:** ~55 dBc (practically eliminating the
    need for aggressive analog filtering).

---

## Evolution: The Road to 90 MHz

Achieving 90 MHz with robust margin on the iCE40 architecture required
moving away from general-purpose fabric logic for arithmetic and
adopting a highly pipelined design.

### 2.1 The Fabric Bottleneck
Initial RTL designs relied on fabric-based 32-bit adders and
multipliers. These failed timing catastrophically (maxing out at ~70
MHz) due to long carry chains and routing delays.

### 2.2 The Breakthrough: Amaranth and Pipelining
The move to Amaranth allowed us to implement a **Pipelined NCO**. By
splitting the 48-bit phase addition into 6 stages of 8-bit chunks, we
reduced the carry chain length, allowing the logic to close timing at
90 MHz with significant margin.

Furthermore, we utilize the eight `SB_MAC16` DSP blocks for all
multiplication and phase-to-state mapping. These hard macros operate
at >200 MHz, providing production-grade timing margin.

---

## RF Synthesis Chain (The Exciter)

The exciter uses a **"1-2-1" stepped modulation scheme** to generate
high-purity RF. By driving a push-pull transformer with specific
stepped amplitude states (`+1, +2, +1, -1, -2, -1`), the 3rd harmonic
is theoretically eliminated (measured at **-54.9 dBc**).

### 3.1 DSP-Hardened Pipeline
The synthesis path is implemented as a pipeline across several
`SB_MAC16` blocks:

1.  **Pipelined NCO:** A 48-bit phase accumulator split into 6 stages.
2.  **Phase to State Multipliers:** Performs $State = \lfloor
    \frac{Phase[47:32] \times 6}{2^{16}} \rfloor$ using `SB_MAC16`
    hard macros.
3.  **PRNG Generator:** A Linear Congruential Generator (LCG)
    implemented in a DSP block to produce pseudo-random noise for
    phase dithering.

### 3.2 Phase Dithering
To eliminate "phase truncation breathing" (limit cycles), we inject
pseudo-random noise into the fractional remainder of the state mapping.
This turns low-frequency phase drift into high-frequency broadband noise
that is easily filtered by the RF bandpass filters.

---

## Pipeline Tick Pseudocode

```python
	def exciterPipelineTick(ncoPhase, prngNoise):
		# =======================================================
		# TICK 0: The Initial State
		# =======================================================
		ncoPhase = ncoPhase				# phaseR(Tick0)
		prngNoise = prngNoise			# noise(Tick0)
		ddrOffset = (tw >> 1)			# 0.5 cycle offset

		# =======================================================
		# TICK 1: First Stage Math (Multiplier & Offset)
		# =======================================================

		# 1. Rising Edge Math (mr DSP)
		# The multiplier takes 1 clock cycle. 
		mulRT1 = ncoPhase * 6 + prngNoise

		# 2. Noise Delay Registration
		# Invert the noise and hold it for the falling edge math
		noiseD1invT1 = ~prngNoise

		# 3. Falling Edge Offset (offs DSP)
		phaseFT1 = ncoPhase * 1 + ddrOffset 

		# =======================================================
		# TICK 2: Falling Edge Math & Rising Edge Wait State
		# =======================================================

		# 1. Falling Edge Math (mf DSP)
		# Now that phaseFT1 and noiseD1invT1 are ready, compute the falling edge.
		mulFT2 = phaseFT1 * 6 + noiseD1invT1
		stateFraw = mulFT2[16:19]  # 3-bit state extracted

		# 2. Rising Edge Wait State 1
		# Because the Falling Edge took an extra clock cycle to compute the offset,
		# the Rising Edge must be delayed to wait for it.
		stateRraw = mulRT1[16:19]
		stateRD1T2 = stateRraw     # Latch it into stateRD1

		# =======================================================
		# TICK 3: Final Alignment to DDR Pins
		# =======================================================

		# Rising edge state finishes waiting
		stateRfinal = stateRD1T2

		# Falling edge state arrives
		stateFfinal = stateFraw

		# RESULT: Both states map to the DDR pins on the exact same 90MHz clock tick!
		return (stateRfinal, stateFfinal)
```

## SPI Interface Protocol

The SPI control plane is implemented using oversampling on the 90 MHz
system clock, ensuring robust operation without complex Clock Domain
Crossing (CDC) constraints.

### 4.1 Frame Format (40-bit)
| Bits | Field | Description |
| :--- | :--- | :--- |
| 39 | **W/nR** | 1 = Write Operation, 0 = Read Operation |
| 38:32 | **Address** | 7-bit Register Address |
| 31:0 | **Data** | 32-bit Data (Payload) |

### 4.2 Register Map
| Address | Name | Type | Description |
| :--- | :--- | :--- | :--- |
| 0x00 | **CONTROL** | R/W | `[31:3]` Reserved<br>`[2]` PLL Locked (RO)<br>`[1]` Square Wave Mode<br>`[0]` TX Enable |
| 0x01 | **TUNING_LOW**| R/W | Lower 32 bits of 48-bit NCO Tuning Word. |
| 0x02 | **TUNING_HIGH**| R/W | Upper 16 bits of 48-bit NCO Tuning Word. |
| 0x03 | **PPS** | RO | `[31:5]` 27-bit Counter latched at PPS rising edge.<br>`[4:0]` PPS generation counter. |
| 0x0F | **SIGNATURE**| RO | Fixed value `0x52505357` ("WSPR"). |

---

## Frequency Calibration (FreqCounter)

To ensure sub-Hz accuracy, the FPGA includes a high-speed frequency
counter synchronized to the GNSS PPS signal.

*   **Pipelined Counter:** A 28-bit counter implemented in 7-bit
    pipelined stages to meet 90 MHz timing.
*   **PPS Latching:** The rising edge of the GNSS PPS signal latches
    the counter value.
*   **MCU Integration:** The ESP32 reads the `PPS` register to
    calculate TCXO drift and corrects the `TUNING` word.

---

## Timing Closure & Constraints

1.  **DSP Priority:** All 16-bit and 32-bit arithmetic must remain in
    `SB_MAC16` macros.
2.  **CDC Handling:** `nextpnr-ice40` lacks `set_false_path`. To
    prevent false timing failures on Clock Domain Crossing (CDC) from
    the 12 MHz SPI clock, we "fake" the SPI clock frequency to 0.1 MHz
    in constraints, forcing the router to prioritize the 90 MHz
    internal logic.
3.  **IO Packing:** Always use `SB_IO` internal registers for RF
    signals.
