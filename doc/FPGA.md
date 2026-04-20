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

### Key Specifications
*   **System Clock:** 90 MHz (11.1 ns period), synthesized from a 40
    MHz TCXO via PLL.
*   **Effective Sample Rate:** 180 Msps (via `SB_IO` DDR).
*   **Modulation:** 1-2-1 stepped amplitude synthesis (6 samples per
    RF cycle).
*   **Timing Margin:** ~35% (Achieved 121 MHz Fmax).
*   **3rd Harmonic Suppression:** ~55 dBc (practically eliminating the
    need for aggressive analog filtering).

---

## Evolution: The Road to 90 MHz

Achieving 90 MHz with robust margin on the iCE40 architecture required
moving away from general-purpose fabric logic for arithmetic.

### 2.1 The Fabric Bottleneck
Initial RTL designs relied on fabric-based 32-bit adders and
multipliers. These failed timing catastrophically (maxing out at ~70
MHz) due to:
1.  **Long Carry Chains:** 32-bit additions require signals to ripple
    through 32 LUTs.
2.  **Routing Delays:** Moving data between the NCO, multipliers, and
    comparators consumed the 11.1 ns budget.

### 2.2 The Pipelining Trap
Attempting to solve this with aggressive pipelining (down to 4-bit
stages) also failed. While pipelining shortens carry chains, the
"entry/exit penalty" of moving signals from `SB_CARRY` hardware into
the routing matrix to reach a register adds 2-3 ns per stage. This
traded carry delay for massive routing delay, barely reaching 88 MHz
and requiring "lucky" placement seeds.

### 2.3 The Breakthrough: 100% DSP Hardening
To achieve production-grade margin, the entire critical path was moved
into the eight `SB_MAC16` DSP blocks. These hard macros operate at
>200 MHz because signals never leave the localized silicon of the DSP
tile. This shift increased the maximum frequency from ~85 MHz to
**121.92 MHz**, providing a 35% timing margin.

---

## Breathing and Dithering

## The Problem: Phase Truncation Limit Cycles (The "Breathing" Bug)
During testing of the 1-2-1 modulated RF output (specifically
`rfPullPeak`), we observed a "breathing" effect where the pulse width
would vary, maintaining an incorrect width for several seconds at a
time before shifting.

This is not an electrical issue or a random logic glitch; we slammed
into a fundamental digital synthesis wall known as a **Phase
Truncation Limit Cycle**.

### The Math Behind the Breathing
At a 90MHz system clock, generating a 9MHz RF output gives us exactly
10 clock cycles per RF period. However, our 1-2-1 synthesis requires
dividing that period into **6 equal states** ($60^\circ$ wedges).
* $10 \div 6 = 1.666$ clock cycles per state.

Because the FPGA can only sample on clock edges, the states map out in
a Bresenham-style sequence of durations (e.g., `[2, 2, 1, 2, 2, 1]`
clock cycles).

Furthermore, our ideal 32-bit tuning word for 9MHz is `429,496,729.6`.
Because we truncate this to an integer (`0x19999999`), we introduce an
incredibly small phase error of ~0.6 counts per clock cycle. It takes
about **13.2 seconds** for this microscopic error to accumulate enough
to drift the grid mismatch by one full state.

Instead of the pulse widths averaging out cycle-by-cycle, the system
gets stuck in a "limit cycle," holding a slightly-too-wide or
slightly-too-narrow pulse for seconds at a time. This manifests as
low-frequency phase modulation (audio-rate jitter) on the RF output.

## The Solution: Phase Dithering
To stop the 13-second breathing and force the pulse widths to average
out over a few cycles, we must break the limit cycle. We achieve this
through **Phase Dithering**.

By adding a fast, pseudo-random number to our phase accumulator just
before we slice it into the 6 states, we intentionally shake the phase
back and forth across the state boundaries.
* **The Result:** The slow, 13-second drift is shattered. The state
  boundaries jitter rapidly on a cycle-by-cycle basis.
