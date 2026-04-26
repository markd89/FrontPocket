#!/usr/bin/env bash
#
# FrontPocket Installer
# Installs FrontPocket TTS server with systemd user service
#
# Usage: install.sh [OPTIONS]
#
# Options:
#   --cpu          Force CPU-only PyTorch installation
#   --gpu          Force GPU/CUDA PyTorch installation
#   --uninstall    Remove FrontPocket completely
#   --status       Show installation status
#   --yes          Accept all defaults (non-interactive mode)
#   --token TOKEN  Set Hugging Face token (or set HF_TOKEN env var)
#   --no-sudo      Skip commands requiring sudo
#   --help         Show this help message
#

set -uo pipefail

# ─── Constants ───────────────────────────────────────────────────────────────

REPO_URL="https://github.com/markd89/FrontPocket.git"
INSTALL_DIR="$HOME/FrontPocket"
CONFIG_DIR="$HOME/.config/FrontPocket"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_DIR="$HOME/.config/systemd/user"
TEMP_LOG="/tmp/frontpocket-install-$$.log"
FINAL_LOG="$INSTALL_DIR/install.log"
LOG_FILE="$TEMP_LOG"
WRAPPER_PATH="/usr/local/bin/fp"
LOCAL_BIN_DIR="$HOME/.local/bin"
LOCAL_WRAPPER_PATH="$LOCAL_BIN_DIR/fp"
TOOLBAR_WRAPPER="$LOCAL_BIN_DIR/fp-toolbar"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/frontpocket-toolbar.desktop"

MIN_PYTHON_VERSION="3.10"
DISK_SPACE_CPU_MB=2048
DISK_SPACE_GPU_MB=4096

# ─── Colors ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ─── Globals (set by arg parsing) ────────────────────────────────────────────

MODE="install"
FORCE_CPU=false
FORCE_GPU=false
YES_MODE=false
ARG_TOKEN=""
NO_SUDO=false

# ─── Trap ────────────────────────────────────────────────────────────────────

trap 'echo -e "\n${YELLOW}Interrupted. Partial installation may exist.${NC}"; [ -f "$TEMP_LOG" ] && echo -e "${DIM}Partial log: $TEMP_LOG${NC}"; exit 130' INT TERM

# ─── Logging ─────────────────────────────────────────────────────────────────

log() {
    local level="$1"; shift
    local msg="$*"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    if [ -n "${LOG_FILE:-}" ]; then
        mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
        echo "[$timestamp] [$level] $msg" >> "$LOG_FILE" 2>/dev/null || true
    fi

    case "$level" in
        INFO)    echo -e "${BLUE}[INFO]${NC}  $msg" ;;
        WARN)    echo -e "${YELLOW}[WARN]${NC}  $msg" ;;
        ERROR)   echo -e "${RED}[ERROR]${NC} $msg" ;;
        SUCCESS) echo -e "${GREEN}  [✓]${NC}  $msg" ;;
        STEP)    echo -e "\n${CYAN}${BOLD}── $msg ──${NC}" ;;
        *)       echo "$msg" ;;
    esac
}

log_banner() {
    echo -e "${BOLD}${CYAN}"
    cat << 'BANNER'
 ╔═══════════════════════════════════════════╗
 ║          FrontPocket Installer            ║
 ║     Low-Latency Text-to-Speech Server     ║
 ╚═══════════════════════════════════════════╝
BANNER
    echo -e "${NC}"
}

# ─── Log Finalization ────────────────────────────────────────────────────────

finalize_log() {
    if [ -f "$TEMP_LOG" ] && [ -d "$INSTALL_DIR" ]; then
        cp "$TEMP_LOG" "$FINAL_LOG"
        rm -f "$TEMP_LOG"
        LOG_FILE="$FINAL_LOG"
    fi
}

# ─── Utility Functions ───────────────────────────────────────────────────────

ask_yes_no() {
    local prompt="$1"
    local default="${2:-y}"

    if [ "$YES_MODE" = true ]; then
        [ "$default" = "y" ] && return 0 || return 1
    fi

    local yn
    if [ "$default" = "y" ]; then
        read -rp "$(echo -e "${BOLD}$prompt${NC} [Y/n] ")" yn
        yn="${yn:-y}"
    else
        read -rp "$(echo -e "${BOLD}$prompt${NC} [y/N] ")" yn
        yn="${yn:-n}"
    fi

    case "$yn" in
        [Yy]*) return 0 ;;
        *)     return 1 ;;
    esac
}

ask_input() {
    local prompt="$1"
    local default="${2:-}"

    if [ "$YES_MODE" = true ]; then
        echo "$default"
        return
    fi

    if [ -n "$default" ]; then
        local input
        read -rp "$(echo -e "${BOLD}$prompt${NC} [$default] ")" input
        echo "${input:-$default}"
    else
        local input
        read -rp "$(echo -e "${BOLD}$prompt${NC} ")" input
        echo "$input"
    fi
}

