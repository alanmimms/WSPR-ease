# RTL for the WSPR-ease FPGA.
# Designed for Lattice iCE40UP5K-SG48.

from amaranth import *
from amaranth.lib import enum, data, cdc, wiring
from amaranth.lib.wiring import In, Out
from amaranth.lib.data import StructLayout
from dataclasses import dataclass
from functools import partial
from types import SimpleNamespace

# Alias for Signal that defaults to reset_less=True
SignalR = partial(Signal, reset_less=True)


# I hate Python's sequence syntax and Amaranth's stupid wrong-ordered Cat().
def VCat(*signals):
    """
    Verilog-style Concatenation: {MSB, ..., LSB}
    Takes arguments from MSB down to LSB and packs them correctly.
    """
    # Amaranth's Cat() wants LSB to MSB, so we just reverse your inputs.
    return Cat(*reversed(signals))

def VSlice(signal, msb, lsb):
    """
    Verilog-style Slicing: signal[msb : lsb]
    Uses inclusive boundaries, ordered from MSB down to LSB.
    """
    # Amaranth wants [start:stop] where start is LSB, stop is exclusive MSB.
    return signal[lsb : msb + 1]


@dataclass
class SPIRegister:
    address: int
    name: str
    layout: StructLayout
    # You can easily add read_only: bool, default_val: int, etc.

# Define our SPI register set
R = SimpleNamespace()
R.Control = SPIRegister(
    address = 0x00,
    name = "Control",
    layout = StructLayout({
        "txEnable":  1,         # Enable RF output
        "modeSquare": 1,        # Enable pure square wave mode
        "softReset": 1          # Soft reset for internal state machines
    })
)

R.PhaseDelay = SPIRegister(
    address = 0x01,
    name = "PhaseDelay",
    layout = StructLayout({
        "baseDelay": 8,         # Base delay in I2S sample periods
        "delayCoeff": 8,        # Dynamic delay coefficient
        "paEnThreshold": 16     # Threshold for paEn output
    })
)

R.PPSCounter = SPIRegister(
    address = 0x02,
    name = "PPSCounter",
    layout = StructLayout({
        "val": 32
    })
)

# These are always at these addresses
R.Build = SPIRegister(
    address = 0x7E,
    name = "Build",
    layout = StructLayout({
        "val": 32
    })
)

R.Signature = SPIRegister(
    address = 0x7F,
    name = "Signature",
    layout = StructLayout({
        "val": 32
    })
)


# This exports the defined registers in R to the specified file as a
# C++ packed struct and defines the addresses as well.
def exportRegs(filename: str):

    with open(filename, "w") as f:
        f.write("#pragma once\n")

        for name, item in vars(R).items():
            address = item.address
            layout = item.layout
            f.write(f"struct __attribute__((packed)) {name} {{\n")
            f.write(f"  static constexpr unsigned ADDRESS = 0x{address:02X};\n")

            for fieldName, shape in layout.members.items():
                f.write(f"  unsigned {fieldName}: {shape};\n")
            
            f.write("};\n\n")


exportRegs("foo.hpp")


