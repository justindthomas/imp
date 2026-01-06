# VPP Modules

IMP uses a modular VPP architecture where secondary VPP instances run as separate processes connected to the core via memif shared memory interfaces.

## Overview

### Why Modules?

VPP's det44 (deterministic NAT) is architecturally incompatible with the linux-cp plugin used for FRR integration. The det44 `out2in` node drops packets that don't match NAT mappings before they reach linux-cp, breaking routing visibility. Running NAT in a separate VPP instance connected via memif solves this issue.

This modular approach also enables:
- **Isolation**: Module failures don't affect core routing
- **Flexibility**: Add/remove services without recompiling
- **Scalability**: Dedicated CPU cores per module
- **Extensibility**: Add new services (NAT64, IDS, etc.) as modules

### Architecture

```
                    ┌──────────┐
                    │   NAT    │  (module)
                    │ (det44)  │
                    └────▲─────┘
                         │ memif
┌──────────┐       ┌─────┴─────┐
│  Core    │◄─────►│   Core    │
│  (DPDK)  │       │   VPP     │
└──────────┘       └─────┬─────┘
                         │ memif
                    ┌────▼─────┐
                    │  NAT64   │  (module)
                    │          │
                    └──────────┘
```

Traffic flow:
1. Packet arrives at core VPP external interface
2. ACL-based forwarding (ABF) steers matching traffic to module memif
3. Module processes packet (NAT translation, etc.)
4. Module returns packet to core via memif
5. Core VPP forwards to destination

## Quick Start

```bash
# List available module examples
imp> config modules available

# Install NAT module definition
imp> config modules install nat

# Enable NAT module
imp> config modules enable nat

# Configure NAT mappings
imp> config nat mappings add 192.168.0.0/16 23.177.24.96/30

# Apply changes (generates configs, restarts services)
imp> apply
```

## Module YAML Reference

Module definitions are stored in `/persistent/config/modules/<name>.yaml`.

### Basic Structure

```yaml
name: nat                              # Module identifier (lowercase, no spaces)
display_name: "Deterministic NAT"      # Human-readable name
description: "IPv4 NAT using det44"    # Brief description

topology:
  connections:
    - name: internal                   # Connection identifier
      purpose: "Traffic from LANs"     # Description
      create_lcp: false                # Expose to Linux?

plugins:
  - memif_plugin.so                    # Required plugins
  - det44_plugin.so

disable_plugins:
  - dpdk_plugin.so                     # Plugins to disable

cpu:
  min_cores: 0                         # Minimum dedicated cores
  ideal_cores: 2                       # Preferred cores

config_schema:                         # User-configurable fields
  bgp_prefix:
    type: string
    format: ipv4_cidr

show_commands:                         # CLI/agent show commands
  - name: sessions
    vpp_command: "show det44 sessions"
    description: "Active sessions"

abf:                                   # Traffic steering rules
  source: internal_interfaces
  exclude:
    - container_network
    - bypass_pairs

commands: |                            # VPP commands (Jinja2)
  det44 plugin enable
  ...
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Module identifier (lowercase, a-z, 0-9, -, _) |
| `display_name` | string | Human-readable name |
| `description` | string | Brief description |
| `topology.connections` | list | Memif connections to core |
| `topology.connections[].name` | string | Connection identifier |
| `topology.connections[].purpose` | string | Description |
| `topology.connections[].create_lcp` | bool | Create Linux interface for FRR visibility |
| `plugins` | list | VPP plugins to load |
| `disable_plugins` | list | VPP plugins to disable |
| `cpu.min_cores` | int | Minimum dedicated cores (0 = share) |
| `cpu.ideal_cores` | int | Preferred dedicated cores |
| `config_schema` | dict | Schema for user-configurable fields |
| `show_commands` | list | VPP show commands exposed in CLI |
| `abf` | dict | ACL-based forwarding rules |
| `commands` | string | Jinja2 template for VPP commands |

### ABF Configuration

The `abf` field controls how core VPP steers traffic to the module.

**Source-based (NAT-style):**
```yaml
abf:
  source: internal_interfaces    # Apply to all internal interfaces
  exclude:
    - container_network          # Don't match container traffic
    - bypass_pairs               # Respect configured bypasses
```

**Destination-based (NAT64-style):**
```yaml
abf:
  match: destination_prefix
  prefix_field: prefix           # Use config.prefix value
```

### Commands Template Variables

The `commands` field is a Jinja2 template with access to:

| Variable | Description |
|----------|-------------|
| `module` | Module instance object |
| `module.connections` | List of allocated connections |
| `module.connections[].socket_id` | Memif socket ID |
| `module.connections[].core_ip` | IP on core side |
| `module.connections[].module_ip` | IP on module side |
| `module.config` | User configuration from router.json |
| `external` | External interface config |
| `internal` | List of internal interfaces |
| `container` | Container network config |

## Configuration via CLI

### Module Management

```bash
# List available module examples
imp> config modules available

# Install from examples (copies to /persistent/config/modules/)
imp> config modules install nat

# List installed modules and status
imp> config modules list

# Enable a module
imp> config modules enable nat

# Disable a module (keeps config)
imp> config modules disable nat
```

### Module Configuration

Once enabled, configure module-specific settings:

```bash
# NAT configuration
imp> config nat mappings add 192.168.0.0/16 23.177.24.96/30
imp> config nat bypass add 192.168.0.0/16 10.0.0.0/8
imp> config nat set-prefix 23.177.24.96/29

