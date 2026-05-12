from amaranth import *
from amaranth.lib import enum, data, cdc
from amaranth.back import verilog
import os

# Import the generated registers if they exist, or provide placeholders for first run
try:
    from .regs_gen import ControlStruct, TuningLowStruct, TuningHighStruct, PPSStruct, SigStruct, WSPRAddr

except ImportError:
    class ControlStruct(data.Struct):
        txEnable: unsigned(1)
        modeSquare: unsigned(1)
        pllLocked: unsigned(1)
        reserved0: unsigned(29)

    class TuningLowStruct(data.Struct):
        word: unsigned(32)

    class TuningHighStruct(data.Struct):
        word: unsigned(16)
        reserved0: unsigned(16)

    class PPSStruct(data.Struct):
        gen: unsigned(5)
        count: unsigned(27)

    class SigStruct(data.Struct):
        val: unsigned(32)

    class WSPRAddr(enum.Enum, shape=7):
        Control = 0x00
        TuningLow = 0x01
        TuningHigh = 0x02
        PPS = 0x03
        Sig = 0x0F

class PipelinedNCO(Elaboratable):
    def __init__(self, width=48):
        self.width = width
        # 48-bit Tuning Word
        self.tw = Signal(width)
        # 48-bit NCO Phase
        self.phase = Signal(width)

    def elaborate(self, platform):
        m = Module()

        # Tuning word split into 16-bit segments for the DSP pipeline
        twChunks = [self.tw[i*16 : (i+1)*16] for i in range(3)]

        twDelayed = []

        for i, chunk in enumerate(twChunks):
            curr = chunk

            for j in range(i):
                reg = Signal(16, reset_less=True)
                m.d.sync += reg.eq(curr)
                curr = reg

            twDelayed.append(curr)
        
        # Accumulator segments output from the DSP blocks
        accChunks = []
        # Carry signal propagated between pipelined DSP stages
        carryIn = Signal()

        for i in range(3):
            accOut = Signal(32)
            coOut = Signal()

            m.submodules[f"mac_{i}"] = Instance("SB_MAC16",
                                                p_BOTADDSUB_LOWERINPUT=0,
                                                p_BOTADDSUB_UPPERINPUT=1,
                                                p_BOTADDSUB_CARRYSELECT=3 if i > 0 else 0,
                                                i_B=0xFFFF,
                                                i_D=0,
                                                i_CI=carryIn,
                                                p_TOPADDSUB_LOWERINPUT=0,
                                                p_TOPADDSUB_UPPERINPUT=2,
                                                p_TOPADDSUB_CARRYSELECT=2,
                                                p_TOPOUTPUT_SELECT=1,
                                                i_A=twDelayed[i], o_O=accOut, o_CO=coOut,
                                                i_CLK=ClockSignal(),
                                                i_CE=1,
                                                o_ACCUMCO=Signal(),
                                                o_SIGNEXTOUT=Signal())

            accChunks.append(accOut[16:32])
            nc = Signal(reset_less=True)
            m.d.sync += nc.eq(coOut)
            carryIn = nc

        finalAcc = []

        for i in range(3):
            delay = 2 - i
            curr = accChunks[i]

            for j in range(delay):
                reg = Signal(16, reset_less=True)
                m.d.sync += reg.eq(curr)
                curr = reg

            finalAcc.append(curr)
        m.d.comb += self.phase.eq(Cat(*finalAcc))
        return m

