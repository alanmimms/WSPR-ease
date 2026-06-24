# RTL for the WSPR-ease FPGA.

# NOTE: Keep this file in camelCase, with real language for the names
# of identifiers and continue to use the whitespace style I have used
# here so it's readable.

from amaranth import *
from amaranth.lib import enum, data, cdc
from amaranth.back import verilog
from amaranth import DomainRenamer
import os

# Define the registers from our metadata description.
from regs import regs


# This lets us build Amaranth data structures from our register
# metadata.
class RegFactory:
    @staticmethod
    def mkField(f: Field) -> Signal:
        """Dynamically creates an isolated Amaranth Signal from any Field type."""
        shape = (f.bits, f.signed)
        return Signal(shape, reset=f.default, name=f.name)

    @staticmethod
    def mkRecord(reg: Register) -> Record:
        """Converts an entire Register into an Amaranth Record bus."""
        layout = []
        for f in reg.fields:
            # Amaranth layouts take a tuple of (name, shape)
            shape = (f.bits, f.signed)
            layout.append((f.name, shape))
            
        record = Record(layout, name=reg.name)
        
        # Apply default reset values to the Record's underlying signals
        for f in reg.fields:
            getattr(record, f.name).reset = f.default
            
        return record


class PipelinedNCO(Elaboratable):
    def __init__(self, width=48):
        self.width = width
        # 48-bit Tuning Word
        self.tw = Signal(width)
        # 48-bit NCO Phase
        self.phase = Signal(width)

    def elaborate(self, platform):
        m = Module()

        # Split the tuning word into 16-bit segments for the DSP pipeline.
        twQ = []

        for i in range(3):
            curr = self.tw[i*16 : (i+1)*16]

            for j in range(i):
                reg = Signal(16, reset_less=True)
                m.d.sync += reg.eq(curr)
                curr = reg

            twQ.append(curr)
        
        # Accumulator segments output from the DSP blocks
        acc = [None] * 3

        carryIn = 0

        for i in range(3):
            acc[i] = Signal(32)
            cOut = Signal()

            # 48-bit (three stage) accumulator with carry propagated between 16-bit chunks.
            # acc_16_all_pipelined_unsigned
            m.submodules[f"NCOAccum{i}"] = Instance("SB_MAC16",
                                                    p_B_SIGNED=0,
                                                    p_A_SIGNED=0,
                                                    p_MODE_8x8=0, # CONTRARY to iCE40 Tech Lib doc.

                                                    p_BOTADDSUB_CARRYSELECT=0b00,
                                                    p_BOTADDSUB_UPPERINPUT=0,
                                                    p_BOTADDSUB_LOWERINPUT=0b00,
                                                    p_BOTOUTPUT_SELECT=0b01,

                                                    p_TOPADDSUB_CARRYSELECT=0b00,
                                                    p_TOPADDSUB_UPPERINPUT=0,
                                                    p_TOPADDSUB_LOWERINPUT=0,
                                                    p_TOPOUTPUT_SELECT=0b01,

                                                    p_PIPELINE_16x16_MULT_REG2=0,
                                                    p_PIPELINE_16x16_MULT_REG1=0,
                                                    p_BOT_8x8_MULT_REG=0,
                                                    p_TOP_8x8_MULT_REG=0,

                                                    p_A_REG=1,
                                                    p_B_REG=0,
                                                    p_C_REG=0,
                                                    p_D_REG=0,

                                                    i_A=twQ[i], # Note this is the adder's UPPER 16-bits
                                                    i_B=0,
                                                    i_C=0,
                                                    i_D=0,
                                                    i_CI=carryIn,

                                                    o_O=acc[i],
                                                    o_CO=cOut,

                                                    i_CLK=ClockSignal(),
                                                    i_CE=1,
                                                    o_ACCUMCO=Signal(),
                                                    o_SIGNEXTOUT=Signal())

            carryIn = Signal()  # Next iteration's carry in
            m.d.sync += carryIn.eq(cOut)

        finalAcc = []

        for i in range(3):
            delay = 2 - i
            curr = acc[i][16:32]        # Our SB_MAC16 accumulates in UPPER 16 bits

            for j in range(delay):
                reg = Signal(16, reset_less=True)
                m.d.sync += reg.eq(curr)
                curr = reg

            finalAcc.append(curr)

        m.d.comb += self.phase.eq(Cat(*finalAcc))
        return m