command_exists() {
    command -v "$1" &>/dev/null
}

version_gte() {
    # Returns 0 if $1 >= $2 (version strings like "3.10.1" "3.10")
    local actual="$1"
    local required="$2"
    printf '%s\n%s\n' "$required" "$actual" | sort -V -C
}

detect_gpu() {
    if command_exists nvidia-smi && nvidia-smi &>/dev/null; then
        return 0
    fi
    if command_exists lspci && lspci 2>/dev/null | grep -qi 'nvidia.*3d\|nvidia.*vga\|nvidia.*display'; then
        return 0
    fi
    return 1
}

get_gpu_name() {
    if command_exists nvidia-smi; then
        nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1
    elif command_exists lspci; then
        lspci 2>/dev/null | grep -i 'nvidia.*3d\|nvidia.*vga\|nvidia.*display' | head -1 | sed 's/.*: //'
    fi
}

detect_pkg_manager() {
    if command_exists apt-get; then
        echo "apt"
    elif command_exists dnf; then
        echo "dnf"
    elif command_exists pacman; then
        echo "pacman"
    else
        echo "unknown"
    fi
}

detect_display_server() {
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        echo "wayland"
    elif [ -n "${XDG_SESSION_TYPE:-}" ]; then
        echo "$XDG_SESSION_TYPE"
    elif [ -n "${DISPLAY:-}" ]; then
        echo "x11"
    else
        echo "unknown"
    fi
}

check_disk_space() {
    local required_mb="$1"
    local available_mb
    available_mb=$(df -m "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')
    if [ -z "$available_mb" ]; then
        return 0  # Can't check, assume OK
    fi
    [ "$available_mb" -ge "$required_mb" ]
}

check_python_version() {
    local py_path="$1"
    local version
    version=$("$py_path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null)
    if [ -z "$version" ]; then
        return 1
    fi
    version_gte "$version" "$MIN_PYTHON_VERSION"
}

# ─── Package Installation ────────────────────────────────────────────────────

install_packages_apt() {
    local packages=("$@")
    log INFO "Installing via apt: ${packages[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${packages[@]}"
}

install_packages_dnf() {
    local packages=("$@")
    log INFO "Installing via dnf: ${packages[*]}"
    sudo dnf install -y -q "${packages[@]}"
}

install_packages_pacman() {
    local packages=("$@")
    log INFO "Installing via pacman: ${packages[*]}"
    sudo pacman -S --noconfirm --needed "${packages[@]}"
}

install_system_packages() {
    local pkg_manager
    pkg_manager=$(detect_pkg_manager)

    local display_server
    display_server=$(detect_display_server)

    case "$pkg_manager" in
        apt)
            local apt_packages=(python3-venv libasound2-dev rubberband-cli)
            if [ "$display_server" = "wayland" ]; then
                apt_packages+=(wl-clipboard)
                if ! command_exists xclip; then
                    apt_packages+=(xclip)
                fi
            else
                apt_packages+=(xclip)
            fi
            if ! command_exists git; then
                apt_packages+=(git)
            fi
            install_packages_apt "${apt_packages[@]}"
            ;;
        dnf)
            local dnf_packages=(alsa-lib-devel rubberband-cli)
            if [ "$display_server" = "wayland" ]; then
                dnf_packages+=(wl-clipboard)
                if ! command_exists xclip; then
                    dnf_packages+=(xclip)
                fi
            else
                dnf_packages+=(xclip)
            fi
            if ! command_exists git; then
                dnf_packages+=(git)
            fi
            install_packages_dnf "${dnf_packages[@]}"
            ;;
        pacman)
            local pacman_packages=(alsa-lib rubberband)
            if [ "$display_server" = "wayland" ]; then
                pacman_packages+=(wl-clipboard)
                if ! command_exists xclip; then
                    pacman_packages+=(xclip)
                fi
            else
                pacman_packages+=(xclip)
            fi
            if ! command_exists git; then
                pacman_packages+=(git)
            fi
            install_packages_pacman "${pacman_packages[@]}"
            ;;
        *)
            log ERROR "Unsupported package manager. Please install manually:"
            echo "        alsa-lib-dev (libasound2-dev / alsa-lib-devel / alsa-lib)"
            echo "        rubberband-cli (or rubberband)"
            echo "        xclip and/or wl-clipboard"
            echo "        git"
            return 1
            ;;
    esac
}

# ─── Preflight Checks ────────────────────────────────────────────────────────

