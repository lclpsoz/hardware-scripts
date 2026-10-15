import sys
import datetime
import argparse
import time
import shutil # For shutil.which
import config # Import the config module
import subprocess
from system_query import get_cpu_temp, get_all_cpu_freqs_single_sample, get_max_cpu_freq_single_sample, get_cpupower_max_freq_ghz, set_cpupower_max_freq_mhz
from core_utils import calculate_stats, get_temp_color, get_freq_color

# Named frequency profiles (MHz) accepted by --max-freq
FREQ_PROFILES_MHZ = {
    "min": 800,
    "mid": 2000,
    "safe": 3000,
    "max": 4100,
}
DEFAULT_FREQ_PROFILE = "safe"

def parse_max_freq(value):
    """argparse type: accepts a profile name (min/mid/safe/max) or a plain integer in MHz."""
    key = value.strip().lower()
    if key in FREQ_PROFILES_MHZ:
        return FREQ_PROFILES_MHZ[key]
    try:
        mhz = int(key)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid value '{value}': use a profile ({', '.join(FREQ_PROFILES_MHZ)}) or an integer in MHz")
    if mhz <= 0:
        raise argparse.ArgumentTypeError("frequency must be a positive number of MHz")
    return mhz

NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"
CPUINFO_MAX_FREQ_PATH = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"

def _read_int(path):
    """Reads an integer from a sysfs file. Returns None if unavailable."""
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None