class FreqCounter(Elaboratable):

    def __init__(self):
        # Input signal for the Pulse-Per-Second GNSS timing edge
        self.samplePPS = Signal()

        # Latched count of system clock cycles per PPS interval
        self.ppsCount = Signal(32, reset_less=True)

        # 8-bit generation counter incremented at each PPS edge
        self.ppsGen = Signal(regs, reset_less=True)

    def elaborate(self, platform):
        m = Module()
        countOut = Signal(32, reset_less=True)

        # FPGACLK frequency counter gated by 1pps signal from GNSS.
        # This is acc_32_all_pipelined_unsigned using internally
        # generated LSB carry input of 1.
        m.submodules.freqCounter = Instance("SB_MAC16",
                                            p_B_SIGNED=0,
                                            p_A_SIGNED=0,
                                            p_MODE_8x8=0, # CONTRARY to iCE40 Tech Lib doc.

                                            p_BOTADDSUB_CARRYSELECT=0b01, # Carry in is always 1
                                            p_BOTADDSUB_UPPERINPUT=1,     # D
                                            p_BOTADDSUB_LOWERINPUT=0b00,  # B
                                            p_BOTOUTPUT_SELECT=0b01,      # S

                                            p_TOPADDSUB_CARRYSELECT=0b10, # Carry in is carry out of bot adder
                                            p_TOPADDSUB_UPPERINPUT=1,     # C
                                            p_TOPADDSUB_LOWERINPUT=0b00,  # A
                                            p_TOPOUTPUT_SELECT=0b01,      # Q

                                            p_PIPELINE_16x16_MULT_REG2=0,
                                            p_PIPELINE_16x16_MULT_REG1=0,
                                            p_BOT_8x8_MULT_REG=0,
                                            p_TOP_8x8_MULT_REG=0,

                                            p_A_REG=0,
                                            p_B_REG=0,
                                            p_C_REG=0,
                                            p_D_REG=0,

                                            i_A=0,
                                            i_B=0,
                                            i_C=0,
                                            i_D=0,
                                            i_CLK=ClockSignal(),
                                            i_CE=1,

                                            o_O=countOut,
                                            o_CO=Signal(),
                                            o_ACCUMCO=Signal(),
                                            o_SIGNEXTOUT=Signal())

        syncPPS = Signal()
        m.submodules.ppsSynchronizer = cdc.FFSynchronizer(self.samplePPS, syncPPS)
        lastPPS = Signal(reset_less=True)
        m.d.sync += lastPPS.eq(syncPPS)

        # Synchronized rising edge detection of the GNSS PPS signal
        risingPPS = Signal(reset_less=True)
        m.d.sync += risingPPS.eq(syncPPS & ~lastPPS)

        # Count seconds as "generations" so we can read the full
        # 32-bit value coherently by reading twice or even three time
        # and using the one where generation does not change between
        # start and finish of reading.
        with m.If(risingPPS):
            m.d.sync += [
                self.ppsGen.eq(self.ppsGen + 1),
                self.ppsCount.eq(countOut)
            ]

        return m

class SPIRegisters(Elaboratable):

# Factory to build an Amaranth Shape (for Signal) from a regTool Field.
def mkShapeFromField(field, **kwopts):
    return Shape(field.bits, signed=field.signed, **kwargs)

