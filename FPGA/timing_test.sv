`timescale 1ns / 100ps
module Top (
    input wire clk40,
    input wire reset,
    output wire rfPushBase,
    output wire rfPushPeak,
    output wire rfPullBase,
    output wire rfPullPeak
);
    wire clk90;
    // Simple bypass or PLL for timing test
    assign clk90 = clk40; 

    Exciter dut (
        .clk90(clk90),
        .reset(reset),
        .tuningWord(32'h12345678),
        .powerThreshold(8'hFF),
        .txEnable(1'b1),
        .rfPushBase(rfPushBase),
        .rfPushPeak(rfPushPeak),
        .rfPullBase(rfPullBase),
        .rfPullPeak(rfPullPeak)
    );
endmodule
