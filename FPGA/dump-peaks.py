import numpy as np
import pandas as pd
from scipy.signal import welch

def dump_peaks(csv_filename, sample_rate_hz=180e6):
    data = pd.read_csv(csv_filename)
    signal = data['Amplitude'].values
    frequencies, psd = welch(signal, fs=sample_rate_hz, window='blackmanharris', nperseg=16384)
    psd_db = 10 * np.log10(psd + 1e-20)
    
    # Sort indices by power
    top_indices = np.argsort(psd_db)[-10:][::-1]
    
    print(f"{'Frequency (MHz)':>15} {'Power (dB)':>12}")
    for idx in top_indices:
        print(f"{frequencies[idx]/1e6:15.6f} {psd_db[idx]:12.2f}")

if __name__ == "__main__":
    dump_peaks("rf_output.csv")
