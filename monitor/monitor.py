import sys
import datetime
import argparse
import time
import shutil # For shutil.which
import config # Import the config module
from system_query import get_cpu_temp, get_all_cpu_freqs_single_sample, get_max_cpu_freq_single_sample, get_cpupower_max_freq_ghz, set_cpupower_max_freq_mhz
from core_utils import calculate_stats, get_temp_color, get_freq_color

def print_header():
    """Prints the table header."""
    # Previous: ts_w, temp_w, cpumax_w, freq_w, status_w = 16, 22, 15, 20, 12
    # New: temp_w 22->19, cpumax_w 15->10. Others from previous changes.
    ts_w, temp_w, cpumax_w, freq_w, status_w = 16, 19, 10, 20, 12
    config.COLUMN_WIDTHS = { # Store for main_loop to use for clearing line
        "ts_w": ts_w, "temp_w": temp_w, "cpumax_w": cpumax_w,
        "freq_w": freq_w, "status_w": status_w
    }
    header_str = (
        f"{config.Colors.BOLD}{config.Colors.HEADER}{'Timestamp':<{ts_w}}{config.Colors.ENDC} | "
        # Changed Temp Header from 'Temp (°C) Min¦Mean¦Max'
        f"{config.Colors.BOLD}{config.Colors.HEADER}{'Temp Min¦Mean¦Max':<{temp_w}}{config.Colors.ENDC} | "
        # Changed CPU Max Header from 'CPU Max (GHz)'
        f"{config.Colors.BOLD}{config.Colors.HEADER}{'CPU Max':<{cpumax_w}}{config.Colors.ENDC} | "
        f"{config.Colors.BOLD}{config.Colors.HEADER}{'Freq Min¦Mean¦MaxMHz':<{freq_w}}{config.Colors.ENDC} | "
        f"{config.Colors.BOLD}{config.Colors.HEADER}{'Temp Status':<{status_w}}{config.Colors.ENDC}"
    )
    separator_str = (
        f"{config.Colors.DIM}{'-'*ts_w}{config.Colors.ENDC} | "
        f"{config.Colors.DIM}{'-'*temp_w}{config.Colors.ENDC} | "
        f"{config.Colors.DIM}{'-'*cpumax_w}{config.Colors.ENDC} | "
        f"{config.Colors.DIM}{'-'*freq_w}{config.Colors.ENDC} | "
        f"{config.Colors.DIM}{'-'*status_w}{config.Colors.ENDC}"
    )
    print(header_str)
    print(separator_str)
    # Calculate plain text line width including separators " | " (3 chars each, 4 separators)
    return ts_w + 3 + temp_w + 3 + cpumax_w + 3 + freq_w + 3 + status_w


