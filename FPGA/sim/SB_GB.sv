`timescale 1ns / 1ps
// Simulation-only behavioral model for Lattice SB_GB (Global Buffer)
module SB_GB (
    input  logic USER_SIGNAL_TO_GLOBAL_BUFFER,
    output logic GLOBAL_BUFFER_OUTPUT
);
    assign GLOBAL_BUFFER_OUTPUT = USER_SIGNAL_TO_GLOBAL_BUFFER;
endmodule
