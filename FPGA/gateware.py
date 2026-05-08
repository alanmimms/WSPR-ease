from amaranth import *
from amaranth.lib import enum, data, cdc
import os

# Import the generated registers if they exist, or provide placeholders for first run
try:
    from .regs_gen import ControlStruct, TuningLowStruct, TuningHighStruct, PPSStruct, SigStruct, WSPRAddr
except ImportError:
    # Minimal placeholders to allow initial elaboration if needed
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
    """
    A generic pipelined NCO that uses elaboration to create stages.
    This avoids carry-chain bottlenecks by splitting the addition across clock cycles.
    """
    def __init__(self, width=48, stages=6):
        assert width % stages == 0, "Width must be divisible by stages"
        self.width = width
        self.stages = stages
        self.chunk_w = width // stages
        
        self.tw = Signal(width)
        self.phase = Signal(width)

    def elaborate(self, platform):
        m = Module()
        cw = self.chunk_w
        
        acc_chunks = [Signal(cw, name=f"acc_chunk_{i}") for i in range(self.stages)]
        tw_chunks = [self.tw[i*cw : (i+1)*cw] for i in range(self.stages)]
        
        # Pipeline the tuning word chunks to match the accumulator latency
        # Chunk 0: 0 delay, Chunk 1: 1 delay, etc.
        pipelined_tw = []
        for i, chunk in enumerate(tw_chunks):
            current = chunk
            for j in range(i):
                next_reg = Signal(cw, name=f"tw_p{i}_s{j}")
                m.d.sync += next_reg.eq(current)
                current = next_reg
            pipelined_tw.append(current)

        carry = Signal()
        for i in range(self.stages):
            sum_w_carry = Signal(cw + 1, name=f"sum_w_carry_{i}")
            m.d.comb += sum_w_carry.eq(acc_chunks[i] + pipelined_tw[i] + carry)
            
            next_carry = Signal(name=f"next_carry_{i}")
            m.d.sync += [
                acc_chunks[i].eq(sum_w_carry[:cw]),
                next_carry.eq(sum_w_carry[cw])
            ]
            carry = next_carry

        # Reassemble phase. Note: the chunks are naturally skewed in time.
        # acc_chunk_0 is from T+0, acc_chunk_1 is from T+1, etc.
        # But since they are all outputs of registers, we can just cat them.
        # For RF synthesis, this skew is actually fine as long as we use the MSB.
        m.d.comb += self.phase.eq(Cat(*acc_chunks))
        
        return m

class FreqCounter(Elaboratable):
    """
    Pipelined frequency counter using elaboration to split carry chains.
    """
    def __init__(self, width=28, chunk_w=7):
        self.width = width
        self.chunk_w = chunk_w
        self.stages = width // chunk_w
        
        self.sample_pps = Signal()
        self.pps_count = Signal(width - 1)
        self.pps_gen = Signal(5)

    def elaborate(self, platform):
        m = Module()
        cw = self.chunk_w
        
        chunks = [Signal(cw, name=f"c{i}") for i in range(self.stages)]
        wraps = [Signal(name=f"w{i}") for i in range(self.stages - 1)]

        # Stage 0
        m.d.sync += chunks[0].eq(chunks[0] + 1)
        m.d.sync += wraps[0].eq(chunks[0] == (2**cw - 2)) # Wrap early to account for reg delay

        # Subsequent stages
        for i in range(1, self.stages):
            with m.If(wraps[i-1]):
                m.d.sync += chunks[i].eq(chunks[i] + 1)
                if i < self.stages - 1:
                    m.d.sync += wraps[i].eq(chunks[i] == (2**cw - 2))
            with m.Else():
                if i < self.stages - 1:
                    m.d.sync += wraps[i].eq(0)

        current_count = Signal(self.width)
        m.d.comb += current_count.eq(Cat(*chunks))

        # PPS Sync and Edge Detect
        sync_pps = Signal()
        m.submodules.pps_sync = cdc.FFSynchronizer(self.sample_pps, sync_pps)
        
        last_pps = Signal()
        m.d.sync += last_pps.eq(sync_pps)
        rising_pps = Signal()
        m.d.comb += rising_pps.eq(sync_pps & ~last_pps)

        with m.If(rising_pps):
            m.d.sync += self.pps_gen.eq(self.pps_gen + 1)
            m.d.sync += self.pps_count.eq(current_count[:self.width-1])

        return m

