from amaranth import *
from amaranth.lib import enum, data, cdc
from amaranth.back import verilog
import os

# Import the generated registers if they exist, or provide placeholders for first run
try:
    from .regs_gen import ControlStruct, TuningLowStruct, TuningHighStruct, PPSStruct, SigStruct, WSPRAddr
except ImportError:
    class ControlStruct(data.Struct):
        txEnable: unsigned(1); modeSquare: unsigned(1); pllLocked: unsigned(1); reserved0: unsigned(29)
    class TuningLowStruct(data.Struct): word: unsigned(32)
    class TuningHighStruct(data.Struct): word: unsigned(16); reserved0: unsigned(16)
    class PPSStruct(data.Struct): gen: unsigned(5); count: unsigned(27)
    class SigStruct(data.Struct): val: unsigned(32)
    class WSPRAddr(enum.Enum, shape=7):
        Control = 0x00; TuningLow = 0x01; TuningHigh = 0x02; PPS = 0x03; Sig = 0x0F

class PipelinedNCO(Elaboratable):
    def __init__(self, width=48):
        self.width = width; self.tw = Signal(width); self.phase = Signal(width)
    def elaborate(self, platform):
        m = Module()
        tw_chunks = [self.tw[i*16 : (i+1)*16] for i in range(3)]
        tw_delayed = []
        for i, chunk in enumerate(tw_chunks):
            curr = chunk
            for j in range(i):
                reg = Signal(16, reset_less=True); m.d.sync += reg.eq(curr); curr = reg
            tw_delayed.append(curr)
        acc_chunks = []; carry_in = Signal()
        for i in range(3):
            acc_out = Signal(32); co_out = Signal()
            m.submodules[f"mac_{i}"] = Instance("SB_MAC16",
                p_BOTADDSUB_LOWERINPUT=0, p_BOTADDSUB_UPPERINPUT=1, p_BOTADDSUB_CARRYSELECT=3 if i > 0 else 0,
                i_B=0xFFFF, i_D=0, i_CI=carry_in,
                p_TOPADDSUB_LOWERINPUT=0, p_TOPADDSUB_UPPERINPUT=2, p_TOPADDSUB_CARRYSELECT=2, p_TOPOUTPUT_SELECT=1,
                i_A=tw_delayed[i], o_O=acc_out, o_CO=co_out,
                i_CLK=ClockSignal(), i_CE=1, o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal()
            )
            acc_chunks.append(acc_out[16:32]); nc = Signal(reset_less=True); m.d.sync += nc.eq(co_out); carry_in = nc
        final_acc = []
        for i in range(3):
            delay = 2 - i; curr = acc_chunks[i]
            for j in range(delay):
                reg = Signal(16, reset_less=True); m.d.sync += reg.eq(curr); curr = reg
            final_acc.append(curr)
        m.d.comb += self.phase.eq(Cat(*final_acc))
        return m

class FreqCounter(Elaboratable):
    def __init__(self, width=32):
        self.width = width
        self.sample_pps = Signal()
        self.pps_count = Signal(width, reset_less=True)
        self.pps_gen = Signal(5, reset_less=True)
    def elaborate(self, platform):
        m = Module()
        count_out = Signal(32)
        m.submodules.counter_dsp = Instance("SB_MAC16",
            p_BOTADDSUB_LOWERINPUT=0, p_BOTADDSUB_UPPERINPUT=2, p_BOTADDSUB_CARRYSELECT=1,
            i_B=0, i_CI=0,
            p_TOPADDSUB_LOWERINPUT=0, p_TOPADDSUB_UPPERINPUT=2, p_TOPADDSUB_CARRYSELECT=2,
            i_A=0, p_TOPOUTPUT_SELECT=1, p_BOTOUTPUT_SELECT=1,
            i_CLK=ClockSignal(), i_CE=1, o_O=count_out,
            o_CO=Signal(), o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal()
        )
        sync_pps = Signal(); m.submodules.pps_sync = cdc.FFSynchronizer(self.sample_pps, sync_pps)
        last_pps = Signal(reset_less=True); m.d.sync += last_pps.eq(sync_pps)
        rising_pps = Signal(reset_less=True); m.d.sync += rising_pps.eq(sync_pps & ~last_pps)
        with m.If(rising_pps):
            m.d.sync += [self.pps_gen.eq(self.pps_gen + 1), self.pps_count.eq(count_out)]
        return m

