# WSPR-ease FPGA Technical Reference

This document outlines the "Path B" digital RF architecture for the
WSPR-ease transmitter. It completely eliminates the traditional 32-bit
Numerically Controlled Oscillator (NCO) phase accumulator inside the
FPGA.

By delegating frequency synthesis entirely to the Si5351 and utilizing
the FPGA strictly as a **Phase-Interpolated Ring Counter**, we
achieve:

1. **Zero "Pulse-Swallowing" Jitter:** The carrier edges are
   mathematically perfect and tied directly to the Si5351's analog
   PLL.
2. **Infinite SSB Phase Resolution:** We retain 16-bit phase
   modulation capability for Polar/SSB transmission.
3. **Flawless Timing Closure:** The 32-bit carry chains are
   eliminated. The FPGA logic tops out at fast 16-bit additions,
   easily closing timing at 150+ MHz.

> [!IMPORTANT] **This is an Amaranth HDL-based design.** All source
> logic is written in Python using Amaranth HDL (see
> [gateware.py](file:///home/alan/ham/WSPR-ease/FPGA/gateware.py)).
> The SystemVerilog code is not written directly; it is generated as
> an intermediate build artifact (under the `gen-hw` and `gen-sim`
> directories) during the build process. Do not modify the
> SystemVerilog files directly; edits should always be made in
> [gateware.py](file:///home/alan/ham/WSPR-ease/FPGA/gateware.py).


## 2. System Division of Labor

To understand the architecture, the developer must strictly separate
*Frequency* from *Phase*.

* **The Si5351 (Frequency Generator):** The microcontroller
  (ESP32/RP2040) drives the Si5351 via I2C. The Si5351 outputs a
  high-speed clock that is a direct, static multiple of the target
  transmit frequency ($F_{tx}$). All sub-Hz WSPR FSK frequency
  shifting is handled by the Si5351's internal glitchless shadow
  registers.
* **The iCE40UP5K FPGA (Phase Modulator):** The FPGA receives this
  high-speed clock. It divides the clock down to generate the RF
  waveform states (1-2-1 or Square Wave), while simultaneously
  injecting Delta-Sigma dither and SPI-driven Phase Modulation (PM).

## 3. The Mathematical Foundation: Clock Multipliers

To perform clean Delta-Sigma phase dithering, the FPGA needs "temporal
headroom." Mathematically, it requires exactly **2.5 clock ticks per
output state** to dither edges without collapsing the waveform.

To satisfy this physics constraint across all HF bands while staying
under the FPGA's ~120 MHz comfortable routing limit, the system uses
two discrete modes based on a **10 MHz crossover rule**:

| Mode | Freq Range | Waveform | States / Cycle | Si5351 Clock Mult | Ticks / State | Max FPGA Clock |
| --- | --- | --- | --- | --- | --- | --- |
| **Low Bands** | < 10 MHz | 1-2-1 Emission | 6 | **15x** | 2.5 | 109.5 MHz (40m) |
| **High Bands** | $\ge$ 10 MHz | Square Wave | 2 | **5x** | 2.5 | 148.5 MHz (10m) |

*Note: For the 10m band at 28.0 MHz, $5 \times 28.0 = 140 \text{
MHz}$. At 140 MHz, the iCE40 DSP blocks and simple 16-bit adders can
still pass timing closure, whereas a 32-bit accumulator would fail.*

---

## 4. The 4-Stage FPGA Datapath

This is the core pipeline to be implemented in Amaranth HDL. It must
be heavily pipelined (one register stage per step) to guarantee timing
closure at $>140\text{ MHz}$.

### Stage 1: The Rigid Ring Counter

Instead of an accumulator adding a tuning word, we use a simple
integer counter that wraps based on the multiplier $N$.

* **If $N=15$ (Low Bands):** Counter increments by 1 every clock tick.
  Wraps to 0 after 14.
* **If $N=5$ (High Bands):** Counter increments by 1 every clock tick.
  Wraps to 0 after 4.

**Developer Note:** Because this counter rigidly divides the Si5351
clock without any fractional skipping, the base carrier has absolutely
zero digital jitter.

### Stage 2: The Fractional Angle LUT

To apply phase modulation, we must convert the integer counter state
back into a 16-bit fractional phase space ($0$ to $65535$,
representing $0^\circ$ to $360^\circ$).

This is done via a hardcoded Lookup Table (LUT). The LUT value
represents the base phase of the carrier at that exact clock tick.

* **Math:** `Step_Size = 65536 / N`
* **For 15F:** Step Size is `4369`. (LUT: `0, 4369, 8738, 13107...
  61166`)
* **For 5F:** Step Size is `13107`. (LUT: `0, 13107, 26214, 39321,
  52428`)

**Developer Note:** In Amaranth, use an `Array` or a `Case` statement.
This synthesizes to a fast block of combinatorial logic (LUT4s) that
resolves in a single gate delay.

### Stage 3: The Phase Modulation & Dither Adder

Here we inject the sub-degree phase control from the microcontroller
and the dithering noise to smooth the quantization steps.

```python
# 16-bit addition (allow it to naturally overflow/wrap)
Total_Phase_16b = Base_Phase_LUT + SPI_Phase_Offset_16b + LFSR_Dither_16b

```

* **SPI Phase Offset:** Received from the ESP32. Allows for SSB
  transmission (Polar Modulation).
* **LFSR Dither:** A 16-bit Pseudo-Random Number Generator.
* **Zero-Mean Dither Rule:** Ensure the noise is treated as a *signed*
  offset (or shifted accordingly) so it dithers symmetrically around
  the targeted phase edge, rather than constantly pushing the phase
  forward. Scale the volume of the noise (e.g., right-shift by 4) so
  it only chatters the edge, rather than jumping entire states.

### Stage 4: The DSP State Mapper (SB_MAC16)

We now take the 16-bit `Total_Phase` and map it to the physical
transformer pin states. Because multiplying by 6 is expensive in
fabric, we route this directly into the iCE40's hard `SB_MAC16` DSP
blocks.

* **For 1-2-1 Emission (15F Mode):**
* We need to divide the 16-bit space into 6 states ($0$ through $5$).
* Math: `(Total_Phase_16b * 6) >> 16`
* The DSP multiplies the phase by 6. We extract the top 3 bits of the
  32-bit DSP output to get states `000` to `101`.


* **For Square Wave (5F Mode):**
* We need to divide the space into 2 states ($0$ and $1$).
* Math: `(Total_Phase_16b * 2) >> 16`
* The DSP multiplies by 2. We extract the top 1 bit to get state `0`
  or `1`.



**Output Decoding:** Finally, standard combinatorial logic maps the
resulting state (0-5 or 0-1) to the 4 physical DDR output pins
(`PushBase`, `PushPeak`, `PullBase`, `PullPeak`).

---

## 5. Implementation & Synthesis Guidelines

### Eliminating the `SB_MAC16` C/D Port Trap

When configuring the `SB_MAC16` instance in Amaranth:

* Do NOT pass your `SPI_Phase_Offset` or `LFSR_Dither` directly into
  the `C` or `D` accumulation ports of the DSP block.
* Perform the 16-bit addition (Stage 3) entirely in the standard FPGA
  logic fabric.
* Only pass the finalized `Total_Phase_16b` into the `A` port, and the
  constant `6` or `2` into the `B` port of the DSP. This avoids the
  simulation mismatches and pipeline latency bugs documented in the
  project logs.

### Pipeline Register Strategy

To achieve the 148.5 MHz timing required for the 10m band, you must
insert an `m.d.sync` register between *every* stage of this datapath:

1. `Tick_Counter` (Reg)
2. `Base_Phase_LUT` (Reg)
3. `Total_Phase_16b` (Reg)
4. `DSP_Multiplier_Output` (Use internal `SB_MAC16` output registers)

Total pipeline latency from clock tick to pin state will be 4 clock
cycles, which is completely irrelevant to the RF transmission, but
guarantees flawless routing.

### Double Data Rate (DDR) Output

Because the Si5351 clock is running at $N \times F_{tx}$, you DO NOT
need to calculate a "lookahead" phase for the falling edge of the
clock (as was required in the old 90 MHz asynchronous architecture).

* Use the rising edge to output the calculated state.
* The physical output pins will update exactly at the $15F$ or $5F$
  intervals.

## 6. Conclusion

By adopting the Phase-Interpolated Ring Counter, the WSPR-ease project
resolves the fundamental paradox of digital RF synthesis. It leverages
the Si5351 for what it does best (glitchless, jitter-free frequency
generation) and leverages the iCE40 FPGA for what it does best
(high-speed parallel phase manipulation and state mapping). The result
is a mathematically pure, harmonic-canceling transmitter that scales
effortlessly from 80m to 10m.