preflight_checks() {
    log STEP "Running preflight checks"

    # Not running as root
    if [ "$(id -u)" -eq 0 ]; then
        log ERROR "This script should not be run as root."
        log ERROR "Run it as your regular user — sudo is used only where needed."
        exit 1
    fi
    log SUCCESS "Running as user: $(whoami)"

    # OS detection
    local os_info="Unknown"
    if [ -f /etc/os-release ]; then
        os_info=$(grep '^PRETTY_NAME=' /etc/os-release | cut -d= -f2 | tr -d '"')
    fi
    log INFO "OS: $os_info"

    # Package manager
    local pkg_manager
    pkg_manager=$(detect_pkg_manager)
    if [ "$pkg_manager" = "unknown" ]; then
        log WARN "Could not detect package manager (apt/dnf/pacman)"
        if ! ask_yes_no "Continue anyway?" "n"; then
            exit 0
        fi
    else
        log SUCCESS "Package manager: $pkg_manager"
    fi

    # Python check
    local python_path=""
    if command_exists python3 && check_python_version python3; then
        python_path=$(command -v python3)
    fi

    if [ -z "$python_path" ]; then
        log ERROR "Python $MIN_PYTHON_VERSION+ is required but not found."
        log ERROR "Please install python3.$(echo "$MIN_PYTHON_VERSION" | cut -d. -f2) or later."
        exit 1
    fi

    local py_version
    py_version=$("$python_path" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    log SUCCESS "Python: $python_path (v$py_version)"

    # Venv module check
    local test_venv="/tmp/fp-test-venv-$$"
    if ! "$python_path" -m venv "$test_venv" &>/dev/null; then
        rm -rf "$test_venv"
        
        if [ "$NO_SUDO" = true ] || [ "$pkg_manager" != "apt" ]; then
            log ERROR "Python venv module is not available."
            if [ "$pkg_manager" = "apt" ]; then
                log ERROR "Fix: sudo apt install python3-venv"
            elif [ "$pkg_manager" = "pacman" ]; then
                log ERROR "Fix: sudo pacman -S python"
            elif [ "$pkg_manager" = "dnf" ]; then
                log ERROR "Fix: sudo dnf install python3"
            else
                log ERROR "Ensure the python3-venv (or equivalent) package is installed."
            fi
            exit 1
        else
            log WARN "Python venv module is missing (will be installed automatically)"
        fi
    else
        rm -rf "$test_venv"
        log SUCCESS "Python venv module is available"
    fi

    # Desktop session check
    local display_server
    display_server=$(detect_display_server)
    if [ "$display_server" = "unknown" ]; then
        log WARN "No desktop session detected (no DISPLAY or WAYLAND_DISPLAY set)"
        log WARN "FrontPocket requires a desktop audio session to produce sound."
        if ! ask_yes_no "Continue anyway?" "n"; then
            exit 0
        fi
    else
        log SUCCESS "Display server: $display_server"
    fi

    # Audio session check
    local audio_found=false
    if systemctl --user is-active pipewire &>/dev/null; then
        audio_found=true
    elif systemctl --user is-active pipewire-pulse &>/dev/null; then
        audio_found=true
    elif systemctl --user is-active pulseaudio &>/dev/null; then
        audio_found=true
    elif pgrep -x pipewire &>/dev/null; then
        audio_found=true
    elif pgrep -x pulseaudio &>/dev/null; then
        audio_found=true
    fi

    if [ "$audio_found" = false ]; then
        log WARN "No audio session (PipeWire/PulseAudio) detected as active."
        log WARN "Audio may not work. You can continue and fix this later."
    else
        log SUCCESS "Audio session detected"
    fi

    # Disk space
    local required_space="$DISK_SPACE_CPU_MB"
    if [ "$FORCE_GPU" = true ]; then
        required_space="$DISK_SPACE_GPU_MB"
    fi

    if ! check_disk_space "$required_space"; then
        local available
        available=$(df -h "$HOME" | awk 'NR==2 {print $4}')
        log ERROR "Insufficient disk space."
        log ERROR "Required: ~$((required_space / 1024))GB, Available: $available"
        log ERROR "Free up space and try again."
        exit 1
    fi
    log SUCCESS "Disk space: OK (~$((required_space / 1024))GB needed)"

    # Sudo check
    if [ "$NO_SUDO" = false ]; then
        if ! sudo -n true 2>/dev/null; then
            echo ""
            log INFO "Sudo access is needed to install system packages and the 'fp' command."
            if ! sudo -v 2>/dev/null; then
                log WARN "sudo authentication failed or not available."
                log INFO "Falling back to --no-sudo mode."
                log INFO "  - System packages will be skipped (install them manually)."
                log INFO "  - 'fp' will be placed in ~/.local/bin instead of /usr/local/bin."
                NO_SUDO=true
            fi
        fi
    fi

    if [ "$NO_SUDO" = true ]; then
        log INFO "Running in --no-sudo mode"
    fi
}

# ─── Existing Installation ───────────────────────────────────────────────────

# Returns 0 = proceed with clone, 1 = skip clone (already handled)
handle_existing_install() {
    if [ ! -d "$INSTALL_DIR" ]; then
        return 0
    fi

    log WARN "Existing installation found at $INSTALL_DIR"

    if [ -d "$INSTALL_DIR/.git" ]; then
        if ask_yes_no "Update existing installation (git pull)?" "y"; then
            log INFO "Pulling latest changes..."
            if (cd "$INSTALL_DIR" && git pull --ff-only); then
                log SUCCESS "Repository updated"
                return 1
            else
                log ERROR "git pull failed. Check network or merge conflicts."
                if ! ask_yes_no "Reinstall from scratch instead?" "n"; then
                    exit 1
                fi
                # Fall through to backup
            fi
        fi

        if ! ask_yes_no "Reinstall from scratch (backs up existing dir)?" "n"; then
            log INFO "Keeping existing installation, skipping clone."
            return 1
        fi
    fi

    # Backup
    local backup_dir="${INSTALL_DIR}.bak.$(date +%Y%m%d%H%M%S)"
    log INFO "Backing up to $backup_dir ..."
    if mv "$INSTALL_DIR" "$backup_dir"; then
        log SUCCESS "Backup created"
    else
        log ERROR "Failed to back up existing installation."
        exit 1
    fi
    return 0
}

# ─── Installation Steps ──────────────────────────────────────────────────────

step_install_deps() {
    log STEP "Installing system dependencies"

    if [ "$NO_SUDO" = true ]; then
        log WARN "Skipping system package installation (--no-sudo mode)"
        log INFO "Make sure these are installed manually:"
        echo "        - ALSA dev library  (libasound2-dev / alsa-lib-devel / alsa-lib)"
        echo "        - rubberband-cli    (or rubberband on Arch)"
        echo "        - xclip and/or wl-clipboard"
        echo "        - git"
        echo ""
        return
    fi

    if install_system_packages; then
        log SUCCESS "System dependencies installed"
    else
        log ERROR "Failed to install system dependencies."
        exit 1
    fi
}

step_clone_repo() {
    log STEP "Cloning repository"

    if ! handle_existing_install; then
        return  # Already updated or kept
    fi

    log INFO "Cloning $REPO_URL ..."
    if git clone "$REPO_URL" "$INSTALL_DIR"; then
        log SUCCESS "Cloned to $INSTALL_DIR"
    else
        log ERROR "Failed to clone repository."
        log ERROR "Check your network connection and try again."
        exit 1
    fi
}

step_setup_venv() {
    log STEP "Setting up Python virtual environment"

    local python_path
    python_path=$(command -v python3)

    # ── GPU decision ──
    local use_gpu=false
    if [ "$FORCE_GPU" = true ]; then
        use_gpu=true
    elif [ "$FORCE_CPU" = true ]; then
        use_gpu=false
    elif detect_gpu; then
        local gpu_name
        gpu_name=$(get_gpu_name)
        log INFO "NVIDIA GPU detected: ${gpu_name:-NVIDIA}"
        if ask_yes_no "Install GPU (CUDA) version of PyTorch?" "y"; then
            use_gpu=true
        else
            log INFO "Using CPU-only PyTorch as requested"
        fi
    else
        log INFO "No NVIDIA GPU detected — using CPU-only PyTorch"
    fi

    # ── (Re)create venv ──
    if [ -d "$VENV_DIR" ]; then
        if ask_yes_no "Recreate virtual environment? (existing packages will be removed)" "n"; then
            log INFO "Removing old venv..."
            rm -rf "$VENV_DIR"
        else
            log INFO "Keeping existing virtual environment, skipping pip install"
            return
        fi
    fi

    log INFO "Creating virtual environment at $VENV_DIR ..."
    if ! "$python_path" -m venv "$VENV_DIR"; then
        log ERROR "Failed to create virtual environment."
        log ERROR "Ensure python3-venv is installed and try again."
        exit 1
    fi
    log SUCCESS "Virtual environment created"

    # ── Install PyTorch ──
    if [ "$use_gpu" = true ]; then
        log INFO "Installing PyTorch with CUDA support..."
        log INFO "(This downloads ~2 GB — please be patient)"
        if ! "$VENV_DIR/bin/pip" install --quiet torch; then
            log ERROR "Failed to install PyTorch (CUDA)."
            log ERROR "Make sure the NVIDIA CUDA toolkit is installed."
            log ERROR "You can retry with --cpu to use CPU-only PyTorch."
            exit 1
        fi
        log SUCCESS "PyTorch (CUDA) installed"
    else
        log INFO "Installing PyTorch CPU-only..."
        log INFO "(This downloads ~200 MB — please be patient)"
        if ! "$VENV_DIR/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu; then
            log ERROR "Failed to install PyTorch (CPU)."
            exit 1
        fi
        log SUCCESS "PyTorch (CPU) installed"
    fi

    # ── Install requirements.txt ──
    if [ ! -f "$INSTALL_DIR/requirements.txt" ]; then
        log ERROR "requirements.txt not found in $INSTALL_DIR"
        log ERROR "The repository clone may be incomplete."
        exit 1
    fi

    log INFO "Installing remaining dependencies from requirements.txt ..."
    if ! "$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"; then
        log ERROR "Failed to install Python dependencies."
        exit 1
    fi
    log SUCCESS "All Python dependencies installed"
}

step_setup_config() {
    log STEP "Setting up configuration and directories"

    # Create directories
    mkdir -p "$CONFIG_DIR"
    mkdir -p "$INSTALL_DIR/voices"
    mkdir -p "$INSTALL_DIR/sounds"
    log SUCCESS "Directories created"

    # Copy notification sound if available
    if [ -f "$INSTALL_DIR/notification.wav" ] && [ ! -f "$INSTALL_DIR/sounds/notification.wav" ]; then
        cp "$INSTALL_DIR/notification.wav" "$INSTALL_DIR/sounds/"
        log SUCCESS "Sample notification sound copied to sounds/"
    fi

    # Config file
    if [ -f "$CONFIG_DIR/frontpocket.ini" ]; then
        log INFO "Existing config preserved at $CONFIG_DIR/frontpocket.ini"
        log INFO "Edit it manually if needed: nano $CONFIG_DIR/frontpocket.ini"
    elif [ -f "$INSTALL_DIR/frontpocket.ini" ]; then
        cp "$INSTALL_DIR/frontpocket.ini" "$CONFIG_DIR/frontpocket.ini"
        rm -f "$INSTALL_DIR/frontpocket.ini"
        log SUCCESS "Config copied to $CONFIG_DIR/frontpocket.ini"
    else
        log WARN "No frontpocket.ini template found in the repository."
        log WARN "You may need to create $CONFIG_DIR/frontpocket.ini manually."
    fi

    # Ensure symlink (canonical config → repo location)
    if [ -f "$CONFIG_DIR/frontpocket.ini" ] && [ ! -L "$INSTALL_DIR/frontpocket.ini" ]; then
        ln -sf "$CONFIG_DIR/frontpocket.ini" "$INSTALL_DIR/frontpocket.ini"
        log SUCCESS "Symlink: $INSTALL_DIR/frontpocket.ini → $CONFIG_DIR/frontpocket.ini"
    elif [ -L "$INSTALL_DIR/frontpocket.ini" ]; then
        log SUCCESS "Config symlink already in place"
    fi
}

step_setup_hf_token() {
    log STEP "Setting up Hugging Face token"

    local token="$ARG_TOKEN"
    local env_file="$CONFIG_DIR/environment"

    # Priority: --token flag > HF_TOKEN env var > existing file > prompt

    # Check env var if no flag
    if [ -z "$token" ]; then
        token="${HF_TOKEN:-}"
    fi

    # Check existing file
    if [ -z "$token" ] && [ -f "$env_file" ]; then
        local existing_token
        existing_token=$(grep '^HF_TOKEN=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2-)
        if [ -n "$existing_token" ]; then
            log INFO "Existing HF_TOKEN found in $env_file"
            if ask_yes_no "Overwrite with a new token?" "n"; then
                token=""
            else
                log SUCCESS "Keeping existing token"
                return
            fi
        fi
    fi

    # Prompt if still empty
    if [ -z "$token" ]; then
        echo ""
        echo -e "${YELLOW}A Hugging Face token is recommended to download the TTS model.${NC}"
        echo -e "${DIM}Without it, voice cloning won't work, but basic TTS will still function.${NC}"
        echo ""

        if ask_yes_no "Enter your Hugging Face token now?" "y"; then
            token=$(ask_input "Hugging Face token")
        fi
    fi

    # Ensure env file exists with correct permissions
    mkdir -p "$CONFIG_DIR"
    touch "$env_file"
    chmod 600 "$env_file"

    if [ -n "$token" ]; then
        # Remove old HF_TOKEN line (handle possible duplicates)
        sed -i '/^HF_TOKEN=/d' "$env_file" 2>/dev/null || true
        # Strip any surrounding quotes the user might have added
        token=$(echo "$token" | sed 's/^["'\''"]\|["'\''"]$//g')
        echo "HF_TOKEN=$token" >> "$env_file"
        log SUCCESS "HF_TOKEN saved to $env_file"
    else
        echo ""
        log WARN "No Hugging Face token provided."
        log WARN "  - Voice cloning will NOT be available."
        log WARN "  - Add your token later to: $env_file"
        log WARN "  - Format: HF_TOKEN=your_token_here  (no quotes, no spaces)"
        echo ""
    fi
}

step_install_service() {
    log STEP "Installing systemd user service"

    local service_src="$INSTALL_DIR/frontpocket.service"
    if [ ! -f "$service_src" ]; then
        log ERROR "Service file not found: $service_src"
        log ERROR "The repository may be incomplete. Try re-cloning."
        exit 1
    fi

    # Quick sanity: can we use systemd user services?
    if ! systemctl --user list-units --dry-run &>/dev/null; then
        log WARN "systemd user services don't seem available on this system."
        log WARN "You can run the server manually instead:"
        echo -e "        ${DIM}$VENV_DIR/bin/python3 $INSTALL_DIR/frontpocket_server.py${NC}"
        if ! ask_yes_no "Continue with the rest of the installation?" "y"; then
            return
        fi
    fi

    mkdir -p "$SERVICE_DIR"
    cp "$service_src" "$SERVICE_DIR/"
    log SUCCESS "Service file installed to $SERVICE_DIR"

    systemctl --user daemon-reload || {
        log WARN "daemon-reload returned an error (non-fatal)"
    }

    systemctl --user enable frontpocket || {
        log WARN "Failed to enable service"
    }
    log SUCCESS "Service enabled (will start with your session)"

    # Start it now
    log INFO "Starting FrontPocket service now ..."
    log INFO "(First start downloads the TTS model — this can take several minutes)"

    systemctl --user start frontpocket

    # Brief pause then check
    sleep 3

    if systemctl --user is-active frontpocket &>/dev/null; then
        log SUCCESS "Service is running"
    else
        echo ""
        log WARN "Service does not appear to be running yet."
        log WARN "This is normal on first start — the model is still downloading."
        log WARN ""
        log WARN "Monitor progress with:"
        echo -e "        ${DIM}journalctl --user -u frontpocket -f${NC}"
        log WARN ""
        log WARN "If it fails, common fixes:"
        echo "        - Check HF_TOKEN in $CONFIG_DIR/environment"
        echo "        - Check network connection"
        echo "        - Restart with: systemctl --user restart frontpocket"
    fi
}

step_install_wrappers() {
    log STEP "Installing CLI wrappers and desktop entries"

    # ── fp client wrapper ──
    if [ "$NO_SUDO" = false ]; then
        log INFO "Creating /usr/local/bin/fp (requires sudo) ..."
        sudo tee "$WRAPPER_PATH" > /dev/null << EOF
#!/bin/bash
exec $VENV_DIR/bin/python3 $INSTALL_DIR/frontpocket_client.py "\$@"
EOF
        sudo chmod +x "$WRAPPER_PATH"
        log SUCCESS "Created $WRAPPER_PATH"
    else
        log INFO "Creating ~/.local/bin/fp (--no-sudo mode) ..."
        mkdir -p "$LOCAL_BIN_DIR"
        cat > "$LOCAL_WRAPPER_PATH" << EOF
#!/bin/bash
exec $VENV_DIR/bin/python3 $INSTALL_DIR/frontpocket_client.py "\$@"
EOF
        chmod +x "$LOCAL_WRAPPER_PATH"
        log SUCCESS "Created $LOCAL_WRAPPER_PATH"
    fi

    # Check PATH for local bin
    if [ "$NO_SUDO" = true ] && ! echo "$PATH" | tr ':' '\n' | grep -qxF "$LOCAL_BIN_DIR"; then
        echo ""
        log WARN "$LOCAL_BIN_DIR is not in your PATH."
        log WARN "Add this line to your ~/.bashrc or ~/.zshrc:"
        echo -e "        ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
        echo ""
    fi

    # ── fp-toolbar wrapper ──
    mkdir -p "$LOCAL_BIN_DIR"
    cat > "$TOOLBAR_WRAPPER" << EOF
#!/bin/bash
exec $VENV_DIR/bin/python3 $INSTALL_DIR/frontpocket_toolbar.py "\$@"
EOF
    chmod +x "$TOOLBAR_WRAPPER"
    log SUCCESS "Created $TOOLBAR_WRAPPER"

    # ── Desktop entry for toolbar ──
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=FrontPocket Toolbar
Comment=FrontPocket TTS Control Toolbar
Exec=$TOOLBAR_WRAPPER
Icon=audio-speaker
Terminal=false
Type=Application
Categories=Audio;AudioVideo;
StartupNotify=true
EOF
    log SUCCESS "Created desktop entry"

    # Refresh desktop database
    if command_exists update-desktop-database; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi
}

# ─── Summary ─────────────────────────────────────────────────────────────────

print_summary() {
    local fp_cmd="fp"
    if [ "$NO_SUDO" = true ]; then
        fp_cmd="$LOCAL_WRAPPER_PATH"
    fi

    echo ""
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${GREEN}   FrontPocket installed successfully!              ${NC}"
    echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${BOLD}  Quick start:${NC}"
    echo "    $fp_cmd --ping              # Check server is running"
    echo "    $fp_cmd --list-voices       # Show available voices"
    echo "    $fp_cmd \"Hello world\"      # Speak text"
    echo "    $fp_cmd                      # Speak clipboard contents"
    echo ""
    echo -e "${BOLD}  Toolbar:${NC}"
    echo "    fp-toolbar                  # Launch GUI toolbar"
    echo "    (also available in your desktop application menu)"
    echo ""
    echo -e "${BOLD}  Service management:${NC}"
    echo "    systemctl --user status  frontpocket"
    echo "    systemctl --user stop     frontpocket"
    echo "    systemctl --user restart  frontpocket"
    echo "    journalctl --user -u frontpocket -f   # Live logs"
    echo ""
    echo -e "${BOLD}  Configuration:${NC}"
    echo "    nano $CONFIG_DIR/frontpocket.ini"
    echo ""
    echo -e "${BOLD}  Custom voices:${NC}"
    echo "    Place .safetensors files in: $INSTALL_DIR/voices/"
    echo "    Then add entries to the [voices] section of frontpocket.ini"
    echo ""
    echo -e "${BOLD}  Install log:${NC}"
    echo "    $FINAL_LOG"
    echo ""
    echo -e "${YELLOW}  ⚠ First run downloads the TTS model (~1 GB).${NC}"
    echo -e "${YELLOW}    If the service is still starting up, check:${NC}"
    echo "    journalctl --user -u frontpocket -f"
    echo ""
}

# ─── Status ──────────────────────────────────────────────────────────────────

do_status() {
    echo -e "${BOLD}FrontPocket Installation Status${NC}"
    echo "─────────────────────────────────────────────────"

    local issues=0

    # Install dir
    if [ -d "$INSTALL_DIR" ]; then
        local git_sha=""
        if [ -d "$INSTALL_DIR/.git" ]; then
            git_sha=$(cd "$INSTALL_DIR" && git rev-parse --short HEAD 2>/dev/null)
        fi
        if [ -n "$git_sha" ]; then
            echo -e "  Install dir:  ${GREEN}✓${NC} $INSTALL_DIR ($git_sha)"
        else
            echo -e "  Install dir:  ${GREEN}✓${NC} $INSTALL_DIR"
        fi
    else
        echo -e "  Install dir:  ${RED}✗${NC} $INSTALL_DIR (not found)"
        ((issues++))
    fi

    # Venv
    if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python3" ]; then
        echo -e "  Venv:         ${GREEN}✓${NC} $VENV_DIR"
    else
        echo -e "  Venv:         ${RED}✗${NC} not found or broken"
        ((issues++))
    fi

    # Config
    if [ -f "$CONFIG_DIR/frontpocket.ini" ]; then
        echo -e "  Config:       ${GREEN}✓${NC} $CONFIG_DIR/frontpocket.ini"
    else
        echo -e "  Config:       ${RED}✗${NC} not found"
        ((issues++))
    fi

    # Symlink
    if [ -L "$INSTALL_DIR/frontpocket.ini" ]; then
        local target
        target=$(readlink -f "$INSTALL_DIR/frontpocket.ini" 2>/dev/null)
        echo -e "  Config link:  ${GREEN}✓${NC} → $target"
    else
        echo -e "  Config link:  ${YELLOW}!${NC} missing or not a symlink"
        ((issues++))
    fi

    # HF Token
    if [ -f "$CONFIG_DIR/environment" ] && grep -q '^HF_TOKEN=.\+' "$CONFIG_DIR/environment" 2>/dev/null; then
        echo -e "  HF Token:     ${GREEN}✓${NC} configured"
    else
        echo -e "  HF Token:     ${YELLOW}!${NC} not configured (voice cloning unavailable)"
        ((issues++))
    fi

    # Service file
    if [ -f "$SERVICE_DIR/frontpocket.service" ]; then
        echo -e "  Service file: ${GREEN}✓${NC} installed"
    else
        echo -e "  Service file: ${RED}✗${NC} not installed"
        ((issues++))
    fi

    # Service status
    if systemctl --user is-active frontpocket &>/dev/null; then
        echo -e "  Service:      ${GREEN}✓${NC} running"
    elif systemctl --user is-enabled frontpocket &>/dev/null; then
        echo -e "  Service:      ${YELLOW}!${NC} enabled but not running"
        ((issues++))
    elif [ -f "$SERVICE_DIR/frontpocket.service" ]; then
        echo -e "  Service:      ${RED}✗${NC} installed but not enabled"
        ((issues++))
    else
        echo -e "  Service:      ${RED}✗${NC} not installed"
        ((issues++))
    fi

    # fp wrapper
    if [ -x "$WRAPPER_PATH" ]; then
        echo -e "  CLI (fp):     ${GREEN}✓${NC} $WRAPPER_PATH"
    elif [ -x "$LOCAL_WRAPPER_PATH" ]; then
        echo -e "  CLI (fp):     ${GREEN}✓${NC} $LOCAL_WRAPPER_PATH"
    else
        echo -e "  CLI (fp):     ${RED}✗${NC} not found"
        ((issues++))
    fi

    # Toolbar
    if [ -x "$TOOLBAR_WRAPPER" ]; then
        echo -e "  Toolbar cmd:  ${GREEN}✓${NC} $TOOLBAR_WRAPPER"
    else
        echo -e "  Toolbar cmd:  ${RED}✗${NC} not found"
        ((issues++))
    fi

    # Desktop entry
    if [ -f "$DESKTOP_FILE" ]; then
        echo -e "  Desktop entry:${GREEN}✓${NC} installed"
    else
        echo -e "  Desktop entry:${RED}✗${NC} not found"
        ((issues++))
    fi

    echo "─────────────────────────────────────────────────"
    if [ "$issues" -eq 0 ]; then
        echo -e "${GREEN}${BOLD}All components installed and service is running.${NC}"
    else
        echo -e "${YELLOW}${BOLD}$issues issue(s) detected. Re-run this script to fix.${NC}"
    fi
    echo ""
}

# ─── Uninstall ───────────────────────────────────────────────────────────────

do_uninstall() {
    echo -e "${RED}${BOLD}Uninstalling FrontPocket${NC}"
    echo ""

    if ! ask_yes_no "Remove FrontPocket and all its data? This cannot be undone." "n"; then
        echo "Aborted."
        exit 0
    fi

    echo ""

    # Stop & disable service
    log INFO "Stopping service..."
    systemctl --user stop frontpocket 2>/dev/null || true
    systemctl --user disable frontpocket 2>/dev/null || true
    log SUCCESS "Service stopped and disabled"

    # Remove service file
    log INFO "Removing service file..."
    rm -f "$SERVICE_DIR/frontpocket.service"
    systemctl --user daemon-reload 2>/dev/null || true
    log SUCCESS "Service file removed"

    # Remove wrappers
    log INFO "Removing CLI wrappers..."
    if [ -L "$WRAPPER_PATH" ] || [ -f "$WRAPPER_PATH" ]; then
        sudo rm -f "$WRAPPER_PATH"
        log SUCCESS "Removed $WRAPPER_PATH"
    fi
    rm -f "$LOCAL_WRAPPER_PATH"
    rm -f "$TOOLBAR_WRAPPER"
    log SUCCESS "Removed local wrappers"

    # Remove desktop entry
    rm -f "$DESKTOP_FILE"
    if command_exists update-desktop-database; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi
    log SUCCESS "Removed desktop entry"

    # Remove app dir
    log INFO "Removing $INSTALL_DIR ..."
    rm -rf "$INSTALL_DIR"
    log SUCCESS "Removed"

    # Remove config dir
    log INFO "Removing $CONFIG_DIR ..."
    rm -rf "$CONFIG_DIR"
    log SUCCESS "Removed"

    echo ""
    echo -e "${GREEN}${BOLD}FrontPocket has been completely removed.${NC}"
    echo ""
}

# ─── Help ────────────────────────────────────────────────────────────────────

show_help() {
    cat << 'HELPTEXT'
FrontPocket Installer — Low-Latency Text-to-Speech Server

Usage: install.sh [OPTIONS]

Options:
  --cpu          Force CPU-only PyTorch installation
  --gpu          Force GPU/CUDA PyTorch installation
  --uninstall    Remove FrontPocket completely
  --status       Show installation status and health check
  --yes          Accept all defaults (non-interactive mode)
  --token TOKEN  Set Hugging Face token (or set HF_TOKEN env var)
  --no-sudo      Skip commands requiring sudo (installs fp to ~/.local/bin)
  --help         Show this help message

Examples:
  ./install.sh                        # Interactive install, auto-detect GPU
  ./install.sh --cpu --yes            # Non-interactive, CPU-only
  ./install.sh --gpu --token hf_abc   # GPU install with token pre-set
  ./install.sh --status               # Health check
  ./install.sh --uninstall            # Full removal

Environment variables:
  HF_TOKEN    Hugging Face token (used if --token is not passed)

HELPTEXT
}

# ─── Argument Parsing ────────────────────────────────────────────────────────

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --cpu)       FORCE_CPU=true; shift ;;
            --gpu)       FORCE_GPU=true; shift ;;
            --uninstall) MODE="uninstall"; shift ;;
            --status)    MODE="status"; shift ;;
            --yes)       YES_MODE=true; shift ;;
            --token)
                if [ -z "${2:-}" ]; then
                    echo -e "${RED}Error: --token requires a value.${NC}"
                    exit 1
                fi
                ARG_TOKEN="$2"; shift 2
                ;;
            --no-sudo)   NO_SUDO=true; shift ;;
            --help|-h)   show_help; exit 0 ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                echo "Run with --help for usage information."
                exit 1
                ;;
        esac
    done

    if [ "$FORCE_CPU" = true ] && [ "$FORCE_GPU" = true ]; then
        echo -e "${RED}Error: --cpu and --gpu are mutually exclusive.${NC}"
        exit 1
    fi
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"

    case "$MODE" in
        status)
            do_status
            ;;
        uninstall)
            do_uninstall
            ;;
        install)
            log_banner
            preflight_checks
            step_install_deps
            step_clone_repo
            step_setup_venv
            step_setup_config
            step_setup_hf_token
            step_install_service
            step_install_wrappers
            finalize_log
            print_summary
            ;;
    esac
}

main "$@"