class SPIRegisters(Elaboratable):
    def __init__(self):
        self.i_sclk = Signal(); self.i_mosi = Signal(); self.o_miso = Signal(); self.i_ncs = Signal()
        self.tuning_word = Signal(48); self.tx_enable = Signal(); self.mode_square = Signal(); self.pll_locked = Signal()
        self.pps_count = Signal(32); self.pps_gen = Signal(5)
    def elaborate(self, platform):
        m = Module()
        sclk_sync = Signal(); mosi_sync = Signal(); ncs_sync = Signal()
        m.submodules += [cdc.FFSynchronizer(self.i_sclk, sclk_sync), cdc.FFSynchronizer(self.i_mosi, mosi_sync), cdc.FFSynchronizer(self.i_ncs, ncs_sync)]
        sclk = Signal(reset_less=True); mosi = Signal(reset_less=True); ncs = Signal(reset_less=True)
        m.d.sync += [sclk.eq(sclk_sync), mosi.eq(mosi_sync), ncs.eq(ncs_sync)]
        last_sclk = Signal(reset_less=True); m.d.sync += last_sclk.eq(sclk)
        sclk_rising = Signal(reset_less=True); sclk_falling = Signal(reset_less=True)
        m.d.sync += [sclk_rising.eq(sclk & ~last_sclk), sclk_falling.eq(~sclk & last_sclk)]
        bit_count = Signal(6, reset_less=True); is_bit7_r = Signal(reset_less=True); is_bit7_f = Signal(reset_less=True); is_bit39_r = Signal(reset_less=True)
        mosi_shift = Signal(40, reset_less=True); miso_shift = Signal(32, reset_less=True); miso_reg = Signal(reset_less=True)
        m.d.comb += self.o_miso.eq(miso_reg)
        with m.If(ncs): m.d.sync += [bit_count.eq(0), is_bit7_r.eq(0), is_bit7_f.eq(0), is_bit39_r.eq(0), miso_reg.eq(0)]
        with m.Else():
            with m.If(sclk_rising): m.d.sync += [bit_count.eq(bit_count + 1), is_bit7_r.eq(bit_count == 6), is_bit39_r.eq(bit_count == 38), mosi_shift.eq((mosi_shift << 1) | mosi)]
            with m.If(sclk_falling): m.d.sync += [is_bit7_f.eq(bit_count == 7), miso_reg.eq(miso_shift[31]), miso_shift.eq(miso_shift << 1)]
        ctrl_reg = Signal(ControlStruct, reset_less=True); tw_low = Signal(32, reset_less=True); tw_high = Signal(16, reset_less=True)
        local_pll = Signal(reset_less=True); local_pps_c = Signal(32, reset_less=True); local_pps_g = Signal(5, reset_less=True)
        m.d.sync += [local_pll.eq(self.pll_locked), local_pps_c.eq(self.pps_count), local_pps_g.eq(self.pps_gen)]
        read_val_pipe = Signal(32, reset_less=True); is_write_latch = Signal(reset_less=True); addr_latch = Signal(7, reset_less=True)
        with m.If(sclk_rising):
            with m.If(is_bit7_r): a = Cat(mosi, mosi_shift[0:6]); m.d.sync += [is_write_latch.eq(mosi_shift[6]), addr_latch.eq(a)]
        is_ctrl = Signal(reset_less=True); is_twlow = Signal(reset_less=True); is_twhigh = Signal(reset_less=True); is_pps = Signal(reset_less=True); is_sig = Signal(reset_less=True)
        m.d.sync += [is_ctrl.eq(addr_latch == WSPRAddr.Control), is_twlow.eq(addr_latch == WSPRAddr.TuningLow), is_twhigh.eq(addr_latch == WSPRAddr.TuningHigh), is_pps.eq(addr_latch == WSPRAddr.PPS), is_sig.eq(addr_latch == WSPRAddr.Sig)]
        v_ctrl = Signal(32); v_twlow = Signal(32); v_twhigh = Signal(32); v_pps = Signal(32); v_sig = Signal(32)
        m.d.comb += [v_ctrl.eq(Mux(is_ctrl, Cat(ctrl_reg.txEnable, ctrl_reg.modeSquare, local_pll, Const(0, 29)), 0)), v_twlow.eq(Mux(is_twlow, tw_low, 0)), v_twhigh.eq(Mux(is_twhigh, Cat(tw_high, Const(0, 16)), 0)), v_pps.eq(Mux(is_pps, local_pps_c, 0)), v_sig.eq(Mux(is_sig, 0x52505357, 0))]
        v_stage1_0 = Signal(32, reset_less=True); v_stage1_1 = Signal(32, reset_less=True)
        m.d.sync += [v_stage1_0.eq(v_ctrl | v_twlow | v_twhigh), v_stage1_1.eq(v_pps | v_sig)]
        m.d.sync += read_val_pipe.eq(v_stage1_0 | v_stage1_1)
        load_miso_en = Signal(reset_less=True); m.d.sync += load_miso_en.eq(is_bit7_f & ~is_write_latch)
        with m.If(sclk_falling & load_miso_en): m.d.sync += [miso_shift.eq(read_val_pipe << 1), miso_reg.eq(read_val_pipe[31])]
        do_write_any = sclk_rising & is_bit39_r & is_write_latch; d_write = Signal(32, reset_less=True); m.d.sync += d_write.eq(Cat(mosi, mosi_shift[0:31]))
        with m.If(do_write_any):
            with m.If(is_ctrl): m.d.sync += [ctrl_reg.txEnable.eq(d_write[0]), ctrl_reg.modeSquare.eq(d_write[1])]
            with m.If(is_twlow): m.d.sync += tw_low.eq(d_write)
            with m.If(is_twhigh): m.d.sync += tw_high.eq(d_write[:16])
        m.d.comb += [self.tuning_word.eq(Cat(tw_low, tw_high)), self.tx_enable.eq(ctrl_reg.txEnable), self.mode_square.eq(ctrl_reg.modeSquare)]
        return m

