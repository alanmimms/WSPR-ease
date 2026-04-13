import numpy as np
import matplotlib.pyplot as plt

def calculate_lc_filter_response(frequencies_hz, components, z0=50.0):
    """Calculates S21 magnitude response (in dB) using ABCD matrices."""
    f = np.maximum(frequencies_hz, 1.0) 
    w = 2 * np.pi * f
    s21_db = np.zeros(len(w))
    
    for i, freq_rad in enumerate(w):
        abcd = np.array([[1.0 + 0j, 0.0 + 0j], 
                         [0.0 + 0j, 1.0 + 0j]])
        
        for comp_type, value in components:
            if comp_type == 'C':
                abcd = abcd @ np.array([[1, 0], [1j * freq_rad * value, 1]])
            elif comp_type == 'L':
                abcd = abcd @ np.array([[1, 1j * freq_rad * value], [0, 1]])
                
        A, B = abcd[0, 0], abcd[0, 1]
        C, D = abcd[1, 0], abcd[1, 1]
        
        S21 = 2 / (A + B/z0 + C*z0 + D)
        magnitude = np.abs(S21)
        s21_db[i] = -160.0 if magnitude < 1e-8 else 20 * np.log10(magnitude)
        
    return s21_db

def monte_carlo_filter(frequencies_hz, base_components, tolerance_pct=5.0, trials=200):
    """
    Runs multiple simulated builds of the filter with random component variations.
    Returns the nominal response, the absolute worst-case response, and all trial runs.
    """
    print(f"Running {trials} Monte Carlo iterations with +/-{tolerance_pct}% tolerance...")
    
    # Pre-allocate array to hold all frequency responses
    all_responses = np.zeros((trials, len(frequencies_hz)))
    tol_frac = tolerance_pct / 100.0
    
    for i in range(trials):
        perturbed_components = []
        for comp_type, nominal_val in base_components:
            # Generate random variation between (1 - tol) and (1 + tol)
            variation = np.random.uniform(1.0 - tol_frac, 1.0 + tol_frac)
            perturbed_components.append((comp_type, nominal_val * variation))
            
        all_responses[i, :] = calculate_lc_filter_response(frequencies_hz, perturbed_components)
        
    # Calculate bounds
    nominal_response = calculate_lc_filter_response(frequencies_hz, base_components)
    
    # For regulatory compliance, we care about the "worst-case" upper bound 
    # (i.e., the least amount of attenuation at harmonic frequencies)
    worst_case_response = np.max(all_responses, axis=0)
    best_case_response = np.min(all_responses, axis=0)
    
    return nominal_response, worst_case_response, best_case_response, all_responses

# ====================================================================
# Example Usage
# ====================================================================

# Assuming 'frequencies' and 'psd_dbc' are already generated from your FFT script
# frequencies = ... 
# psd_dbc = ...

# Baseline components for an 80m Low Pass Filter
lpf_80m = [
    ('C', 1100e-12),
    ('L', 2.5e-6),  
    ('C', 2200e-12),
    ('L', 2.5e-6),  
    ('C', 1100e-12) 
]

# Run 500 trials with +/- 10% component tolerances
nominal_db, worst_db, best_db, all_runs = monte_carlo_filter(
    frequencies, 
    lpf_80m, 
    tolerance_pct=10.0, 
    trials=500
)

# Calculate the worst-case antenna port spectrum
worst_case_spectrum_dbc = psd_dbc + worst_db

# --- Plotting the Results ---
plt.figure(figsize=(12, 7))

# 1. Plot the envelope of all Monte Carlo trials (Shaded Region)
plt.fill_between(frequencies / 1e6, best_db, worst_db, color='lightblue', alpha=0.5, 
                 label='+/- 10% Component Variation Spread')

# 2. Plot the Nominal Filter Response
plt.plot(frequencies / 1e6, nominal_db, color='blue', linestyle='--', label='Nominal Filter')

# 3. Plot the Raw FPGA Spectrum
plt.plot(frequencies / 1e6, psd_dbc, color='lightgrey', label='Raw FPGA Output')

# 4. Plot the Worst-Case Antenna Port Output
plt.plot(frequencies / 1e6, worst_case_spectrum_dbc, color='red', linewidth=1.5, 
         label='Worst-Case Antenna Spectrum')

plt.xlim(0, 30)
plt.ylim(-120, 10)
plt.title("WSPR Output Spectrum with Monte Carlo Filter Analysis")
plt.xlabel("Frequency (MHz)")
plt.ylabel("Magnitude (dBc)")
plt.legend()
plt.grid(True, which="both", ls="-", alpha=0.5)
plt.tight_layout()
plt.show()
