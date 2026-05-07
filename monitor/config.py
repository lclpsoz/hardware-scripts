class Colors:
    HEADER = '\033[95m'    # Light Magenta
    OKBLUE = '\033[94m'    # Light Blue
    OKCYAN = '\033[96m'    # Light Cyan
    OKGREEN = '\033[92m'   # Light Green
    WARNING = '\033[93m'   # Yellow
    FAIL = '\033[91m'      # Red
    ENDC = '\033[0m'       # End color
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    DIM = '\033[2m'        # Dim color for less important info

# Default configuration values
DEFAULT_NUM_SAMPLES = 20
DEFAULT_SAMPLE_INTERVAL = 0.5
DEFAULT_TEMP_VIZ_CHAR = "●" 
DEFAULT_FREQ_MODE = "all_cores"
MIN_MAX_CPU_FREQ_MHZ = 800 # Minimum frequency for throttling
DEFAULT_ABSOLUTE_MAX_FREQ_MHZ = None # User can override auto-detection

# Global variables to be set by the main script and used by other modules
cli_args = None
SENSORS_CMD_PATH = None 
CPUPOWER_CMD_PATH = None 
SYSTEM_ABSOLUTE_MAX_FREQ_MHZ = None # Auto-detected or user-set absolute max CPU freq
COLUMN_WIDTHS = {}


# --- Throttling & Recovery Settings ---
DEFAULT_TEMP_THRESHOLD_SAFE = 80   # Recovery starts when below this
DEFAULT_MEAN_TEMP_THRESHOLD = 90
DEFAULT_TEMP_THRESHOLD_OK = 95

FREQ_UP_STEP_MHZ = 50                # How much to jump per step
FREQ_DOWN_STEP_MHZ = 200              # How much to drop per step
# This will be populated at runtime to ensure we don't over-boost
INITIAL_MAX_FREQ_MHZ = 4100