class Exciter(Elaboratable):
    def __init__(self, pb_pin, pp_pin, lb_pin, lp_pin):
        self.tw = Signal(48); self.mode_square = Signal(); self.tx_enable = Signal()
        self.pb_pin = pb_pin; self.pp_pin = pp_pin; self.lb_pin = lb_pin; self.lp_pin = lp_pin
    def elaborate(self, platform):
        m = Module()
        m.submodules.nco = nco = PipelinedNCO(width=48)
        m.d.comb += nco.tw.eq(self.tw)
        lcg = Signal(32)
        m.submodules.prng = Instance("SB_MAC16", p_BOTADDSUB_UPPERINPUT=2, p_BOTADDSUB_LOWERINPUT=0, p_TOPADDSUB_UPPERINPUT=2, p_TOPADDSUB_LOWERINPUT=1, i_A=lcg[:16], i_B=25173, i_D=13849, p_TOPOUTPUT_SELECT=1, i_CLK=ClockSignal(), i_CE=1, o_O=lcg, o_CO=Signal(), o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal())
        noise = lcg[:16]; ph_r = nco.phase[32:48]; ph_f_32 = Signal(32); ph_f = Signal(16)
        m.submodules.offs = Instance("SB_MAC16", p_TOPADDSUB_LOWERINPUT=0, p_TOPADDSUB_UPPERINPUT=1, i_A=ph_r, i_B=0, i_D=self.tw[32:48] >> 1, p_TOPOUTPUT_SELECT=1, i_CLK=ClockSignal(), i_CE=1, o_O=ph_f_32, o_CO=Signal(), o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal())
        m.d.comb += ph_f.eq(ph_f_32[16:32])
        mul_r = Signal(32); mul_f = Signal(32)
        m.submodules.mr = Instance("SB_MAC16", p_BOTADDSUB_LOWERINPUT=1, p_TOPADDSUB_LOWERINPUT=2, i_A=ph_r, i_B=6, i_D=noise, p_TOPOUTPUT_SELECT=1, p_BOTOUTPUT_SELECT=1, i_CLK=ClockSignal(), i_CE=1, o_O=mul_r, o_CO=Signal(), o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal())
        m.submodules.mf = Instance("SB_MAC16", p_BOTADDSUB_LOWERINPUT=1, p_TOPADDSUB_LOWERINPUT=2, i_A=ph_f, i_B=6, i_D=~noise, p_TOPOUTPUT_SELECT=1, p_BOTOUTPUT_SELECT=1, i_CLK=ClockSignal(), i_CE=1, o_O=mul_f, o_CO=Signal(), o_ACCUMCO=Signal(), o_SIGNEXTOUT=Signal())
        st_r = mul_r[16:19]; st_f = mul_f[16:19]
        tx_p = Signal(6, reset_less=True); mode_p = Signal(6, reset_less=True); m.d.sync += [tx_p.eq(Cat(self.tx_enable, tx_p[:-1])), mode_p.eq(Cat(self.mode_square, mode_p[:-1]))]
        st_f_reg = Signal(3, reset_less=True); m.d.sync += st_f_reg.eq(st_f)
        pb_r = Signal(); pp_r = Signal(); lb_r = Signal(); lp_r = Signal(); sq_r = st_r < 3
        with m.If(tx_p[5]):
            with m.If(mode_p[5]): m.d.comb += [pb_r.eq(sq_r), pp_r.eq(sq_r), lb_r.eq(~sq_r), lp_r.eq(~sq_r)]
            with m.Else(): m.d.comb += [pb_r.eq((st_r == 0) | (st_r == 2)), pp_r.eq(st_r == 1), lb_r.eq((st_r == 3) | (st_r == 5)), lp_r.eq(st_r == 4)]
        pb_f = Signal(); pp_f = Signal(); lb_f = Signal(); lp_f = Signal(); sq_f = st_f_reg < 3
        with m.If(tx_p[5]):
            with m.If(mode_p[5]): m.d.comb += [pb_f.eq(sq_f), pp_f.eq(sq_f), lb_f.eq(~sq_f), lp_f.eq(~sq_f)]
            with m.Else(): m.d.comb += [pb_f.eq((st_f_reg == 0) | (st_f_reg == 2)), pp_f.eq(st_f_reg == 1), lb_f.eq((st_f_reg == 3) | (st_f_reg == 5)), lp_f.eq(st_f_reg == 4)]
        pb_or = Signal(reset_less=True); pp_or = Signal(reset_less=True); lb_or = Signal(reset_less=True); lp_or = Signal(reset_less=True)
        pb_of = Signal(reset_less=True); pp_of = Signal(reset_less=True); lb_of = Signal(reset_less=True); lp_of = Signal(reset_less=True)
        m.d.sync += [pb_or.eq(pb_r), pp_or.eq(pp_r), lb_or.eq(lb_r), lp_or.eq(lp_r), pb_of.eq(pb_f), pp_of.eq(pp_f), lb_of.eq(lb_f), lp_of.eq(lp_f)]
        pinType = 17
        m.submodules.io_pb = Instance("SB_IO", p_PIN_TYPE=pinType, o_PACKAGE_PIN=self.pb_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=pb_or, i_D_OUT_1=pb_of)
        m.submodules.io_pp = Instance("SB_IO", p_PIN_TYPE=pinType, o_PACKAGE_PIN=self.pp_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=pp_or, i_D_OUT_1=pp_of)
        m.submodules.io_lb = Instance("SB_IO", p_PIN_TYPE=pinType, o_PACKAGE_PIN=self.lb_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=lb_or, i_D_OUT_1=lb_of)
        m.submodules.io_lp = Instance("SB_IO", p_PIN_TYPE=pinType, o_PACKAGE_PIN=self.lp_pin, i_OUTPUT_CLK=ClockSignal(), i_D_OUT_0=lp_or, i_D_OUT_1=lp_of)
        return m