# Factory to build an Amaranth Enum from a regTool Enum.
def mkEnumFromRegEnum(e):
    pairs = [(vName if vName else f"Val{vVal}", vVal) for vName, vVal in e.values]
    return enum.Enum(f"{e.name}Enum", pairs, shape=e.bits)


    def __init__(self, buildNum=0):
        self.buildNum = buildNum
        self.iSCLK = Signal()
        self.iMOSI = Signal()
        self.oMISO = Signal()
        self.iNCS = Signal()
        self.tw = Signal(48)
        self.txEn = Signal()
        self.modeSq = Signal()
        self.pllLocked = Signal()
        self.ppsCount = Signal(32)
        self.ppsGen = Signal(mkShapeFromField(regs.PPS.gen.bits))

    def elaborate(self, platform):
        m = Module()
        sclkSync = Signal()
        mosiSync = Signal()
        ncsSync = Signal()

        m.submodules += [
            cdc.FFSynchronizer(self.iSCLK, sclkSync),
            cdc.FFSynchronizer(self.iMOSI, mosiSync),
            cdc.FFSynchronizer(self.iNCS, ncsSync)]

        sclk = Signal(reset_less=True)
        mosi = Signal(reset_less=True)
        ncs = Signal(reset_less=True)
        m.d.sync += [
            sclk.eq(sclkSync),
            mosi.eq(mosiSync), ncs.eq(ncsSync)
        ]

        lastSclk = Signal(reset_less=True)
        m.d.sync += lastSclk.eq(sclk)

        # Rising/Falling edge detection of the SPI clock
        sclkR = Signal(reset_less=True)
        sclkF = Signal(reset_less=True)
        m.d.sync += [
            sclkR.eq(sclk & ~lastSclk),
            sclkF.eq(~sclk & lastSclk)
        ]
        
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
        m.d.comb += self.oMISO.eq(misoReg)
        
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
        
        # Internal register holding the contents of the CONTROL register
        ctrlReg = Signal(ControlStruct, reset_less=True)

        # Clock domain crossing from 40MHz to 90MHz

        # Internal registers for the 48-bit NCO tuning word
        twLow = Signal(32, reset_less=True)
        twHi = Signal(16, reset_less=True)
        pllLockedQ = Signal(reset_less=True)
        ppsCountQ = Signal(32, reset_less=True)
        ppsGenQ = Signal(8, reset_less=True)
        m.d.sync += [
            pllLockedQ.eq(self.pllLocked),
            ppsCountQ.eq(self.ppsCount),
            ppsGenQ.eq(self.ppsGen)
        ]
        
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
        isBuildNo = Signal(reset_less=True)
        isSig = Signal(reset_less=True)
        m.d.sync += [
            isCtrl.eq(addrLatch == WSPRAddr.Control),
            isTwLow.eq(addrLatch == WSPRAddr.TuningLow),
            isTwHi.eq(addrLatch == WSPRAddr.TuningHigh),
            isPPS.eq(addrLatch == WSPRAddr.PPS),
            isBuildNo.eq(addrLatch == WSPRAddr.BuildNo),
            isSig.eq(addrLatch == WSPRAddr.Sig)
        ]
        
        vCtrl = Signal(32)
        vTwLow = Signal(32)
        vTwHi = Signal(32)
        vPPS = Signal(32)
        vBuildNo = Signal(32)
        vSig = Signal(32)
        m.d.comb += [
            vCtrl.eq(Mux(isCtrl, Cat(ctrlReg.txEnable, ctrlReg.modeSquare, pllLockedQ, Const(0, 29)), 0)),
            vTwLow.eq(Mux(isTwLow, twLow, 0)),
            vTwHi.eq(Mux(isTwHi, Cat(twHi, Const(0, 16)), 0)),
            vPPS.eq(Mux(isPPS, ppsCountQ, 0)),
            vBuildNo.eq(Mux(isBuildNo, Const(self.buildNum, 32), 0)),
            vSig.eq(Mux(isSig, 0x52505357, 0))
        ]
        
        vStage1d0 = Signal(32, reset_less=True)
        vStage1d1 = Signal(32, reset_less=True)
        m.d.sync += [
            vStage1d0.eq(vCtrl | vTwLow | vTwHi),
            vStage1d1.eq(vPPS | vBuildNo | vSig),
            readValPipe.eq(vStage1d0 | vStage1d1)
        ]
        
        loadMisoEn = Signal(reset_less=True)
        m.d.sync += loadMisoEn.eq(isBit7F & ~isWriteLatch)

        with m.If(sclkF & loadMisoEn):
            m.d.sync += [
                misoSR.eq(readValPipe << 1),
                misoReg.eq(readValPipe[31])
            ]
        
        doWriteAny = sclkR & isBit39R & isWriteLatch
        dWrite = Signal(32, reset_less=True)
        m.d.sync += dWrite.eq(Cat(mosi, mosiSR[0:31]))

        with m.If(doWriteAny):

            with m.If(isCtrl):
                m.d.sync += [
                    ctrlReg.txEnable.eq(dWrite[0]),
                    ctrlReg.modeSquare.eq(dWrite[1])
                ]

            with m.If(isTwLow):
                m.d.sync += twLow.eq(dWrite)

            with m.If(isTwHi):
                m.d.sync += twHi.eq(dWrite[:16])
        
        m.d.comb += [
            self.tw.eq(Cat(twLow, twHi)),
            self.txEn.eq(ctrlReg.txEnable),
            self.modeSq.eq(ctrlReg.modeSquare)
        ]

        return m