class FreqCounter(Elaboratable):
    def __init__(self, width=32):
        self.width = width

        # Input signal for the Pulse-Per-Second GNSS timing edge
        self.samplePPS = Signal()

        # Latched count of system clock cycles per PPS interval
        self.ppsCount = Signal(width, reset_less=True)

        # 5-bit generation counter incremented at each PPS edge
        self.ppsGen = Signal(5, reset_less=True)

    def elaborate(self, platform):
        m = Module()
        countOut = Signal(32)

        m.submodules.counter_dsp = Instance("SB_MAC16",
                                            p_BOTADDSUB_LOWERINPUT=0,
                                            p_BOTADDSUB_UPPERINPUT=2,
                                            p_BOTADDSUB_CARRYSELECT=1,
                                            i_B=0,
                                            i_CI=0,
                                            p_TOPADDSUB_LOWERINPUT=0,
                                            p_TOPADDSUB_UPPERINPUT=2,
                                            p_TOPADDSUB_CARRYSELECT=2,
                                            i_A=0,
                                            p_TOPOUTPUT_SELECT=1,
                                            p_BOTOUTPUT_SELECT=1,
                                            i_CLK=ClockSignal(),
                                            i_CE=1,
                                            o_O=countOut,
                                            o_CO=Signal(),
                                            o_ACCUMCO=Signal(),
                                            o_SIGNEXTOUT=Signal())

        syncPPS = Signal()
        m.submodules.pps_sync = cdc.FFSynchronizer(self.samplePPS, syncPPS)
        lastPPS = Signal(reset_less=True)
        m.d.sync += lastPPS.eq(syncPPS)

        # Synchronized rising edge detection of the GNSS PPS signal
        risingPPS = Signal(reset_less=True)
        m.d.sync += risingPPS.eq(syncPPS & ~lastPPS)

        with m.If(risingPPS):
            m.d.sync += [self.ppsGen.eq(self.ppsGen + 1),
                         self.ppsCount.eq(countOut)]

        return m

