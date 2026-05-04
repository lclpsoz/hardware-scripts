import subprocess
import sys
import re 
import config 

def run_bash_command(command):
    """
    Executes a Bash command and returns its standard output.
    Uses config.cli_args for verbose logging.
    """
    try:
        current_cli_args = config.cli_args
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Error executing command: {command}{config.Colors.ENDC}", file=sys.stderr)
                print(f"{config.Colors.DIM}DEBUG: Stderr: {result.stderr.strip()}{config.Colors.ENDC}", file=sys.stderr)
            return None
    except FileNotFoundError:
        current_cli_args = config.cli_args
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: Command not found for: {command.split()[0]}{config.Colors.ENDC}", file=sys.stderr)
        return None
    except Exception as e:
        current_cli_args = config.cli_args
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: Unexpected error running command '{command}': {e}{config.Colors.ENDC}", file=sys.stderr)
        return None

def get_max_cpu_freq_single_sample():
    """
    Get current maximum CPU frequency across all cores for a single sample point.
    """
    current_cli_args = config.cli_args
    command = "awk 'BEGIN{max=0} /cpu MHz/ {if($4>max) max=$4} END{printf \"%.0f\", max}' /proc/cpuinfo"
    output = run_bash_command(command)
    if output:
        try:
            return int(float(output))
        except ValueError:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse MAX CPU frequency from output: '{output}'{config.Colors.ENDC}", file=sys.stderr)
            return None
    return None

def get_all_cpu_freqs_single_sample():
    """
    Get current CPU frequency for ALL cores for a single sample point.
    """
    current_cli_args = config.cli_args
    command = "awk '/cpu MHz/ {printf \"%.0f\\n\", $4}' /proc/cpuinfo"
    output = run_bash_command(command)
    if output:
        try:
            freqs = [int(float(f)) for f in output.strip().split('\n') if f.strip()]
            return freqs if freqs else None
        except ValueError:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse ALL CPU frequencies from output: '{output}'{config.Colors.ENDC}", file=sys.stderr)
            return None
    return None

def get_cpu_temp():
    """
    Get current CPU package temperature.
    Uses the config.SENSORS_CMD_PATH found by shutil.which.
    """
    current_cli_args = config.cli_args
    
    if not config.SENSORS_CMD_PATH:
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: SENSORS_CMD_PATH not set in config. Cannot get CPU temp.{config.Colors.ENDC}", file=sys.stderr)
        return None

    command_package = f"{config.SENSORS_CMD_PATH} 2>/dev/null | awk '/Package id 0:/ {{gsub(/\\+|°C/,\"\",$4); printf \"%.0f\", $4}}'"
    output = run_bash_command(command_package)
    if output:
        try:
            return int(float(output))
        except ValueError:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse CPU temp (Package id 0) from: '{output}'{config.Colors.ENDC}", file=sys.stderr)
    
    command_tdie = f"{config.SENSORS_CMD_PATH} 2>/dev/null | awk '/Tdie:/ {{gsub(/\\+|°C/,\"\",$2); printf \"%.0f\", $2}}'"
    output = run_bash_command(command_tdie)
    if output:
        try:
            return int(float(output))
        except ValueError:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse CPU temp (Tdie) from: '{output}'{config.Colors.ENDC}", file=sys.stderr)

    command_core0 = f"{config.SENSORS_CMD_PATH} 2>/dev/null | awk '/Core 0:/ {{gsub(/\\+|°C/,\"\",$3); printf \"%.0f\", $3}}'"
    output = run_bash_command(command_core0)
    if output:
        try:
            return int(float(output))
        except ValueError:
             if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse CPU temp (Core 0) from: '{output}'{config.Colors.ENDC}", file=sys.stderr)
    
    command_temp1 = f"{config.SENSORS_CMD_PATH} 2>/dev/null | awk '/temp1_input:/ {{gsub(/\\+|°C/,\"\",$2); printf \"%.0f\", $2}}'"
    output = run_bash_command(command_temp1)
    if output:
        try:
            return int(float(output))
        except ValueError:
            if current_cli_args and current_cli_args.verbose:
                print(f"{config.Colors.DIM}DEBUG: Could not parse CPU temp (temp1_input) from: '{output}'{config.Colors.ENDC}", file=sys.stderr)
    return None

