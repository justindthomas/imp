# IMP Testing Infrastructure Plan

## Overview

This document outlines a testing infrastructure for the IMP router project using Containerlab as the orchestration platform, with a focus on open-source routing projects for day-to-day development and the ability to integrate Cisco IOS for vendor interoperability testing when needed.

## Goals

1. **Fast local iteration** - Spin up test topologies quickly without licensing friction
2. **Protocol validation** - Test OSPF, BGP, and other routing protocols against known-good implementations
3. **VPP dataplane testing** - Validate packet forwarding, NAT, ACLs
4. **Vendor interop** - Occasional testing against Cisco IOS for real-world compatibility
5. **CI/CD integration** - Automated testing on commits

## Technology Stack

### Orchestration: Containerlab

[Containerlab](https://containerlab.dev/) provides:
- Declarative YAML topology definitions
- Virtual network wiring between nodes
- Support for both containers and VMs (via vrnetlab)
- Simple CLI: `containerlab deploy`, `containerlab destroy`
- Works with Docker or Podman

### Virtualization: QEMU/KVM

- No VMware/Broadcom or VirtualBox/Oracle dependencies
- Native Linux performance with KVM acceleration
- vrnetlab packages VM images as containers for Containerlab integration

### Routing Peers (Open Source)

| Project | Protocols | Use Case |
|---------|-----------|----------|
| **FRR** | BGP, OSPF, IS-IS, MPLS, PIM | Primary testing - same stack IMP uses |
| **BIRD** | BGP, OSPF, RIP | IXP-style BGP testing, alternative implementation |
| **GoBGP** | BGP | Programmatic BGP testing, easy to script |
| **OpenBGPD** | BGP | Clean BSD implementation, good for edge cases |

### Cisco IOS (When Needed)

- **vrnetlab** packages Cisco images (IOSv, CSR1000v, IOS-XR) as containers
- Requires Cisco CML license (~$200/year personal) or DevNet access
- Use for final interop validation, not daily development

## Test Topology Design

### Basic BGP Peering Test

```
                    ┌─────────────┐
                    │   Client    │
                    │  (Alpine)   │
                    └──────┬──────┘
                           │ 10.0.1.0/24
                    ┌──────┴──────┐
                    │     IMP     │
                    │   Router    │
                    │  (AS 65001) │
                    └──────┬──────┘
                           │ 192.168.100.0/24
                    ┌──────┴──────┐
                    │  FRR Peer   │
                    │  (AS 65002) │
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  Internet   │
                    │  (simulated)│
                    └─────────────┘
```

### Multi-Peer BGP + OSPF Test

```
                         ┌─────────────┐
                         │  FRR Peer 1 │
                         │  (AS 65002) │
                         └──────┬──────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
 ┌──────┴──────┐         ┌──────┴──────┐         ┌──────┴──────┐
 │  BIRD Peer  │         │     IMP     │         │  FRR Peer 2 │
 │  (AS 65003) │◄───────►│   Router    │◄───────►│  (AS 65004) │
 └─────────────┘  OSPF   │  (AS 65001) │  OSPF   └─────────────┘
                         └──────┬──────┘
                                │
                         ┌──────┴──────┐
                         │   Clients   │
                         │  (NAT pool) │
                         └─────────────┘
```

## Containerlab Topology Files

### `lab/basic-bgp.clab.yml`

```yaml
name: imp-basic-bgp

topology:
  nodes:
    imp:
      kind: linux
      image: imp-router:latest
      binds:
        - /dev/hugepages:/dev/hugepages
      exec:
        - ip addr add 192.168.100.1/24 dev eth1
        - ip addr add 10.0.1.1/24 dev eth2

    frr-peer:
      kind: linux
      image: frrouting/frr:latest
      binds:
        - lab/configs/frr-peer:/etc/frr

    client:
      kind: linux
      image: alpine:latest
      exec:
        - ip route add default via 10.0.1.1

  links:
    - endpoints: ["imp:eth1", "frr-peer:eth0"]
    - endpoints: ["imp:eth2", "client:eth0"]
```

### `lab/cisco-interop.clab.yml`

```yaml
name: imp-cisco-interop

topology:
  nodes:
    imp:
      kind: linux
      image: imp-router:latest

    cisco-csr:
      kind: cisco_csr1000v
      image: vrnetlab/vr-csr:17.03.04

    frr-rr:
      kind: linux
      image: frrouting/frr:latest

  links:
    - endpoints: ["imp:eth1", "cisco-csr:Gi2"]
    - endpoints: ["imp:eth2", "frr-rr:eth0"]
    - endpoints: ["cisco-csr:Gi3", "frr-rr:eth1"]
```

## IMP Router Container Image

Build a container image that can boot the IMP system for testing:

### `lab/Dockerfile.imp`

```dockerfile
FROM debian:bookworm

# Install test dependencies
RUN apt-get update && apt-get install -y \
    iproute2 \
    iputils-ping \
    tcpdump \
    iperf3 \
    && rm -rf /var/lib/apt/lists/*

# Copy IMP configuration and scripts
COPY --from=imp-build /usr/local/bin/imp /usr/local/bin/
COPY --from=imp-build /usr/local/bin/imp_*.py /usr/local/bin/
COPY --from=imp-build /etc/imp /etc/imp

# VPP and FRR would need special handling for container mode
# May need to run VPP in polling mode without DPDK for testing

CMD ["/bin/bash"]
```

**Note:** Full VPP+DPDK testing may require VM mode rather than containers due to hardware requirements. Consider:
- Container mode for control plane testing (FRR, configuration)
- VM mode for dataplane testing (VPP, NAT, ACLs)

## Test Categories

### 1. Control Plane Tests

- BGP session establishment
- OSPF adjacency formation
- Route advertisement and reception
- Route filtering and policy
- Graceful restart
- BFD integration

### 2. Dataplane Tests

- IPv4/IPv6 forwarding
- NAT44 (det44) translation
- ACL permit/deny
- VLAN handling
- QoS marking

### 3. Configuration Tests

- `imp` REPL commands
- `imp agent` LLM interactions
- Config persistence across reboots
- Template rendering

### 4. Integration Tests

- Full boot from ZFS
- Service startup order
- Failover scenarios

## Test Scripts

### `lab/tests/test_bgp_basic.sh`

```bash
#!/bin/bash
# Test basic BGP peering

set -e

# Deploy topology
containerlab deploy -t lab/basic-bgp.clab.yml

# Wait for convergence
sleep 30

# Verify BGP session
docker exec clab-imp-basic-bgp-imp vtysh -c "show bgp summary" | grep -q "Established"

# Verify route reception
docker exec clab-imp-basic-bgp-imp vtysh -c "show ip route" | grep -q "192.168.200.0"

# Test connectivity
docker exec clab-imp-basic-bgp-client ping -c 3 192.168.200.1

# Cleanup
containerlab destroy -t lab/basic-bgp.clab.yml

echo "PASS: Basic BGP test"
```

### `lab/tests/test_nat.sh`

```bash
#!/bin/bash
# Test NAT functionality

set -e

containerlab deploy -t lab/nat-test.clab.yml

# Verify NAT translation
docker exec clab-nat-test-client curl -s http://external-server/ip | grep -q "23.177.24."

# Check NAT session table
docker exec clab-nat-test-imp vppctl -s /run/vpp/nat-cli.sock show det44 sessions

containerlab destroy -t lab/nat-test.clab.yml

echo "PASS: NAT test"
```

## CI/CD Integration

### GitHub Actions Workflow

```yaml
# .github/workflows/test.yml
name: Integration Tests

on: [push, pull_request]

jobs:
  containerlab-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install Containerlab
        run: |
          bash -c "$(curl -sL https://get.containerlab.dev)"

      - name: Build IMP container
        run: |
          docker build -t imp-router:latest -f lab/Dockerfile.imp .

      - name: Run BGP tests
        run: |
          ./lab/tests/test_bgp_basic.sh

      - name: Run NAT tests
        run: |
          ./lab/tests/test_nat.sh
```

## Directory Structure

```
imp/
├── lab/
│   ├── topologies/
│   │   ├── basic-bgp.clab.yml
│   │   ├── multi-peer.clab.yml
│   │   ├── cisco-interop.clab.yml
│   │   └── nat-test.clab.yml
│   ├── configs/
│   │   ├── frr-peer/
│   │   │   ├── frr.conf
│   │   │   └── daemons
│   │   ├── bird-peer/
│   │   │   └── bird.conf
│   │   └── imp/
│   │       └── router.json
│   ├── tests/
│   │   ├── test_bgp_basic.sh
│   │   ├── test_bgp_filtering.sh
│   │   ├── test_ospf.sh
│   │   ├── test_nat.sh
│   │   └── test_config.sh
│   ├── Dockerfile.imp
│   └── README.md
└── TESTING_PLAN.md
```

## Implementation Phases

### Phase 1: Basic Infrastructure
- [ ] Set up Containerlab on development machine
- [ ] Create IMP container image (control plane only)
- [ ] Basic FRR peer configuration
- [ ] Single BGP peering test

### Phase 2: Protocol Testing
- [ ] Multi-peer BGP topology
- [ ] OSPF adjacency tests
- [ ] Route filtering tests
- [ ] Add BIRD as alternative peer

### Phase 3: Dataplane Testing
- [ ] VM-based IMP for VPP testing
- [ ] NAT validation tests
- [ ] ACL tests
- [ ] Performance benchmarks with iperf3

### Phase 4: Vendor Interop
- [ ] Set up vrnetlab with Cisco images
- [ ] Cisco CSR1000v BGP test
- [ ] Document any compatibility issues

### Phase 5: CI/CD
- [ ] GitHub Actions workflow
- [ ] Automated test on PR
- [ ] Test result reporting

## Resources

- [Containerlab Documentation](https://containerlab.dev/)
- [vrnetlab - VM images as containers](https://containerlab.dev/manual/vrnetlab/)
- [FRR Documentation](https://docs.frrouting.org/)
- [BIRD Documentation](https://bird.network.cz/)
- [Cisco DevNet Sandbox](https://developer.cisco.com/site/sandbox/)

## Notes

- VPP with DPDK requires specific CPU flags and hugepages - may need dedicated test VM
- Cisco images require separate licensing - don't commit to repo
- Consider using GoBGP for programmatic test scenarios (inject routes, simulate failures)
- For CI, use lightweight FRR containers; reserve Cisco testing for release validation