class SPIRegisters(Elaboratable):

    def __init__(self):
        self.i_sclk = Signal()
        self.i_mosi = Signal()
        self.o_miso = Signal()
        self.i_ncs = Signal()
        self.tw = Signal(48)
        self.txEn = Signal()
        self.modeSq = Signal()
        self.pllLocked = Signal()
        self.ppsCount = Signal(32)
        self.ppsGen = Signal(5)

    def elaborate(self, platform):
        m = Module()
        sclkSync = Signal()
        mosiSync = Signal()
        ncsSync = Signal()

        m.submodules += [
            cdc.FFSynchronizer(self.i_sclk, sclkSync),
            cdc.FFSynchronizer(self.i_mosi, mosiSync),
            cdc.FFSynchronizer(self.i_ncs, ncsSync)]

        sclk = Signal(reset_less=True)
        mosi = Signal(reset_less=True)
        ncs = Signal(reset_less=True)
        m.d.sync += [sclk.eq(sclkSync), mosi.eq(mosiSync), ncs.eq(ncsSync)]
        lastSclk = Signal(reset_less=True)
        m.d.sync += lastSclk.eq(sclk)
        # Rising/Falling edge detection of the SPI clock
        sclkR = Signal(reset_less=True)
        sclkF = Signal(reset_less=True)
        m.d.sync += [sclkR.eq(sclk & ~lastSclk), sclkF.eq(~sclk & lastSclk)]
        
        bitCount = Signal(6, reset_less=True)

        # Flag indicating the 7th bit on a rising SPI clock edge
        isBit7R = Signal(reset_less=True)
        isBit7F = Signal(reset_less=True)
        isBit39R = Signal(reset_less=True)

        # 40-bit shift register for incoming MOSI data
        mosiSR = Signal(40, reset_less=True)

        # 32-bit shift register for outgoing MISO data
        misoSR = Signal(32, reset_less=True)
        misoReg = Signal(reset_less=True)
        m.d.comb += self.o_miso.eq(misoReg)
        
        with m.If(ncs):
            m.d.sync += [bitCount.eq(0),
                         isBit7R.eq(0),
                         isBit7F.eq(0),
                         isBit39R.eq(0),
                         misoReg.eq(0)]

        with m.Else():

            with m.If(sclkR):
                m.d.sync += [bitCount.eq(bitCount + 1),
                             isBit7R.eq(bitCount == 6),
                             isBit39R.eq(bitCount == 38),
                             mosiSR.eq((mosiSR << 1) | mosi)]

            with m.If(sclkF):
                m.d.sync += [isBit7F.eq(bitCount == 7),
                             misoReg.eq(misoSR[31]),
                             misoSR.eq(misoSR << 1)]
        
        # Internal register holding the contents of the CONTROL register
        ctrlReg = Signal(ControlStruct, reset_less=True)

        # Internal registers for the 48-bit NCO tuning word
        twLow = Signal(32, reset_less=True)
        twHi = Signal(16, reset_less=True)
        localPLL = Signal(reset_less=True)
        localPpsC = Signal(32, reset_less=True)
        localPpsG = Signal(5, reset_less=True)
        m.d.sync += [localPLL.eq(self.pllLocked), localPpsC.eq(self.ppsCount), localPpsG.eq(self.ppsGen)]
        
        readValPipe = Signal(32, reset_less=True)

        # Latched flag indicating a Write (1) or Read (0) operation
        isWriteLatch = Signal(reset_less=True)

        # Latched 7-bit register address for the current SPI transaction
        addrLatch = Signal(7, reset_less=True)
        
        with m.If(sclkR):

            with m.If(isBit7R):
                a = Cat(mosi, mosiSR[0:6])
                m.d.sync += [isWriteLatch.eq(mosiSR[6]), addrLatch.eq(a)]
        
        isCtrl = Signal(reset_less=True)
        isTwLow = Signal(reset_less=True)
        isTwHi = Signal(reset_less=True)
        isPPS = Signal(reset_less=True)
        isSig = Signal(reset_less=True)

        m.d.sync += [isCtrl.eq(addrLatch == WSPRAddr.Control),
                     isTwLow.eq(addrLatch == WSPRAddr.TuningLow),
                     isTwHi.eq(addrLatch == WSPRAddr.TuningHigh),
                     isPPS.eq(addrLatch == WSPRAddr.PPS),
                     isSig.eq(addrLatch == WSPRAddr.Sig)]
        
        vCtrl = Signal(32)
        vTwLow = Signal(32)
        vTwHi = Signal(32)
        vPPS = Signal(32)
        vSig = Signal(32)
        m.d.comb += [vCtrl.eq(Mux(isCtrl, Cat(ctrlReg.txEnable, ctrlReg.modeSquare, localPLL, Const(0, 29)), 0)),
                     vTwLow.eq(Mux(isTwLow, twLow, 0)),
                     vTwHi.eq(Mux(isTwHi, Cat(twHi, Const(0, 16)), 0)),
                     vPPS.eq(Mux(isPPS, localPpsC, 0)),
                     vSig.eq(Mux(isSig, 0x52505357, 0))]
        
        vStage1_0 = Signal(32, reset_less=True)
        vStage1_1 = Signal(32, reset_less=True)
        m.d.sync += [vStage1_0.eq(vCtrl | vTwLow | vTwHi),
                     vStage1_1.eq(vPPS | vSig)]
        m.d.sync += readValPipe.eq(vStage1_0 | vStage1_1)
        
        loadMisoEn = Signal(reset_less=True)
        m.d.sync += loadMisoEn.eq(isBit7F & ~isWriteLatch)

        with m.If(sclkF & loadMisoEn):
            m.d.sync += [misoSR.eq(readValPipe << 1),
                         misoReg.eq(readValPipe[31])]
        
        doWriteAny = sclkR & isBit39R & isWriteLatch
        dWrite = Signal(32, reset_less=True)
        m.d.sync += dWrite.eq(Cat(mosi, mosiSR[0:31]))

        with m.If(doWriteAny):

            with m.If(isCtrl):
                m.d.sync += [ctrlReg.txEnable.eq(dWrite[0]),
                             ctrlReg.modeSquare.eq(dWrite[1])]
            with m.If(isTwLow):
                m.d.sync += twLow.eq(dWrite)

            with m.If(isTwHi):
                m.d.sync += twHi.eq(dWrite[:16])
        
        m.d.comb += [
            self.tw.eq(Cat(twLow, twHi)),
            self.txEn.eq(ctrlReg.txEnable),
            self.modeSq.eq(ctrlReg.modeSquare)]

        return m