def get_cpupower_max_freq_ghz():
    """
    Gets the current maximum frequency policy limit set by cpupower for CPU 0.
    Returns the frequency in GHz as a float, or "N/A".
    """
    current_cli_args = config.cli_args
    if not config.CPUPOWER_CMD_PATH:
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: CPUPOWER_CMD_PATH not set. Cannot get cpupower max freq.{config.Colors.ENDC}", file=sys.stderr)
        return "N/A"

    cmd = f"sudo {config.CPUPOWER_CMD_PATH} -c 0 frequency-info -p"
    output = run_bash_command(cmd)

    if output:
        match = re.search(r"current policy:.*?and\s+([0-9.]+)\s*GHz", output)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                if current_cli_args and current_cli_args.verbose:
                    print(f"{config.Colors.DIM}DEBUG: Could not parse cpupower max freq (GHz) from: '{match.group(1)}'{config.Colors.ENDC}", file=sys.stderr)
                return "N/A"
        else: 
            match_mhz = re.search(r"current policy:.*?and\s+([0-9.]+)\s*MHz", output)
            if match_mhz:
                try:
                    return float(match_mhz.group(1)) / 1000.0 
                except ValueError:
                    if current_cli_args and current_cli_args.verbose:
                        print(f"{config.Colors.DIM}DEBUG: Could not parse cpupower max freq (MHz) from: '{match_mhz.group(1)}'{config.Colors.ENDC}", file=sys.stderr)
                    return "N/A"
            elif current_cli_args and current_cli_args.verbose:
                if "current policy" in output:
                    print(f"{config.Colors.DIM}DEBUG: Could not find cpupower max freq pattern in 'frequency-info -p' output.{config.Colors.ENDC}", file=sys.stderr)
    elif current_cli_args and current_cli_args.verbose:
        print(f"{config.Colors.DIM}DEBUG: Failed to execute or get output from 'cpupower frequency-info -p'. Check sudo permissions.{config.Colors.ENDC}", file=sys.stderr)
    return "N/A"

def get_hardware_max_freq_mhz():
    """
    Gets the hardware maximum frequency limit for CPU 0 from cpupower.
    Returns the frequency in MHz as an int, or None.
    """
    current_cli_args = config.cli_args
    if not config.CPUPOWER_CMD_PATH:
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: CPUPOWER_CMD_PATH not set. Cannot get hardware max freq.{config.Colors.ENDC}", file=sys.stderr)
        return None

    cmd = f"sudo {config.CPUPOWER_CMD_PATH} -c 0 frequency-info" # No -p for hardware limits
    output = run_bash_command(cmd)

    if output:
        # Example: "hardware limits: 800 MHz - 4.10 GHz" or "hardware limits: 800000 kHz - 4100000 kHz"
        match = re.search(r"hardware limits:.*?-\s*([0-9.]+)\s*(GHz|MHz|kHz)", output, re.IGNORECASE)
        if match:
            val_str, unit = match.groups()
            try:
                val = float(val_str)
                if unit.upper() == "GHZ":
                    return int(val * 1000)
                elif unit.upper() == "MHZ":
                    return int(val)
                elif unit.upper() == "KHZ":
                    return int(val / 1000)
            except ValueError:
                if current_cli_args and current_cli_args.verbose:
                    print(f"{config.Colors.DIM}DEBUG: Could not parse hardware max freq value from: '{val_str}'{config.Colors.ENDC}", file=sys.stderr)
                return None
        elif current_cli_args and current_cli_args.verbose:
            if "hardware limits" in output:
                 print(f"{config.Colors.DIM}DEBUG: Could not find hardware max freq pattern in 'frequency-info' output.{config.Colors.ENDC}", file=sys.stderr)
    elif current_cli_args and current_cli_args.verbose:
        print(f"{config.Colors.DIM}DEBUG: Failed to execute 'cpupower frequency-info' for hardware limits.{config.Colors.ENDC}", file=sys.stderr)
    return None


def set_cpupower_max_freq_mhz(freq_mhz):
    """
    Sets the cpupower maximum frequency for all CPUs.
    Args:
        freq_mhz (int): The frequency to set in MHz.
    Returns:
        bool: True if command execution was successful (or seemed to be), False otherwise.
    """
    current_cli_args = config.cli_args 
    if not config.CPUPOWER_CMD_PATH:
        if current_cli_args and current_cli_args.verbose:
            print(f"{config.Colors.DIM}DEBUG: CPUPOWER_CMD_PATH not set. Cannot set cpupower max freq.{config.Colors.ENDC}", file=sys.stderr)
        return False

    cmd = f"sudo {config.CPUPOWER_CMD_PATH} frequency-set -u {int(freq_mhz)}MHz"
    if current_cli_args and current_cli_args.verbose:
        print(f"{config.Colors.DIM}DEBUG: Attempting to execute: {cmd}{config.Colors.ENDC}", file=sys.stderr)
    
    # run_bash_command returns None on error, or output string on success (often empty for set commands)
    result_output = run_bash_command(cmd) 
    
    # Assume success if run_bash_command did not return None (which indicates a failure to execute or non-zero exit)
    # Some successful `set` commands might not produce stdout.
    # For more robust check, `run_bash_command` could return a tuple (stdout, stderr, retcode)
    command_successful = result_output is not None 

    if command_successful:
      if not (current_cli_args and current_cli_args.quiet):
          sys.stderr.write(f"{config.Colors.WARNING}INFO: `cpupower frequency-set -u {int(freq_mhz)}MHz` executed.{config.Colors.ENDC}\n")
          sys.stderr.flush()
    else: # Command execution likely failed
      if not (current_cli_args and current_cli_args.quiet):
          sys.stderr.write(f"{config.Colors.FAIL}ERROR: `cpupower frequency-set -u {int(freq_mhz)}MHz` may have failed. Check verbose logs or sudo permissions.{config.Colors.ENDC}\n")
          sys.stderr.flush()
          
    return command_successful