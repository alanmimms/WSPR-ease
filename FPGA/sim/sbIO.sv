`timescale 1ns / 100ps
// Behavioral model for Lattice SB_IO
module SB_IO #(
    parameter [5:0] PIN_TYPE = 6'b000000,
    parameter [0:0] PULLUP = 1'b0,
    parameter [0:0] NEG_TRIGGER = 1'b0,
    parameter IO_STANDARD = "SB_LVCMOS"
) (
    inout  wire  PACKAGE_PIN,
    output logic PACKAGE_PIN_OUT,
    input  logic OUTPUT_CLK,
    input  logic CLOCK_ENABLE,
    input  logic OUTPUT_ENABLE,
    input  logic D_OUT_0,
    input  logic D_OUT_1,
    output logic D_IN_0,
    output logic D_IN_1,
    input  logic INPUT_CLK,
    input  logic LATCH_INPUT_VALUE
);

    logic dout_q_0 = 0;
    logic dout_q_1 = 0;
    
    // Sample the outputs on the clock edges if we are in a registered mode
    always @(posedge OUTPUT_CLK) begin
        dout_q_0 <= D_OUT_0;
    end
    always @(negedge OUTPUT_CLK) begin
        dout_q_1 <= D_OUT_1;
    end

    logic dout;
    always @(*) begin
        if (PIN_TYPE[3:2] == 2'b10) begin
            // Combinatorial (e.g., 6'b011000)
            dout = D_OUT_0;
        end else if (PIN_TYPE[3:2] == 2'b00) begin
            // DDR Registered (e.g., 6'b010000)
            // Outputs dout_q_0 on high phase of OUTPUT_CLK, dout_q_1 on low phase
            dout = OUTPUT_CLK ? dout_q_0 : dout_q_1;
        end else begin
            // Default to D_OUT_0 for others right now to avoid simulation issues
            dout = D_OUT_0;
        end
    end
    
    // For simulation, assume OUTPUT_ENABLE is 1 if not connected
    assign PACKAGE_PIN = dout;
    assign PACKAGE_PIN_OUT = PACKAGE_PIN;

endmodule
