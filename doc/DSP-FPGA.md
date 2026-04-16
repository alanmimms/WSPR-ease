Here is the comprehensive markdown documentation detailing the
architectural shift. You can save this as `docs/DSP_Architecture.md`
or append it to your existing technical reference.

***

# WSPR-ease FPGA: DSP-Centric RF Synthesis Architecture

## 1. Motivation and Background
The WSPR-ease FPGA requires a stable 90 MHz internal clock to produce
180 Msps Double Data Rate (DDR) output on the RF pins.

The legacy architecture relied on a fabric-based 32-bit phase
accumulator. Because a standard 32-bit carry chain cannot reliably
close timing at 90 MHz on the iCE40UP5K due to routing delays, the
accumulator was aggressively sliced into 11 pipelined 4-bit stages.
While this technically achieved ~94 MHz, the timing closure remained
highly fragile and sensitive to unrelated routing congestion.
Furthermore, mapping the NCO phase to the 6 discrete RF states
required a predictive overflow walking-ring counter, adding
combinatorial complexity.

**The Solution:** A DSP-centric architecture. By utilizing the
iCE40UP5K's built-in `SB_MAC16` hard macros, we can eliminate the
fragile fabric logic, drastically reduce the pipeline depth (from 11
stages to 2), and calculate the exact RF phase state deterministically
via multiplication rather than state machines.

---

## 2. Core Architecture: The DSP Approach

### 2.1 The Two-Stage 32-bit Phase Accumulator
Instead of 4-bit segments, the 32-bit NCO is split into two 16-bit
stages. A 16-bit carry chain easily meets the 11.1 ns (90 MHz) timing
requirement because it fits cleanly within adjacent logic clusters,
minimizing global routing.

* **Stage 1 (LSB):** Computes $Acc_{L}[n] = Acc_{L}[n-1] + M_{L}$. The
  16-bit result and the Carry-Out are registered.
* **Stage 2 (MSB):** Computes $Acc_{H}[n] = Acc_{H}[n-1] + M_{H} +
  Carry$.

This produces a coherent, highly stable 32-bit phase vector
($P_{base}$) representing the full $0^\circ$ to $360^\circ$
fundamental frequency $F$.

### 2.2 Phase-to-State Mapping (The Multiplier Magic)
The $360^\circ$ phase circle must be divided into 6 discrete wedges
($60^\circ$ each) to generate the 1-2-1 weighted RF drive signals.
Mathematically, the State (0 to 5) is: $State = \lfloor \frac{P_{base}
\cdot 6}{2^{32}} \rfloor$

Since fabric division is inefficient, we utilize the `SB_MAC16` block
as a $16 \times 16$ multiplier. By multiplying the top 16 bits of the
phase by the constant 6: $$DSP_{out} = P_{base}[31:16] \times 6$$

* The output is 19 bits wide.
* **Bits** deterministically yield the current state (0 through 5).
  This directly feeds the pin decoder, completely eliminating the
  walking ring.
* **Bits [15:0]** represent the fractional phase progression *within*
  the current $60^\circ$ wedge.

### 2.3 Free Duty Cycle / Power Control
The fractional phase bits provide a zero-cost method for transmit
power reduction. To insert dead-time into the RF waveform, we simply
compare the upper byte of the fractional phase (`DSP_out[15:8]`)
against the SPI-provided `powerThreshold`. If the fractional phase
exceeds the threshold, the RF output pins are pulled low.

---

## 3. Handling DDR Output (The Falling Edge)

To achieve 180 Msps, the FPGA must calculate the state for both the
rising ($P_R$) and falling ($P_F$) edges of the 90 MHz clock.
* $P_R = P_{base}$
* $P_F = P_{base} + M/2$

We will dedicate two `SB_MAC16` blocks (out of the 8 available) to
compute the state multiplication: one for the Rising phase, one for
the Falling phase. However, calculating $P_F$ presents an
architectural choice. We are evaluating two options to determine which
yields the best routing margin in `nextpnr`.

### Option A: The Fabric Pipeline (Inline Addition)
In this approach, the $M/2$ addition is handled by standard fabric
logic, structured identically to the base NCO.

1.  **Architecture:** A secondary 2-stage (16-bit) pipelined adder
    runs parallel to the main NCO. It takes $P_{base}$ and adds $M \gg
    1$.
2.  **Pros:** Leaves more DSP blocks free. Very predictable pipeline
    matching.
3.  **Cons:** Consumes more fabric Logic Cells (LCs) and routing
    resources to carry the 32-bit $P_{base}$ vector into the second
    adder.

### Option B: The DSP Offset (Zero-Routing Addition)
In this approach, we move the falling edge addition out of the fabric
entirely by utilizing a third `SB_MAC16` block configured purely as a
16-bit adder.

Because the state mapping only cares about the top 16 bits of the NCO,
we only need to accurately calculate $P_F[31:16]$.
1.  **Architecture:** We feed $P_{base}[31:16]$ and $(M/2)[31:16]$
    directly into the input registers of DSP Block 3. The DSP performs
    the addition, and the output is fed into the Falling Edge
    multiplier (DSP Block 2).
2.  **Pros:** Drastically reduces fabric routing congestion. The delay
    between the adder DSP and the multiplier DSP is handled over
    dedicated fast-tracks on the die.
3.  **Cons:** Consumes an extra DSP block (total of 3). Requires
    careful handling of the carry-bit from the lower 16 bits of $M/2$
    if strict sub-degree phase accuracy is required on the falling
    edge (though truncation here is generally acceptable for RF
    mapping).

---

## 4. Implementation Next Steps

1.  **Remove legacy files:** Deprecate the 11-stage segmented adder
    and predictive overflow logic.
2.  **Synthesize NCO:** Implement the 2-stage 16-bit fabric NCO.
3.  **Test Option A vs B:** Write both falling-edge logic paths and
    run them through `yosys` and `nextpnr-ice40 --freq 90` to observe
    the `Max frequency` and `Slack` reports.
4.  **Implement DDR I/O:** Feed the states from the two `SB_MAC16`
    multipliers into a simple combinational LUT to drive the `SB_IO`
    Double Data Rate registers.
