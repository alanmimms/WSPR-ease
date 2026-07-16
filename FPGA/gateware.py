# RTL for the WSPR-ease FPGA.
# Designed for Lattice iCE40UP5K-SG48.

from amaranth import *
from amaranth.lib import enum, data, cdc
from regs_gen import FPGAAddr

class SPIRegisters(Elaboratable):
    def __init__(self, buildNum=0):
        self.buildNum = buildNum
        
        # SPI Bus Pins
        self.iSCLK = Signal()
        self.iMOSI = Signal()
        self.oMISO = Signal()
        self.iNCS = Signal()
        
        # PPS latched counter value (from Top)
        self.pps_latched = Signal(32)
        
        # Decoded outputs to other clock domains
        self.txEn = Signal(reset_less=True)
        self.modeSq = Signal(reset_less=True)
        self.softReset = Signal(reset_less=True)
        self.modMode = Signal(2, reset_less=True)
        self.amp = Signal(16, reset_less=True)
        self.phase = Signal(16, reset_less=True)
        self.baseDelay = Signal(8, reset_less=True)
        self.delayCoeff = Signal(8, reset_less=True)
        self.paEnThreshold = Signal(16, reset_less=True)
        
        # The crucial IOB-packed MISO output register
        self.oMISOR = Signal(attrs={"iob": "true"}, reset_less=True)

    def elaborate(self, platform):
        m = Module()
        
        # Synchronize SPI inputs to sync clock domain (40MHz TCXO)
        sclkSync = Signal()
        mosiSync = Signal()
        ncsSync = Signal()

        m.submodules += [
            cdc.FFSynchronizer(self.iSCLK, sclkSync),
            cdc.FFSynchronizer(self.iMOSI, mosiSync),
            cdc.FFSynchronizer(self.iNCS, ncsSync)
        ]

        # Edge detection for SCLK
        sclk_prev = Signal()
        m.d.sync += sclk_prev.eq(sclkSync)
        
        sclk_rise = Signal()
        sclk_fall = Signal()
        m.d.comb += [
            sclk_rise.eq(~sclk_prev & sclkSync),
            sclk_fall.eq(sclk_prev & ~sclkSync)
        ]

        # SPI State
        mosi_shift = Signal(39, reset_less=True) # We only need to buffer the last 39 bits
        miso_shift = Signal(32, reset_less=True)
        bit_counter = Signal(6)
        
        # Pipelined Read registers
        internal_read_mux = Signal(32)
        pipelined_read_data = Signal(32, reset_less=True)
        
        # Control Signals
        addr_latched = Signal(7, reset_less=True)
        is_write_latched = Signal(reset_less=True)

        # Handle NCS reset asynchronously to SPI transaction, but synchronous to 40MHz
        with m.If(ncsSync):
            m.d.sync += [
                bit_counter.eq(0),
                self.oMISOR.eq(0)
            ]
        with m.Else():
            
            # ----------------------------------------------------
            # 1. RISING EDGE: Sample Data and Latch Commands
            # ----------------------------------------------------
            with m.If(sclk_rise):
                m.d.sync += [
                    mosi_shift.eq(Cat(mosiSync, mosi_shift[0:38])), # Shift Left
                    bit_counter.eq(bit_counter + 1)
                ]

                # --- The Read Pipeline Trigger (End of Address Byte) ---
                with m.If(bit_counter == 7):
                    m.d.sync += [
                        addr_latched.eq(Cat(mosiSync, mosi_shift[0:6])),
                        is_write_latched.eq(mosi_shift[6])
                    ]

                # --- The Write Trigger (End of Data Bytes) ---
                with m.If(bit_counter == 39):
                    with m.If(is_write_latched):
                        # Extract the 32 LSBs holding our payload
                        write_data = Cat(mosiSync, mosi_shift[0:31])
                        
                        # Direct dictionary-style mapping for writes
                        with m.Switch(addr_latched):
                            with m.Case(FPGAAddr.Control):
                                m.d.sync += [
                                    self.txEn.eq(write_data[0]),
                                    self.modeSq.eq(write_data[1]),
                                    self.softReset.eq(write_data[2]),
                                    self.modMode.eq(write_data[3:5])
                                ]
                            with m.Case(FPGAAddr.PolarMod):
                                m.d.sync += [
                                    self.amp.eq(write_data[0:16]),
                                    self.phase.eq(write_data[16:32])
                                ]
                            with m.Case(FPGAAddr.PhaseDelayCtrl):
                                m.d.sync += [
                                    self.baseDelay.eq(write_data[0:8]),
                                    self.delayCoeff.eq(write_data[8:16]),
                                    self.paEnThreshold.eq(write_data[16:32])
                                ]

            # ----------------------------------------------------
            # 2. FALLING EDGE: Shift Out MISO Data
            # ----------------------------------------------------
            with m.If(sclk_fall):
                with m.If(bit_counter == 8):
                    # 8th falling edge: Load the pipeline and output the first bit immediately
                    m.d.sync += miso_shift.eq(pipelined_read_data << 1)
                    m.d.sync += self.oMISOR.eq(pipelined_read_data[31])
                with m.Elif(bit_counter > 8):
                    # Shift out remaining bits
                    m.d.sync += miso_shift.eq(miso_shift << 1)
                    m.d.sync += self.oMISOR.eq(miso_shift[31])