class LFSR32(Elaboratable):
    def __init__(self):
        self.out = Signal(32)

    def elaborate(self, platform):
        m = Module()

        # State MUST be initialized to a non-zero value, otherwise it
        # will stay stuck at 0 forever.
        state = Signal(32, reset=0xBEE5CAFE, reset_less=True)
        
        # Polynomial: x^32 + x^22 + x^2 + x^1 + 1
        # Represented as tap mask (bits 31, 21, 1, 0)
        taps = 0x80200003
        
        # Feedback is the MSB
        feedback = state[31]
        
        with m.If(feedback):
            # Shift left and apply XOR taps in parallel (1 LUT delay)
            m.d.sync += state.eq((state << 1) ^ taps)
        with m.Else():
            # Just shift left (0 LUT delay, just routing)
            m.d.sync += state.eq(state << 1)
            
        m.d.comb += self.out.eq(state)
        
        return m

class Exciter(Elaboratable):
    def __init__(self, pbPin, ppPin, lbPin, lpPin):
        self.tw = Signal(48)
        self.modeSq = Signal()
        self.txEn = Signal()
        self.pbPin = pbPin
        self.ppPin = ppPin
        self.lbPin = lbPin
        self.lpPin = lpPin

    def elaborate(self, platform):
        m = Module()
        m.submodules.nco = nco = PipelinedNCO(width=48)
        m.d.comb += nco.tw.eq(self.tw)
        
        # Use a LFSR Galois pseudo-random number generator (PRNG) to
        # compute noise to dither the freq somewhat to eliminate
        # artifacts from tuning right near a freq that divides into
        # our 90MHz clock evenly.
        m.submodules.lfsr = lfsr = LFSR32()

        daNoise = False

        if daNoise:
            noiseQ = Signal(Shape(32, signed=True), reset_less=True)
            noiseQinv = Signal(Shape(32, signed=True), reset_less=True)
            m.d.sync += [
                noiseQ.eq(lfsr.out.as_signed()),
                noiseQinv.eq(-lfsr.out.as_signed())
            ]
        else:
            noiseQ = 0
            noiseQinv = 0

        # 16 LSBs of NCO phase for the rising edge sample.
        # Falling edge is computed below, relative to this.
        phaseR = nco.phase[32:48]

        # ========================================================
        # STAGE 1: Fabric Pipeline
        # Break the routing distance by giving the 16-bit phaseF 
        # addition its own dedicated clock cycle in the fabric.
        # ========================================================
        phaseRQ = Signal(16, reset_less=True)
        phaseFQ = Signal(16, reset_less=True)
        noiseRQ = Signal(Shape(32, signed=True), reset_less=True)
        noiseFQ = Signal(Shape(32, signed=True), reset_less=True)

        # Number of bits to shift left to scale the noise from LFSR to
        # get the (signed value) to dither by.
        noiseShift = 2

        # Register the tuning word half-step locally to break the SPI routing path.
        twHalfStep = Signal(16, reset_less=True)

        m.d.sync += [
            twHalfStep.eq(self.tw[32:48] >> 1),        # Divide by two for 0.5 cycle.

            phaseRQ.eq(phaseR),
            phaseFQ.eq(phaseR + twHalfStep),

            noiseRQ.eq(noiseQ << noiseShift),
            noiseFQ.eq(noiseQinv << noiseShift)
        ]

        # 32-bit multiplier outputs
        mulR = Signal(32)
        mulF = Signal(32)

        # ========================================================
        # STAGE 2 & 3: DSP Multiplication and Addition
        # Math:
        #   Rising  edge: A=phaseRQ * 6 + C,D=noiseRQ
        #   Falling edge: A=phaseFQ * 6 + C,D=noiseFQ
        # ========================================================
        # mult_add_sub_32_all_pipelined_unsigned
        m.submodules.macR = Instance("SB_MAC16",
            p_A_SIGNED=0,       # C23
            p_B_SIGNED=0,       # C24
            p_MODE_8x8=0,       # C22
            
            p_BOTADDSUB_CARRYSELECT=0b00, # C21,C20
            p_BOTADDSUB_UPPERINPUT=1,     # C19
            p_BOTADDSUB_LOWERINPUT=0b10,  # C18,C17
            p_BOTOUTPUT_SELECT=0b01,      # C16,C15
            
            p_TOPADDSUB_CARRYSELECT=0b10, # C14,C13
            p_TOPADDSUB_UPPERINPUT=1,     # C12
            p_TOPADDSUB_LOWERINPUT=0b10,  # C11,C10
            p_TOPOUTPUT_SELECT=0b01,      # C9,C8

            p_PIPELINE_16x16_MULT_REG2=1, # C7
            p_PIPELINE_16x16_MULT_REG1=1, # C6
            p_BOT_8x8_MULT_REG=1,         # C5
            p_TOP_8x8_MULT_REG=1,         # C4

            p_A_REG=1,          # C1
            p_B_REG=0,          # C2
            p_C_REG=0,          # C0
            p_D_REG=1,          # C3

            i_A=phaseRQ,
            i_B=Const(6, 16),
            i_C=noiseRQ[16:32],
            i_D=noiseRQ[0:16],
            
            i_CLK=ClockSignal(),
            i_CE=1,

            i_OLOADTOP=0,               # NOT an internal feedback multiply+add XXXX sb=1
            i_OLOADBOT=0,               # XXXX sb=1
            
            # CRITICAL: Tie off all resets to prevent global routing drag
            i_IRSTTOP=0, i_IRSTBOT=0, 
            i_ORSTTOP=0, i_ORSTBOT=0,

            o_O=mulR)

        # mac_32_all_pipelined_unsigned
        m.submodules.macF = Instance("SB_MAC16",
            p_A_SIGNED=0,
            p_B_SIGNED=0,
            p_MODE_8x8=0,
            
            p_BOTADDSUB_CARRYSELECT=0b00,
            p_BOTADDSUB_UPPERINPUT=0,
            p_BOTADDSUB_LOWERINPUT=0b10,
            p_BOTOUTPUT_SELECT=0b01,
            
            p_TOPADDSUB_CARRYSELECT=0b10,
            p_TOPADDSUB_UPPERINPUT=0,
            p_TOPADDSUB_LOWERINPUT=0b10,
            p_TOPOUTPUT_SELECT=0b01,

            p_PIPELINE_16x16_MULT_REG2=1,
            p_PIPELINE_16x16_MULT_REG1=1,
            p_BOT_8x8_MULT_REG=1,
            p_TOP_8x8_MULT_REG=1,

            p_A_REG=1,
            p_B_REG=6,
            p_C_REG=1,
            p_D_REG=1,

            # Data Ports
            i_A=phaseFQ,
            i_B=Const(6, 16),
            i_C=noiseFQ[16:32],
            i_D=noiseFQ[0:16],
            
            i_OLOADTOP=1,
            i_OLOADBOT=1,
            
            # Control Ports
            i_CLK=ClockSignal(),
            i_CE=1,
            i_IRSTTOP=0, i_IRSTBOT=0, 
            i_ORSTTOP=0, i_ORSTBOT=0,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0, 
            i_OHOLDTOP=0, i_OHOLDBOT=0,
            
            o_O=mulF)

        # Aligned 3-bit state values
        stateRReg = Signal(3, reset_less=True)
        stateFReg = Signal(3, reset_less=True)

        m.d.sync += [
            stateRReg.eq(mulR[16:19]),
            stateFReg.eq(mulF[16:19])
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
        sqLevelR = stateRReg < 3

        with m.If(txEnPipe[7]):

            with m.If(modeSqPipe[7]):
                m.d.comb += [
                    pushBaseR.eq(sqLevelR),
                    pushPeakR.eq(0),
                    pullBaseR.eq(~sqLevelR),
                    pullPeakR.eq(0)
                ]

            with m.Else():
                m.d.comb += [
                    pushBaseR.eq((stateRReg == 0) | (stateRReg == 2)),
                    pushPeakR.eq(stateRReg == 1),
                    pullBaseR.eq((stateRReg == 3) | (stateRReg == 5)),
                    pullPeakR.eq(stateRReg == 4)
                ]
        
        # Calculated pin levels for Falling edge
        pushBaseF = Signal()
        pushPeakF = Signal()
        pullBaseF = Signal()
        pullPeakF = Signal()

        # Boolean level for square wave mode on the falling edge
        sqLevelF = stateFReg < 3

        with m.If(txEnPipe[7]):

            with m.If(modeSqPipe[7]):
                m.d.comb += [
                    pushBaseF.eq(sqLevelF),
                    pushPeakF.eq(0),
                    pullBaseF.eq(~sqLevelF),
                    pullPeakF.eq(0)
                ]

            with m.Else():
                m.d.comb += [
                    pushBaseF.eq((stateFReg == 0) | (stateFReg == 2)),
                    pushPeakF.eq(stateFReg == 1),
                    pullBaseF.eq((stateFReg == 3) | (stateFReg == 5)),
                    pullPeakF.eq(stateFReg == 4)
                ]

        # Fabric registers to ease timing:
        # Rising edge outputs registered on positive edge (1-stage delay)
        pushBaseRegR = Signal(reset_less=True)
        pushPeakRegR = Signal(reset_less=True)
        pullBaseRegR = Signal(reset_less=True)
        pullPeakRegR = Signal(reset_less=True)

        # Falling edge outputs registered on positive edge (double-stage for timing and 0.5-cycle alignment)
        pushBaseRegF = Signal(reset_less=True)
        pushPeakRegF = Signal(reset_less=True)
        pullBaseRegF = Signal(reset_less=True)
        pullPeakRegF = Signal(reset_less=True)

        pushBaseRegF2 = Signal(reset_less=True)
        pushPeakRegF2 = Signal(reset_less=True)
        pullBaseRegF2 = Signal(reset_less=True)
        pullPeakRegF2 = Signal(reset_less=True)

        m.d.sync += [
            # Stage 1 Rising edge
            pushBaseRegR.eq(pushBaseR),
            pushPeakRegR.eq(pushPeakR),
            pullBaseRegR.eq(pullBaseR),
            pullPeakRegR.eq(pullPeakR),

            # Stage 1 Falling edge
            pushBaseRegF.eq(pushBaseF),
            pushPeakRegF.eq(pushPeakF),
            pullBaseRegF.eq(pullBaseF),
            pullPeakRegF.eq(pullPeakF),

            # Stage 2 Falling edge
            pushBaseRegF2.eq(pushBaseRegF),
            pushPeakRegF2.eq(pushPeakRegF),
            pullBaseRegF2.eq(pullBaseRegF),
            pullPeakRegF2.eq(pullPeakRegF)
        ]

        #           543210
        pinType = 0b011000      # DDR
        m.submodules.mPushBase = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.pbPin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pushBaseRegR, i_D_OUT_1=pushBaseRegF2)
        m.submodules.mPushPeak = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.ppPin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pushPeakRegR, i_D_OUT_1=pushPeakRegF2)
        m.submodules.mPullBase = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.lbPin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pullBaseRegR, i_D_OUT_1=pullBaseRegF2)
        m.submodules.mPullPeak = Instance("SB_IO",
                                          p_PIN_TYPE=pinType,
                                          o_PACKAGE_PIN=self.lpPin,
                                          i_OUTPUT_CLK=ClockSignal(),
                                          i_D_OUT_0=pullPeakRegR, i_D_OUT_1=pullPeakRegF2)
        return m