class SPIRegisters(wiring.Component):
    # SPI Bus Pins
    iSCK: In(1)
    iMOSI: In(1)
    oMISO: Out(1)
    iNCS: In(1)

    # Decoded outputs to other clock domains
    txEn: Out()
    modeSq: Out()
    softReset: Out()
    modMode: Out(2)
    baseDelay: Out(8)
    delayCoeff: Out(8)
    paEnThreshold: Out(16)

    def elaborate(self, platform):
        m = Module()
        
        # Synchronize SPI inputs to tcxo clock domain
        sckS = Signal()
        mosiS = Signal()
        csS = Signal()

        m.submodules += [
            cdc.FFSynchronizer(self.iSCK, sckS),
            cdc.FFSynchronizer(self.iMOSI, mosiS),
            cdc.FFSynchronizer(~self.iNCS, csS)
        ]

        # Edge detection for SCK
        sckPrev = Signal()
        m.d.sync += sckPrev.eq(sckS)
        sckRise = Signal()
        sckFall = Signal()
        m.d.comb += [
            sckRise.eq(~sckPrev & sckS),
            sckFall.eq(sckPrev & ~sckS)
        ]

        # SPI State
        # We only need to buffer the last 39 bits, but that is a
        # stupid optimization that makes debugging and visualization
        # difficult so we always fill up all 40 bits.
        mosiShift = SignalR(40)
        misoShift = SignalR(32)
        bitCnt = Signal(6)

        addr = SignalR(7)
        isWrite = SignalR()
        
        # Handle CS reset asynchronously to SPI transaction, but synchronous to 40MHz
        with m.If(csS):

            m.d.sync += [
                bitCnt.eq(0),
                self.oMISOR.eq(0)
            ]

        with m.Else():
            
            # ----------------------------------------------------
            # 1. RISING EDGE: Sample Data and Latch Commands
            # ----------------------------------------------------
            with m.If(sckRise):

                # Serialize in the command in MSb first order
                m.d.sync += [
                    mosiShift.eq((mosiShift << 1) | mosiS),
                    bitCnt.eq(bitCnt + 1)
                ]

                # --- The Read Pipeline Trigger (End of Address Byte) ---
                with m.If(bitCnt == 7):
                    m.d.sync += [
                        addr.eq(Cat(mosiS, mosiShift[0:6])),
                        isWrite.eq(mosiShift[6])
                    ]

                # --- The Write Trigger (End of Data Bytes) ---
                with m.If(bitCnt == 39):
                    with m.If(isWrite):
                        # Extract the 32 LSBs holding our payload
                        writeData = VCat(VSlice(mosiShift, 31, 0), mosiS)
                        
                        # Direct dictionary-style mapping for writes
                        with m.Switch(addrR):

                            with m.Case(FPGAAddr.Control):

                                m.d.sync += [
                                    self.txEn.eq(writeData[0]),
                                    self.modeSq.eq(writeData[1]),
                                    self.softReset.eq(writeData[2]),
                                    self.modMode.eq(VSlice(writeData, 4, 3))
                                ]

                            with m.Case(FPGAAddr.PhaseDelayCtrl):

                                m.d.sync += [
                                    self.baseDelay.eq(VSlice(writeData, 7, 0)),
                                    self.delayCoeff.eq(VSlice(writeData, 15, 8)),
                                    self.paEnThreshold.eq(VSlice(writeData, 31, 16))
                                ]

            # ----------------------------------------------------
            # 2. FALLING EDGE: Shift Out MISO Data
            # ----------------------------------------------------
            with m.If(sckFall):
                with m.If(bitCnt == 8):
                    # 8th falling edge: Load the pipeline and output the first bit immediately
                    m.d.sync += misoShift.eq(pipelined_read_data << 1)
                    m.d.sync += self.oMISOR.eq(pipelined_read_data[31])
                with m.Elif(bitCnt > 8):
                    # Shift out remaining bits
                    m.d.sync += misoShift.eq(misoShift << 1)
                    m.d.sync += self.oMISOR.eq(misoShift[31])


# ----------------------------------------------------
        # 3. THE PIPELINE: Breaking the 38ns Read Bottleneck
        # ----------------------------------------------------
        
        # Step A: Isolate cross-chip routing from the MUX logic.
        # This flip-flop catches the GNSS counter value as soon as it arrives at the SPI module,
        # spending our 25ns budget entirely on the vertical cross-chip wire journey.
        ppsLocal = SignalR(32)
        m.d.sync += ppsLocal.eq(self.ppsR)

        # Step B: The Local MUX
        # Now the MUX only evaluates signals that are physically co-located.
        with m.Switch(addr):
            with m.Case(FPGAAddr.Control): 
                m.d.comb += internal_read_mux.eq(Cat(self.txEn, self.modeSq, self.softReset, self.modMode, C(0, 27)))
            with m.Case(FPGAAddr.PhaseDelayCtrl):
                m.d.comb += internal_read_mux.eq(Cat(self.baseDelay, self.delayCoeff, self.paEnThreshold))
            with m.Case(FPGAAddr.PpsLatch):
                m.d.comb += internal_read_mux.eq(ppsLocal) # <-- USE THE LOCAL REGISTER HERE
            with m.Case(FPGAAddr.BuildNo):
                m.d.comb += internal_read_mux.eq(buildNum)
            with m.Case(FPGAAddr.Sig):
                m.d.comb += internal_read_mux.eq(0x57535052) # "WSPR" Signature
            with m.Default():
                m.d.comb += internal_read_mux.eq(0)

        # Step C: The timing firewall (already in your code)
        m.d.sync += pipelined_read_data.eq(internal_read_mux)

        # Map combinatorial pin
        m.d.comb += self.oMISO.eq(self.oMISOR)

        return m

