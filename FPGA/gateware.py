# RTL for the WSPR-ease CPLD.
# Designed for Lattice LCMXO2-256HC-4SG32C.

from amaranth import *
from amaranth.lib import enum, data, cdc
from amaranth.back import verilog
import os

# Import address map from generated definitions
from regs_gen import FPGAAddr

class SPIRegisters(Elaboratable):
    def __init__(self, buildNum=0):
        self.buildNum = buildNum
        
        # SPI Bus Pins
        self.iSCLK = Signal()
        self.iMOSI = Signal()
        self.oMISO = Signal()
        self.iNCS = Signal()
        
        # Decoded outputs to other clock domains
        self.txEn = Signal()
        self.modeSq = Signal()
        self.softReset = Signal()
        self.amp = Signal(16)
        self.phase = Signal(16)

    def elaborate(self, platform):
        m = Module()
        
        # Synchronize SPI inputs to CPLD sync clock domain (38MHz OSCH)
        sclkSync = Signal()
        mosiSync = Signal()
        ncsSync = Signal()

        m.submodules += [
            cdc.FFSynchronizer(self.iSCLK, sclkSync),
            cdc.FFSynchronizer(self.iMOSI, mosiSync),
            cdc.FFSynchronizer(self.iNCS, ncsSync)
        ]

        # Double buffer synchronized inputs to detect edges
        sclk = Signal(reset_less=True)
        mosi = Signal(reset_less=True)
        ncs = Signal(reset_less=True)
        m.d.sync += [
            sclk.eq(sclkSync),
            mosi.eq(mosiSync),
            ncs.eq(ncsSync)
        ]

        lastSclk = Signal(reset_less=True)
        m.d.sync += lastSclk.eq(sclk)

        # SPI Clock edge detection
        sclkR = Signal(reset_less=True)
        sclkF = Signal(reset_less=True)
        m.d.sync += [
            sclkR.eq(sclk & ~lastSclk),
            sclkF.eq(~sclk & lastSclk)
        ]
        
        # 40-bit shift registers for 8-bit command + 32-bit data
        bitCount = Signal(6, reset_less=True)
        mosiSR = Signal(40, reset_less=True)
        misoSR = Signal(32, reset_less=True)
        misoReg = Signal(reset_less=True)
        
        m.d.comb += self.oMISO.eq(misoReg)
        
        # SPI transaction state machine
        isBit7R = Signal(reset_less=True)
        isBit7F = Signal(reset_less=True)
        isBit39R = Signal(reset_less=True)

        with m.If(ncs):
            m.d.sync += [
                bitCount.eq(0),
                isBit7R.eq(0),
                isBit7F.eq(0),
                isBit39R.eq(0),
                misoReg.eq(0)
            ]
        with m.Else():
            with m.If(sclkR):
                m.d.sync += [
                    bitCount.eq(bitCount + 1),
                    isBit7R.eq(bitCount == 6),
                    isBit39R.eq(bitCount == 38),
                    mosiSR.eq((mosiSR << 1) | mosi)
                ]
            with m.If(sclkF):
                m.d.sync += [
                    isBit7F.eq(bitCount == 7),
                    misoReg.eq(misoSR[31]),
                    misoSR.eq(misoSR << 1)
                ]
        
        # Latch address and write mode at bit 7
        isWriteLatch = Signal(reset_less=True)
        addrLatch = Signal(7, reset_less=True)
        
        with m.If(sclkR & isBit7R):
            a = Cat(mosi, mosiSR[0:6])
            m.d.sync += [
                isWriteLatch.eq(mosiSR[6]),
                addrLatch.eq(a)
            ]
        
        # Address decoding
        isCtrl = Signal(reset_less=True)
        isPolarMod = Signal(reset_less=True)
        isBuildNo = Signal(reset_less=True)
        isSig = Signal(reset_less=True)
        
        m.d.sync += [
            isCtrl.eq(addrLatch == FPGAAddr.Control),
            isPolarMod.eq(addrLatch == FPGAAddr.PolarMod),
            isBuildNo.eq(addrLatch == FPGAAddr.BuildNo),
            isSig.eq(addrLatch == FPGAAddr.Sig)
        ]
        
        # Registers
        ctrl_txEnable = Signal()
        ctrl_modeSquare = Signal()
        ctrl_softReset = Signal()
        ampReg = Signal(16)
        phaseReg = Signal(16)
        
        # Read Mux
        vCtrl = Signal(32)
        vPolarMod = Signal(32)
        vBuildNo = Signal(32)
        vSig = Signal(32)
        
        m.d.comb += [
            vCtrl.eq(Mux(isCtrl, Cat(ctrl_txEnable, ctrl_modeSquare, ctrl_softReset, Const(0, 29)), 0)),
            vPolarMod.eq(Mux(isPolarMod, Cat(ampReg, phaseReg), 0)),
            vBuildNo.eq(Mux(isBuildNo, Const(self.buildNum, 32), 0)),
            vSig.eq(Mux(isSig, 0x52505357, 0))
        ]
        
        # Pipeline read value back to MISO
        readValPipe = Signal(32, reset_less=True)
        vStage0 = Signal(32, reset_less=True)
        vStage1 = Signal(32, reset_less=True)
        
        m.d.sync += [
            vStage0.eq(vCtrl | vPolarMod),
            vStage1.eq(vBuildNo | vSig),
            readValPipe.eq(vStage0 | vStage1)
        ]
        
        loadMisoEn = Signal(reset_less=True)
        m.d.sync += loadMisoEn.eq(isBit7F & ~isWriteLatch)

        with m.If(sclkF & loadMisoEn):
            m.d.sync += [
                misoSR.eq(readValPipe << 1),
                misoReg.eq(readValPipe[31])
            ]
        
        # Write operations
        doWriteAny = sclkR & isBit39R & isWriteLatch
        dWrite = Signal(32, reset_less=True)
        m.d.sync += dWrite.eq(Cat(mosi, mosiSR[0:31]))

        with m.If(doWriteAny):
            with m.If(isCtrl):
                m.d.sync += [
                    ctrl_txEnable.eq(dWrite[0]),
                    ctrl_modeSquare.eq(dWrite[1]),
                    ctrl_softReset.eq(dWrite[2])
                ]
            with m.If(isPolarMod):
                m.d.sync += [
                    ampReg.eq(dWrite[0:16]),
                    phaseReg.eq(dWrite[16:32])
                ]
        
        # Wire external outputs
        m.d.comb += [
            self.txEn.eq(ctrl_txEnable),
            self.modeSq.eq(ctrl_modeSquare),
            self.softReset.eq(ctrl_softReset),
            self.amp.eq(ampReg),
            self.phase.eq(phaseReg)
        ]

        return m