* **The RF Impact:** This turns low-frequency phase drift into
  high-frequency broadband phase noise. Because it's pushed to high
  frequencies, our physical RF bandpass filters will effortlessly
  smooth it out into a perfectly clean, average RF wave.

## Implementation: "Zero-Cost" DSP Injection
At 90MHz on the iCE40UP5K, soft-logic (like fabric-based LFSRs and
adders) will destroy our timing closure due to routing sprawl.
Instead, we implement the dither entirely within the hard DSP slices
(`SB_MAC16`), requiring **zero extra fabric logic and zero extra
latency**.

### Step A: The PRNG Generator (DSP 4)
We use a Linear Congruential Generator (LCG) to create pseudo-random
noise. The formula is $X_{n+1} = (X_n \cdot 25173 + 13849)
\pmod{2^{16}}$. This algorithm is entirely multiplication and
addition, making it a perfect fit for a standalone `SB_MAC16` block.

```systemverilog
  // =====================================================================
  // Phase Dither Generator (LCG PRNG in DSP)
  // =====================================================================
  wire [31:0] lcg_out;
  
  // X_next = (X * 25173) + 13849
  SB_MAC16 #(
    .A_REG(1'b1), .B_REG(1'b0), .C_REG(1'b0), .D_REG(1'b0),
    .TOPADDSUB_LOWERINPUT(2'b00), .TOPADDSUB_UPPERINPUT(1'b1), // Top add C
    .BOTADDSUB_LOWERINPUT(2'b00), .BOTADDSUB_UPPERINPUT(1'b1), // Bot add D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // Propagate carry
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11)         // Register output
  ) dsp_prng (
    .CLK(clk90), .CE(1'b1),
    .A(lcg_out[15:0]), .B(16'd25173), // Multiplier (a)
    .C(16'd0), .D(16'd13849),         // Increment (c)
    .O(lcg_out),
    .IRSTTOP(rst_nco), .IRSTBOT(rst_nco), .ORSTTOP(rst_nco), .ORSTBOT(rst_nco)
  );

  // The top 16 bits of an LCG state have the best entropy.
  // We use the full 16-bit state to ensure dither spans the entire fractional
  // remainder of the phase-to-state mapping.
  wire [15:0] noise_r = lcg_out[15:0];
  wire [15:0] noise_f = ~lcg_out[15:0]; 
```

### Step B: Zero-Cost Injection into State Mapping
Currently, our phase-to-state mapping multiplies the upper 16 bits of
phase by 6 (e.g., `State = (Phase * 6) >> 16`).

The `SB_MAC16` block natively calculates $(A \times B) + \{C, D\}$. By
piping our `noise` signals directly into the `D` port (the lower 16
bits) of the existing multipliers (`m_r` and `m_f`), we get the dither
addition for free.

**Crucial Configuration:** We must set
`.TOPADDSUB_CARRYSELECT(2'b10)`. This ensures that when the injected
noise causes the fractional remainder to overflow, the carry bit
correctly propagates up and increments the actual State bits
(`d1[18:16]`).

```systemverilog
  // =====================================================================
  // Multipliers (DSP 2 & 3) with Zero-Cost Dither Injection
  // =====================================================================
  wire [31:0] d1, d2;

  // Rising Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1), .D_REG(1'b1), 
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1), // Top: Mult_High + C
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1), // Bot: Mult_Low + D
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), // MUST propagate carry to State bits
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) 
  ) m_r ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_r_h), .B(16'd6), 
    .C(16'd0), .D(noise_r), // Dither injected into fractional remainder
    .O(d1),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );

  // Falling Edge Mapping
  SB_MAC16 #( 
    .A_REG(1'b1), .B_REG(1'b1), .C_REG(1'b1), .D_REG(1'b1), 
    .TOPADDSUB_LOWERINPUT(2'b10), .TOPADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_LOWERINPUT(2'b10), .BOTADDSUB_UPPERINPUT(1'b1), 
    .BOTADDSUB_CARRYSELECT(2'b00), .TOPADDSUB_CARRYSELECT(2'b10), 
    .TOPOUTPUT_SELECT(2'b11), .BOTOUTPUT_SELECT(2'b11) 
  ) m_f ( 
    .CLK(clk90), .CE(1'b1), 
    .A(ph_f_h), .B(16'd6), 
    .C(16'd0), .D(noise_f), // Dither injected into fractional remainder
    .O(d2),
    .IRSTTOP(1'b0), .IRSTBOT(1'b0), .ORSTTOP(1'b0), .ORSTBOT(1'b0) 
  );
```