# View configuration
imp> config nat show
```

### Applying Changes

```bash
# Review pending changes
imp> show config

# Apply changes (regenerates configs, restarts services)
imp> apply
```

## Writing Custom Modules

### Step 1: Create Module Definition

Copy an existing example:
```bash
cp /usr/share/imp/module-examples/nat.yaml /persistent/config/modules/mymodule.yaml
```

Edit the YAML:
```yaml
name: mymodule
display_name: "My Custom Module"
description: "Custom VPP processing module"

topology:
  connections:
    - name: traffic
      purpose: "Receives and returns processed traffic"
      create_lcp: false

plugins:
  - memif_plugin.so
  - my_plugin.so

disable_plugins:
  - dpdk_plugin.so

config_schema:
  setting1:
    type: string
    description: "A custom setting"

commands: |
  {% set conn = module.connections | first %}
  # My custom VPP commands here
  my_plugin enable
  ...
```

### Step 2: Enable and Configure

```bash
imp> config modules enable mymodule
imp> config mymodule setting1 value
imp> apply
```

### Step 3: Test

```bash
# Check module service
systemctl status vpp-mymodule

# Access module CLI
imp> shell mymodule
```

## Troubleshooting

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Module 'X' not found" | Not installed | `config modules install X` |
| "YAML syntax error" | Invalid YAML | Check YAML syntax |
| "Missing required field" | Incomplete YAML | Add required fields |
| "Template syntax error" | Invalid Jinja2 in commands | Check template syntax |
| Socket not found | Service not running | Check `systemctl status vpp-<name>` |

### Checking Module Status

```bash
# Service status
systemctl status vpp-nat

# View logs
journalctl -u vpp-nat -f

# Check socket exists
ls -la /run/vpp/*-cli.sock

# Connect to module CLI
vppctl -s /run/vpp/nat-cli.sock
```

### Validating Module YAML

The module loader validates:
1. YAML syntax
2. Required fields (`name`, `topology.connections`, `commands`)
3. Unique connection names
4. Valid Jinja2 template syntax
5. Config schema field types

Validation errors are shown during `apply`.

## Resource Allocation

### Memif Addresses

Each module connection gets sequential socket IDs and /31 IP pairs:

```
Socket 1: 169.254.1.0/31 (core .0 ↔ module .1)
Socket 2: 169.254.1.2/31 (core .2 ↔ module .3)
Socket 3: 169.254.1.4/31 (core .4 ↔ module .5)
...
```

### CPU Cores

Modules request cores via `cpu.min_cores` and `cpu.ideal_cores`. The system auto-allocates from the configured `module_pool` in router.json:

```json
{
  "cpu": {
    "module_pool": "6-7"
  }
}
```

Allocation priority: modules with higher `ideal_cores` get allocated first.

## File Locations

| Path | Purpose |
|------|---------|
| `/persistent/config/modules/*.yaml` | Module definitions |
| `/persistent/config/router.json` | Module enable/config |
| `/usr/share/imp/module-examples/` | Example modules (shipped) |
| `/etc/vpp/startup-<name>.conf` | Generated startup config |
| `/etc/vpp/commands-<name>.txt` | Generated VPP commands |
| `/etc/systemd/system/vpp-<name>.service` | Generated systemd service |
| `/run/vpp/<name>-cli.sock` | Runtime CLI socket |

## Complete NAT Example

### Module Definition (`/persistent/config/modules/nat.yaml`)

```yaml
name: nat
display_name: "Deterministic NAT (det44)"
description: "Carrier-grade NAT for IPv4"

topology:
  connections:
    - name: internal
      purpose: "Traffic from internal networks"
      create_lcp: false
    - name: external
      purpose: "Translated traffic to internet"
      create_lcp: true

plugins:
  - memif_plugin.so
  - det44_plugin.so

disable_plugins:
  - dpdk_plugin.so

cpu:
  min_cores: 0
  ideal_cores: 2

config_schema:
  bgp_prefix:
    type: string
    format: ipv4_cidr
  mappings:
    type: array
  bypass_pairs:
    type: array

show_commands:
  - name: sessions
    vpp_command: "show det44 sessions"
  - name: mappings
    vpp_command: "show det44 mappings"

abf:
  source: internal_interfaces
  exclude:
    - container_network
    - bypass_pairs

commands: |
  det44 plugin enable
  {% set int_conn = module.connections | selectattr('name', 'eq', 'internal') | first %}
  {% set ext_conn = module.connections | selectattr('name', 'eq', 'external') | first %}
  set interface det44 inside memif{{ int_conn.socket_id }}/0 outside memif{{ ext_conn.socket_id }}/0
  {% for mapping in module.config.mappings %}
  det44 add in {{ mapping.source_network }} out {{ mapping.nat_pool }}
  {% endfor %}
  ip route add 0.0.0.0/0 via {{ ext_conn.core_ip }} memif{{ ext_conn.socket_id }}/0
```

### router.json Configuration

```json
{
  "modules": [
    {
      "name": "nat",
      "enabled": true,
      "config": {
        "bgp_prefix": "23.177.24.96/29",
        "mappings": [
          {"source_network": "192.168.20.0/24", "nat_pool": "23.177.24.96/30"}
        ],
        "bypass_pairs": [
          {"source": "192.168.20.0/24", "destination": "10.0.0.0/8"}
        ]
      }
    }
  ]
}
```