class Exciter(Elaboratable):
    def __init__(self, pb_pin, pp_pin, lb_pin, lp_pin):
        self.tw = Signal(48)
        self.modeSq = Signal()
        self.txEn = Signal()
        self.pb_pin = pb_pin
        self.pp_pin = pp_pin
        self.lb_pin = lb_pin
        self.lp_pin = lp_pin

    def elaborate(self, platform):
        m = Module()
        m.submodules.nco = nco = PipelinedNCO(width=48)
        m.d.comb += nco.tw.eq(self.tw)
        lcg = Signal(32)

        m.submodules.prng = Instance("SB_MAC16",
                                     p_BOTADDSUB_UPPERINPUT=2,
                                     p_BOTADDSUB_LOWERINPUT=0,
                                     p_TOPADDSUB_UPPERINPUT=2,
                                     p_TOPADDSUB_LOWERINPUT=1,
                                     i_A=lcg[:16],
                                     i_B=25173,
                                     i_D=13849,
                                     p_TOPOUTPUT_SELECT=1,
                                     i_CLK=ClockSignal(),
                                     i_CE=1,
                                     o_O=lcg,
                                     o_CO=Signal(),
                                     o_ACCUMCO=Signal(),
                                     o_SIGNEXTOUT=Signal())

        noise = lcg[:16]

        # Upper 16 bits of NCO phase for the rising edge sample
        phaseR = nco.phase[32:48]
        phaseF32 = Signal(32)

        # Phase for the falling edge sample (calculated with 0.5 cycle offset)
        phaseF = Signal(16)

        m.submodules.offs = Instance("SB_MAC16",
                                     p_TOPADDSUB_LOWERINPUT=0,
                                     p_TOPADDSUB_UPPERINPUT=1,
                                     i_A=phaseR,
                                     i_B=0,
                                     i_D=self.tw[32:48] >> 1,
                                     p_TOPOUTPUT_SELECT=1,
                                     i_CLK=ClockSignal(),
                                     i_CE=1,
                                     o_O=phaseF32,
                                     o_CO=Signal(),
                                     o_ACCUMCO=Signal(),
                                     o_SIGNEXTOUT=Signal())

        m.d.comb += phaseF.eq(phaseF32[16:32])
        
        # 32-bit DSP multiplier output for rising/falling edge state mapping
        mulR = Signal(32)
        mulF = Signal(32)

        m.submodules.mr = Instance("SB_MAC16",
                                   p_BOTADDSUB_LOWERINPUT=1,
                                   p_TOPADDSUB_LOWERINPUT=2,
                                   i_A=phaseR,
                                   i_B=6,
                                   i_D=noise,
                                   p_TOPOUTPUT_SELECT=1,
                                   p_BOTOUTPUT_SELECT=1,
                                   i_CLK=ClockSignal(),
                                   i_CE=1,
                                   o_O=mulR,
                                   o_CO=Signal(),
                                   o_ACCUMCO=Signal(),
                                   o_SIGNEXTOUT=Signal())

        m.submodules.mf = Instance("SB_MAC16",
                                   p_BOTADDSUB_LOWERINPUT=1,
                                   p_TOPADDSUB_LOWERINPUT=2,
                                   i_A=phaseF,
                                   i_B=6,
                                   i_D=~noise,
                                   p_TOPOUTPUT_SELECT=1,
                                   p_BOTOUTPUT_SELECT=1,
                                   i_CLK=ClockSignal(),
                                   i_CE=1,
                                   o_O=mulF,
                                   o_CO=Signal(),
                                   o_ACCUMCO=Signal(),
                                   o_SIGNEXTOUT=Signal())
        
        # Raw 3-bit state values from the multipliers
        stateRRaw = mulR[16:19]
        stateFRaw = mulF[16:19]

        # Aligned 3-bit state values (with pipeline balancing)
        stateRD1 = Signal(3, reset_less=True)
        stateR = Signal(3, reset_less=True)
        stateF = Signal(3, reset_less=True)

        m.d.sync += [
            stateRD1.eq(stateRRaw),
            stateR.eq(stateRD1),
            stateF.eq(stateFRaw)
        ]
        
        # Pipeline delay registers for TX enable and Mode Square signals
        txEnPipe = Signal(8, reset_less=True)
        modeSqPipe = Signal(8, reset_less=True)

        m.d.sync += [
            txEnPipe.eq(Cat(self.txEn, txEnPipe[:-1])),
            modeSqPipe.eq(Cat(self.modeSq, modeSqPipe[:-1]))
        ]
        
        # Calculated pin levels for Rising edge
        pushBaseR = Signal()
        pushPeakR = Signal()
        pullBaseR = Signal()
        pullPeakR = Signal()

        # Boolean level for square wave mode on the rising edge
        sqLevelR = stateR < 3

        with m.If(txEnPipe[7]):

            with m.If(modeSqPipe[7]): m.d.comb += [pushBaseR.eq(sqLevelR),
                                                   pushPeakR.eq(sqLevelR),
                                                   pullBaseR.eq(~sqLevelR),
                                                   pullPeakR.eq(~sqLevelR)]

            with m.Else(): m.d.comb += [pushBaseR.eq((stateR == 0) | (stateR == 2)),
                                        pushPeakR.eq(stateR == 1),
                                        pullBaseR.eq((stateR == 3) | (stateR == 5)),
                                        pullPeakR.eq(stateR == 4)]
        
        # Calculated pin levels for Falling edge
        pushBaseF = Signal()
        pushPeakF = Signal()
        pullBaseF = Signal()
        pullPeakF = Signal()

        # Boolean level for square wave mode on the falling edge
        sqLevelF = stateF < 3

        with m.If(txEnPipe[7]):

            with m.If(modeSqPipe[7]): m.d.comb += [pushBaseF.eq(sqLevelF),
                                                   pushPeakF.eq(sqLevelF),
                                                   pullBaseF.eq(~sqLevelF),
                                                   pullPeakF.eq(~sqLevelF)]

            with m.Else(): m.d.comb += [pushBaseF.eq((stateF == 0) | (stateF == 2)),
                                        pushPeakF.eq(stateF == 1),
                                        pullBaseF.eq((stateF == 3) | (stateF == 5)),
                                        pullPeakF.eq(stateF == 4)]
        
        # Final registered levels for DDR IO stage
        pushBaseRegR = Signal(reset_less=True)
        pushPeakRegR = Signal(reset_less=True)
        pullBaseRegR = Signal(reset_less=True)
        pullPeakRegR = Signal(reset_less=True)
        pushBaseRegF = Signal(reset_less=True)
        pushPeakRegF = Signal(reset_less=True)
        pullBaseRegF = Signal(reset_less=True)
        pullPeakRegF = Signal(reset_less=True)

        m.d.sync += [pushBaseRegR.eq(pushBaseR),
                     pushPeakRegR.eq(pushPeakR),
                     pullBaseRegR.eq(pullBaseR),
                     pullPeakRegR.eq(pullPeakR),
                     pushBaseRegF.eq(pushBaseF),
                     pushPeakRegF.eq(pushPeakF),
                     pullBaseRegF.eq(pullBaseF),
                     pullPeakRegF.eq(pullPeakF)]
        
        pinType = 17
        m.submodules.mPushBase = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.pb_pin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pushBaseRegR, i_D_OUT_1=pushBaseRegF)
        m.submodules.mPushPeak = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.pp_pin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pushPeakRegR, i_D_OUT_1=pushPeakRegF)
        m.submodules.mPullBase = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.lb_pin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pullBaseRegR, i_D_OUT_1=pullBaseRegF)
        m.submodules.mPullPeak = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.lp_pin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pullPeakRegR, i_D_OUT_1=pullPeakRegF)
        return m

