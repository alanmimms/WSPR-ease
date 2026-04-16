#!/usr/bin/env python3
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.signal import welch, find_peaks

def analyze_spectrum(csv_filename, sample_rate_hz=180e6):
    print(f"Loading data from {csv_filename}...")
    
    # 1. Load the CSV data
    # Assuming CSV format: Time_ns, Frequency_Hz, Amplitude
    try:
        data = pd.read_csv(csv_filename)
        signal = data['Amplitude'].values
    except FileNotFoundError:
        print(f"Error: Could not find '{csv_filename}'. Make sure your SV simulation has run.")
        return

    num_samples = len(signal)
    sim_time_sec = num_samples / sample_rate_hz
    print(f"Loaded {num_samples:,} samples. Simulated time: {sim_time_sec * 1000:.2f} ms")

    # 2. Configure the FFT / Welch Parameters
    # nperseg defines the frequency resolution. Higher nperseg = finer resolution 
    # but more noise variance. It should be a power of 2.
    # For a ~1M sample dataset (2^20), 2^16 or 2^17 is a great sweet spot.
    if num_samples >= 2**20:
        nperseg = 2**17 
    elif num_samples >= 2**16:
        nperseg = 2**14
    else:
        nperseg = num_samples // 4

    freq_res = sample_rate_hz / nperseg
    print(f"FFT segment size: {nperseg:,} points. Frequency resolution: {freq_res:.2f} Hz")

    # 3. Calculate Power Spectral Density
    # Blackman-Harris window provides >90dB of side-lobe suppression, 
    # perfect for spotting close-in phase noise artifacts.
    print("Calculating FFT / Power Spectral Density...")
    frequencies, psd = welch(
        signal, 
        fs=sample_rate_hz, 
        window='blackmanharris', 
        nperseg=nperseg,
        scaling='spectrum' # 'spectrum' returns units of V**2, good for discrete tones
    )

    # 4. Normalize to the Carrier (dBc)
    # Convert linear power to logarithmic (dB)
    psd_db = 10 * np.log10(psd + 1e-20) # Add tiny offset to avoid log(0)
    
    # Find the peak (the fundamental carrier)
    carrier_idx = np.argmax(psd_db)
    carrier_freq = frequencies[carrier_idx]
    carrier_power = psd_db[carrier_idx]
    
    # Normalize everything so the carrier sits exactly at 0 dBc
    psd_dbc = psd_db - carrier_power

    print(f"Detected Carrier at {carrier_freq / 1e6:.6f} MHz")

    # Detect Harmonics
    for h in [3, 5]:
        h_freq = carrier_freq * h
        # Wrap around Nyquist if necessary (aliasing)
        aliased_h_freq = h_freq
        while aliased_h_freq > (sample_rate_hz / 2):
            aliased_h_freq = abs(sample_rate_hz - aliased_h_freq)
        
        # Find the index of the closest frequency
        h_idx = np.argmin(np.abs(frequencies - aliased_h_freq))
        # Look in a small window around h_idx for the local peak
        window = 10
        local_idx = h_idx - window + np.argmax(psd_dbc[max(0, h_idx-window):min(len(psd_dbc), h_idx+window)])
        
        h_power = psd_dbc[local_idx]
        print(f"{h}rd Harmonic (aliased at {frequencies[local_idx]/1e6:.6f} MHz): {h_power:.2f} dBc")

    # 5. Plot the Spectrum
    print("Generating plot...")
    plt.figure(figsize=(12, 7))
    plt.plot(frequencies / 1e6, psd_dbc, color='navy', linewidth=1.0)
    
    # Formatting
    plt.title(f"1-2-1 Synthesized RF Spectrum\nCarrier: {carrier_freq/1e6:.4f} MHz | fs: {sample_rate_hz/1e6:.1f} Msps")
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Relative Magnitude (dBc)")
    
    # Set useful viewing limits
    # Look at the full Nyquist zone (0 to fs/2)
    plt.xlim(0, (sample_rate_hz / 2) / 1e6) 
    plt.ylim(-120, 10) # 10dB of headroom, floor at -120 dBc
    
    # Add a marker at the carrier
    plt.plot(carrier_freq / 1e6, 0, 'ro', label=f'Carrier ({carrier_freq/1e6:.2f} MHz)')
    
    plt.grid(True, which="both", ls="-", alpha=0.5)
    plt.legend()
    plt.tight_layout()
    
    # Save and show
    plt.savefig('spectrum_output.png', dpi=300)
    plt.show()

if __name__ == "__main__":
    # Ensure you have the required libraries installed:
    # pip install numpy pandas scipy matplotlib
    analyze_spectrum("rf_output.csv")
    
