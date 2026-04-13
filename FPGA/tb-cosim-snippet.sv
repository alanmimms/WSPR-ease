// Inside your testbench:
integer file_out;
real analog_val;

initial begin
  file_out = $fopen("rf_output.csv", "w");
  // Write header
  $fwrite(file_out, "Time_ns,Frequency_Hz,Amplitude\n");
end

  // Continuously log the output
  always @(posedge clk90) begin
    // Convert 1-2-1 gates back to analog integer equivalents
    if (pb_func && pp_func)      analog_val = 2.0;
    else if (pb_func)            analog_val = 1.0;
    else if (lb_func && lp_func) analog_val = -2.0;
    else if (lb_func)            analog_val = -1.0;
    else                         analog_val = 0.0;

    $fwrite(file_out, "%0t,%0d,%f\n", $time, current_test_freq, analog_val);
  end