class Top(Elaboratable):
    def __init__(self, sim=False, buildNum=0):
        self.sim = sim
        self.buildNum = buildNum
        
        # CPLD Clock Inputs
        self.rf_clk_pin = Signal(name="rf_clk_pin")     # Pin 21 (from Si5351 CLK0, modulated carrier)
        self.pwm_clk_pin = Signal(name="pwm_clk_pin")   # Pin 4 (from Si5351 CLK2, PWM rate clock)
        
        # CPLD Output Pins
        self.pwm_out = Signal(name="pwm_out")           # Pin 5 (PWM Output)
        self.nPullPeak = Signal(name="nPullPeak")       # Pin 11 (Active-Low)
        self.nPullBase = Signal(name="nPullBase")       # Pin 12 (Active-Low)
        self.nPushPeak = Signal(name="nPushPeak")       # Pin 13 (Active-Low)
        self.nPushBase = Signal(name="nPushBase")       # Pin 14 (Active-Low)
        
        # SPI Bus Pins
        self.fpgaSCLKpin = Signal(name="fpgaSCLKpin")   # Pin 9
        self.fpgaMOSI = Signal(name="fpgaMOSI")         # Pin 17
        self.fpgaMISO = Signal(name="fpgaMISO")         # Pin 10
        self.fpgaNCS = Signal(name="fpgaNCS")           # Pin 8

    def getPorts(self):
        return [
            self.rf_clk_pin,
            self.pwm_clk_pin,
            self.pwm_out,
            self.nPullPeak,
            self.nPullBase,
            self.nPushPeak,
            self.nPushBase,
            self.fpgaSCLKpin,
            self.fpgaMOSI,
            self.fpgaMISO,
            self.fpgaNCS
        ]

    def elaborate(self, platform):
        m = Module()

        # ========================================================
        # 1. Housekeeping Clock: OSCH (Internal Oscillator)
        # ========================================================
        # Instantiate internal MachXO2 OSCH to run housekeeping and SPI registers
        m.domains.sync = ClockDomain("sync")
        
        # If simulating, bypass the OSCH cell
        if self.sim:
            m.d.comb += ClockSignal("sync").eq(self.fpgaSCLKpin)
        else:
            m.submodules.osch = Instance("OSCH",
                                         p_NOM_FREQ="38.00", # 38.00 MHz
                                         i_stdby=0,
                                         o_OSC=ClockSignal("sync"))

        # ========================================================
        # 2. Clock Domain Setup for Si5351 Inputs
        # ========================================================
        m.domains.rf_clk = ClockDomain("rf_clk")
        m.domains.pwm_clk = ClockDomain("pwm_clk")
        
        m.d.comb += [
            ClockSignal("rf_clk").eq(self.rf_clk_pin),
            ClockSignal("pwm_clk").eq(self.pwm_clk_pin)
        ]

        # ========================================================
        # 3. SPI registers Instantiation (Runs in OSCH sync domain)
        # ========================================================
        spi = SPIRegisters(buildNum=self.buildNum)
        m.submodules.spi = spi
        m.d.comb += [
            spi.iSCLK.eq(self.fpgaSCLKpin),
            spi.iMOSI.eq(self.fpgaMOSI),
            self.fpgaMISO.eq(spi.oMISO),
            spi.iNCS.eq(self.fpgaNCS)
        ]

        # ========================================================
        # 4. PWM/PDM Modulator (Runs in pwm_clk domain)
        # ========================================================
        # CDC synchronizers for control and amplitude registers
        txEnable_pwm = Signal()
        softReset_pwm = Signal()
        m.submodules.sync_txEn_pwm = cdc.FFSynchronizer(spi.txEn, txEnable_pwm)
        m.submodules.sync_softRst_pwm = cdc.FFSynchronizer(spi.softReset, softReset_pwm)
        
        amp_pwm = Signal(16)
        amp_pwm_temp = Signal(16)
        m.d.pwm_clk += [
            amp_pwm_temp.eq(spi.amp),
            amp_pwm.eq(amp_pwm_temp)
        ]

        # 16-bit first-order Delta-Sigma PDM Modulator (extremely resource efficient)
        accum = Signal(17)
        with m.If(softReset_pwm | ~txEnable_pwm):
            m.d.pwm_clk += accum.eq(0)
        with m.Else():
            m.d.pwm_clk += accum.eq(accum[0:16] + amp_pwm)

        pdm_out = Signal()
        m.d.comb += pdm_out.eq(accum[16])
        
        # When disabled/idle, drive PWM Pin 5 high (constant 1) to force buck
        # converter to minimum output voltage (0.65V), turning off the RF PA.
        m.d.comb += self.pwm_out.eq(Mux(txEnable_pwm, pdm_out, 1))

        # ========================================================
        # 5. Modulo-6 Exciter Sequencer (Runs in rf_clk domain)
        # ========================================================
        txEnable_rf = Signal()
        modeSquare_rf = Signal()
        softReset_rf = Signal()
        m.submodules.sync_txEn_rf = cdc.FFSynchronizer(spi.txEn, txEnable_rf)
        m.submodules.sync_modeSq_rf = cdc.FFSynchronizer(spi.modeSq, modeSquare_rf)
        m.submodules.sync_softRst_rf = cdc.FFSynchronizer(spi.softReset, softReset_rf)
        
        phase_rf = Signal(16)
        phase_rf_temp = Signal(16)
        m.d.rf_clk += [
            phase_rf_temp.eq(spi.phase),
            phase_rf.eq(phase_rf_temp)
        ]

        # Modulo-6 counter clocked by Si5351 CLK0
        rf_counter = Signal(3)
        with m.If(softReset_rf | ~txEnable_rf):
            m.d.rf_clk += rf_counter.eq(0)
        with m.Else():
            m.d.rf_clk += rf_counter.eq(Mux(rf_counter == 5, 0, rf_counter + 1))

        # Map 16-bit phase directly to a 0-5 offset index (equivalent to multiplying by 6/65536)
        phase_offset = Signal(3)
        m.d.comb += phase_offset.eq((phase_rf * 6) >> 16)

        # Modulo-6 addition using sum-subtraction logic to save LUTs
        sum_phase = Signal(4)
        m.d.comb += sum_phase.eq(rf_counter + phase_offset)
        
        state = Signal(3)
        m.d.comb += state.eq(Mux(sum_phase >= 6, sum_phase - 6, sum_phase))

        # Drive outputs (active-low)
        pb = Signal(reset=1)
        pp = Signal(reset=1)
        lb = Signal(reset=1)
        lp = Signal(reset=1)

        with m.If(txEnable_rf & ~softReset_rf):
            with m.If(modeSquare_rf):
                # Standard Square Wave Mode:
                # 0-180 deg: Push Base (0) active
                # 180-360 deg: Pull Base (0) active
                m.d.comb += [
                    pb.eq(Mux(state < 3, 0, 1)),
                    lb.eq(Mux(state >= 3, 0, 1)),
                    pp.eq(1),
                    lp.eq(1)
                ]
            with m.Else():
                # Harmonic Suppression "1-2-1" Mode:
                # State 0, 2: Push Base (0)
                # State 1:    Push Peak (0)
                # State 3, 5: Pull Base (0)
                # State 4:    Pull Peak (0)
                m.d.comb += [
                    pb.eq(Mux((state == 0) | (state == 2), 0, 1)),
                    pp.eq(Mux(state == 1, 0, 1)),
                    lb.eq(Mux((state == 3) | (state == 5), 0, 1)),
                    lp.eq(Mux(state == 4, 0, 1))
                ]
        with m.Else():
            # Disabled / Idle state: All gates OFF (high)
            m.d.comb += [
                pb.eq(1),
                pp.eq(1),
                lb.eq(1),
                lp.eq(1)
            ]

        # Assign back to actual output pins
        m.d.comb += [
            self.nPushBase.eq(pb),
            self.nPushPeak.eq(pp),
            self.nPullBase.eq(lb),
            self.nPullPeak.eq(lp)
        ]

        return m