class SPIRegisters(Elaboratable):
    def __init__(self):
        # SPI Pins (Internal)
        self.i_sclk = Signal()
        self.i_mosi = Signal()
        self.o_miso = Signal()
        self.i_ncs = Signal()

        # Internal Domains
        self.tuning_word = Signal(48)
        self.tx_enable = Signal()
        self.mode_square = Signal()
        self.pll_locked = Signal()
        
        # From FreqCounter
        self.pps_count = Signal(27)
        self.pps_gen = Signal(5)

    def elaborate(self, platform):
        m = Module()

        # SPI Domain Clocking
        # Using a separate domain for SPI logic
        m.domains.spi = ClockDomain(local=True)
        m.d.comb += ClockSignal("spi").eq(self.i_sclk)
        m.d.comb += ResetSignal("spi").eq(self.i_ncs)

        # Synchronize PLL lock into SPI domain
        pll_locked_spi = Signal()
        m.submodules.pll_sync = cdc.FFSynchronizer(self.pll_locked, pll_locked_spi, o_domain="spi")

        # Shadow registers for PPS data (latched on NCS rising)
        # We'll use a MultiRegStage to safely pass data from sys to spi
        # Actually, since it's latched on NCS, we can just use a simple sync
        pps_count_shadow = Signal(27)
        pps_gen_shadow = Signal(5)
        
        # Latch on sys domain when NCS is high (inactive)
        with m.If(self.i_ncs):
            m.d.sync += [
                pps_count_shadow.eq(self.pps_count),
                pps_gen_shadow.eq(self.pps_gen)
            ]

        # Register Storage (SPI Domain)
        ctrl_reg = Signal(ControlStruct)
        tw_low = Signal(32)
        tw_high = Signal(16)
        
        # SPI State Machine
        bit_count = Signal(6)
        is_write = Signal()
        addr = Signal(7)
        shift_reg = Signal(32)
        
        with m.If(~self.i_ncs):
            m.d.spi += bit_count.eq(bit_count + 1)
            
            with m.If(bit_count == 0):
                m.d.spi += is_write.eq(self.i_mosi)
            with m.Elif(bit_count < 8):
                m.d.spi += addr.eq(Cat(self.i_mosi, addr[:-1]))
            
            # Read logic
            with m.If((bit_count == 8) & ~is_write):
                with m.Switch(addr):
                    with m.Case(WSPRAddr.Control):
                        m.d.spi += shift_reg.eq(Cat(ctrl_reg.txEnable, ctrl_reg.modeSquare, pll_locked_spi, Const(0, 29)))
                    with m.Case(WSPRAddr.TuningLow):
                        m.d.spi += shift_reg.eq(tw_low)
                    with m.Case(WSPRAddr.TuningHigh):
                        m.d.spi += shift_reg.eq(tw_high)
                    with m.Case(WSPRAddr.PPS):
                        m.d.spi += shift_reg.eq(Cat(pps_gen_shadow, pps_count_shadow))
                    with m.Case(WSPRAddr.Sig):
                        m.d.spi += shift_reg.eq(0x52505357)
                    with m.Default():
                        m.d.spi += shift_reg.eq(0xDEADBEEF)

            with m.If((bit_count >= 8) & ~is_write):
                m.d.comb += self.o_miso.eq(shift_reg[31])
                m.d.spi += shift_reg.eq(shift_reg << 1)
            
            # Write logic
            with m.If(is_write):
                m.d.spi += shift_reg.eq(Cat(self.i_mosi, shift_reg[:-1]))
                with m.If(bit_count == 39):
                    final_data = Cat(self.i_mosi, shift_reg[:-1])
                    with m.Switch(addr):
                        with m.Case(WSPRAddr.Control):
                            m.d.spi += [
                                ctrl_reg.txEnable.eq(final_data[0]),
                                ctrl_reg.modeSquare.eq(final_data[1])
                            ]
                        with m.Case(WSPRAddr.TuningLow):
                            m.d.spi += tw_low.eq(final_data)
                        with m.Case(WSPRAddr.TuningHigh):
                            m.d.spi += tw_high.eq(final_data[:16])

        # Synchronize to Sys domain
        m.submodules.tw_sync = cdc.FFSynchronizer(Cat(tw_low, tw_high), self.tuning_word)
        m.submodules.tx_sync = cdc.FFSynchronizer(ctrl_reg.txEnable, self.tx_enable)
        m.submodules.mode_sync = cdc.FFSynchronizer(ctrl_reg.modeSquare, self.mode_square)

        return m