# ----------------------------------------------------
        # 3. THE PIPELINE: Breaking the 38ns Read Bottleneck
        # ----------------------------------------------------
        
        # Step A: Isolate cross-chip routing from the MUX logic.
        # This flip-flop catches the GNSS counter value as soon as it arrives at the SPI module,
        # spending our 25ns budget entirely on the vertical cross-chip wire journey.
        pps_local = Signal(32, reset_less=True)
        m.d.sync += pps_local.eq(self.pps_latched)

        # Step B: The Local MUX
        # Now the MUX only evaluates signals that are physically co-located.
        with m.Switch(addr_latched):
            with m.Case(FPGAAddr.Control): 
                m.d.comb += internal_read_mux.eq(Cat(self.txEn, self.modeSq, self.softReset, self.modMode, C(0, 27)))
            with m.Case(FPGAAddr.PolarMod):
                m.d.comb += internal_read_mux.eq(Cat(self.amp, self.phase))
            with m.Case(FPGAAddr.PhaseDelayCtrl):
                m.d.comb += internal_read_mux.eq(Cat(self.baseDelay, self.delayCoeff, self.paEnThreshold))
            with m.Case(FPGAAddr.PpsLatch):
                m.d.comb += internal_read_mux.eq(pps_local) # <-- USE THE LOCAL REGISTER HERE
            with m.Case(FPGAAddr.BuildNo):
                m.d.comb += internal_read_mux.eq(self.buildNum)
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
    def __init__(self, sim=False, buildNum=0):
        self.sim = sim
        self.buildNum = buildNum
        
        # Clock Inputs
        self.fpgaSCLKpin = Signal()     # Pin 15
        self.FPGACLK = Signal()         # Pin 35 (from Si5351 CLK0, modulated carrier clock)
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
            self.fpgaSCLKpin,
            self.FPGACLK,
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
        tcxo_gb = Signal()
        m.submodules.tcxo_gb_buf = Instance(
            "SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.tcxo,
            o_GLOBAL_BUFFER_OUTPUT=tcxo_gb
        )
        
        m.domains.sync = ClockDomain("sync")
        m.d.comb += ClockSignal("sync").eq(tcxo_gb)

        # ========================================================
        # 2. Variable Exciter Clock: Si5351 CLK0 -> fpgaclk Domain
        # ========================================================
        # Route Si5351 CLK0 through a global buffer block for the high-frequency RF domain
        fpgaclk_gb = Signal()
        m.submodules.fpgaclk_gb_buf = Instance(
            "SB_GB",
            i_USER_SIGNAL_TO_GLOBAL_BUFFER=self.FPGACLK,
            o_GLOBAL_BUFFER_OUTPUT=fpgaclk_gb
        )
        
        m.domains.fpgaclk = ClockDomain("fpgaclk", reset_less=True)
        m.d.comb += ClockSignal("fpgaclk").eq(fpgaclk_gb)

        # ========================================================
        # 3. SPI Registers (Runs in 40MHz sync domain)
        # ========================================================
        spi = SPIRegisters(buildNum=self.buildNum)
        m.submodules.spi = spi
        
        m.d.comb += [
            spi.iSCLK.eq(self.fpgaSCLKpin),
            spi.iMOSI.eq(self.fpgaMOSI),
            self.fpgaMISO.eq(spi.oMISOR),
            spi.iNCS.eq(self.fpgaNCS)
        ]

        # SPI Soft Reset + Hardware /fpgaNRESET input synchronisation
        nreset_sync = Signal()
        m.submodules.sync_nreset = cdc.FFSynchronizer(self.fpgaNRESET, nreset_sync)
        
        softReset_comb = Signal()
        m.d.comb += softReset_comb.eq(spi.softReset | ~nreset_sync)
        
        softReset = Signal()
        m.d.sync += softReset.eq(softReset_comb)

        # ========================================================
        # 4. PPS Calibration Counter (Runs in 40MHz sync domain)
        # ========================================================
        pps_counter = Signal(32)
        pps_latched_reg = Signal(32)
        
        # Synchronise GNSS 1PPS to 40MHz
        pps_sync = Signal()
        m.submodules.sync_pps = cdc.FFSynchronizer(self.gnssPPS, pps_sync)
        
        last_pps = Signal()
        m.d.sync += last_pps.eq(pps_sync)
        
        pps_rising = Signal()
        m.d.comb += pps_rising.eq(pps_sync & ~last_pps)
        
        m.d.sync += pps_counter.eq(pps_counter + 1)
        with m.If(pps_rising):
            m.d.sync += pps_latched_reg.eq(pps_counter)
            
        m.d.comb += spi.pps_latched.eq(pps_latched_reg)

        # ========================================================
        # 5. I2S Receiver (Oversampled in 40MHz sync domain)
        # ========================================================
        bclk_sync = Signal()
        sync_sync = Signal()
        data_sync = Signal()
        
        m.submodules += [
            cdc.FFSynchronizer(self.txBCLK, bclk_sync),
            cdc.FFSynchronizer(self.txSYNC, sync_sync),
            cdc.FFSynchronizer(self.txI2Sdata, data_sync)
        ]
        
        last_bclk = Signal()
        last_sync = Signal()
        m.d.sync += [
            last_bclk.eq(bclk_sync),
            last_sync.eq(sync_sync)
        ]
        
        bclk_rising = Signal()
        m.d.comb += bclk_rising.eq(bclk_sync & ~last_bclk)
        
        sync_transition = Signal()
        m.d.comb += sync_transition.eq(sync_sync ^ last_sync)
        
        bit_count = Signal(6)
        shift_reg = Signal(16)
        am_val = Signal(16)
        phase_val = Signal(16)
        
        phase_updated_toggle = Signal()
        
        with m.If(sync_transition):
            m.d.sync += bit_count.eq(0)
        with m.Else():
            with m.If(bclk_rising):
                m.d.sync += bit_count.eq(bit_count + 1)
                with m.If((bit_count >= 1) & (bit_count <= 16)):
                    m.d.sync += shift_reg.eq((shift_reg << 1) | data_sync)
                with m.If(bit_count == 16):
                    # Copy out shifted word. Sync low is Left (AM), High is Right (Phase)
                    with m.If(~last_sync):
                        m.d.sync += am_val.eq((shift_reg << 1) | data_sync)
                    with m.Else():
                        m.d.sync += [
                            phase_val.eq((shift_reg << 1) | data_sync),
                            phase_updated_toggle.eq(~phase_updated_toggle)
                        ]

        # ========================================================
        # 6. Dynamic Phase Delay Line (Runs in 40MHz sync domain)
        # ========================================================
        phase_history = [Signal(16, name=f"phase_history_{i}") for i in range(16)]
        last_toggle = Signal()
        m.d.sync += last_toggle.eq(phase_updated_toggle)
        
        # Shift on Right channel I2S frame updates
        with m.If(phase_updated_toggle ^ last_toggle):
            m.d.sync += phase_history[0].eq(phase_val)
            for i in range(15):
                m.d.sync += phase_history[i+1].eq(phase_history[i])
                
        # Stage A: Pipelined multiplication (Runs in sync domain)
        mult_temp = Signal(24)
        m.d.sync += mult_temp.eq(am_val * spi.delayCoeff)
        
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
        # 7. CDC: Phase Latch (sync -> fpgaclk Handshake)
        # ========================================================
        # Safe Clock Domain Crossing for the I2S phase data to the variable RF clock
        phase_to_rf_toggle = Signal()
        with m.If(phase_updated_toggle ^ last_toggle):
            m.d.sync += phase_to_rf_toggle.eq(~phase_to_rf_toggle)
            
        phase_to_rf_toggle_rf = Signal()
        m.submodules.sync_phase_toggle = cdc.FFSynchronizer(
            phase_to_rf_toggle,
            phase_to_rf_toggle_rf,
            o_domain="fpgaclk"
        )
        
        last_phase_toggle_rf = Signal()
        m.d.fpgaclk += last_phase_toggle_rf.eq(phase_to_rf_toggle_rf)
        
        # Pipelined load enable to resolve clock enable fanout timing bottleneck
        phase_load_en = Signal()
        m.d.fpgaclk += phase_load_en.eq(phase_to_rf_toggle_rf ^ last_phase_toggle_rf)
        
        phase_rf = Signal(16)
        with m.If(phase_load_en):
            m.d.fpgaclk += phase_rf.eq(delayed_phase_reg)

        # ========================================================
        # 8. Amplitude Tracking PWM/PDM (Runs in 40MHz sync domain)
        # ========================================================
        amp_sel = Signal(16)
        with m.If(spi.modMode == 0):
            m.d.comb += amp_sel.eq(spi.amp)
        with m.Else():
            m.d.comb += amp_sel.eq(am_val)
            
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
        # 9. Pipelined RF Sequencer (Runs in fpgaclk domain)
        # ========================================================
        # Synchronise Control registers to fpgaclk domain
        txEnable_rf = Signal()
        softReset_rf = Signal()
        modeSquare_rf = Signal()
        modMode_rf = Signal(2)
        staticPhase_rf = Signal(16)
        
        m.submodules.sync_txEn_rf = cdc.FFSynchronizer(spi.txEn, txEnable_rf, o_domain="fpgaclk")
        m.submodules.sync_softRst_rf = cdc.FFSynchronizer(softReset, softReset_rf, o_domain="fpgaclk")
        m.submodules.sync_modeSq_rf = cdc.FFSynchronizer(spi.modeSq, modeSquare_rf, o_domain="fpgaclk")
        
        modMode_rf_temp = Signal(2)
        staticPhase_rf_temp = Signal(16)
        m.d.fpgaclk += [
            modMode_rf_temp.eq(spi.modMode),
            modMode_rf.eq(modMode_rf_temp),
            staticPhase_rf_temp.eq(spi.phase),
            staticPhase_rf.eq(staticPhase_rf_temp)
        ]
        
        # Select target Phase (Registered to Stage 2)
        phase_target_stage2 = Signal(16, attrs={"nosdff": "1"})
        m.d.fpgaclk += phase_target_stage2.eq(Mux(modMode_rf == 2, phase_rf, staticPhase_rf))
        
        # Pipeline register to Stage 3
        phase_target_stage3 = Signal(16, attrs={"nosdff": "1"})
        m.d.fpgaclk += phase_target_stage3.eq(phase_target_stage2)

        # Combined reset register to offload reset logic to the DFF's dedicated SR pin
        seq_reset_rf = Signal(attrs={"keep": "1"})
        modeSquare_seq_rf = Signal(attrs={"keep": "1"})
        modeSquare_dsp_rf = Signal(attrs={"keep": "1"})
        
        m.d.fpgaclk += [
            seq_reset_rf.eq(softReset_rf | ~txEnable_rf),
            modeSquare_seq_rf.eq(modeSquare_rf),
            modeSquare_dsp_rf.eq(modeSquare_rf)
        ]

        # Stage 1: Dual physical counters to minimize feedback loop logic depth (forces 1 LUT feedback)
        next_sq = Array([0 if i >= 4 else i + 1 for i in range(16)])
        next_15 = Array([0 if i >= 14 else i + 1 for i in range(16)])
        
        # Stage 1: Dual counters with locked placement close to the DSP block (column 1)
        next_sq = Array([0 if i >= 4 else i + 1 for i in range(16)])
        next_15 = Array([0 if i >= 14 else i + 1 for i in range(16)])
        
        counter_sq = Signal(4)
        counter_15 = Signal(4)
        
        counter_sq_bits = [Signal(attrs={"bel": f"X1/Y10/lc{i}", "keep": "1"}) for i in range(4)]
        counter_15_bits = [Signal(attrs={"bel": f"X1/Y11/lc{i}", "keep": "1"}) for i in range(4)]
        
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
                i_C=ClockSignal("fpgaclk"),
                i_R=seq_reset_rf,
                i_D=next_sq_val[i],
                o_Q=counter_sq_bits[i]
            )
            m.submodules[f"dff_15_{i}"] = Instance("SB_DFFSR",
                i_C=ClockSignal("fpgaclk"),
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
        
        lut_product_sq_comb = Signal(19, attrs={"keep": "1"})
        lut_product_15_comb = Signal(19, attrs={"keep": "1"})
        
        m.d.comb += [
            lut_product_sq_comb.eq(lut_vals[counter_sq]),
            lut_product_15_comb.eq(lut_vals[counter_15])
        ]
        
        m.d.fpgaclk += [
            lut_product_sq_19.eq(lut_product_sq_comb),
            lut_product_15_19.eq(lut_product_15_comb)
        ]
        
        # Stage 2b: Pipelined Multiplier Coefficient (coeff_rf_stage2)
        coeff_rf_stage2 = Signal(16, attrs={"nosdff": "1"})
        m.d.fpgaclk += coeff_rf_stage2.eq(Mux(modeSquare_dsp_rf, 2, 6))

        # Stage 3: Combine and register LUT Product and Coefficient (1 LUT delay only)
        lut_product_stage2 = Signal(19)
        m.d.comb += lut_product_stage2.eq(Mux(modeSquare_seq_rf, lut_product_sq_19, lut_product_15_19))
        
        lut_product_stage3 = Signal(32, attrs={"nosdff": "1"})
        m.d.fpgaclk += lut_product_stage3.eq(lut_product_stage2)

        coeff_rf_stage3 = Signal(16, attrs={"nosdff": "1"})
        m.d.fpgaclk += coeff_rf_stage3.eq(coeff_rf_stage2)

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
            i_CLK=ClockSignal("fpgaclk"),
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
        
        # Register the outputs in fpgaclk domain
        m.d.fpgaclk += [
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