def main_loop(args_param): # Renamed args to args_param to avoid conflict with module name
    """
    Main function to run the CPU monitoring script.
    """
    plain_text_line_width = print_header()
    # Get column widths from config where print_header stored them
    ts_w = config.COLUMN_WIDTHS["ts_w"]
    temp_w = config.COLUMN_WIDTHS["temp_w"]
    cpumax_w = config.COLUMN_WIDTHS["cpumax_w"]
    freq_w = config.COLUMN_WIDTHS["freq_w"]
    status_w = config.COLUMN_WIDTHS["status_w"]

    throttling_cooldown = 0 # Cooldown counter for throttling

    try:
        while True:
            timestamp = datetime.datetime.now().strftime("%m-%d %H:%M:%S") # Year removed
            colored_timestamp = f"{config.Colors.OKCYAN}{timestamp}{config.Colors.ENDC}"

            freq_readings_collector = []
            temp_readings_collector = []

            if not args_param.quiet:
                sampling_message_prefix = f"{config.Colors.DIM}Sampling ({args_param.num_samples}x @ {args_param.sample_interval}s, FreqMode: {args_param.freq_mode}): {config.Colors.ENDC}"
                print(sampling_message_prefix, end="")
                sys.stdout.flush()

            for i in range(args_param.num_samples):
                current_temp = get_cpu_temp()
                if current_temp is not None:
                    temp_readings_collector.append(current_temp)

                if args_param.freq_mode == "all_cores":
                    current_sample_all_core_freqs = get_all_cpu_freqs_single_sample()
                    if current_sample_all_core_freqs:
                        freq_readings_collector.extend(current_sample_all_core_freqs)
                else:
                    current_max_freq_one_sample = get_max_cpu_freq_single_sample()
                    if current_max_freq_one_sample is not None:
                        freq_readings_collector.append(current_max_freq_one_sample)

                if not args_param.quiet:
                    print(f"{config.Colors.DIM}.{config.Colors.ENDC}", end="")
                    sys.stdout.flush()

                if i < args_param.num_samples - 1:
                    time.sleep(args_param.sample_interval)

            if not args_param.quiet:
                print('\r' + ' ' * plain_text_line_width + '\r', end='')
                sys.stdout.flush()

            if args_param.verbose:
                # Print to stderr to avoid messing with stdout's \r logic
                sys.stderr.write(f"{config.Colors.DIM}DEBUG [{timestamp}] Temp Readings (Cycle): {temp_readings_collector}{config.Colors.ENDC}\n")
                sys.stderr.write(f"{config.Colors.DIM}DEBUG [{timestamp}] Freq Readings (Cycle, Mode: {args_param.freq_mode}): {freq_readings_collector}{config.Colors.ENDC}\n")
                sys.stderr.flush()


            freq_min, freq_mean, freq_max = calculate_stats(freq_readings_collector)
            temp_min, temp_mean, temp_max = calculate_stats(temp_readings_collector)

            # Get current cpupower max frequency for display
            cpupower_allowed_max_ghz_val = get_cpupower_max_freq_ghz() # Call it fresh for display
            if cpupower_allowed_max_ghz_val != "N/A" and isinstance(cpupower_allowed_max_ghz_val, float):
                cpupower_max_str = f"{cpupower_allowed_max_ghz_val:.2f}"
            else:
                cpupower_max_str = "N/A"
            colored_cpupower_max_str = f"{config.Colors.OKCYAN}{cpupower_max_str:>5}{config.Colors.ENDC}"


            temp_status_output = f"{config.Colors.DIM}N/A{config.Colors.ENDC}"
            if isinstance(temp_max, int):
                if temp_max < args_param.temp_threshold_ok:
                    temp_status_output = f"{config.Colors.OKGREEN}OK{config.Colors.ENDC}"
                else:
                    num_chars = temp_max - args_param.temp_threshold_ok + 1
                    if num_chars < 0: num_chars = 0
                    viz = args_param.temp_viz_char * num_chars
                    if not viz and temp_max >= args_param.temp_threshold_ok :
                        viz = args_param.temp_viz_char
                    if temp_max >= args_param.temp_threshold_ok + 10:
                        temp_status_output = f"{config.Colors.FAIL}{viz}{config.Colors.ENDC}"
                    elif temp_max >= args_param.temp_threshold_ok:
                        temp_status_output = f"{config.Colors.WARNING}{viz}{config.Colors.ENDC}"

            t_min_col = get_temp_color(temp_min, args_param.temp_threshold_ok)
            t_mean_col = get_temp_color(temp_mean, args_param.temp_threshold_ok)
            t_max_col = get_temp_color(temp_max, args_param.temp_threshold_ok)

            temp_min_str = f"{t_min_col}{str(temp_min):>3}{config.Colors.ENDC if t_min_col != config.Colors.DIM else ''}"
            if temp_mean == "N/A":
                colored_temp_mean_val = f"{config.Colors.DIM}{str(temp_mean):>3}{config.Colors.ENDC}"
            else:
                colored_temp_mean_val = f"{t_mean_col}{str(temp_mean):>3}{config.Colors.ENDC if t_mean_col != config.Colors.DIM else ''}"
            temp_max_str = f"{t_max_col}{str(temp_max):>3}{config.Colors.ENDC if t_max_col != config.Colors.DIM else ''}"
            temp_stats_str = f"{temp_min_str} ¦{colored_temp_mean_val} ¦{temp_max_str}"

            f_min_col = get_freq_color(freq_min)
            f_mean_col = get_freq_color(freq_mean)
            f_max_col = get_freq_color(freq_max)

            freq_min_str = f"{f_min_col}{str(freq_min):>4}{config.Colors.ENDC if f_min_col != config.Colors.DIM else ''}"
            if freq_mean == "N/A":
                colored_freq_mean_val = f"{config.Colors.DIM}{str(freq_mean):>4}{config.Colors.ENDC}"
            else:
                colored_freq_mean_val = f"{f_mean_col}{str(freq_mean):>4}{config.Colors.ENDC if f_mean_col != config.Colors.DIM else ''}"
            freq_max_str = f"{f_max_col}{str(freq_max):>4}{config.Colors.ENDC if f_max_col != config.Colors.DIM else ''}"
            freq_stats_str = f"{freq_min_str}¦{colored_freq_mean_val}¦{freq_max_str}"


            _ts_plain = timestamp
            # Changed: _t_stats_plain to accurately reflect plain text of temp_stats_str
            # e.g., " 48 ¦ 49 ¦ 51" (length 15)
            _t_stats_plain = f"{str(temp_min):>3} ¦{str(temp_mean):>3} ¦{str(temp_max):>3}"
            _cpumax_plain = f"{cpupower_max_str:>5}"
            _f_stats_plain = f"{str(freq_min):>4}¦{str(freq_mean):>4}¦{str(freq_max):>4}"


            _temp_status_plain = temp_status_output
            for color_code_key in dir(config.Colors): # Use config.Colors
                if not color_code_key.startswith("__"):
                    color_code_val = getattr(config.Colors, color_code_key)
                    if isinstance(color_code_val, str) and color_code_val.startswith('\033'):
                        _temp_status_plain = _temp_status_plain.replace(color_code_val, "")

            part_ts = f"{colored_timestamp}"
            part_temp = f"{temp_stats_str} {config.Colors.DIM}°C{config.Colors.ENDC}" # Unit " °C" still here
            part_cpumax = f"{colored_cpupower_max_str} {config.Colors.DIM}GHz{config.Colors.ENDC}" # Unit " GHz" still here
            part_freq = f"{freq_stats_str}{config.Colors.DIM}MHz{config.Colors.ENDC}"
            part_status = f"{temp_status_output}"

            len_part_ts_actual = len(_ts_plain)
            # Changed: len_part_temp_actual calculation based on new _t_stats_plain and " °C" suffix (length 3)
            len_part_temp_actual = len(_t_stats_plain) + 3
            len_part_cpumax_actual = len(_cpumax_plain) + 4 # Remains correct for " GHz" (length 4)
            len_part_freq_actual = len(_f_stats_plain) + 3
            len_part_status_actual = len(_temp_status_plain)

            print(
                f"{part_ts}{' ' * (ts_w - len_part_ts_actual)} | "
                f"{part_temp}{' ' * (temp_w - len_part_temp_actual)} | "
                f"{part_cpumax}{' ' * (cpumax_w - len_part_cpumax_actual)} | "
                f"{part_freq}{' ' * (freq_w - len_part_freq_actual)} | "
                f"{part_status}{' ' * (status_w - len_part_status_actual)}"
            )

            # --- Throttling Logic ---
            if config.CPUPOWER_CMD_PATH: # Only attempt throttling if cpupower is available
                if throttling_cooldown > 0:
                    throttling_cooldown -= 1
                # Ensure values are numbers before comparison
                elif isinstance(temp_max, int) and isinstance(temp_mean, int) and isinstance(freq_max, int):
                    count_over_threhold = 0
                    if temp_max >= config.DEFAULT_TEMP_THRESHOLD_OK:
                        count_over_threhold += 1
                    if temp_mean >= config.DEFAULT_MEAN_TEMP_THRESHOLD:
                        count_over_threhold += 1
                    if count_over_threhold > 0:
                        if freq_max != "N/A" and isinstance(freq_max, int):
                            new_target_max_mhz = freq_max - 200 * count_over_threhold
                            new_target_max_mhz_rounded = max(config.MIN_MAX_CPU_FREQ_MHZ, (new_target_max_mhz // 100) * 100)

                            if new_target_max_mhz_rounded < freq_max:
                                if set_cpupower_max_freq_mhz(new_target_max_mhz_rounded):
                                    throttling_cooldown = 1
                                    if args_param.verbose or not args_param.quiet:
                                        sys.stderr.write(f"{config.Colors.FAIL}THROTTLING: CPU too hot (MaxT:{temp_max}°C, MeanT:{temp_mean}°C). Attempting to set max freq to {new_target_max_mhz_rounded}MHz.{config.Colors.ENDC}\n")
                                        sys.stderr.flush()
            sys.stdout.flush()

    except KeyboardInterrupt:
        print(f"\n{config.Colors.OKGREEN}Monitoring stopped by user.{config.Colors.ENDC}")
    except Exception as e:
        print(f"\n{config.Colors.FAIL}An unexpected error occurred: {e}{config.Colors.ENDC}")
        if config.cli_args and config.cli_args.verbose:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor CPU temperature and frequency.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-n", "--num-samples", type=int, default=config.DEFAULT_NUM_SAMPLES,
                        help="Number of readings to take for calculating min/mean/max in each cycle.")
    parser.add_argument("-i", "--sample-interval", type=float, default=config.DEFAULT_SAMPLE_INTERVAL,
                        help="Seconds to sleep between samples.")
    parser.add_argument("-t", "--temp-threshold-ok", type=int, default=config.DEFAULT_TEMP_THRESHOLD_OK,
                        help="Temperature (Celsius) threshold for 'OK' status (effects visualization and red color).")
    parser.add_argument("--temp-viz-char", type=str, default=config.DEFAULT_TEMP_VIZ_CHAR,
                        help="Character used for temperature visualization above threshold.")
    parser.add_argument("--freq-mode", type=str, default=config.DEFAULT_FREQ_MODE, choices=["all_cores", "max_per_sample"],
                        help="Frequency collection mode: "
                             "'all_cores' (default): Collects all core frequencies for true min/mean/max. "
                             "'max_per_sample': Collects only the max core frequency at each sample point.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose output for debugging.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress informational messages like sampling progress.")

    config.cli_args = parser.parse_args()

    # --- Initial checks ---
    SENSORS_CMD_PATH_CHECK = shutil.which("sensors")
    if SENSORS_CMD_PATH_CHECK is None:
        print(f"{config.Colors.FAIL}Error: 'sensors' command not found. Please install lm-sensors and ensure it's in your PATH.{config.Colors.ENDC}", file=sys.stderr)
        sys.exit(1)
    else:
        config.SENSORS_CMD_PATH = SENSORS_CMD_PATH_CHECK
        if config.cli_args and config.cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: Found 'sensors' command at: {config.SENSORS_CMD_PATH}{config.Colors.ENDC}", file=sys.stderr)

    CPUPOWER_CMD_PATH_CHECK = shutil.which("cpupower")
    if CPUPOWER_CMD_PATH_CHECK is None:
        if not config.cli_args.quiet:
            print(f"{config.Colors.WARNING}Warning: 'cpupower' command not found. CPU Max (GHz) display and automatic frequency throttling will be disabled.{config.Colors.ENDC}", file=sys.stderr)
        config.CPUPOWER_CMD_PATH = None
    else:
        config.CPUPOWER_CMD_PATH = CPUPOWER_CMD_PATH_CHECK
        if config.cli_args and config.cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: Found 'cpupower' command at: {config.CPUPOWER_CMD_PATH}{config.Colors.ENDC}", file=sys.stderr)

        if not config.cli_args.quiet:
            print(f"{config.Colors.DIM}INFO: Initializing cpupower access (may prompt for sudo password if needed)...{config.Colors.ENDC}", file=sys.stderr)
        initial_freq_check = get_cpupower_max_freq_ghz()
        if not config.cli_args.quiet:
            if initial_freq_check != "N/A":
                print(f"{config.Colors.DIM}INFO: cpupower access successful. Current max policy: {initial_freq_check} GHz.{config.Colors.ENDC}", file=sys.stderr)
            else:
                print(f"{config.Colors.WARNING}WARN: cpupower command found, but failed to get frequency policy. Throttling may not work. Check sudo permissions or 'cpupower' output.{config.Colors.ENDC}", file=sys.stderr)
        sys.stderr.flush()


    try:
        with open("/proc/cpuinfo", "r") as f:
            f.readline()
    except FileNotFoundError:
        print(f"{config.Colors.FAIL}Error: Cannot access /proc/cpuinfo. This script is intended for Linux systems.{config.Colors.ENDC}", file=sys.stderr)
        sys.exit(1)

    main_loop(config.cli_args)