class Exciter(Elaboratable):
    def __init__(self):
        self.tw = Signal(48)
        self.mode_square = Signal()
        self.tx_enable = Signal()
        
        self.rf_push_base = Signal()
        self.rf_push_peak = Signal()
        self.rf_pull_base = Signal()
        self.rf_pull_peak = Signal()

    def elaborate(self, platform):
        m = Module()

        # 1. Pipelined NCO
        m.submodules.nco = nco = PipelinedNCO(width=48, stages=6)
        m.d.comb += nco.tw.eq(self.tw)

        # 2. PRNG for Dither (SB_MAC16 Hardened)
        # X_next = (X * 25173) + 13849
        lcg_state = Signal(32)
        m.submodules.dsp_prng = Instance("SB_MAC16",
            p_A_REG=1, p_B_REG=0, p_C_REG=0, p_D_REG=0,
            p_TOPADDSUB_LOWERINPUT=0, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=0, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=lcg_state[:16], i_B=25173,
            i_C=0, i_D=13849,
            o_O=lcg_state
        )
        noise = lcg_state[:16]

        # 3. Phase to State Mapping (SB_MAC16 Hardened)
        # ph_f = ph_r + (tw >> 1)
        # we pipeline this to match NCO delay
        ph_r = nco.phase[32:48]
        ph_f = Signal(16)
        m.d.sync += ph_f.eq(ph_r + (self.tw[32:48] >> 1))

        # Multipliers
        mul_r = Signal(32)
        mul_f = Signal(32)
        
        m.submodules.dsp_mul_r = Instance("SB_MAC16",
            p_A_REG=1, p_B_REG=1, p_C_REG=1, p_D_REG=1,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=ph_r, i_B=6,
            i_C=0, i_D=noise,
            o_O=mul_r
        )
        
        m.submodules.dsp_mul_f = Instance("SB_MAC16",
            p_A_REG=1, p_B_REG=1, p_C_REG=1, p_D_REG=1,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=ph_f, i_B=6,
            i_C=0, i_D=~noise,
            o_O=mul_f
        )

        # 4. Decoder
        # mul[18:16] are the 3-bit state (0-5)
        st_r = mul_r[16:19]
        st_f = mul_f[16:19]
        
        # Square wave bits (MSB of phase)
        # We need to pipeline these to match the DSP multiplier latency (2 cycles)
        sq_pipe = Signal(3)
        m.d.sync += sq_pipe.eq(Cat(nco.phase[47], sq_pipe[:-1]))
        sq_r = sq_pipe[1] # Delayed to match st_r
        # For sq_f, we use the phase-shifted version. 
        # But ph_f = ph_r + offset, so the MSB might flip.
        # Actually, let's just use the state bits for square wave too: 
        # st < 3 is one half, st >= 3 is the other.
        sq_r_val = st_r < 3
        sq_f_val = st_f < 3

        # Pipelined Decoder
        # We need to delay tx_enable and mode_square to match DSP pipeline
        # Latency check: 
        # 1. nco.phase is reg output
        # 2. ph_f calculation (1 cycle)
        # 3. MAC (2 cycles)
        # Total from NCO: 3 cycles for F, 2 cycles for R? 
        # No, MAC has A_REG=1, so both are delayed.
        
        tx_pipe = Signal(4)
        mode_pipe = Signal(4)
        m.d.sync += [
            tx_pipe.eq(Cat(self.tx_enable, tx_pipe[:-1])),
            mode_pipe.eq(Cat(self.mode_square, mode_pipe[:-1]))
        ]
        tx_final = tx_pipe[3]
        mode_final = mode_pipe[3]

        # 1-2-1 Decode
        pb_r = Signal()
        pp_r = Signal()
        lb_r = Signal()
        lp_r = Signal()
        
        pb_f = Signal()
        pp_f = Signal()
        lb_f = Signal()
        lp_f = Signal()

        with m.If(tx_final):
            with m.If(mode_final):
                # Square Wave Mode
                m.d.comb += [
                    pb_r.eq(sq_r_val), pp_r.eq(sq_r_val),
                    lb_r.eq(~sq_r_val), lp_r.eq(~sq_r_val),
                    pb_f.eq(sq_f_val), pp_f.eq(sq_f_val),
                    lb_f.eq(~sq_f_val), lp_f.eq(~sq_f_val),
                ]
            with m.Else():
                # 1-2-1 Mode
                m.d.comb += [
                    pb_r.eq((st_r == 0) | (st_r == 2)),
                    pp_r.eq(st_r == 1),
                    lb_r.eq((st_r == 3) | (st_r == 5)),
                    lp_r.eq(st_r == 4),
                    
                    pb_f.eq((st_f == 0) | (st_f == 2)),
                    pp_f.eq(st_f == 1),
                    lb_f.eq((st_f == 3) | (st_f == 5)),
                    lp_f.eq(st_f == 4),
                ]

        # DDR Output Drive
        m.submodules.io_pb = Instance("SB_IO",
            p_PIN_TYPE=0b010001,
            i_PACKAGE_PIN=self.rf_push_base,
            i_OUTPUT_CLK=ClockSignal(),
            i_D_OUT_0=pb_r,
            i_D_OUT_1=pb_f
        )
        m.submodules.io_pp = Instance("SB_IO",
            p_PIN_TYPE=0b010001,
            i_PACKAGE_PIN=self.rf_push_peak,
            i_OUTPUT_CLK=ClockSignal(),
            i_D_OUT_0=pp_r,
            i_D_OUT_1=pp_f
        )
        m.submodules.io_lb = Instance("SB_IO",
            p_PIN_TYPE=0b010001,
            i_PACKAGE_PIN=self.rf_pull_base,
            i_OUTPUT_CLK=ClockSignal(),
            i_D_OUT_0=lb_r,
            i_D_OUT_1=lb_f
        )
        m.submodules.io_lp = Instance("SB_IO",
            p_PIN_TYPE=0b010001,
            i_PACKAGE_PIN=self.rf_pull_peak,
            i_OUTPUT_CLK=ClockSignal(),
            i_D_OUT_0=lp_r,
            i_D_OUT_1=lp_f
        )

        return m

