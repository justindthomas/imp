#!/bin/bash
#
# configure-router.sh - Interactive router configuration script
#
# This script configures VPP, FRR, and network interfaces for the IMP platform.
# It can be run on first boot or from the installer ISO.
#
# Usage: configure-router.sh [--apply-only]
#   --apply-only    Skip interactive prompts, apply existing config from /persistent/config/router.conf
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="/etc/imp/templates"
CONFIG_FILE="/persistent/config/router.conf"
GENERATED_DIR="/tmp/imp-generated-config"

# Source the library
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/router-config-lib.sh" 2>/dev/null || \
source /usr/local/lib/imp/router-config-lib.sh 2>/dev/null || \
    { echo "ERROR: Cannot find router-config-lib.sh"; exit 1; }

# =============================================================================
# Configuration Variables
# =============================================================================

# Hardware
MANAGEMENT_IFACE=""
EXTERNAL_IFACE=""
EXTERNAL_PCI=""
declare -a INTERNAL_IFACES=()
declare -a INTERNAL_PCIS=()
declare -a INTERNAL_NAMES=()

# External interface
EXTERNAL_IPV4=""
EXTERNAL_IPV4_PREFIX=""
EXTERNAL_IPV4_GW=""
EXTERNAL_IPV6=""
EXTERNAL_IPV6_PREFIX=""
EXTERNAL_IPV6_GW=""

# Internal interfaces (arrays)
declare -a INTERNAL_IPV4=()
declare -a INTERNAL_IPV4_PREFIX=()
declare -a INTERNAL_IPV6=()
declare -a INTERNAL_IPV6_PREFIX=()

# Management
MANAGEMENT_MODE="dhcp"
MANAGEMENT_IPV4=""
MANAGEMENT_IPV4_PREFIX=""
MANAGEMENT_IPV4_GW=""

# BGP
BGP_ENABLED="false"
BGP_ASN=""
BGP_ROUTER_ID=""
BGP_PEER_IPV4=""
BGP_PEER_IPV6=""
BGP_PEER_ASN=""

# NAT
NAT_POOL=""
declare -a NAT_INTERNAL_NETWORKS=()

# Container network
CONTAINER_NETWORK="10.234.116.0/24"
CONTAINER_GATEWAY="10.234.116.5"
CONTAINER_PREFIX="24"
CONTAINER_IPV6=""
CONTAINER_IPV6_PREFIX=""

# Hostname
HOSTNAME=$(hostname)

# =============================================================================
# Main Functions
# =============================================================================

show_banner() {
    echo ""
    echo "==========================================="
    echo "  IMP Router Configuration"
    echo "==========================================="
    echo ""
}