## Summary
By offloading the PRNG generation to the 4th DSP block and routing the
noise into the unused `D` ports of our existing multipliers, we
completely resolve the limit cycle bug. The 13-second phase breathing
is eliminated, transforming into easily filtered high-frequency
dither, all while preserving our strict 90MHz timing closure and
leaving fabric logic free.


## RF Synthesis Chain (The Exciter)

The exciter uses a **"1-2-1" stepped modulation scheme** to generate
high-purity RF. By driving a push-pull transformer with specific
stepped amplitude states (`+1, +2, +1, -1, -2, -1`), the 3rd harmonic
is theoretically eliminated (measured at **-54.9 dBc**).

### 3.1 DSP-Hardened Pipeline
The synthesis path is implemented as a 12-cycle pipeline across five
`SB_MAC16` blocks:

1.  **DSP 0 (NCO):** A 32-bit phase accumulator. Calculates `Phase =
    Phase + TuningWord` in a single cycle.
2.  **DSP 1 (Falling Edge Offset):** Calculates the mid-cycle phase
    ($P_F = P_R + TuningWord/2$) for the DDR output.
3.  **DSP 2 & 3 (State Multipliers):** Performs $State = \lfloor
    \frac{Phase[31:16] \times 6}{2^{16}} \rfloor$ to map phase to the
    6 RF states. These blocks also handle **Zero-Cost Dither
    Injection** by adding PRNG noise to the fractional remainder.
4.  **DSP 4 (PRNG Generator):** A Linear Congruential Generator (LCG)
    that produces pseudo-random noise to break phase truncation limit
    cycles.

### 3.2 Duty Cycle & Output Stage
The design currently hardcodes a **100% duty cycle** (always active
within each $60^\circ$ wedge) to maximize power output and minimize
fabric complexity.

The drive signals are registered and packed into `SB_IO` primitives in
DDR mode (`PIN_TYPE=6'b011000`). This ensures samples are precisely
aligned to the rising and falling edges of the physical clock,
eliminating routing-dependent jitter.

### 3.3 Latency
The total pipeline depth from Tuning Word input to physical DDR pin
output is **12 clock cycles**. This latency is constant and
negligible for WSPR operation.

---

## SPI Interface Protocol

The SPI control plane is decoupled from the 90 MHz domain using shadow
registers and multi-stage synchronizers.

### 4.1 Frame Format (40-bit)
| Bits | Field | Description |
| :--- | :--- | :--- |
| 39 | **W/nR** | 1 = Write Operation, 0 = Read Operation |
| 38:32 | **Address** | 7-bit Register Address |
| 31:0 | **Data** | 32-bit Data (Payload) |

### 4.2 Register Map
| Address | Name | Type | Description |
| :--- | :--- | :--- | :--- |
| 0x00 | **CONTROL** | R/W | `[31:2]` Reserved<br>`[1]` PLL Locked (RO)<br>`[0]` TX Enable |
| 0x01 | **TUNING** | R/W | 32-bit NCO Tuning Word. $M = \frac{6 \cdot f_{out} \cdot 2^{32}}{f_{clk} \cdot 2}$ |
| 0x03 | **PPS** | RO | `[31:5]` 27-bit Counter latched at GNSS PPS rising edge.<br>`[4:0]` PPS generation counter. |
| 0x0B | **SIGNATURE**| RO | Fixed value `0x52505357` ("WSPR"). |

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
