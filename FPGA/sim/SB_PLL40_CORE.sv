`timescale 1ns / 100ps
// Simulation-only behavioral model for Lattice SB_PLL40_CORE
`default_nettype none
module SB_PLL40_CORE #(
    parameter FEEDBACK_PATH = "SIMPLE",
    parameter integer DIVR = 0,
    parameter integer DIVF = 0,
    parameter integer DIVQ = 0,
    parameter integer FILTER_RANGE = 0
) (
    input  logic REFERENCECLK,
    output logic PLLOUTCORE,
    output logic LOCK,
    input  logic RESETB,
    input  logic BYPASS
);

    // This is a drastically simplified, simulation-only model to avoid timing hangs.
    
    reg [1:0] counter = 0;
    reg lock_reg = 0;
    assign LOCK = lock_reg;
    
    assign PLLOUTCORE = counter[1];

    always @(posedge REFERENCECLK or negedge RESETB) begin
        if (!RESETB) begin
            counter <= 0;
            lock_reg <= 0;
        end else begin
            // "Lock" after a few cycles
            lock_reg <= 1; 
            counter <= counter + 1;
        end
    end

endmodule
