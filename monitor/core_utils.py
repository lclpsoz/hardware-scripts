import statistics
import config 

def calculate_stats(readings):
    """
    Calculate min, mean, and max from a list of numeric readings.
    """
    if not readings:
        return "N/A", "N/A", "N/A"
    numeric_readings = [r for r in readings if isinstance(r, (int, float))]
    if not numeric_readings:
        return "N/A", "N/A", "N/A"
    min_val = min(numeric_readings)
    max_val = max(numeric_readings)
    mean_val = round(statistics.mean(numeric_readings))
    return min_val, mean_val, max_val

def get_freq_color(freq_value):
    """
    Determines the color for a given CPU frequency value.
    """
    if not isinstance(freq_value, int): 
        return config.Colors.DIM 
    if 800 <= freq_value < 2000:
        return config.Colors.OKBLUE 
    elif 2000 <= freq_value < 2500:
        return config.Colors.OKGREEN
    elif 2500 <= freq_value < 2950:
        return config.Colors.WARNING 
    elif 2950 <= freq_value < 3800:
        return f"{config.Colors.BOLD}{config.Colors.WARNING}" 
    elif freq_value >= 3800:
        return config.Colors.FAIL 
    else: 
        return config.Colors.OKBLUE 

def get_temp_color(temp_value, threshold_ok):
    """
    Determines the color for a given CPU temperature value.
    """
    if not isinstance(temp_value, int): 
        return config.Colors.DIM
    if temp_value < 50:
        return config.Colors.OKBLUE  
    elif 50 <= temp_value < 70: 
        return config.Colors.OKGREEN 
    elif 70 <= temp_value < 85: 
        return config.Colors.WARNING 
    elif 85 <= temp_value < threshold_ok: 
        return f"{config.Colors.BOLD}{config.Colors.WARNING}" 
    elif temp_value >= threshold_ok:
        return config.Colors.FAIL    
    else: 
        return config.Colors.DIM