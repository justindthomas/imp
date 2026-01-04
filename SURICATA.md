# Suricata-VPP Integration for IMP

This document outlines integration approaches for adding Suricata IDS/IPS capabilities to IMP's VPP-based dataplane.

## Why Suricata + VPP?

IMP uses VPP (Vector Packet Processing) for high-performance packet forwarding. Adding Suricata provides:

- **Deep Packet Inspection (DPI)** - Protocol analysis beyond L3/L4
- **Intrusion Detection** - Signature-based threat detection (ET, Proofpoint rules)
- **Intrusion Prevention** - Inline blocking of malicious traffic
- **Compliance** - Network monitoring requirements

The challenge is integrating Suricata's inspection engine with VPP's dataplane without sacrificing performance.

## Integration Approaches

### Option A: DPDK Memif

Suricata's native DPDK capture mode with memif virtual device.

**How it works:**
- VPP creates memif socket, Suricata connects as DPDK secondary process
- Packets flow through memif interfaces between VPP and Suricata
- Uses Suricata's existing DPDK code path

**Pros:**
- Uses existing Suricata capture code
- Minimal custom development
- Zero-copy possible with proper configuration

**Cons:**
- Requires DPDK-enabled Suricata build
- Hugepage memory sharing complexity
- Two processes competing for DPDK resources

### Option B: VPP Plugin + Library Mode

Create a VPP plugin similar to the existing Snort plugin.

**How it works:**
- VPP plugin with shared memory queue pairs (like snort plugin)
- Suricata 8.0+ library mode with queue-based packet injection
- Explicit verdict passing via shared memory

**Pros:**
- Tightest integration, lowest latency
- Full control over packet flow
- Single process possible (Suricata as library)

**Cons:**
- Significant development effort
- Requires Suricata 8.0+ for library mode
- Custom DAQ-like module needed

### Option C: Custom Suricata Source Module

Implement a native Suricata capture module.

**How it works:**
- New source module: `source-vpp.c`, `runmode-vpp.c`
- Reads from VPP shared memory queues
- Standard Suricata pipeline after capture

**Pros:**
- Clean separation of concerns
- Standard Suricata processing pipeline

**Cons:**
- Requires Suricata source modification
- Upstream maintenance burden
- Rebuild required for updates

## Recommended: Memif IPS Architecture

For IMP, the memif-based IPS approach provides the best balance of capability and complexity.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Dataplane Namespace                        │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                      VPP Core                            │   │
│  │                                                          │   │
│  │  dpdk-input ──► ip4-input ──► suricata-enq ──────────┐   │   │
│  │       ▲                                               │   │   │
│  │       │                                         memif-in   │   │
│  │       │                                               │   │   │
│  │  dpdk-output ◄── ip4-rewrite ◄── suricata-deq ◄──────┘   │   │
│  │                                               │           │   │
│  │                                          memif-out        │   │
│  └──────────────────────────────────────────┼───┼───────────┘   │
│                                              │   │               │
│  ┌──────────────────────────────────────────┼───┼───────────┐   │
│  │              Suricata (DPDK IPS mode)    │   │           │   │
│  │                                          ▼   │           │   │
│  │         memif-in ──► detection ──► memif-out           │   │
│  │                          │                               │   │
│  │                     [drop malicious]                     │   │
│  │                          │                               │   │
│  │                     eve.json alerts                      │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Traffic Flow

1. Packet arrives at VPP external interface
2. VPP diverts to memif-suricata-in (via ABF or feature arc)
3. Suricata receives packet via DPDK memif vdev
4. Suricata inspects against loaded rules
5. **Pass verdict**: Suricata sends to memif-suricata-out
6. **Drop verdict**: Suricata discards packet (never returns)
7. VPP receives on memif-suricata-out, continues normal forwarding

The verdict is implicit - packets that pass inspection return to VPP; dropped packets simply don't return.

### VPP Configuration

