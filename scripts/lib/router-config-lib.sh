#!/bin/bash
#
# router-config-lib.sh - Shared functions for router configuration
#

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

# =============================================================================
# Interface Detection Functions
# =============================================================================

# Get list of physical network interfaces (excludes lo, veth, br, etc.)
detect_interfaces() {
    local ifaces=()
    for iface in /sys/class/net/*; do
        iface=$(basename "$iface")
        # Skip virtual interfaces
        [[ "$iface" == "lo" ]] && continue
        [[ "$iface" == veth* ]] && continue
        [[ "$iface" == br* ]] && continue
        [[ "$iface" == docker* ]] && continue
        [[ "$iface" == virbr* ]] && continue
        [[ "$iface" == incusbr* ]] && continue

        # Check if it's a physical device (has a device symlink)
        if [[ -L "/sys/class/net/$iface/device" ]]; then
            ifaces+=("$iface")
        fi
    done
    echo "${ifaces[@]}"
}

# Get PCI address for an interface
get_pci_address() {
    local iface="$1"
    local device_path="/sys/class/net/$iface/device"
    if [[ -L "$device_path" ]]; then
        local pci_path
        pci_path=$(readlink -f "$device_path")
        basename "$pci_path"
    fi
}

# Get MAC address for an interface
get_mac_address() {
    local iface="$1"
    cat "/sys/class/net/$iface/address" 2>/dev/null || echo "unknown"
}

# Get driver for an interface
get_driver() {
    local iface="$1"
    local driver_path="/sys/class/net/$iface/device/driver"
    if [[ -L "$driver_path" ]]; then
        basename "$(readlink -f "$driver_path")"
    else
        echo "unknown"
    fi
}

# Display interface table
show_interface_table() {
    local ifaces=("$@")
    local i=1

    printf "\n${BOLD}%-4s %-18s %-19s %-14s %-10s${NC}\n" "#" "NAME" "MAC" "PCI" "DRIVER"
    printf "%s\n" "────────────────────────────────────────────────────────────────────"

    for iface in "${ifaces[@]}"; do
        local mac pci driver
        mac=$(get_mac_address "$iface")
        pci=$(get_pci_address "$iface")
        driver=$(get_driver "$iface")
        printf "%-4s %-18s %-19s %-14s %-10s\n" "$i)" "$iface" "$mac" "${pci:-N/A}" "$driver"
        ((i++))
    done
    echo ""
}

# =============================================================================
# Input Validation Functions
# =============================================================================

# Validate IPv4 address
validate_ipv4() {
    local ip="$1"
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        local IFS='.'
        read -ra octets <<< "$ip"
        for octet in "${octets[@]}"; do
            ((octet > 255)) && return 1
        done
        return 0
    fi
    return 1
}

# Validate IPv4 CIDR (address/prefix)
validate_ipv4_cidr() {
    local cidr="$1"
    if [[ "$cidr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]{1,2})$ ]]; then
        local ip="${cidr%/*}"
        local prefix="${cidr#*/}"
        validate_ipv4 "$ip" && ((prefix >= 0 && prefix <= 32))
        return $?
    fi
    return 1
}

# Validate IPv6 address (basic validation)
validate_ipv6() {
    local ip="$1"
    # Remove any prefix
    ip="${ip%/*}"
    # Basic check - contains colons and valid hex chars
    if [[ "$ip" =~ ^[0-9a-fA-F:]+$ ]] && [[ "$ip" == *:* ]]; then
        return 0
    fi
    return 1
}

# Validate IPv6 CIDR
validate_ipv6_cidr() {
    local cidr="$1"
    if [[ "$cidr" =~ /([0-9]{1,3})$ ]]; then
        local ip="${cidr%/*}"
        local prefix="${cidr#*/}"
        validate_ipv6 "$ip" && ((prefix >= 0 && prefix <= 128))
        return $?
    fi
    return 1
}

# Validate AS number
validate_asn() {
    local asn="$1"
    [[ "$asn" =~ ^[0-9]+$ ]] && ((asn >= 1 && asn <= 4294967295))
}

# Extract IP from CIDR
ip_from_cidr() {
    echo "${1%/*}"
}

# Extract prefix from CIDR
prefix_from_cidr() {
    echo "${1#*/}"
}

# =============================================================================
# User Prompt Functions
# =============================================================================

