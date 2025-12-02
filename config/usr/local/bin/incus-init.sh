#!/bin/bash
#
# incus-init.sh - Initialize Incus for IMP dataplane integration
#
# Run this once after first boot to configure Incus networking.
# The bridge is configured to route through VPP rather than using
# Incus's built-in NAT.
#
# Usage: incus-init.sh [--non-interactive]
#

set -euo pipefail

NON_INTERACTIVE="${1:-}"

echo "Initializing Incus for IMP..."

# Check if Incus is already initialized
if incus network show incusbr0 &>/dev/null; then
    echo "Incus already initialized, updating network configuration..."
else
    echo "Running incus admin init..."
    if [[ "$NON_INTERACTIVE" == "--non-interactive" ]]; then
        # Minimal preseed for non-interactive init
        cat <<EOF | incus admin init --preseed
config: {}
networks:
- config:
    ipv4.address: 10.234.116.1/24
    ipv4.nat: "false"
    ipv6.address: none
  description: ""
  name: incusbr0
  type: bridge
storage_pools:
- config: {}
  description: ""
  name: default
  driver: dir
profiles:
- config: {}
  description: ""
  devices:
    eth0:
      name: eth0
      network: incusbr0
      type: nic
    root:
      path: /
      pool: default
      type: disk
  name: default
EOF
    else
        incus admin init
    fi
fi

echo "Configuring incusbr0 for VPP integration..."

# Disable Incus NAT - VPP handles NAT
incus network set incusbr0 ipv4.nat=false
incus network set incusbr0 ipv6.nat=false

# Set gateway to VPP's host-interface address
# Containers will route through VPP for internet access
incus network set incusbr0 ipv4.dhcp.gateway=10.234.116.5

# Set DHCP range (leave .1-.99 for static assignments)
incus network set incusbr0 ipv4.dhcp.ranges="10.234.116.100-10.234.116.254"

# Disable IPv6 on the bridge - VPP handles RA on host-incus-dataplane
incus network set incusbr0 ipv6.address=none

echo ""
echo "Incus network configuration:"
incus network show incusbr0

echo ""
echo "Incus initialization complete."
echo ""
echo "Next steps:"
echo "  1. Ensure vpp-core and incus-dataplane services are running"
echo "  2. Launch containers: incus launch images:debian/12 mycontainer"
echo "  3. Containers will get DHCP from incusbr0, route via VPP"
