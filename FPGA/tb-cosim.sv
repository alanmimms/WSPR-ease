`timescale 1ns / 100ps

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
  integer      current_test_freq = 0;
  
  // Synthesizable Pipeline Outputs
  wire pb_synth, pp_synth, lb_synth, lp_synth;
  
  // Functional Model Outputs
  wire pb_func, pp_func, lb_func, lp_func;
  
  // --- LOGGING ---
  integer file_out;
  real analog_val;

  initial begin
    file_out = $fopen("rf_output.csv", "w");
    $fwrite(file_out, "Time_ns,Frequency_Hz,Amplitude\n");
  end

  // Log at 1ns resolution to see DDR sub-cycle transitions
  always #1 begin
    // Convert gates back to analog integer equivalents
    if (pp_synth)                 analog_val = 2.0;
    else if (pb_synth)            analog_val = 1.0;
    else if (lp_synth)            analog_val = -2.0;
    else if (lb_synth)            analog_val = -1.0;
    else                         analog_val = 0.0;

    $fwrite(file_out, "%0t,%0d,%f\n", $time, current_test_freq, analog_val);
  end

  // --- PIPELINE ALIGNMENT ---
  // Final production model latency = 11. Functional = 2. Delay = 9.
  localparam int PIPELINE_DELAY = 9; 

  
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
  ExciterFunctional #(.SQUARE_WAVE(1)) dut_func (
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
    
    // Scenario 1: 5 MHz Frequency
    $display("[%0t] Setting freq to 5 MHz...", $time);
    current_test_freq = 5000000;
    // tuningWord = (5.0 / 90) * 2^32 = 238609294
    tuningWord = 32'd238609294;
    #1000000;
    
    // Scenario 4: Stop TX
    $display("[%0t] Disabling TX...", $time);
    txEnable = 0;
    #500;
    
    $fclose(file_out);
    
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