# Prompt for interface selection
# Usage: prompt_select_interface "Select EXTERNAL interface:" "${available[@]}"
# Returns: Selected interface name in $REPLY
prompt_select_interface() {
    local prompt="$1"
    shift
    local options=("$@")

    echo -e "\n${BOLD}$prompt${NC}"

    local i=1
    for opt in "${options[@]}"; do
        echo "  $i) $opt"
        ((i++))
    done

    while true; do
        read -rp "Choice [1-${#options[@]}]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#options[@]})); then
            REPLY="${options[$((choice-1))]}"
            return 0
        fi
        warn "Invalid selection. Please enter 1-${#options[@]}"
    done
}

# Prompt for yes/no
# Usage: if prompt_yes_no "Enable BGP?"; then ...
prompt_yes_no() {
    local prompt="$1"
    local default="${2:-n}"

    local yn_hint="y/N"
    [[ "$default" == "y" ]] && yn_hint="Y/n"

    while true; do
        read -rp "$prompt [$yn_hint]: " answer
        answer="${answer:-$default}"
        case "${answer,,}" in
            y|yes) return 0 ;;
            n|no) return 1 ;;
            *) warn "Please answer yes or no" ;;
        esac
    done
}

# Prompt for IP configuration
# Usage: prompt_ip_config "External" ipv4_var prefix_var gateway_var [ipv6_var ipv6_prefix_var ipv6_gw_var]
prompt_ip_config() {
    local label="$1"
    local -n v4_addr="$2"
    local -n v4_prefix="$3"
    local -n v4_gw="$4"

    echo -e "\n${BOLD}Configure $label interface:${NC}"

    # IPv4
    while true; do
        read -rp "  IPv4 Address [CIDR, e.g., 192.168.1.1/24]: " cidr
        if validate_ipv4_cidr "$cidr"; then
            v4_addr=$(ip_from_cidr "$cidr")
            v4_prefix=$(prefix_from_cidr "$cidr")
            break
        fi
        warn "Invalid IPv4 CIDR format"
    done

    while true; do
        read -rp "  IPv4 Gateway: " v4_gw
        if validate_ipv4 "$v4_gw"; then
            break
        fi
        warn "Invalid IPv4 address"
    done

    # IPv6 (optional)
    if [[ $# -ge 7 ]]; then
        local -n v6_addr="$5"
        local -n v6_prefix="$6"
        local -n v6_gw="$7"

        read -rp "  IPv6 Address [CIDR, optional]: " cidr
        if [[ -n "$cidr" ]]; then
            if validate_ipv6_cidr "$cidr"; then
                v6_addr=$(ip_from_cidr "$cidr")
                v6_prefix=$(prefix_from_cidr "$cidr")

                while true; do
                    read -rp "  IPv6 Gateway: " v6_gw
                    if validate_ipv6 "$v6_gw"; then
                        break
                    fi
                    warn "Invalid IPv6 address"
                done
            else
                warn "Invalid IPv6 CIDR format, skipping IPv6"
            fi
        fi
    fi
}

# Prompt for comma-separated list
# Usage: prompt_list "Internal networks to NAT:" result_array
prompt_list() {
    local prompt="$1"
    local -n result="$2"

    read -rp "$prompt " input
    IFS=',' read -ra result <<< "$input"
    # Trim whitespace from each element
    for i in "${!result[@]}"; do
        result[$i]=$(echo "${result[$i]}" | xargs)
    done
}

# =============================================================================
# Configuration Generation Functions
# =============================================================================

# Generate DPDK device entries for startup-core.conf
generate_dpdk_devices() {
    local -n pcis="$1"
    local -n names="$2"
    local output=""

    for i in "${!pcis[@]}"; do
        output+="  dev ${pcis[$i]} {\n"
        output+="    name ${names[$i]}\n"
        output+="  }\n"
    done
    echo -e "$output"
}

# Generate interface move commands for systemd service
generate_iface_moves() {
    local -n ifaces="$1"
    local output=""

    for iface in "${ifaces[@]}"; do
        output+="ExecStart=/usr/sbin/ip link set $iface netns dataplane\n"
    done
    echo -e "$output"
}

# Generate VPP interface configuration
generate_vpp_interface_config() {
    local name="$1"
    local ipv4="$2"
    local ipv4_prefix="$3"
    local ipv6="$4"
    local ipv6_prefix="$5"

    local output=""
    output+="lcp create $name host-if $name\n"
    output+="set interface state $name up\n"
    output+="set interface ip address $name $ipv4/$ipv4_prefix\n"
    [[ -n "$ipv6" ]] && output+="set interface ip address $name $ipv6/$ipv6_prefix\n"
    output+="set interface mtu 1500 $name\n"
    echo -e "$output"
}

# Generate ABF ACL rules for NAT steering
generate_abf_acls() {
    local -n networks="$1"
    local -n ifaces="$2"
    local output=""
    local acl_id=0

    for i in "${!networks[@]}"; do
        local net="${networks[$i]}"
        local iface="${ifaces[$i]}"

        # Create ACL that permits only this network (for NAT steering)
        # Deny traffic to RFC1918 and other internal networks, permit rest
        output+="set acl-plugin acl deny src $net dst 10.234.116.0/24, deny src $net dst 192.168.0.0/16, deny src $net dst 172.16.0.0/12, deny src $net dst 10.0.0.0/8, permit src $net\n"
        output+="abf policy add id $acl_id acl $acl_id via 169.254.1.5 memif1/0\n"
        output+="abf attach ip4 policy $acl_id $iface\n"
        ((acl_id++))
    done
    echo -e "$output"
}

# Generate NAT mappings
generate_nat_mappings() {
    local -n networks="$1"
    local pool="$2"
    local output=""

    # Split pool evenly among internal networks
    # For simplicity, using /30 subnets from the pool
    local pool_base="${pool%/*}"

    for i in "${!networks[@]}"; do
        local net="${networks[$i]}"
        # This is simplified - real implementation would calculate proper subnets
        output+="det44 add in $net out $pool_base/$((30 + i))\n"
    done
    echo -e "$output"
}

# Generate FRR BGP configuration
generate_bgp_config() {
    local asn="$1"
    local router_id="$2"
    local peer_v4="$3"
    local peer_v6="$4"
    local peer_asn="$5"
    shift 5
    local networks_v4=("$@")

    local output=""
    output+="router bgp $asn\n"
    output+=" bgp router-id $router_id\n"
    output+=" no bgp default ipv4-unicast\n"

    if [[ -n "$peer_v4" ]]; then
        output+=" neighbor $peer_v4 remote-as $peer_asn\n"
        output+=" neighbor $peer_v4 update-source $router_id\n"
    fi

    if [[ -n "$peer_v6" ]]; then
        output+=" neighbor $peer_v6 remote-as $peer_asn\n"
    fi

    output+=" !\n"
    output+=" address-family ipv4 unicast\n"
    for net in "${networks_v4[@]}"; do
        output+="  network $net\n"
    done
    output+="  redistribute connected route-map ALLOW_OUT\n"
    [[ -n "$peer_v4" ]] && output+="  neighbor $peer_v4 activate\n"
    [[ -n "$peer_v4" ]] && output+="  neighbor $peer_v4 soft-reconfiguration inbound\n"
    output+=" exit-address-family\n"

    if [[ -n "$peer_v6" ]]; then
        output+=" !\n"
        output+=" address-family ipv6 unicast\n"
        output+="  redistribute connected route-map ALLOW_OUT\n"
        output+="  neighbor $peer_v6 activate\n"
        output+="  neighbor $peer_v6 soft-reconfiguration inbound\n"
        output+=" exit-address-family\n"
    fi

    output+="exit\n"
    echo -e "$output"
}

# Generate IPv6 RA configuration
generate_ipv6_ra() {
    local iface="$1"
    local prefix="$2"

    if [[ -n "$prefix" ]]; then
        echo "\$CMD ip6 nd $iface ra-interval 30 15"
        echo "\$CMD ip6 nd $iface prefix $prefix infinite"
        echo "\$CMD ip6 nd $iface no ra-suppress"
    fi
}

# =============================================================================
# Configuration File Functions
# =============================================================================

# Save configuration to router.conf
save_config() {
    local config_file="$1"
    shift

    mkdir -p "$(dirname "$config_file")"

    {
        echo "# Router configuration"
        echo "# Generated by configure-router.sh on $(date)"
        echo "#"
        for var in "$@"; do
            declare -p "$var" 2>/dev/null | sed 's/^declare -[-aA]* //'
        done
    } > "$config_file"

    log "Configuration saved to $config_file"
}

# Load configuration from router.conf
load_config() {
    local config_file="$1"
    if [[ -f "$config_file" ]]; then
        # shellcheck disable=SC1090
        source "$config_file"
        return 0
    fi
    return 1
}

# Process a template file
# Usage: process_template template_file output_file
process_template() {
    local template="$1"
    local output="$2"

    if [[ ! -f "$template" ]]; then
        error "Template not found: $template"
        return 1
    fi

    # Use envsubst for simple ${VAR} substitution
    envsubst < "$template" > "$output"
}