def _write_no_turbo(value):
    """Writes no_turbo directly, falling back to 'sudo tee' if we lack permission."""
    try:
        with open(NO_TURBO_PATH, "w") as f:
            f.write(str(value))
        return True
    except PermissionError:
        pass  # not root, try sudo below
    except OSError:
        return False
    try:
        subprocess.run(["sudo", "tee", NO_TURBO_PATH], input=f"{value}\n", text=True,
                       stdout=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def ensure_turbo_enabled(quiet=False):
    """Startup check: reports the no_turbo state and clears it (sets 0) if turbo is disabled."""
    before = _read_int(NO_TURBO_PATH)
    if before is None:
        if not quiet:
            print(f"{config.Colors.DIM}INFO: intel_pstate no_turbo not available, skipping turbo check.{config.Colors.ENDC}", file=sys.stderr)
        return
    if before == 0:
        if not quiet:
            print(f"{config.Colors.DIM}INFO: Turbo already enabled (no_turbo=0).{config.Colors.ENDC}", file=sys.stderr)
        return

    hw_before_khz = _read_int(CPUINFO_MAX_FREQ_PATH)
    print(f"{config.Colors.WARNING}WARN: Turbo was DISABLED (no_turbo=1, hardware max "
          f"{hw_before_khz // 1000 if hw_before_khz else 'N/A'}MHz). Enabling it...{config.Colors.ENDC}", file=sys.stderr)

    if not _write_no_turbo(0):
        print(f"{config.Colors.FAIL}Error: could not write no_turbo=0. Run as root or check sudo permissions. "
              f"Turbo stays disabled.{config.Colors.ENDC}", file=sys.stderr)
        return

    after = _read_int(NO_TURBO_PATH)
    hw_after_khz = _read_int(CPUINFO_MAX_FREQ_PATH)
    if after == 0:
        print(f"{config.Colors.OKGREEN}INFO: no_turbo changed 1 -> 0. Turbo enabled, hardware max now "
              f"{hw_after_khz // 1000 if hw_after_khz else 'N/A'}MHz.{config.Colors.ENDC}", file=sys.stderr)
    else:
        print(f"{config.Colors.FAIL}Error: no_turbo reads {after} after the write. "
              f"Something else is overriding it.{config.Colors.ENDC}", file=sys.stderr)

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

import re

def strip_ansi(text):
    """Utility to remove ANSI codes for length calculations."""
    return re.sub(r'\033\[[0-9;]*m', '', text)

def main_loop(args_param): # Renamed args to args_param to avoid conflict with module name
    """
    Main function to run the CPU monitoring script.
    """
    plain_text_line_width = print_header()
    
    # Capture the INITIAL state of the CPU so we know our 'ceiling'
    initial_ghz = get_cpupower_max_freq_ghz()
    if isinstance(initial_ghz, float):
        config.INITIAL_MAX_FREQ_MHZ = round(initial_ghz * 1000)

    # --max-freq is a request. The effective policy max is whatever the hardware/kernel
    # actually accepted, so we read it back and use that as the ceiling.
    if args_param.max_freq is not None:
        if not config.CPUPOWER_CMD_PATH:
            sys.stderr.write(f"{config.Colors.WARNING}Warning: --max-freq ignored, 'cpupower' not available.{config.Colors.ENDC}\n")
        else:
            requested_mhz = max(args_param.max_freq, config.MIN_MAX_CPU_FREQ_MHZ)
            if requested_mhz != args_param.max_freq:
                sys.stderr.write(f"{config.Colors.WARNING}Warning: --max-freq {args_param.max_freq}MHz is below the script "
                                 f"minimum ({config.MIN_MAX_CPU_FREQ_MHZ}MHz). Using the minimum.{config.Colors.ENDC}\n")

            set_ok = set_cpupower_max_freq_mhz(requested_mhz)
            if not set_ok:
                sys.stderr.write(f"{config.Colors.FAIL}Error: cpupower rejected {requested_mhz}MHz. "
                                 f"Keeping the current policy.{config.Colors.ENDC}\n")

            # Read back what the policy really is now (works whether the set succeeded, was clamped, or failed)
            effective_ghz = get_cpupower_max_freq_ghz()
            if isinstance(effective_ghz, float):
                effective_mhz = round(effective_ghz * 1000)
                config.INITIAL_MAX_FREQ_MHZ = effective_mhz  # real policy max / ceiling
                if abs(effective_mhz - requested_mhz) > 10:  # tolerate GHz rounding
                    sys.stderr.write(f"{config.Colors.WARNING}Warning: requested {requested_mhz}MHz but hardware/driver "
                                     f"limits it to {effective_mhz}MHz. Using {effective_mhz}MHz as policy max."
                                     f"{config.Colors.ENDC}\n")
            else:
                sys.stderr.write(f"{config.Colors.WARNING}Warning: could not read back the policy max. "
                                 f"Ceiling stays at {config.INITIAL_MAX_FREQ_MHZ}MHz.{config.Colors.ENDC}\n")

    ts_w = config.COLUMN_WIDTHS["ts_w"]
    temp_w = config.COLUMN_WIDTHS["temp_w"]
    cpumax_w = config.COLUMN_WIDTHS["cpumax_w"]
    freq_w = config.COLUMN_WIDTHS["freq_w"]
    status_w = config.COLUMN_WIDTHS["status_w"]

    throttling_cooldown = 0

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

            # --- BIDIRECTIONAL FREQUENCY CONTROL ---
            if config.CPUPOWER_CMD_PATH and config.INITIAL_MAX_FREQ_MHZ:
                if throttling_cooldown > 0:
                    throttling_cooldown -= 1
                
                elif isinstance(temp_max, int) and isinstance(freq_max, int):
                    current_limit_mhz = int(get_cpupower_max_freq_ghz() * 1000)
                    new_limit_mhz = current_limit_mhz
                    
                    # CASE A: THROTTLE DOWN (Too Hot)
                    if temp_max >= args_param.temp_threshold_ok or temp_mean >= config.DEFAULT_MEAN_TEMP_THRESHOLD:
                        severity = 1 + (1 if temp_max >= args_param.temp_threshold_ok + 10 else 0)
                        drop_amount = config.FREQ_DOWN_STEP_MHZ * severity
                        new_limit_mhz = max(config.MIN_MAX_CPU_FREQ_MHZ, current_limit_mhz - drop_amount)
                        
                        if new_limit_mhz < current_limit_mhz:
                            if set_cpupower_max_freq_mhz(new_limit_mhz):
                                throttling_cooldown = 2 # Shorter cooldown for safety
                                sys.stderr.write(f"\n{config.Colors.FAIL}🔥\tCOOLING: Temp {temp_max}°C. Throttling to {new_limit_mhz}MHz{config.Colors.ENDC}\n")

                    # CASE B: RECOVERY (Cool enough to boost)
                    elif temp_max <= config.DEFAULT_TEMP_THRESHOLD_SAFE:
                        # Only boost if we are currently below the hardware's initial max
                        if current_limit_mhz < config.INITIAL_MAX_FREQ_MHZ:
                            new_limit_mhz = min(config.INITIAL_MAX_FREQ_MHZ, current_limit_mhz + config.FREQ_UP_STEP_MHZ)
                            
                            if set_cpupower_max_freq_mhz(new_limit_mhz):
                                throttling_cooldown = 5 # Longer cooldown to prevent "jitter"
                                sys.stderr.write(f"\n{config.Colors.OKGREEN}❄️\tRECOVERY: Temp {temp_max}°C. Boosting to {new_limit_mhz}MHz{config.Colors.ENDC}\n")

            sys.stdout.flush()

    except KeyboardInterrupt:
        # Senior Tip: Always try to reset the CPU to initial max on exit!
        if config.INITIAL_MAX_FREQ_MHZ:
             set_cpupower_max_freq_mhz(config.INITIAL_MAX_FREQ_MHZ)
        print(f"\n{config.Colors.OKGREEN}Monitoring stopped. Frequency reset to {config.INITIAL_MAX_FREQ_MHZ}MHz.{config.Colors.ENDC}")

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
    parser.add_argument("-m", "--max-freq", type=parse_max_freq, nargs="?",
                        default=DEFAULT_FREQ_PROFILE, const=DEFAULT_FREQ_PROFILE, metavar="PROFILE|MHZ",
                        help="Set the CPU max frequency policy. Accepts a profile "
                             f"({', '.join(f'{k}={v}MHz' for k, v in FREQ_PROFILES_MHZ.items())}) or a value in MHz. "
                             "This becomes the new policy max and the ceiling for automatic thermal recovery. "
                             "It is kept on exit.")
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

    ensure_turbo_enabled(config.cli_args.quiet)
    main_loop(config.cli_args)