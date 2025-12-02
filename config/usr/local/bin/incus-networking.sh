#!/usr/bin/env bash

export CMD="vppctl -s /run/vpp/core-cli.sock"

ip link add incus-host type veth peer name incus-dataplane
ip link set incus-host master incusbr0
ip link set incus-host up
ip link set incus-dataplane netns dataplane
ip netns exec dataplane ip link set incus-dataplane up

$CMD create host-interface name incus-dataplane
$CMD set int state host-incus-dataplane up
$CMD set int ip address host-incus-dataplane 10.234.116.5/24
$CMD set int ip address host-incus-dataplane 2602:f90e:10:1:ffff:ffff:ffff:fffe/64

$CMD set acl-plugin acl deny src 10.234.116.0/24 dst 192.168.0.0/16, deny src 10.234.116.0/24 dst 172.16.0.0/12, deny src 10.234.116.0/24 dst 10.0.0.0/8, permit src 10.234.116.0/24
$CMD abf policy add id 1 acl 1 via 169.254.1.5 memif1/0
$CMD abf attach ip4 policy 1 host-incus-dataplane

sleep 15

$CMD ip6 nd host-incus-dataplane ra-interval 30 15
$CMD ip6 nd host-incus-dataplane prefix 2602:f90e:10:1::/64 infinite
$CMD ip6 nd host-incus-dataplane no ra-suppress