class Top(Elaboratable):
    def __init__(self):
        self.clk40 = Signal(name="clk40")
        self.gnssPPS = Signal(name="gnssPPS")
        self.fpgaNRESET = Signal(name="fpgaNRESET")
        self.fpgaSCLK_pin = Signal(name="fpgaSCLK_pin")
        self.fpgaMOSI = Signal(name="fpgaMOSI")
        self.fpgaMISO = Signal(name="fpgaMISO")
        self.fpgaNCS = Signal(name="fpgaNCS")
        self.rfPushBase = Signal(name="rfPushBase")
        self.rfPushPeak = Signal(name="rfPushPeak")
        self.rfPullBase = Signal(name="rfPullBase")
        self.rfPullPeak = Signal(name="rfPullPeak")
        self.driverNEN = Signal(name="driverNEN")

    def elaborate(self, platform):
        m = Module()
        clk90 = Signal()
        pllLockedRaw = Signal()

        m.submodules.pll = Instance("SB_PLL40_PAD",
                                    p_FEEDBACK_PATH="SIMPLE",
                                    p_DIVR=0,
                                    p_DIVF=17,
                                    p_DIVQ=3,
                                    p_FILTER_RANGE=2,
                                    i_PACKAGEPIN=self.clk40,
                                    i_RESETB=1,
                                    i_BYPASS=0,
                                    o_PLLOUTCORE=clk90,
                                    o_PLLOUTGLOBAL=Signal(),
                                    o_LOCK=pllLockedRaw)

        clk90Gb = Signal()
        m.submodules.clk_gb = Instance("SB_GB",
                                       i_USER_SIGNAL_TO_GLOBAL_BUFFER=clk90,
                                       o_GLOBAL_BUFFER_OUTPUT=clk90Gb)

        pllLocked = Signal()
        m.submodules.lock_gb = Instance("SB_GB",
                                        i_USER_SIGNAL_TO_GLOBAL_BUFFER=pllLockedRaw,
                                        o_GLOBAL_BUFFER_OUTPUT=pllLocked)

        sclkGb = Signal()
        m.submodules.sclk_gb = Instance("SB_GB",
                                        i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.fpgaSCLK_pin,
                                        o_GLOBAL_BUFFER_OUTPUT=sclkGb)

        m.domains.sync = ClockDomain()
        m.d.comb += ClockSignal("sync").eq(clk90Gb)

        rstSyncRaw = Signal()
        m.submodules.rst_sync = cdc.FFSynchronizer(~self.fpgaNRESET, rstSyncRaw, reset=1)

        rstGb = Signal()
        m.submodules.rst_gb = Instance("SB_GB", i_USER_SIGNAL_TO_GLOBAL_BUFFER=rstSyncRaw, o_GLOBAL_BUFFER_OUTPUT=rstGb)
        m.d.comb += ResetSignal("sync").eq(rstGb)

        m.submodules.freq = freq = FreqCounter()
        m.d.comb += freq.samplePPS.eq(self.gnssPPS)

        m.submodules.spi = spi = SPIRegisters()
        m.d.comb += [spi.i_sclk.eq(sclkGb),
                     spi.i_mosi.eq(self.fpgaMOSI),
                     self.fpgaMISO.eq(spi.o_miso),
                     spi.i_ncs.eq(self.fpgaNCS),
                     spi.pllLocked.eq(pllLocked),
                     spi.ppsCount.eq(freq.ppsCount),
                     spi.ppsGen.eq(freq.ppsGen)]

        m.submodules.exciter = exciter = Exciter(pb_pin=self.rfPushBase,
                                                 pp_pin=self.rfPushPeak,
                                                 lb_pin=self.rfPullBase,
                                                 lp_pin=self.rfPullPeak)
        m.d.comb += [exciter.tw.eq(spi.tw),
                     exciter.txEn.eq(spi.txEn & pllLocked),
                     exciter.modeSq.eq(spi.modeSq)]
        m.d.sync += self.driverNEN.eq(~(spi.txEn & pllLocked))
        return m

if __name__ == "__main__":
    top = Top()
    ports = [top.clk40,
             top.gnssPPS,
             top.fpgaNRESET,
             top.fpgaSCLK_pin,
             top.fpgaMOSI,
             top.fpgaMISO,
             top.fpgaNCS,
             top.rfPushBase,
             top.rfPushPeak,
             top.rfPullBase,
             top.rfPullPeak,
             top.driverNEN]
    with open("Top.v", "w") as f: f.write(verilog.convert(top, ports=ports))
    print("Generated top.v")