phase1_detect_interfaces() {
    log "Phase 1: Interface Discovery"
    echo ""

    mapfile -t ALL_IFACES < <(detect_interfaces | tr ' ' '\n')

    if [[ ${#ALL_IFACES[@]} -eq 0 ]]; then
        error "No physical network interfaces detected!"
        exit 1
    fi

    info "Found ${#ALL_IFACES[@]} physical network interface(s):"
    show_interface_table "${ALL_IFACES[@]}"
}

phase2_assign_roles() {
    log "Phase 2: Interface Role Assignment"

    # Make a copy of available interfaces
    local available=("${ALL_IFACES[@]}")

    # Management interface
    echo -e "\n${BOLD}Management interface${NC}"
    echo "  This interface stays in the default namespace for SSH access."
    echo "  Typically used for out-of-band management."
    prompt_select_interface "Select MANAGEMENT interface:" "${available[@]}"
    MANAGEMENT_IFACE="$REPLY"

    # Remove from available
    local new_available=()
    for iface in "${available[@]}"; do
        [[ "$iface" != "$MANAGEMENT_IFACE" ]] && new_available+=("$iface")
    done
    available=("${new_available[@]}")

    if [[ ${#available[@]} -lt 2 ]]; then
        error "Need at least 2 more interfaces for external and internal roles"
        exit 1
    fi

    # External interface
    echo -e "\n${BOLD}External interface (WAN/Upstream)${NC}"
    echo "  This interface connects to the upstream provider."
    echo "  It will be managed by VPP with DPDK."
    prompt_select_interface "Select EXTERNAL interface:" "${available[@]}"
    EXTERNAL_IFACE="$REPLY"
    EXTERNAL_PCI=$(get_pci_address "$EXTERNAL_IFACE")

    # Remove from available
    new_available=()
    for iface in "${available[@]}"; do
        [[ "$iface" != "$EXTERNAL_IFACE" ]] && new_available+=("$iface")
    done
    available=("${new_available[@]}")

    # Internal interfaces (one or more)
    echo -e "\n${BOLD}Internal interface(s) (LAN/Downstream)${NC}"
    echo "  These interfaces connect to internal networks."
    echo "  Multiple internal interfaces are supported."

    local internal_count=0
    while [[ ${#available[@]} -gt 0 ]]; do
        prompt_select_interface "Select INTERNAL interface #$((internal_count + 1)):" "${available[@]}"
        INTERNAL_IFACES+=("$REPLY")
        INTERNAL_PCIS+=("$(get_pci_address "$REPLY")")
        INTERNAL_NAMES+=("internal$internal_count")
        ((internal_count++))

        # Remove from available
        new_available=()
        for iface in "${available[@]}"; do
            [[ "$iface" != "$REPLY" ]] && new_available+=("$iface")
        done
        available=("${new_available[@]}")

        if [[ ${#available[@]} -gt 0 ]]; then
            if ! prompt_yes_no "Add another internal interface?"; then
                break
            fi
        fi
    done

    # Summary
    echo -e "\n${BOLD}Interface Assignment Summary:${NC}"
    echo "  Management: $MANAGEMENT_IFACE"
    echo "  External:   $EXTERNAL_IFACE (PCI: $EXTERNAL_PCI)"
    for i in "${!INTERNAL_IFACES[@]}"; do
        echo "  Internal:   ${INTERNAL_IFACES[$i]} (PCI: ${INTERNAL_PCIS[$i]}) -> ${INTERNAL_NAMES[$i]}"
    done
    echo ""
}

phase3_ip_config() {
    log "Phase 3: IP Configuration"

    # External interface
    prompt_ip_config "EXTERNAL ($EXTERNAL_IFACE)" \
        EXTERNAL_IPV4 EXTERNAL_IPV4_PREFIX EXTERNAL_IPV4_GW \
        EXTERNAL_IPV6 EXTERNAL_IPV6_PREFIX EXTERNAL_IPV6_GW

    # Internal interfaces
    for i in "${!INTERNAL_IFACES[@]}"; do
        local iface="${INTERNAL_IFACES[$i]}"
        local ipv4="" prefix="" ipv6="" ipv6_prefix=""

        echo -e "\n${BOLD}Configure INTERNAL interface #$((i+1)) ($iface):${NC}"

        while true; do
            read -rp "  IPv4 Address [CIDR, e.g., 192.168.1.1/24]: " cidr
            if validate_ipv4_cidr "$cidr"; then
                ipv4=$(ip_from_cidr "$cidr")
                prefix=$(prefix_from_cidr "$cidr")
                break
            fi
            warn "Invalid IPv4 CIDR format"
        done

        read -rp "  IPv6 Address [CIDR, optional]: " cidr
        if [[ -n "$cidr" ]] && validate_ipv6_cidr "$cidr"; then
            ipv6=$(ip_from_cidr "$cidr")
            ipv6_prefix=$(prefix_from_cidr "$cidr")
        fi

        INTERNAL_IPV4+=("$ipv4")
        INTERNAL_IPV4_PREFIX+=("$prefix")
        INTERNAL_IPV6+=("$ipv6")
        INTERNAL_IPV6_PREFIX+=("$ipv6_prefix")
    done
}

phase4_management_config() {
    log "Phase 4: Management Interface Configuration"

    echo -e "\n${BOLD}Configure MANAGEMENT interface ($MANAGEMENT_IFACE):${NC}"
    echo "  1) DHCP (recommended for out-of-band management)"
    echo "  2) Static IP"

    while true; do
        read -rp "Choice [1-2]: " choice
        case "$choice" in
            1)
                MANAGEMENT_MODE="dhcp"
                break
                ;;
            2)
                MANAGEMENT_MODE="static"
                while true; do
                    read -rp "  IPv4 Address [CIDR]: " cidr
                    if validate_ipv4_cidr "$cidr"; then
                        MANAGEMENT_IPV4=$(ip_from_cidr "$cidr")
                        MANAGEMENT_IPV4_PREFIX=$(prefix_from_cidr "$cidr")
                        break
                    fi
                    warn "Invalid IPv4 CIDR format"
                done

                while true; do
                    read -rp "  Gateway: " MANAGEMENT_IPV4_GW
                    if validate_ipv4 "$MANAGEMENT_IPV4_GW"; then
                        break
                    fi
                    warn "Invalid IPv4 address"
                done
                break
                ;;
            *)
                warn "Please enter 1 or 2"
                ;;
        esac
    done
}

phase5_bgp_config() {
    log "Phase 5: BGP Configuration (Optional)"

    if prompt_yes_no "Enable BGP routing?"; then
        BGP_ENABLED="true"

        while true; do
            read -rp "  Local AS Number: " BGP_ASN
            if validate_asn "$BGP_ASN"; then
                break
            fi
            warn "Invalid AS number"
        done

        # Default router-id to external IPv4
        BGP_ROUTER_ID="$EXTERNAL_IPV4"
        read -rp "  Router ID [$BGP_ROUTER_ID]: " input
        [[ -n "$input" ]] && BGP_ROUTER_ID="$input"

        while true; do
            read -rp "  Peer IPv4 Address: " BGP_PEER_IPV4
            if validate_ipv4 "$BGP_PEER_IPV4"; then
                break
            fi
            warn "Invalid IPv4 address"
        done

        read -rp "  Peer IPv6 Address [optional]: " BGP_PEER_IPV6
        if [[ -n "$BGP_PEER_IPV6" ]] && ! validate_ipv6 "$BGP_PEER_IPV6"; then
            warn "Invalid IPv6 address, skipping IPv6 peering"
            BGP_PEER_IPV6=""
        fi

        while true; do
            read -rp "  Peer AS Number: " BGP_PEER_ASN
            if validate_asn "$BGP_PEER_ASN"; then
                break
            fi
            warn "Invalid AS number"
        done
    else
        BGP_ENABLED="false"
        info "BGP disabled. Static default routes will be used."
    fi
}

phase6_nat_config() {
    log "Phase 6: NAT Configuration"

    while true; do
        read -rp "  NAT Pool (public IPs, CIDR): " NAT_POOL
        if validate_ipv4_cidr "$NAT_POOL"; then
            break
        fi
        warn "Invalid IPv4 CIDR format"
    done

    echo ""
    echo "  Enter internal networks to NAT (comma-separated)."
    echo "  These can be directly connected or reachable via downstream routers."
    echo "  Example: 192.168.20.0/24, 10.10.30.0/24"
    echo ""

    while true; do
        read -rp "  Internal networks: " input
        IFS=',' read -ra NAT_INTERNAL_NETWORKS <<< "$input"
        # Trim whitespace
        for i in "${!NAT_INTERNAL_NETWORKS[@]}"; do
            NAT_INTERNAL_NETWORKS[$i]=$(echo "${NAT_INTERNAL_NETWORKS[$i]}" | xargs)
        done

        # Validate all
        local valid=true
        for net in "${NAT_INTERNAL_NETWORKS[@]}"; do
            if ! validate_ipv4_cidr "$net"; then
                warn "Invalid CIDR: $net"
                valid=false
            fi
        done
        $valid && break
    done

    # Container network (use defaults)
    echo ""
    info "Container network defaults: $CONTAINER_NETWORK (gateway: $CONTAINER_GATEWAY)"
    if prompt_yes_no "Use default container network?"; then
        : # Keep defaults
    else
        while true; do
            read -rp "  Container Network [CIDR]: " CONTAINER_NETWORK
            if validate_ipv4_cidr "$CONTAINER_NETWORK"; then
                break
            fi
            warn "Invalid IPv4 CIDR format"
        done

        while true; do
            read -rp "  Container Gateway IP: " CONTAINER_GATEWAY
            if validate_ipv4 "$CONTAINER_GATEWAY"; then
                break
            fi
            warn "Invalid IPv4 address"
        done
        CONTAINER_PREFIX=$(prefix_from_cidr "$CONTAINER_NETWORK")
    fi

    # Add container network to NAT list if not already there
    local container_net_base="${CONTAINER_NETWORK}"
    local found=false
    for net in "${NAT_INTERNAL_NETWORKS[@]}"; do
        [[ "$net" == "$container_net_base" ]] && found=true
    done
    if ! $found; then
        NAT_INTERNAL_NETWORKS+=("$CONTAINER_NETWORK")
        info "Added container network $CONTAINER_NETWORK to NAT list"
    fi
}

phase7_confirm() {
    log "Phase 7: Configuration Summary"

    echo ""
    echo "==========================================="
    echo "  Configuration Summary"
    echo "==========================================="
    echo ""
    echo "INTERFACES:"
    echo "  Management: $MANAGEMENT_IFACE ($MANAGEMENT_MODE)"
    echo "  External:   $EXTERNAL_IFACE -> $EXTERNAL_IPV4/$EXTERNAL_IPV4_PREFIX"
    [[ -n "$EXTERNAL_IPV6" ]] && echo "              $EXTERNAL_IPV6/$EXTERNAL_IPV6_PREFIX"
    for i in "${!INTERNAL_IFACES[@]}"; do
        echo "  Internal:   ${INTERNAL_IFACES[$i]} -> ${INTERNAL_IPV4[$i]}/${INTERNAL_IPV4_PREFIX[$i]}"
        [[ -n "${INTERNAL_IPV6[$i]}" ]] && echo "              ${INTERNAL_IPV6[$i]}/${INTERNAL_IPV6_PREFIX[$i]}"
    done
    echo ""
    echo "ROUTING:"
    if [[ "$BGP_ENABLED" == "true" ]]; then
        echo "  BGP AS:     $BGP_ASN"
        echo "  Router ID:  $BGP_ROUTER_ID"
        echo "  Peer:       $BGP_PEER_IPV4 (AS $BGP_PEER_ASN)"
        [[ -n "$BGP_PEER_IPV6" ]] && echo "              $BGP_PEER_IPV6"
    else
        echo "  Static routing (gateway: $EXTERNAL_IPV4_GW)"
    fi
    echo ""
    echo "NAT:"
    echo "  Pool:       $NAT_POOL"
    echo "  Networks:   ${NAT_INTERNAL_NETWORKS[*]}"
    echo ""
    echo "CONTAINERS:"
    echo "  Network:    $CONTAINER_NETWORK"
    echo "  Gateway:    $CONTAINER_GATEWAY"
    echo ""
    echo "==========================================="
    echo ""

    if ! prompt_yes_no "Apply this configuration?" "y"; then
        error "Configuration cancelled"
        exit 1
    fi
}

# =============================================================================
# Configuration Generation
# =============================================================================

generate_configs() {
    log "Generating configuration files..."

    mkdir -p "$GENERATED_DIR"

    # --- startup-core.conf ---
    local internal_dpdk=""
    for i in "${!INTERNAL_PCIS[@]}"; do
        internal_dpdk+="  dev ${INTERNAL_PCIS[$i]} {\n"
        internal_dpdk+="    name ${INTERNAL_NAMES[$i]}\n"
        internal_dpdk+="  }\n"
    done

    export EXTERNAL_PCI HOSTNAME
    export INTERNAL_DPDK_DEVICES="$internal_dpdk"

    sed -e "s|\${EXTERNAL_PCI}|${EXTERNAL_PCI}|g" \
        -e "s|{{INTERNAL_DPDK_DEVICES}}|${internal_dpdk}|" \
        "$TEMPLATE_DIR/vpp/startup-core.conf.tmpl" > "$GENERATED_DIR/startup-core.conf"

    # --- commands-core.txt ---
    local ext_v4_cmd="set interface ip address external $EXTERNAL_IPV4/$EXTERNAL_IPV4_PREFIX"
    local ext_v6_cmd=""
    [[ -n "$EXTERNAL_IPV6" ]] && ext_v6_cmd="set interface ip address external $EXTERNAL_IPV6/$EXTERNAL_IPV6_PREFIX"

    local internal_config=""
    for i in "${!INTERNAL_NAMES[@]}"; do
        local name="${INTERNAL_NAMES[$i]}"
        internal_config+="lcp create $name host-if $name\n"
        internal_config+="set interface state $name up\n"
        internal_config+="set interface ip address $name ${INTERNAL_IPV4[$i]}/${INTERNAL_IPV4_PREFIX[$i]}\n"
        [[ -n "${INTERNAL_IPV6[$i]}" ]] && internal_config+="set interface ip address $name ${INTERNAL_IPV6[$i]}/${INTERNAL_IPV6_PREFIX[$i]}\n"
        internal_config+="set interface mtu 1500 $name\n\n"
    done

    # ABF ACLs for internal networks
    local abf_acls=""
    local acl_id=0
    for i in "${!INTERNAL_NAMES[@]}"; do
        local name="${INTERNAL_NAMES[$i]}"
        local net="${INTERNAL_IPV4[$i]}/${INTERNAL_IPV4_PREFIX[$i]}"
        # Convert host IP/prefix to network
        net=$(echo "$net" | sed 's/\.[0-9]*\//.0\//')

        abf_acls+="set acl-plugin acl deny src ${net} dst 10.234.116.0/24, deny src ${net} dst 192.168.0.0/16, deny src ${net} dst 172.16.0.0/12, permit src ${net}\n"
        abf_acls+="abf policy add id $acl_id acl $acl_id via 169.254.1.5 memif1/0\n"
        abf_acls+="abf attach ip4 policy $acl_id $name\n"
        ((acl_id++))
    done

    local default_v4="ip route add 0.0.0.0/0 via $EXTERNAL_IPV4_GW external"
    local default_v6=""
    [[ -n "$EXTERNAL_IPV6_GW" ]] && default_v6="ip route add ::/0 via $EXTERNAL_IPV6_GW external"

    sed -e "s|{{EXTERNAL_IPV4_CMD}}|${ext_v4_cmd}|" \
        -e "s|{{EXTERNAL_IPV6_CMD}}|${ext_v6_cmd}|" \
        -e "s|{{INTERNAL_INTERFACES_CONFIG}}|${internal_config}|" \
        -e "s|{{ABF_ACLS}}|${abf_acls}|" \
        -e "s|{{DEFAULT_ROUTE_IPV4}}|${default_v4}|" \
        -e "s|{{DEFAULT_ROUTE_IPV6}}|${default_v6}|" \
        -e "s|\${NAT_POOL}|${NAT_POOL}|g" \
        "$TEMPLATE_DIR/vpp/commands-core.txt.tmpl" > "$GENERATED_DIR/commands-core.txt"

    # --- commands-nat.txt ---
    local nat_mappings=""
    local pool_base="${NAT_POOL%/*}"
    for i in "${!NAT_INTERNAL_NETWORKS[@]}"; do
        nat_mappings+="det44 add in ${NAT_INTERNAL_NETWORKS[$i]} out ${pool_base}/$((30))\n"
    done

    local nat_routes=""
    for net in "${NAT_INTERNAL_NETWORKS[@]}"; do
        nat_routes+="ip route add $net via 169.254.1.4 memif1/0\n"
    done

    sed -e "s|{{NAT_MAPPINGS}}|${nat_mappings}|" \
        -e "s|{{NAT_INTERNAL_ROUTES}}|${nat_routes}|" \
        "$TEMPLATE_DIR/vpp/commands-nat.txt.tmpl" > "$GENERATED_DIR/commands-nat.txt"

    # --- frr.conf ---
    local static_routes=""
    for net in "${NAT_INTERNAL_NETWORKS[@]}"; do
        static_routes+="ip route $net blackhole\n"
    done
    static_routes+="ip route $NAT_POOL 169.254.1.6 memif2\n"

    local bgp_config=""
    if [[ "$BGP_ENABLED" == "true" ]]; then
        bgp_config+="router bgp $BGP_ASN\n"
        bgp_config+=" bgp router-id $BGP_ROUTER_ID\n"
        bgp_config+=" no bgp default ipv4-unicast\n"
        bgp_config+=" neighbor $BGP_PEER_IPV4 remote-as $BGP_PEER_ASN\n"
        bgp_config+=" neighbor $BGP_PEER_IPV4 update-source $BGP_ROUTER_ID\n"
        [[ -n "$BGP_PEER_IPV6" ]] && bgp_config+=" neighbor $BGP_PEER_IPV6 remote-as $BGP_PEER_ASN\n"
        bgp_config+=" !\n"
        bgp_config+=" address-family ipv4 unicast\n"
        for net in "${NAT_INTERNAL_NETWORKS[@]}"; do
            bgp_config+="  network $net\n"
        done
        bgp_config+="  network $NAT_POOL\n"
        bgp_config+="  redistribute connected route-map ALLOW_OUT\n"
        bgp_config+="  neighbor $BGP_PEER_IPV4 activate\n"
        bgp_config+="  neighbor $BGP_PEER_IPV4 soft-reconfiguration inbound\n"
        bgp_config+=" exit-address-family\n"
        if [[ -n "$BGP_PEER_IPV6" ]]; then
            bgp_config+=" !\n"
            bgp_config+=" address-family ipv6 unicast\n"
            bgp_config+="  redistribute connected route-map ALLOW_OUT\n"
            bgp_config+="  neighbor $BGP_PEER_IPV6 activate\n"
            bgp_config+="  neighbor $BGP_PEER_IPV6 soft-reconfiguration inbound\n"
            bgp_config+=" exit-address-family\n"
        fi
        bgp_config+="exit\n"
    fi

    sed -e "s|\${HOSTNAME}|${HOSTNAME}|g" \
        -e "s|{{FRR_STATIC_ROUTES}}|${static_routes}|" \
        -e "s|{{BGP_CONFIG}}|${bgp_config}|" \
        "$TEMPLATE_DIR/frr/frr.conf.tmpl" > "$GENERATED_DIR/frr.conf"

    # --- netns-move-interfaces.service ---
    local iface_moves=""
    for iface in "${INTERNAL_IFACES[@]}"; do
        iface_moves+="ExecStart=/usr/sbin/ip link set $iface netns dataplane\n"
    done

    sed -e "s|\${EXTERNAL_IFACE}|${EXTERNAL_IFACE}|g" \
        -e "s|{{INTERNAL_IFACE_MOVES}}|${iface_moves}|" \
        "$TEMPLATE_DIR/systemd/netns-move-interfaces.service.tmpl" > "$GENERATED_DIR/netns-move-interfaces.service"

    # --- vpp-core-config.sh ---
    local ipv6_ra=""
    for i in "${!INTERNAL_NAMES[@]}"; do
        local name="${INTERNAL_NAMES[$i]}"
        local prefix="${INTERNAL_IPV6_PREFIX[$i]}"
        if [[ -n "${INTERNAL_IPV6[$i]}" ]]; then
            # Calculate RA prefix (assume /64 for RA)
            local v6_net
            v6_net=$(echo "${INTERNAL_IPV6[$i]}" | sed 's/:[^:]*$/::/')
            ipv6_ra+="\$CMD ip6 nd $name ra-interval 30 15\n"
            ipv6_ra+="\$CMD ip6 nd $name prefix ${v6_net}/64 infinite\n"
            ipv6_ra+="\$CMD ip6 nd $name no ra-suppress\n"
        fi
    done

    sed -e "s|{{IPV6_RA_CONFIG}}|${ipv6_ra}|" \
        "$TEMPLATE_DIR/scripts/vpp-core-config.sh.tmpl" > "$GENERATED_DIR/vpp-core-config.sh"
    chmod +x "$GENERATED_DIR/vpp-core-config.sh"

    # --- incus-networking.sh ---
    local container_v6_cmd=""
    local container_v6_ra=""
    if [[ -n "$CONTAINER_IPV6" ]]; then
        container_v6_cmd="\$CMD set int ip address host-incus-dataplane $CONTAINER_IPV6/$CONTAINER_IPV6_PREFIX"
        container_v6_ra+="\$CMD ip6 nd host-incus-dataplane ra-interval 30 15\n"
        container_v6_ra+="\$CMD ip6 nd host-incus-dataplane prefix ${CONTAINER_IPV6%:*}::/64 infinite\n"
        container_v6_ra+="\$CMD ip6 nd host-incus-dataplane no ra-suppress\n"
    fi

    sed -e "s|\${CONTAINER_GATEWAY}|${CONTAINER_GATEWAY}|g" \
        -e "s|\${CONTAINER_PREFIX}|${CONTAINER_PREFIX}|g" \
        -e "s|\${CONTAINER_NETWORK}|${CONTAINER_NETWORK}|g" \
        -e "s|{{CONTAINER_IPV6_CMD}}|${container_v6_cmd}|" \
        -e "s|{{CONTAINER_IPV6_RA}}|${container_v6_ra}|" \
        "$TEMPLATE_DIR/scripts/incus-networking.sh.tmpl" > "$GENERATED_DIR/incus-networking.sh"
    chmod +x "$GENERATED_DIR/incus-networking.sh"

    log "Configuration files generated in $GENERATED_DIR"
}

apply_configs() {
    log "Applying configuration..."

    # Copy generated configs to system locations
    cp "$GENERATED_DIR/startup-core.conf" /etc/vpp/startup-core.conf
    cp "$GENERATED_DIR/commands-core.txt" /etc/vpp/commands-core.txt
    cp "$GENERATED_DIR/commands-nat.txt" /etc/vpp/commands-nat.txt
    cp "$GENERATED_DIR/frr.conf" /etc/frr/frr.conf
    cp "$GENERATED_DIR/netns-move-interfaces.service" /etc/systemd/system/netns-move-interfaces.service
    cp "$GENERATED_DIR/vpp-core-config.sh" /usr/local/bin/vpp-core-config.sh
    cp "$GENERATED_DIR/incus-networking.sh" /usr/local/bin/incus-networking.sh

    # Configure management interface
    if [[ "$MANAGEMENT_MODE" == "dhcp" ]]; then
        cat > /etc/systemd/network/10-management.network << EOF
[Match]
Name=$MANAGEMENT_IFACE

[Network]
DHCP=yes
EOF
    else
        cat > /etc/systemd/network/10-management.network << EOF
[Match]
Name=$MANAGEMENT_IFACE

[Network]
Address=$MANAGEMENT_IPV4/$MANAGEMENT_IPV4_PREFIX
Gateway=$MANAGEMENT_IPV4_GW
EOF
    fi

    # Reload systemd
    systemctl daemon-reload

    log "Configuration applied"
}

enable_services() {
    log "Enabling services..."

    systemctl enable systemd-networkd
    systemctl enable netns-dataplane
    systemctl enable netns-move-interfaces
    systemctl enable vpp-core
    systemctl enable vpp-core-config
    systemctl enable vpp-nat
    systemctl enable frr
    systemctl enable incus-dataplane

    log "Services enabled"
}

save_configuration() {
    log "Saving configuration..."

    mkdir -p "$(dirname "$CONFIG_FILE")"

    {
        echo "# Router configuration"
        echo "# Generated by configure-router.sh on $(date)"
        echo "#"
        echo ""
        echo "HOSTNAME=\"$HOSTNAME\""
        echo ""
        echo "# Hardware"
        echo "MANAGEMENT_IFACE=\"$MANAGEMENT_IFACE\""
        echo "EXTERNAL_IFACE=\"$EXTERNAL_IFACE\""
        echo "EXTERNAL_PCI=\"$EXTERNAL_PCI\""
        echo "INTERNAL_IFACES=(${INTERNAL_IFACES[*]@Q})"
        echo "INTERNAL_PCIS=(${INTERNAL_PCIS[*]@Q})"
        echo "INTERNAL_NAMES=(${INTERNAL_NAMES[*]@Q})"
        echo ""
        echo "# External interface"
        echo "EXTERNAL_IPV4=\"$EXTERNAL_IPV4\""
        echo "EXTERNAL_IPV4_PREFIX=\"$EXTERNAL_IPV4_PREFIX\""
        echo "EXTERNAL_IPV4_GW=\"$EXTERNAL_IPV4_GW\""
        echo "EXTERNAL_IPV6=\"$EXTERNAL_IPV6\""
        echo "EXTERNAL_IPV6_PREFIX=\"$EXTERNAL_IPV6_PREFIX\""
        echo "EXTERNAL_IPV6_GW=\"$EXTERNAL_IPV6_GW\""
        echo ""
        echo "# Internal interfaces"
        echo "INTERNAL_IPV4=(${INTERNAL_IPV4[*]@Q})"
        echo "INTERNAL_IPV4_PREFIX=(${INTERNAL_IPV4_PREFIX[*]@Q})"
        echo "INTERNAL_IPV6=(${INTERNAL_IPV6[*]@Q})"
        echo "INTERNAL_IPV6_PREFIX=(${INTERNAL_IPV6_PREFIX[*]@Q})"
        echo ""
        echo "# Management"
        echo "MANAGEMENT_MODE=\"$MANAGEMENT_MODE\""
        echo "MANAGEMENT_IPV4=\"$MANAGEMENT_IPV4\""
        echo "MANAGEMENT_IPV4_PREFIX=\"$MANAGEMENT_IPV4_PREFIX\""
        echo "MANAGEMENT_IPV4_GW=\"$MANAGEMENT_IPV4_GW\""
        echo ""
        echo "# BGP"
        echo "BGP_ENABLED=\"$BGP_ENABLED\""
        echo "BGP_ASN=\"$BGP_ASN\""
        echo "BGP_ROUTER_ID=\"$BGP_ROUTER_ID\""
        echo "BGP_PEER_IPV4=\"$BGP_PEER_IPV4\""
        echo "BGP_PEER_IPV6=\"$BGP_PEER_IPV6\""
        echo "BGP_PEER_ASN=\"$BGP_PEER_ASN\""
        echo ""
        echo "# NAT"
        echo "NAT_POOL=\"$NAT_POOL\""
        echo "NAT_INTERNAL_NETWORKS=(${NAT_INTERNAL_NETWORKS[*]@Q})"
        echo ""
        echo "# Container network"
        echo "CONTAINER_NETWORK=\"$CONTAINER_NETWORK\""
        echo "CONTAINER_GATEWAY=\"$CONTAINER_GATEWAY\""
        echo "CONTAINER_PREFIX=\"$CONTAINER_PREFIX\""
        echo "CONTAINER_IPV6=\"$CONTAINER_IPV6\""
        echo "CONTAINER_IPV6_PREFIX=\"$CONTAINER_IPV6_PREFIX\""
    } > "$CONFIG_FILE"

    log "Configuration saved to $CONFIG_FILE"
}

# =============================================================================
# Main
# =============================================================================

main() {
    # Check we're running as root
    [[ $EUID -ne 0 ]] && { error "This script must be run as root"; exit 1; }

    # Check for --apply-only mode
    if [[ "${1:-}" == "--apply-only" ]]; then
        if [[ -f "$CONFIG_FILE" ]]; then
            log "Applying existing configuration from $CONFIG_FILE"
            # shellcheck disable=SC1090
            source "$CONFIG_FILE"
            generate_configs
            apply_configs
            log "Configuration applied successfully"
            exit 0
        else
            error "No existing configuration found at $CONFIG_FILE"
            exit 1
        fi
    fi

    show_banner

    # Check for existing config
    if [[ -f "$CONFIG_FILE" ]]; then
        warn "Existing configuration found at $CONFIG_FILE"
        if prompt_yes_no "Load existing configuration?"; then
            # shellcheck disable=SC1090
            source "$CONFIG_FILE"
            log "Configuration loaded"
            phase7_confirm
            generate_configs
            apply_configs
            enable_services
            log "Configuration complete!"
            exit 0
        fi
    fi

    # Interactive configuration
    phase1_detect_interfaces
    phase2_assign_roles
    phase3_ip_config
    phase4_management_config
    phase5_bgp_config
    phase6_nat_config
    phase7_confirm

    generate_configs
    apply_configs
    enable_services
    save_configuration

    echo ""
    log "Configuration complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Reboot to apply network changes"
    echo "  2. Verify services: systemctl status vpp-core frr"
    echo "  3. Check VPP: vppctl -s /run/vpp/core-cli.sock show interface"
    echo ""

    if prompt_yes_no "Reboot now?"; then
        reboot
    fi
}

main "$@"
