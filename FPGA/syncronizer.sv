`timescale 1ns / 100ps

module Synchronizer #(
  parameter int WIDTH = 1,
  parameter int STAGES = 2,
  parameter [WIDTH-1:0] INIT = 0
)(
  input  logic clk,
  input  logic [WIDTH-1:0] dIn,
  output logic [WIDTH-1:0] dOut
);
  // Unpacked array for registers
  logic [WIDTH-1:0] sregs [0:STAGES-1];

  always_ff @(posedge clk) begin
    sregs[0] <= dIn;
    for (int i = 1; i < STAGES; i = i + 1) begin
      sregs[i] <= sregs[i-1];
    end
  end

  assign dOut = sregs[STAGES-1];

endmodule
