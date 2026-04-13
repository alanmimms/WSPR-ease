`timescale 1ns / 100ps

// =========================================================================
// Emulated Lattice DDR I/O primitive for Functional Testbenches
// =========================================================================
module SB_IO #(
	       parameter [5:0] PIN_TYPE = 6'b011000
	       )(
		 output logic PACKAGE_PIN,
		 input  logic OUTPUT_CLK,
		 input  logic D_OUT_0,
		 input  logic D_OUT_1
		 );
  logic pin_reg;
  // Emulates DDR: D_OUT_0 active on positive cycle, D_OUT_1 on negative
  always @(OUTPUT_CLK or D_OUT_0 or D_OUT_1) begin
    if (OUTPUT_CLK)
      pin_reg = D_OUT_0;
    else
      pin_reg = D_OUT_1;
  end
  assign PACKAGE_PIN = pin_reg;
endmodule

// =========================================================================
// Co-Simulation Verification Testbench
// =========================================================================
module tbCosim;

  logic clk90 = 0;
  always #5.55 clk90 = ~clk90; // ~90 MHz Clock
  
  // Global Stimulus
  logic        reset = 1;
  logic [31:0] tuningWord = 0;
  logic [7:0]  powerThreshold = 8'hFF;
  logic        txEnable = 0;
  
  // Synthesizable Pipeline Outputs
  wire pb_synth, pp_synth, lb_synth, lp_synth;
  
  // Functional Model Outputs
  wire pb_func, pp_func, lb_func, lp_func;
  
  // --- PIPELINE ALIGNMENT ---
  // The synthesizable nibble-based model has exactly a 14-cycle latency
  // from input to output relative to the golden model. We delay the inputs 
  // to the functional model so the outputs toggle on the exact same picosecond.
  localparam int PIPELINE_DELAY = 14; 
  
  logic [31:0] tw_pipe [0:PIPELINE_DELAY-1];
  logic [7:0]  pt_pipe [0:PIPELINE_DELAY-1];
  logic        te_pipe [0:PIPELINE_DELAY-1];
  logic        rst_pipe[0:PIPELINE_DELAY-1];
  
  always_ff @(posedge clk90) begin
    tw_pipe[0]  <= tuningWord;
    pt_pipe[0]  <= powerThreshold;
    te_pipe[0]  <= txEnable;
    rst_pipe[0] <= reset;
    
    for (int i = 1; i < PIPELINE_DELAY; i++) begin
      tw_pipe[i]  <= tw_pipe[i-1];
      pt_pipe[i]  <= pt_pipe[i-1];
      te_pipe[i]  <= te_pipe[i-1];
      rst_pipe[i] <= rst_pipe[i-1];
    end
  end

  // --- INSTANTIATE SYNTHESIZABLE MODEL (DUT) ---
  Exciter dut_synth (
		     .clk90(clk90),
		     .reset(reset),
		     .tuningWord(tuningWord),
		     .powerThreshold(powerThreshold),
		     .txEnable(txEnable),
		     .rfPushBase(pb_synth),
		     .rfPushPeak(pp_synth),
		     .rfPullBase(lb_synth),
		     .rfPullPeak(lp_synth)
		     );
  
  // --- INSTANTIATE FUNCTIONAL MODEL (GOLDEN) ---
  // Fed with the delayed inputs
  ExciterFunctional dut_func (
			      .clk90(clk90),
			      .reset(rst_pipe[PIPELINE_DELAY-1]),
			      .tuningWord(tw_pipe[PIPELINE_DELAY-1]),
			      .powerThreshold(pt_pipe[PIPELINE_DELAY-1]),
			      .txEnable(te_pipe[PIPELINE_DELAY-1]),
			      .rfPushBase(pb_func),
			      .rfPushPeak(pp_func),
			      .rfPullBase(lb_func),
			      .rfPullPeak(lp_func)
			      );

  // --- CONTINUOUS ASSERTION CHECKER ---
  int mismatch_count = 0;
  always @(pb_synth, pp_synth, lb_synth, lp_synth, pb_func, pp_func, lb_func, lp_func) begin
    // Tiny delta delay ensures both combinational SB_IO blocks have settled
    #0.1;
    if ({pb_synth, pp_synth, lb_synth, lp_synth} != {pb_func, pp_func, lb_func, lp_func}) begin
      $display("[%0t] MISMATCH DETECTED! Synth: %b, Func: %b", 
               $time, 
               {pb_synth, pp_synth, lb_synth, lp_synth}, 
               {pb_func, pp_func, lb_func, lp_func});
      mismatch_count++;
    end
  end

  // --- TEST STIMULUS ---
  initial begin
    $dumpfile("tb-cosim.vcd");
    $dumpvars(0, tbCosim);
    
    #50;
    reset = 0;
    txEnable = 1;
    
    // Scenario 1: Clean divisor (30 MHz)
    $display("[%0t] Setting freq to 30 MHz...", $time);
    tuningWord = 32'd1431655765;
    #2000;
    
    // Scenario 2: Fractional/Jitter test (29.99 MHz)
    $display("[%0t] Setting freq to 29.99 MHz...", $time);
    tuningWord = 32'd1431178650;
    #2000;
    
    // Scenario 3: Duty Cycle restriction (approx 50% power)
    $display("[%0t] Throttling power threshold...", $time);
    powerThreshold = 8'h80;
    #2000;
    
    // Scenario 4: Stop TX
    $display("[%0t] Disabling TX...", $time);
    txEnable = 0;
    #500;
    
    if (mismatch_count == 0) begin
      $display("====================================================");
      $display(" SUCCESS: Functional and Synthesizable models match!");
      $display("====================================================");
    end else begin
      $display("====================================================");
      $display(" FAILURE: %0d total mismatches observed.", mismatch_count);
      $display("====================================================");
    end
    $finish;
  end

endmodule // tbCosim