class Top(Elaboratable):
    def __init__(self):
        self.clk40 = Signal(name="clk40"); self.gnssPPS = Signal(name="gnssPPS"); self.fpgaNRESET = Signal(name="fpgaNRESET")
        self.fpgaSCLK_pin = Signal(name="fpgaSCLK_pin"); self.fpgaMOSI = Signal(name="fpgaMOSI"); self.fpgaMISO = Signal(name="fpgaMISO"); self.fpgaNCS = Signal(name="fpgaNCS")
        self.rfPushBase = Signal(name="rfPushBase"); self.rfPushPeak = Signal(name="rfPushPeak"); self.rfPullBase = Signal(name="rfPullBase"); self.rfPullPeak = Signal(name="rfPullPeak"); self.driverNEN = Signal(name="driverNEN")
    def elaborate(self, platform):
        m = Module()
        clk90 = Signal(); pll_locked_raw = Signal()
        m.submodules.pll = Instance("SB_PLL40_PAD", p_FEEDBACK_PATH="SIMPLE", p_DIVR=0, p_DIVF=17, p_DIVQ=3, p_FILTER_RANGE=2, i_PACKAGEPIN=self.clk40, i_RESETB=1, i_BYPASS=0, o_PLLOUTCORE=clk90, o_PLLOUTGLOBAL=Signal(), o_LOCK=pll_locked_raw)
        clk90_gb = Signal(); m.submodules.clk_gb = Instance("SB_GB", i_USER_SIGNAL_TO_GLOBAL_BUFFER=clk90, o_GLOBAL_BUFFER_OUTPUT=clk90_gb)
        pll_locked = Signal(); m.submodules.lock_gb = Instance("SB_GB", i_USER_SIGNAL_TO_GLOBAL_BUFFER=pll_locked_raw, o_GLOBAL_BUFFER_OUTPUT=pll_locked)
        sclk_gb = Signal(); m.submodules.sclk_gb = Instance("SB_GB", i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.fpgaSCLK_pin, o_GLOBAL_BUFFER_OUTPUT=sclk_gb)
        m.domains.sync = ClockDomain(); m.d.comb += ClockSignal("sync").eq(clk90_gb)
        rst_sync_raw = Signal(); m.submodules.rst_sync = cdc.FFSynchronizer(~self.fpgaNRESET, rst_sync_raw, reset=1)
        rst_gb = Signal(); m.submodules.rst_gb = Instance("SB_GB", i_USER_SIGNAL_TO_GLOBAL_BUFFER=rst_sync_raw, o_GLOBAL_BUFFER_OUTPUT=rst_gb)
        m.d.comb += ResetSignal("sync").eq(rst_gb)
        m.submodules.freq = freq = FreqCounter(); m.d.comb += freq.sample_pps.eq(self.gnssPPS)
        m.submodules.spi = spi = SPIRegisters()
        m.d.comb += [spi.i_sclk.eq(sclk_gb), spi.i_mosi.eq(self.fpgaMOSI), self.fpgaMISO.eq(spi.o_miso), spi.i_ncs.eq(self.fpgaNCS), spi.pll_locked.eq(pll_locked), spi.pps_count.eq(freq.pps_count), spi.pps_gen.eq(freq.pps_gen)]
        m.submodules.exciter = exciter = Exciter(pb_pin=self.rfPushBase, pp_pin=self.rfPushPeak, lb_pin=self.rfPullBase, lp_pin=self.rfPullPeak)
        m.d.comb += [exciter.tw.eq(spi.tuning_word), exciter.tx_enable.eq(spi.tx_enable & pll_locked), exciter.mode_square.eq(spi.mode_square)]
        m.d.sync += self.driverNEN.eq(~(spi.tx_enable & pll_locked))
        return m

if __name__ == "__main__":
    top = Top()
    ports = [top.clk40, top.gnssPPS, top.fpgaNRESET, top.fpgaSCLK_pin, top.fpgaMOSI, top.fpgaMISO, top.fpgaNCS, top.rfPushBase, top.rfPushPeak, top.rfPullBase, top.rfPullPeak, top.driverNEN]
    with open("top_gen.v", "w") as f: f.write(verilog.convert(top, ports=ports))
    print("Generated top_gen.v")
