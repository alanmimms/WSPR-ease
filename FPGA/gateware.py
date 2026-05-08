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
    def __init__(self, width=48, stages=6):
        self.width = width
        self.stages = stages # Ignored for DSP implementation, kept for compat
        self.tw = Signal(width)
        self.phase = Signal(width)

    def elaborate(self, platform):
        m = Module()
        
        # 48-bit NCO using 2 SB_MAC16 blocks
        # Block 0: Lower 32 bits
        co_0 = Signal()
        out_low = Signal(32)
        m.submodules.mac0 = Instance("SB_MAC16",
            p_A_REG=0, p_B_REG=0, p_C_REG=0, p_D_REG=0,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=0, i_B=0,
            i_C=self.tw[16:32], i_D=self.tw[0:16],
            o_O=out_low, o_CO=co_0,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_CI=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )
        
        # Block 1: Upper 16 bits (Bot adder)
        out_high = Signal(32)
        m.submodules.mac1 = Instance("SB_MAC16",
            p_A_REG=0, p_B_REG=0, p_C_REG=0, p_D_REG=0,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=1, p_TOPADDSUB_CARRYSELECT=2, # CARRYSELECT=1 means use CI
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=0, i_B=0,
            i_C=0, i_D=self.tw[32:48],
            i_CI=co_0,
            o_O=out_high,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )
        
        m.d.comb += self.phase.eq(Cat(out_low, out_high[0:16]))
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

        # 1. Synchronize SPI pins
        sclk_sync = Signal()
        mosi_sync = Signal()
        ncs_sync = Signal()
        m.submodules += [
            cdc.FFSynchronizer(self.i_sclk, sclk_sync),
            cdc.FFSynchronizer(self.i_mosi, mosi_sync),
            cdc.FFSynchronizer(self.i_ncs, ncs_sync),
        ]

        # Pipeline them to give the router freedom to place these closer to the SPI logic
        sclk = Signal()
        mosi = Signal()
        ncs = Signal()
        m.d.sync += [
            sclk.eq(sclk_sync),
            mosi.eq(mosi_sync),
            ncs.eq(ncs_sync)
        ]

        # 2. Edge Detection
        last_sclk = Signal()
        m.d.sync += last_sclk.eq(sclk)
        sclk_rising = Signal()
        sclk_falling = Signal()
        m.d.sync += [
            sclk_rising.eq(sclk & ~last_sclk),
            sclk_falling.eq(~sclk & last_sclk)
        ]

        # 3. SPI State Machine
        bit_count = Signal(6, reset_less=True)
        is_bit7_r = Signal(reset_less=True)
        is_bit7_f = Signal(reset_less=True)
        is_bit39_r = Signal(reset_less=True)
        
        mosi_shift = Signal(40, reset_less=True)
        miso_shift = Signal(32, reset_less=True)
        miso_reg = Signal(reset_less=True)
        m.d.comb += self.o_miso.eq(miso_reg)

        with m.If(ncs):
            m.d.sync += [
                bit_count.eq(0),
                is_bit7_r.eq(0),
                is_bit7_f.eq(0),
                is_bit39_r.eq(0),
                miso_reg.eq(0)
            ]
        with m.Else():
            with m.If(sclk_rising):
                m.d.sync += [
                    bit_count.eq(bit_count + 1),
                    is_bit7_r.eq(bit_count == 6),
                    is_bit39_r.eq(bit_count == 38),
                    mosi_shift.eq((mosi_shift << 1) | mosi)
                ]
            with m.If(sclk_falling):
                m.d.sync += [
                    is_bit7_f.eq(bit_count == 7),
                    miso_reg.eq(miso_shift[31]),
                    miso_shift.eq(miso_shift << 1)
                ]

        # 4. Register File
        ctrl_reg = Signal(ControlStruct)
        tw_low = Signal(32)
        tw_high = Signal(16)
        
        # 5. Pipelined Read
        # Latch local copies to break cross-module timing paths
        local_pll = Signal(reset_less=True)
        local_pps_c = Signal(27, reset_less=True)
        local_pps_g = Signal(5, reset_less=True)
        m.d.sync += [
            local_pll.eq(self.pll_locked),
            local_pps_c.eq(self.pps_count),
            local_pps_g.eq(self.pps_gen)
        ]
        
        read_val_pipe = Signal(32, reset_less=True)
        is_write_latch = Signal(reset_less=True)
        addr_latch = Signal(7, reset_less=True)
        
        with m.If(sclk_rising):
            with m.If(is_bit7_r):
                # Address is complete at rising edge 7 (mosi is bit 7)
                a = Cat(mosi, mosi_shift[0:6])
                m.d.sync += [
                    is_write_latch.eq(mosi_shift[6]), # B0
                    addr_latch.eq(a)
                ]

        # Decode address to one-hot to break routing delay
        is_ctrl = Signal(reset_less=True)
        is_twlow = Signal(reset_less=True)
        is_twhigh = Signal(reset_less=True)
        is_pps = Signal(reset_less=True)
        is_sig = Signal(reset_less=True)
        m.d.sync += [
            is_ctrl.eq(addr_latch == WSPRAddr.Control),
            is_twlow.eq(addr_latch == WSPRAddr.TuningLow),
            is_twhigh.eq(addr_latch == WSPRAddr.TuningHigh),
            is_pps.eq(addr_latch == WSPRAddr.PPS),
            is_sig.eq(addr_latch == WSPRAddr.Sig),
        ]

        # Select data based on one-hot decode (Pipelined OR-tree)
        v_ctrl = Signal(32)
        v_twlow = Signal(32)
        v_twhigh = Signal(32)
        v_pps = Signal(32)
        v_sig = Signal(32)
        
        m.d.comb += [
            v_ctrl.eq(Mux(is_ctrl, Cat(ctrl_reg.txEnable, ctrl_reg.modeSquare, local_pll, Const(0, 29)), 0)),
            v_twlow.eq(Mux(is_twlow, tw_low, 0)),
            v_twhigh.eq(Mux(is_twhigh, Cat(tw_high, Const(0, 16)), 0)),
            v_pps.eq(Mux(is_pps, Cat(local_pps_g, local_pps_c), 0)),
            v_sig.eq(Mux(is_sig, 0x52505357, 0))
        ]
        
        v_stage1_0 = Signal(32, reset_less=True)
        v_stage1_1 = Signal(32, reset_less=True)
        m.d.sync += [
            v_stage1_0.eq(v_ctrl | v_twlow | v_twhigh),
            v_stage1_1.eq(v_pps | v_sig)
        ]
        
        # If no address matches, the result is 0x00000000. 
        # (Could add a default fallback, but 0 is fine for invalid addresses).
        m.d.sync += read_val_pipe.eq(v_stage1_0 | v_stage1_1)

        # Load miso_shift at falling edge 7 (start of bit 8)
        # Note: Master samples bit 8 at rising edge 9.
        # We pipeline the enable signal to break the long combinatorial path
        load_miso_en = Signal(reset_less=True)
        m.d.sync += load_miso_en.eq(is_bit7_f & ~is_write_latch)

        with m.If(sclk_falling & load_miso_en):
            m.d.sync += miso_shift.eq(read_val_pipe)

        # 6. Write logic (Pipelined to break routing delays)
        do_write_ctrl = Signal(reset_less=True)
        do_write_twlow = Signal(reset_less=True)
        do_write_twhigh = Signal(reset_less=True)
        d_write = Signal(32, reset_less=True)
        
        do_write_any = sclk_rising & is_bit39_r & is_write_latch
        m.d.sync += [
            do_write_ctrl.eq(do_write_any & is_ctrl),
            do_write_twlow.eq(do_write_any & is_twlow),
            do_write_twhigh.eq(do_write_any & is_twhigh),
            d_write.eq(Cat(mosi, mosi_shift[0:31]))
        ]

        with m.If(do_write_ctrl):
            m.d.sync += [
                ctrl_reg.txEnable.eq(d_write[0]),
                ctrl_reg.modeSquare.eq(d_write[1])
            ]
        with m.If(do_write_twlow):
            m.d.sync += tw_low.eq(d_write)
        with m.If(do_write_twhigh):
            m.d.sync += tw_high.eq(d_write[:16])

        # 7. Outputs
        m.d.comb += [
            self.tuning_word.eq(Cat(tw_low, tw_high)),
            self.tx_enable.eq(ctrl_reg.txEnable),
            self.mode_square.eq(ctrl_reg.modeSquare)
        ]

        return m