class Top(Elaboratable):
    # Pass 'sim' down so the gateware knows whether to bypass the PLL
    def __init__(self, sim=False, buildNum=0):
        self.sim = sim
        self.buildNum = buildNum
        # Use 'clk40' everywhere to prevent C++ Verilator port confusion
        self.clk40 = Signal(name="clk40") 
        self.clk90sim = Signal(name="clk90sim") # Only used in TB
        self.gnssPPS = Signal(name="gnssPPS")
        self.fpgaNRESET = Signal(name="fpgaNRESET")
        self.fpgaSCLKpin = Signal(name="fpgaSCLKpin")
        self.fpgaMOSI = Signal(name="fpgaMOSI")
        self.fpgaMISO = Signal(name="fpgaMISO")
        self.fpgaNCS = Signal(name="fpgaNCS")
        self.rfPushBase = Signal(name="rfPushBase")
        self.rfPushPeak = Signal(name="rfPushPeak")
        self.rfPullBase = Signal(name="rfPullBase")
        self.rfPullPeak = Signal(name="rfPullPeak")
        self.driverEN = Signal(name="driverEN")

    def getPorts(self):
        ports = [
            self.clk40,
            self.gnssPPS,
            self.fpgaNRESET,
            self.fpgaSCLKpin,
            self.fpgaMOSI,
            self.fpgaMISO,
            self.fpgaNCS,
            self.rfPushBase,
            self.rfPushPeak,
            self.rfPullBase,
            self.rfPullPeak,
            self.driverEN
        ]

        # Only expose the sim clock to Verilator if we are simulating
        if self.sim:
            ports.append(self.clk90sim)
            
        return ports        

    def elaborate(self, platform):
        m = Module()

        # ==========================================
        # 40 MHz Domain: TCXO Input -> Global Buffer
        # ==========================================
        clk40GB = Signal()
        m.domains.sync40 = ClockDomain("sync40", local=True)
        m.d.comb += ClockSignal("sync40").eq(clk40GB)

        # ==========================================
        # 90 MHz Domain: PLL/Sim -> Global Buffer
        # ==========================================
        clk90 = Signal() # Raw PLL output
        pllLockedRaw = Signal()

        # Hardware PLL
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
                                    o_PLLOUTGLOBAL=clk40GB,
                                    o_LOCK=pllLockedRaw)

        # MUX the clock source based on the simulation flag
        clk90Src = Signal()
        if self.sim:
            m.d.comb += clk90Src.eq(self.clk90sim), # Inject testbench clock
        else:
            m.d.comb += clk90Src.eq(clk90)  # Use physical PLL

        clk90GB = Signal()
        m.submodules.clk90GB = Instance("SB_GB",
                                        i_USER_SIGNAL_TO_GLOBAL_BUFFER=clk90Src,
                                        o_GLOBAL_BUFFER_OUTPUT=clk90GB)

        m.domains.sync = ClockDomain("sync", local=True)
        m.d.comb += ClockSignal("sync").eq(clk90GB)

        # Globally buffer the lock signal
        pllLockedGB = Signal()
        m.submodules.lockGB = Instance("SB_GB",
                                       i_USER_SIGNAL_TO_GLOBAL_BUFFER=pllLockedRaw,
                                       o_GLOBAL_BUFFER_OUTPUT=pllLockedGB)

        sclkGB = Signal()
        m.submodules.sclkGB = Instance("SB_GB",
                                       i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.fpgaSCLKpin,
                                       o_GLOBAL_BUFFER_OUTPUT=sclkGB)

        rstSyncRaw = Signal()
        m.submodules.rstSync = cdc.FFSynchronizer(~self.fpgaNRESET, rstSyncRaw, reset=1)

        rstGB = Signal()
        m.submodules.rstGB = Instance("SB_GB",
                                      i_USER_SIGNAL_TO_GLOBAL_BUFFER=rstSyncRaw,
                                      o_GLOBAL_BUFFER_OUTPUT=rstGB)
        m.d.comb += ResetSignal().eq(rstGB)
        m.d.comb += ResetSignal("sync").eq(rstGB)
        m.d.comb += ResetSignal("sync40").eq(rstGB)

        freq = FreqCounter()
        # Remap freq into 40MHz domain
        m.submodules.freqRenamed = DomainRenamer({"sync": "sync40"})(freq)
        m.d.comb += freq.samplePPS.eq(self.gnssPPS)

        spi = SPIRegisters(buildNum=self.buildNum)
        m.submodules.spi = spi
        m.d.comb += [
            spi.iSCLK.eq(sclkGB),
            spi.iMOSI.eq(self.fpgaMOSI),
            self.fpgaMISO.eq(spi.oMISO),
            spi.iNCS.eq(self.fpgaNCS),
            spi.pllLocked.eq(pllLockedGB),
            spi.ppsCount.eq(freq.ppsCount),
            spi.ppsGen.eq(freq.ppsGen)
        ]

        txEnSync = Signal(reset_less=True)
        m.d.sync += txEnSync.eq(spi.txEn & pllLockedGB)

        exciter = Exciter(pbPin=self.rfPushBase,
                          ppPin=self.rfPushPeak,
                          lbPin=self.rfPullBase,
                          lpPin=self.rfPullPeak)
        m.submodules.exciter = exciter
        m.d.comb += [
            exciter.tw.eq(spi.tw),
            exciter.txEn.eq(txEnSync),
            exciter.modeSq.eq(spi.modeSq)
        ]

        m.d.sync += self.driverEN.eq(txEnSync)
        return m
