`timescale 1ns / 100ps
// Simulation-only behavioral model for Lattice SB_PLL40_PAD
module SB_PLL40_PAD #(
    parameter FEEDBACK_PATH = "SIMPLE",
    parameter [3:0] DIVR = 4'b0000,
    parameter [6:0] DIVF = 7'b0000000,
    parameter [2:0] DIVQ = 3'b000,
    parameter [2:0] FILTER_RANGE = 3'b000
) (
    input  logic PACKAGEPIN,
    output logic PLLOUTCORE,
    output logic PLLOUTGLOBAL,
    output logic LOCK,
    input  logic RESETB,
    input  logic BYPASS
);

    // Simplistic simulation model: PLLOUTCORE = PACKAGEPIN
    // Real hardware would multiply/divide, but for logic simulation
    // just passing it through (or a fixed ratio) is often sufficient
    // if the testbench doesn't depend on exact phase/freq.
    
    reg lock_reg = 0;
    assign LOCK = lock_reg;
    assign PLLOUTCORE = PACKAGEPIN; // For simulation simplicity
    assign PLLOUTGLOBAL = PACKAGEPIN;

    always @(posedge PACKAGEPIN or negedge RESETB) begin
        if (!RESETB) begin
            lock_reg <= 0;
        end else begin
            lock_reg <= 1;
        end
    end

endmodule