class Exciter(Elaboratable):
    def __init__(self, pb_pin, pp_pin, lb_pin, lp_pin):
        self.tw = Signal(48)
        self.mode_square = Signal()
        self.tx_enable = Signal()
        
        self.pb_pin = pb_pin
        self.pp_pin = pp_pin
        self.lb_pin = lb_pin
        self.lp_pin = lp_pin

    def elaborate(self, platform):
        m = Module()

        # 1. Pipelined NCO (6 stages of 8 bits)
        m.submodules.nco = nco = PipelinedNCO(width=48, stages=6)
        m.d.comb += nco.tw.eq(self.tw)

        # 2. PRNG (DSP)
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
            o_O=lcg_state,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_CI=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )
        noise = lcg_state[:16]

        # 3. Phase to State Mapping
        ph_r = nco.phase[32:48]
        
        # Use DSP for ph_f offset: ph_f = ph_r + (tw >> 1)
        # (A*B) + D where A=1, B=ph_r, D=tw>>1
        ph_f = Signal(16)
        m.submodules.dsp_offset = Instance("SB_MAC16",
            p_A_REG=1, p_B_REG=1, p_C_REG=0, p_D_REG=1,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=1, i_B=ph_r,
            i_C=0, i_D=self.tw[32:48] >> 1,
            o_O=ph_f,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_CI=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )

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
            o_O=mul_r,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_CI=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )
        
        m.submodules.dsp_mul_f = Instance("SB_MAC16",
            p_A_REG=1, p_B_REG=1, p_C_REG=1, p_D_REG=1,
            p_TOPADDSUB_LOWERINPUT=2, p_TOPADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_LOWERINPUT=2, p_BOTADDSUB_UPPERINPUT=1,
            p_BOTADDSUB_CARRYSELECT=0, p_TOPADDSUB_CARRYSELECT=2,
            p_TOPOUTPUT_SELECT=3, p_BOTOUTPUT_SELECT=3,
            i_CLK=ClockSignal(), i_CE=1,
            i_A=ph_f[0:16], i_B=6,
            i_C=0, i_D=~noise,
            o_O=mul_f,
            i_AHOLD=0, i_BHOLD=0, i_CHOLD=0, i_DHOLD=0,
            i_IRSTTOP=0, i_IRSTBOT=0, i_ORSTTOP=0, i_ORSTBOT=0,
            i_OLOADTOP=0, i_OLOADBOT=0, i_ADDSUBTOP=0, i_ADDSUBBOT=0,
            i_OHOLDTOP=0, i_OHOLDBOT=0, i_CI=0, i_ACCUMCI=0, i_SIGNEXTIN=0
        )

        # 4. Decoder
        st_r = mul_r[16:19]
        st_f = mul_f[16:19]
        
        sq_r_val = st_r < 3
        sq_f_val = st_f < 3

        # Pipeline tx and mode to match DSP depth (Approx 4 cycles)
        tx_p = Signal(6)
        mode_p = Signal(6)
        m.d.sync += [
            tx_p.eq(Cat(self.tx_enable, tx_p[:-1])),
            mode_p.eq(Cat(self.mode_square, mode_p[:-1]))
        ]
        tx_f_val = tx_p[5]
        mode_f_val = mode_p[5]

        pb_r = Signal(); pp_r = Signal(); lb_r = Signal(); lp_r = Signal()
        pb_f = Signal(); pp_f = Signal(); lb_f = Signal(); lp_f = Signal()

        with m.If(tx_f_val):
            with m.If(mode_f_val):
                m.d.comb += [
                    pb_r.eq(sq_r_val), pp_r.eq(sq_r_val),
                    lb_r.eq(~sq_r_val), lp_r.eq(~sq_r_val),
                    pb_f.eq(sq_f_val), pp_f.eq(sq_f_val),
                    lb_f.eq(~sq_f_val), lp_f.eq(~sq_f_val),
                ]
            with m.Else():
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

        # Final registration before IO
        pb_or = Signal(); pp_or = Signal(); lb_or = Signal(); lp_or = Signal()
        pb_of = Signal(); pp_of = Signal(); lb_of = Signal(); lp_of = Signal()
        m.d.sync += [
            pb_or.eq(pb_r), pp_or.eq(pp_r), lb_or.eq(lb_r), lp_or.eq(lp_r),
            pb_of.eq(pb_f), pp_of.eq(pp_f), lb_of.eq(lb_f), lp_of.eq(lp_f),
        ]

        # 5. IO Drive
        m.submodules.io_pb = Instance("SB_IO", p_PIN_TYPE=24, o_PACKAGE_PIN=self.pb_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=pb_or, i_D_OUT_1=pb_of)
        m.submodules.io_pp = Instance("SB_IO", p_PIN_TYPE=24, o_PACKAGE_PIN=self.pp_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=pp_or, i_D_OUT_1=pp_of)
        m.submodules.io_lb = Instance("SB_IO", p_PIN_TYPE=24, o_PACKAGE_PIN=self.lb_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=lb_or, i_D_OUT_1=lb_of)
        m.submodules.io_lp = Instance("SB_IO", p_PIN_TYPE=24, o_PACKAGE_PIN=self.lp_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=lp_or, i_D_OUT_1=lp_of)

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
            o_PLLOUTGLOBAL=Signal(), # Dummy to silence Verilator
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

        m.submodules.exciter = exciter = Exciter(
            pb_pin=self.rfPushBase,
            pp_pin=self.rfPushPeak,
            lb_pin=self.rfPullBase,
            lp_pin=self.rfPullPeak
        )
        m.d.comb += [
            exciter.tw.eq(spi.tuning_word),
            exciter.tx_enable.eq(spi.tx_enable & pll_locked),
            exciter.mode_square.eq(spi.mode_square)
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