```
# Create memif socket for Suricata
create memif socket id 10 filename /run/vpp/memif-suricata.sock

# Create bidirectional memif interfaces
create memif id 0 socket-id 10 master
create memif id 1 socket-id 10 master
set interface state memif10/0 up
set interface state memif10/1 up

# ABF rule to divert traffic to Suricata
# (specifics depend on which traffic to inspect)
```

### Suricata Configuration

```yaml
# suricata.yaml
dpdk:
  eal-params:
    proc-type: secondary
    file-prefix: suricata

  interfaces:
    - interface: net_memif0
      copy-mode: ips
      copy-iface: net_memif1
      threads: auto
```

### Key Components

**VPP Side:**
- Two memif interfaces (in and out)
- ABF or feature arc to divert selected traffic
- Timeout handling for Suricata failures

**Suricata Side:**
- DPDK capture with memif PMD
- IPS mode with copy-iface for bidirectional flow
- Workers runmode for parallelization

## Performance Considerations

| Factor | Approach |
|--------|----------|
| **Latency** | Memif is zero-copy; Suricata adds microseconds per packet |
| **Throughput** | Workers mode parallelizes; 10+ Gbps possible |
| **Flow ordering** | DPDK RSS + Suricata cluster_flow maintains order |
| **Memory** | Hugepages shared between VPP and Suricata |

### Optimization Strategies

1. **Bypass known-good flows** - Suricata can bypass TLS after handshake
2. **Selective inspection** - Only inspect traffic matching ABF rules
3. **Rule tuning** - Disable expensive rules for high-volume traffic
4. **CPU pinning** - Dedicate cores to Suricata workers

## Challenges and Mitigations

| Challenge | Mitigation |
|-----------|------------|
| Latency in forwarding path | Bypass for bulk/trusted flows |
| Packet ordering issues | Flow-based load balancing |
| Suricata crash = traffic drop | VPP failopen timeout, watchdog |
| DPDK memory conflicts | Careful hugepage allocation |
| Rule update restarts | Suricata reload-rules via socket |

## Alternative: Hybrid IDS + Reactive Blocking

If inline IPS is too complex or risky, consider:

1. **IDS Mode**: Suricata receives copy of traffic (not inline)
2. **Alert Processing**: Parse eve.json for actionable alerts
3. **Reactive Blocking**: Use VPP API to add ACL for offending flows

```
Traffic ──► VPP ──► [mirror] ──► Suricata (IDS)
              │                       │
              │                   eve.json
              │                       │
              ◄─── VPP API ◄─── Alert processor
              (add ACL)
```

**Advantages:**
- Simpler architecture, no inline latency
- Suricata failure doesn't affect forwarding
- Good for learning/tuning before going inline

**Disadvantages:**
- First packet of attack gets through
- Delay between detection and blocking

## Implementation Phases

### Phase 1: IDS Mode (Proof of Concept)
- Run Suricata in container with AF_PACKET on veth
- Mirror traffic from VPP via host-interface
- Verify detection with test traffic

### Phase 2: DPDK Integration
- Build Suricata with DPDK support
- Configure memif connectivity
- Test IDS mode with DPDK capture

### Phase 3: IPS Mode
- Switch to inline (copy-mode: ips)
- Implement ABF rules for traffic diversion
- Add failopen handling

### Phase 4: Production Hardening
- Alerting and log forwarding
- Rule update automation
- Performance tuning

## References

- [VPP Snort Plugin](https://github.com/FDio/vpp/tree/master/src/plugins/snort) - Reference for VPP IDS integration
- [Suricata DPDK Documentation](https://docs.suricata.io/en/latest/capture-hardware/dpdk.html)
- [Suricata Packet Acquisition API](https://redmine.openinfosecfoundation.org/projects/suricata/wiki/Packet_Acquisition_API)
- [VPP-Suricata Forum Discussion](https://forum.suricata.io/t/vpp-suricata-integration-library-mode-packet-injection/6078)
- [DPDK Suricata Integration](https://www.dpdk.org/elevating-network-security-performance-suricatas-integration-with-dpdk/)