class Top(Elaboratable):
    fpgaSCK: In(1)
    txCLK: In(1)
    tcxo: In(1)
    gnssPPS: In(1)

    def __init__(self, sim=False, buildNum=0):
        self.sim = sim
        
        # Clock Inputs
        self.fpgaSCK = Signal()     # Pin 15
        self.txCLK = Signal()         # Pin 35 (from Si5351 CLK0, modulated carrier clock)
        self.tcxo = Signal()            # Pin 44 (from Si5351 CLK1, 40MHz TCXO clock)
        self.gnssPPS = Signal()         # Pin 25 (GNSS 1PPS calibration pulse)
        
        # SPI Bus Pins
        self.fpgaMOSI = Signal()         # Pin 17
        self.fpgaMISO = Signal()         # Pin 14
        self.fpgaNCS = Signal()          # Pin 16
        
        # I2S Audio Bus Inputs (from ESP32)
        self.txBCLK = Signal()          # Pin 10
        self.txSYNC = Signal()          # Pin 9
        self.txI2Sdata = Signal()       # Pin 6
        
        # RF Exciter & Control Outputs
        self.rfPushPeak = Signal()      # Pin 37 (Active-low gate output)
        self.rfPushBase = Signal()      # Pin 42 (Active-low gate output)
        self.rfPullPeak = Signal()      # Pin 48 (Active-low gate output)
        self.rfPullBase = Signal()      # Pin 45 (Active-low gate output)
        self.paEn = Signal()            # Pin 47 (PA gate drive enable output)
        self.amPWM = Signal()           # Pin 46 (Amplitude tracking PWM/PDM output)
        
        # Debug Pads
        self.padA = Signal()            # Pin 4
        self.padB = Signal()            # Pin 3
        
        # Reset without reconfiguration
        self.fpgaNRESET = Signal()      # Pin 36

    def getPorts(self):
        return [
            self.fpgaSCK,
            self.txCLK,
            self.tcxo,
            self.gnssPPS,
            self.fpgaMOSI,
            self.fpgaMISO,
            self.fpgaNCS,
            self.txBCLK,
            self.txSYNC,
            self.txI2Sdata,
            self.rfPushPeak,
            self.rfPushBase,
            self.rfPullPeak,
            self.rfPullBase,
            self.paEn,
            self.amPWM,
            self.padA,
            self.padB,
            self.fpgaNRESET
        ]

    def elaborate(self, platform):
        m = Module()

        # Set debug pads to logic 0
        m.d.comb += [
            self.padA.eq(0),
            self.padB.eq(0)
        ]

        # ========================================================
        # 1. Housekeeping Clock: 40 MHz TCXO (tcxo) -> sync Domain
        # ========================================================
        # Route 40 MHz TCXO through a global buffer block for low-skew sync clock domain
        tcxoGB = Signal()
        m.submodules.tcxoGBbuf = Instance(
            "SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.tcxo,
            o_GLOBAL_BUFFER_OUTPUT=tcxoGB
        )
        
        m.domains.sync = ClockDomain("sync")
        m.d.comb += ClockSignal("sync").eq(tcxoGB)

        # ========================================================
        # 2. Variable Exciter Clock: Si5351 CLK0 -> txClk Domain
        # ========================================================
        # Route Si5351 CLK0 through a global buffer block for the high-frequency RF domain
        txClkGB = Signal()
        m.submodules.txClkGBbuf = Instance(
            "SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.txCLK,
            o_GLOBAL_BUFFER_OUTPUT=txClkGB
        )
        
        m.domains.txClk = ClockDomain("txClk", reset_less=True)
        m.d.comb += ClockSignal("txClk").eq(txClkGB)

        # ========================================================
        # 3. SPI Registers (Runs in 40MHz sync domain)
        # ========================================================
        spi = SPIRegisters(buildNum=buildNum)
        m.submodules.spi = spi

        m.d.comb += [
            spi.iSCK.eq(self.fpgaSCK),
            spi.iMOSI.eq(self.fpgaMOSI),
            self.fpgaMOSI.eq(spi.oMISOR),
            spi.iNCS.eq(self.fpgaNCS)
        ]

        # SPI Soft Reset + Hardware /fpgaNRESET input synchronisation
        nresetSync = Signal()
        m.submodules.nresetSyncMod = cdc.FFSynchronizer(self.fpgaNRESET, nresetSync)
        
        softResetComb = Signal()
        m.d.comb += softResetComb.eq(spi.softReset | ~nresetSync)
        
        softReset = Signal()
        m.d.sync += softReset.eq(softResetComb)

        # ========================================================
        # 4. PPS Calibration Counter (Runs in 40MHz sync domain)
        # ========================================================
        ppsCount = Signal(32)
        ppsRR = Signal(32)
        
        # Synchronise GNSS 1PPS to 40MHz
        ppsSync = Signal()
        m.submodules.ppsSyncMod = cdc.FFSynchronizer(self.gnssPPS, ppsSync)
        
        lastPPS = Signal()
        m.d.sync += lastPPS.eq(ppsSync)
        
        ppsRising = Signal()
        m.d.comb += ppsRising.eq(ppsSync & ~lastPPS)
        
        m.d.sync += ppsCount.eq(ppsCount + 1)
        with m.If(ppsRising):
            m.d.sync += ppsRR.eq(ppsCount)
            
        m.d.comb += spi.ppsR.eq(ppsRR)

        # ========================================================
        # 5. I2S Receiver (Oversampled in 40MHz sync domain)
        # ========================================================
        bclkSync = Signal()
        syncSync = Signal()
        dataSync = Signal()
        
        m.submodules += [
            cdc.FFSynchronizer(self.txBCLK, bclkSync),
            cdc.FFSynchronizer(self.txSYNC, syncSync),
            cdc.FFSynchronizer(self.txI2Sdata, dataSync)
        ]
        
        bclkPrev = Signal()
        lastSync = Signal()
        m.d.sync += [
            bclkPrev.eq(bclkSync),
            lastSync.eq(syncSync)
        ]
        
        bclkRising = Signal()
        m.d.comb += bclkRising.eq(bclkSync & ~bclkPrev)
        
        syncChanged = Signal()
        m.d.comb += syncChanged.eq(syncSync ^ lastSync)
        
        bitCount = Signal(6)
        shifterR = Signal(16)
        amVal = Signal(16)
        phaseVal = Signal(16)
        
        phaseUpdated = Signal()
        
        with m.If(syncChanged):
            m.d.sync += bitCount.eq(0)
        with m.Else():
            with m.If(bclkRising):
                m.d.sync += bitCount.eq(bitCount + 1)
                with m.If((bitCount >= 1) & (bitCount <= 16)):
                    m.d.sync += shifterR.eq((shifterR << 1) | dataSync)
                with m.If(bitCount == 16):
                    # Copy out shifted word. Sync low is Left (AM), High is Right (Phase)
                    with m.If(~lastSync):
                        m.d.sync += amVal.eq((shifterR << 1) | dataSync)
                    with m.Else():
                        m.d.sync += [
                            phaseVal.eq((shifterR << 1) | dataSync),
                            phaseUpdated.eq(~phaseUpdated)
                        ]

        # ========================================================
        # 6. Dynamic Phase Delay Line (Runs in 40MHz sync domain)
        # ========================================================
        phase_history = [Signal(16, name=f"phase_history_{i}") for i in range(16)]
        phaseUpdatedPrev = Signal()
        m.d.sync += phaseUpdatedPrev.eq(phaseUpdated)
        
        # Shift on Right channel I2S frame updates
        with m.If(phaseUpdated ^ phaseUpdatedPrev):
            m.d.sync += phase_history[0].eq(phaseVal)
            for i in range(15):
                m.d.sync += phase_history[i+1].eq(phase_history[i])
                
        # Stage A: Pipelined multiplication (Runs in sync domain)
        mult_temp = Signal(24)
        m.d.sync += mult_temp.eq(amVal * spi.delayCoeff)
        
        # Stage B: Pipelined tap index calculation (Runs in sync domain)
        raw_tap = Signal(16)
        m.d.comb += raw_tap.eq(spi.baseDelay + (mult_temp >> 16))
        
        tap_index_reg = Signal(4)
        with m.If(raw_tap >= 15):
            m.d.sync += tap_index_reg.eq(15)
        with m.Else():
            m.d.sync += tap_index_reg.eq(raw_tap[0:4])
            
        # Stage C: Pipelined multiplexer selection (delayed_phase_reg)
        delayed_phase_reg = Signal(16)
        with m.Switch(tap_index_reg):
            for i in range(16):
                with m.Case(i):
                    m.d.sync += delayed_phase_reg.eq(phase_history[i])

        # ========================================================
        # 7. CDC: Phase Latch (sync -> txClk Handshake)
        # ========================================================
        # Safe Clock Domain Crossing for the I2S phase data to the variable RF clock
        phase_to_rf_toggle = Signal()
        with m.If(phaseUpdated ^ phaseUpdatedPrev):
            m.d.sync += phase_to_rf_toggle.eq(~phase_to_rf_toggle)
            
        phase_to_rf_toggle_rf = Signal()
        m.submodules.sync_phase_toggle = cdc.FFSynchronizer(
            phase_to_rf_toggle,
            phase_to_rf_toggle_rf,
            o_domain="txClk"
        )
        
        last_phase_toggle_rf = Signal()
        m.d.txClk += last_phase_toggle_rf.eq(phase_to_rf_toggle_rf)
        
        # Pipelined load enable to resolve clock enable fanout timing bottleneck
        phase_load_en = Signal()
        m.d.txClk += phase_load_en.eq(phase_to_rf_toggle_rf ^ last_phase_toggle_rf)
        
        phase_rf = Signal(16)
        with m.If(phase_load_en):
            m.d.txClk += phase_rf.eq(delayed_phase_reg)

        # ========================================================
        # 8. Amplitude Tracking PWM/PDM (Runs in 40MHz sync domain)
        # ========================================================
        amp_sel = Signal(16)
        with m.If(spi.modMode == 0):
            m.d.comb += amp_sel.eq(spi.amp)
        with m.Else():
            m.d.comb += amp_sel.eq(amVal)
            
        # Delta-Sigma PDM Modulator for tracking buck converter control voltage
        accum = Signal(17)
        with m.If(softReset | ~spi.txEn):
            m.d.sync += accum.eq(0)
        with m.Else():
            m.d.sync += accum.eq(accum[0:16] + amp_sel)
            
        pdm_out = Signal()
        m.d.comb += pdm_out.eq(accum[16])
        
        # Drive amPWM output
        amPWMR = Signal(attrs={"iob": "true"})
        m.d.comb += amPWMR.eq(Mux(spi.txEn, pdm_out, 0))
        m.d.sync += self.amPWM.eq(amPWMR)
        
        # Drive paEn output (active high, dropped when amplitude falls below threshold)
        paEnR = Signal(attrs={"iob": "true"})
        m.d.comb += paEnR.eq(spi.txEn & (amp_sel >= spi.paEnThreshold))
        m.d.sync += self.paEn.eq(paEnR)

        # ========================================================
        # 9. Pipelined RF Sequencer (Runs in txClk domain)
        # ========================================================
        # Synchronise Control registers to txClk domain
        txEnable_rf = Signal()
        softReset_rf = Signal()
        modeSquare_rf = Signal()
        modMode_rf = Signal(2)
        staticPhase_rf = Signal(16)
        
        m.submodules.sync_txEn_rf = cdc.FFSynchronizer(spi.txEn, txEnable_rf, o_domain="txClk")
        m.submodules.sync_softRst_rf = cdc.FFSynchronizer(softReset, softReset_rf, o_domain="txClk")
        m.submodules.sync_modeSq_rf = cdc.FFSynchronizer(spi.modeSq, modeSquare_rf, o_domain="txClk")
        
        modMode_rf_temp = Signal(2)
        staticPhase_rf_temp = Signal(16)
        m.d.txClk += [
            modMode_rf_temp.eq(spi.modMode),
            modMode_rf.eq(modMode_rf_temp),
            staticPhase_rf_temp.eq(spi.phase),
            staticPhase_rf.eq(staticPhase_rf_temp)
        ]
        
        # Select target Phase (Registered to Stage 2)
        phase_target_stage2 = Signal(16, attrs={"nosdff": "1"})
        m.d.txClk += phase_target_stage2.eq(Mux(modMode_rf == 2, phase_rf, staticPhase_rf))
        
        # Pipeline register to Stage 3
        phase_target_stage3 = Signal(16, attrs={"nosdff": "1"})
        m.d.txClk += phase_target_stage3.eq(phase_target_stage2)

        # Combined reset register to offload reset logic to the DFF's dedicated SR pin
        seq_reset_rf = Signal(attrs={"keep": "1"})
        modeSquare_seq_rf = Signal(attrs={"keep": "1"})
        modeSquare_dsp_rf = Signal(attrs={"keep": "1"})
        
        m.d.txClk += [
            seq_reset_rf.eq(softReset_rf | ~txEnable_rf),
            modeSquare_seq_rf.eq(modeSquare_rf),
            modeSquare_dsp_rf.eq(modeSquare_rf)
        ]

        # Stage 1: Dual physical counters to minimize feedback loop logic depth (forces 1 LUT feedback)
        next_sq = Array([0 if i >= 4 else i + 1 for i in range(16)])
        next_15 = Array([0 if i >= 14 else i + 1 for i in range(16)])
        
        counter_sq = Signal(4)
        counter_15 = Signal(4)
        
        counter_sq_bits = [Signal() for i in range(4)]
        counter_15_bits = [Signal() for i in range(4)]
        
        m.d.comb += [
            counter_sq.eq(Cat(counter_sq_bits)),
            counter_15.eq(Cat(counter_15_bits))
        ]
        
        next_sq_val = Signal(4)
        next_15_val = Signal(4)
        
        m.d.comb += [
            next_sq_val.eq(next_sq[counter_sq]),
            next_15_val.eq(next_15[counter_15])
        ]
        
        for i in range(4):
            m.submodules[f"dff_sq_{i}"] = Instance("SB_DFFSR",
                i_C=ClockSignal("txClk"),
                i_R=seq_reset_rf,
                i_D=next_sq_val[i],
                o_Q=counter_sq_bits[i]
            )
            m.submodules[f"dff_15_{i}"] = Instance("SB_DFFSR",
                i_C=ClockSignal("txClk"),
                i_R=seq_reset_rf,
                i_D=next_15_val[i],
                o_Q=counter_15_bits[i]
            )
            
        counter = Signal(4)
        m.d.comb += counter.eq(Mux(modeSquare_seq_rf, counter_sq, counter_15))

        # Stage 2: Dual registered ROM lookups (each is exactly 1 LUT layer!)
        # We use keep on combinatorial nets to prevent Yosys from sharing LUTs (which prevents PnR packing)
        # We set nosdff on registers to prevent Yosys from inferring SB_DFFSR cells.
        lut_vals = Array([i * 26214 for i in range(15)] + [0] * 17)
        
        lut_product_sq_19 = Signal(19, reset=0x7ffff, attrs={"nosdff": "1"})
        lut_product_15_19 = Signal(19, reset=0x7ffff, attrs={"nosdff": "1"})
        
        lut_product_sqComb = Signal(19, attrs={"keep": "1"})
        lut_product_15Comb = Signal(19, attrs={"keep": "1"})
        
        m.d.comb += [
            lut_product_sqComb.eq(lut_vals[counter_sq]),
            lut_product_15Comb.eq(lut_vals[counter_15])
        ]
        
        m.d.txClk += [
            lut_product_sq_19.eq(lut_product_sqComb),
            lut_product_15_19.eq(lut_product_15Comb)
        ]
        
        # Stage 2b: Pipelined Multiplier Coefficient (coeff_rf_stage2)
        coeff_rf_stage2 = Signal(16, attrs={"nosdff": "1"})
        m.d.txClk += coeff_rf_stage2.eq(Mux(modeSquare_dsp_rf, 2, 6))

        # Stage 3: Combine and register LUT Product and Coefficient (1 LUT delay only)
        lut_product_stage2 = Signal(19)
        m.d.comb += lut_product_stage2.eq(Mux(modeSquare_seq_rf, lut_product_sq_19, lut_product_15_19))
        
        lut_product_stage3 = Signal(32, attrs={"nosdff": "1"})
        m.d.txClk += lut_product_stage3.eq(lut_product_stage2)

        coeff_rf_stage3 = Signal(16, attrs={"nosdff": "1"})
        m.d.txClk += coeff_rf_stage3.eq(coeff_rf_stage2)

        # Stage 3 & 4: DSP State Mapper (Multiply-Accumulate in SB_MAC16)
        dsp_out = Signal(32)
        m.submodules.dsp = Instance("SB_MAC16",
            # Parameters to configure the block as: O = (A * B) + {C, D}
            p_NEG_TRIGGER=0,
            p_A_REG=1,
            p_B_REG=1,
            p_C_REG=1,
            p_D_REG=1,
            p_TOPADDSUB_LOWERINPUT=2, # 2 = multiplier upper product
            p_TOPADDSUB_UPPERINPUT=1, # 1 = C input
            p_BOTADDSUB_LOWERINPUT=2, # 2 = multiplier lower product
            p_BOTADDSUB_UPPERINPUT=1, # 1 = D input
            p_BOTADDSUB_CARRYSELECT=0,
            p_TOPADDSUB_CARRYSELECT=2, # propagate carry from bot to top
            p_TOPOUTPUT_SELECT=0, # select registered adder output
            p_BOTOUTPUT_SELECT=0, # select registered adder output
            
            # Port Connections
            i_CLK=ClockSignal("txClk"),
            i_CE=1,
            i_IRSTTOP=0,
            i_IRSTBOT=0,
            i_ORSTTOP=0,
            i_ORSTBOT=0,
            i_A=phase_target_stage3,
            i_B=coeff_rf_stage3,
            i_C=lut_product_stage3[16:32],
            i_D=lut_product_stage3[0:16],
            o_O=dsp_out
        )
        
        # Extract output state (equivalent to dsp_out >> 16)
        state = Signal(3)
        m.d.comb += state.eq(dsp_out[16:19])

        # Stage 5: Registered Active-Low Output Driver Mapping
        pb_reg = Signal(reset=1)
        pp_reg = Signal(reset=1)
        lb_reg = Signal(reset=1)
        lp_reg = Signal(reset=1)
        
        # Pre-decode state comparisons
        is_state_0 = Signal()
        is_state_1 = Signal()
        is_state_2 = Signal()
        is_state_3 = Signal()
        is_state_4 = Signal()
        is_state_5 = Signal()
        
        m.d.comb += [
            is_state_0.eq(state == 0),
            is_state_1.eq(state == 1),
            is_state_2.eq(state == 2),
            is_state_3.eq(state == 3),
            is_state_4.eq(state == 4),
            is_state_5.eq(state == 5)
        ]
        
        # Flat boolean equations to force Yosys to compile to minimum LUT depth
        # We rely on paEn to enable/disable the output, so output driver registers
        # do not need separate sync reset/enable, which completely resolves timing.
        pb_next = Signal()
        m.d.comb += pb_next.eq(Mux(modeSquare_rf,
                                   Mux(is_state_0, 0, 1),
                                   Mux(is_state_0 | is_state_2, 0, 1)))
        
        pp_next = Signal()
        m.d.comb += pp_next.eq(Mux(modeSquare_rf, 1, Mux(is_state_1, 0, 1)))
        
        lb_next = Signal()
        m.d.comb += lb_next.eq(Mux(modeSquare_rf,
                                   Mux(is_state_1, 0, 1),
                                   Mux(is_state_3 | is_state_5, 0, 1)))
        
        lp_next = Signal()
        m.d.comb += lp_next.eq(Mux(modeSquare_rf, 1, Mux(is_state_4, 0, 1)))
        
        # Register the outputs in txClk domain
        m.d.txClk += [
            pb_reg.eq(pb_next),
            pp_reg.eq(pp_next),
            lb_reg.eq(lb_next),
            lp_reg.eq(lp_next)
        ]

        # Bind outputs to physical pins
        m.d.comb += [
            self.rfPushBase.eq(pb_reg),
            self.rfPushPeak.eq(pp_reg),
            self.rfPullBase.eq(lb_reg),
            self.rfPullPeak.eq(lp_reg)
        ]

        return m