class Top(Elaboratable):
    def __init__(self):
        # Physical Pins (Matching pins.pcf)
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

        # PLL: 40MHz -> 90MHz
        clk90 = Signal()
        pll_locked_raw = Signal()
        m.submodules.pll = Instance("SB_PLL40_PAD",
            p_FEEDBACK_PATH="SIMPLE",
            p_DIVR=0, p_DIVF=17, p_DIVQ=3, p_FILTER_RANGE=2,
            i_PACKAGEPIN=self.clk40,
            i_RESETB=1,
            i_BYPASS=0,
            o_PLLOUTCORE=clk90,
            o_LOCK=pll_locked_raw
        )

        # Global Clock Buffers
        clk90_gb = Signal()
        m.submodules.clk_gb = Instance("SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=clk90,
            o_GLOBAL_BUFFER_OUTPUT=clk90_gb
        )

        pll_locked = Signal()
        m.submodules.lock_gb = Instance("SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=pll_locked_raw,
            o_GLOBAL_BUFFER_OUTPUT=pll_locked
        )

        sclk_gb = Signal()
        m.submodules.sclk_gb = Instance("SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.fpgaSCLK_pin,
            o_GLOBAL_BUFFER_OUTPUT=sclk_gb
        )

        # Setup System Domain
        m.domains.sync = ClockDomain()
        m.d.comb += ClockSignal("sync").eq(clk90_gb)
        
        # Reset Synchronizer
        m.submodules.rst_sync = cdc.ResetSynchronizer(~self.fpgaNRESET)

        # Submodules
        m.submodules.freq = freq = FreqCounter()
        m.d.comb += freq.sample_pps.eq(self.gnssPPS)

        m.submodules.spi = spi = SPIRegisters()
        m.d.comb += [
            spi.i_sclk.eq(sclk_gb),
            spi.i_mosi.eq(self.fpgaMOSI),
            self.fpgaMISO.eq(spi.o_miso),
            spi.i_ncs.eq(self.fpgaNCS),
            spi.pll_locked.eq(pll_locked),
            spi.pps_count.eq(freq.pps_count),
            spi.pps_gen.eq(freq.pps_gen)
        ]

        m.submodules.exciter = exciter = Exciter()
        m.d.comb += [
            exciter.tw.eq(spi.tuning_word),
            exciter.tx_enable.eq(spi.tx_enable & pll_locked),
            exciter.mode_square.eq(spi.mode_square),
            self.rfPushBase.eq(exciter.rf_push_base),
            self.rfPushPeak.eq(exciter.rf_push_peak),
            self.rfPullBase.eq(exciter.rf_pull_base),
            self.rfPullPeak.eq(exciter.rf_pull_peak)
        ]

        # Driver Enable (Active Low)
        m.d.sync += self.driverNEN.eq(~(spi.tx_enable & pll_locked))

        return m

if __name__ == "__main__":
    from amaranth.back import verilog
    top = Top()
    # Explicitly define ports for the generated Verilog
    ports = [
        top.clk40, top.gnssPPS, top.fpgaNRESET,
        top.fpgaSCLK_pin, top.fpgaMOSI, top.fpgaMISO, top.fpgaNCS,
        top.rfPushBase, top.rfPushPeak,
        top.rfPullBase, top.rfPullPeak,
        top.driverNEN
    ]
    with open("top_gen.v", "w") as f:
        f.write(verilog.convert(top, ports=ports))
    print("Generated top_gen.v")
